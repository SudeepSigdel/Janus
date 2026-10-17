"""Run one goal for the web UI: the same policy and loop as `janus run --goal`."""

from __future__ import annotations

from playwright.sync_api import sync_playwright

from janus import sessions
from janus.agent import run_task
from janus.config import Settings
from janus.goal import extract_inputs
from janus.llm import LLMClient, LLMError
from janus.report import render_report
from janus.ui.session import RunSession
from janus.validator.policy import Policy, allowed_ops_for, origin_of


def model_problem(llm: LLMClient, settings: Settings) -> str:
    """A plain-language reason the model can't be used right now, or "" if it can."""
    try:
        installed = llm.list_models()
    except LLMError:
        return (
            f"The local model server (Ollama) is not reachable at {settings.ollama_base_url}. "
            "Start Ollama and try again."
        )
    if settings.chat_base_url:
        return ""
    names = set(installed)
    if settings.planner_model not in names and f"{settings.planner_model}:latest" not in names:
        return (
            f"The planner model {settings.planner_model} is not installed. Run: ollama create "
            "janus-planner -f models/janus-planner.Modelfile"
        )
    return ""


def execute(session: RunSession, settings: Settings, headed: bool) -> None:
    """Run `session`'s goal to the end, recording everything on the session. Never raises."""
    llm = LLMClient(settings)
    try:
        session.status = "running"
        problem = model_problem(llm, settings)
        if problem:
            session.error = problem
            session.status = "error"
            return
        inputs = extract_inputs(session.goal, llm)
        session.trace("inputs", {"keys": sorted(inputs), "values": dict(inputs)})
        secrets: set[str] = set()
        if session.username:
            inputs["username"] = session.username
        for key, value in (("password", session.password), ("pin", session.pin)):
            if value:
                inputs[key] = value
                secrets.add(key)
        if session.username or secrets:
            session.trace(
                "login_details", {"username": bool(session.username), "secrets": sorted(secrets)}
            )
        policy = Policy(
            allowed_origins=frozenset({origin_of(session.url)}),
            allowed_ops=allowed_ops_for(False, allow_read=True, allow_ask=True),
            max_steps=20,
            prefill="flag",
            secret_inputs=frozenset(secrets),
        )
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=not headed)
            try:
                context = sessions.open_context(browser, session.url, session.remember)
                page = context.new_page()
                result = run_task(
                    page,
                    task_id="ui",
                    instruction=session.goal,
                    start_url=session.url,
                    inputs=inputs,
                    policy=policy,
                    llm=llm,
                    settings=settings.model_copy(
                        update={"max_replan_attempts": settings.interactive_max_replan_attempts}
                    ),
                    escalate=session.escalate,
                    trace=session.trace,
                    stop_after_approved_commit=True,
                    ask_user=session.ask,
                )
                if session.remember and result.status != "blocked":
                    sessions.save(context, session.url)
                    session.trace("session_saved", {})
            finally:
                browser.close()
        session.status = result.status
    except Exception as exc:  # a broken run is shown in the UI, never crashes the server
        session.error = (
            f"{type(exc).__name__}: {str(exc).splitlines()[0][:300] if str(exc) else ''}"
        )
        session.status = "error"
    finally:
        llm.close()
        session.password = session.pin = ""
        session.report_html = render_report(
            list(session.events), title="Janus run", subtitle=session.goal
        )
