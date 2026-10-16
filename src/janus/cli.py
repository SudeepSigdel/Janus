"""`janus` command line: `doctor` (M0), `run` (M5)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from janus.agent import run_task
from janus.config import get_settings
from janus.executor.escalation import cli_escalation
from janus.goal import extract_answer, extract_inputs
from janus.llm import LLMClient, LLMError
from janus.observer.extract import extract_snapshot
from janus.report import render_report
from janus.task import load_task_file
from janus.validator.policy import Policy, allowed_ops_for, origin_of


def has_model(installed: list[str], wanted: str) -> bool:
    names = set(installed)
    return wanted in names or f"{wanted}:latest" in names


def doctor() -> int:
    settings = get_settings()
    client = LLMClient(settings)
    try:
        installed = client.list_models()
    except LLMError as exc:
        print(f"FAIL  Ollama not reachable at {settings.ollama_base_url}: {exc}")
        return 1
    finally:
        client.close()
    print(f"ok    Ollama reachable at {settings.ollama_base_url}")
    if settings.chat_base_url:
        print(f"ok    hosted chat {settings.chat_model} at {settings.chat_base_url}")
    missing = 0
    for model in settings.required_models:
        if has_model(installed, model):
            print(f"ok    model {model}")
        else:
            print(f"FAIL  model {model} missing")
            missing += 1
    return 1 if missing else 0


def _recorder(report: Path | None):
    """A `trace` callback that keeps events, or None when no report was asked for."""
    events: list[dict] = []
    if report is None:
        return events, None

    def trace(stage: str, data: dict) -> None:
        events.append({"stage": stage, "data": data})

    return events, trace


def _write_report(report: Path | None, events: list[dict], title: str, subtitle: str) -> None:
    if report is None:
        return
    report.write_text(render_report(events, title=title, subtitle=subtitle), encoding="utf-8")
    print(f"report: {report}")


def run_goal(url: str, goal: str, *, headed: bool, answer: bool, report: Path | None = None) -> int:
    """B0: run from a goal string and a start URL, with a human approving every
    consequential action at the prompt."""
    settings = get_settings()
    llm = LLMClient(settings)
    events, trace = _recorder(report)
    try:
        inputs = extract_inputs(goal, llm)
        if inputs:
            print("inputs from your goal: " + ", ".join(f"{k}={v!r}" for k, v in inputs.items()))
        policy = Policy(
            allowed_origins=frozenset({origin_of(url)}),
            allowed_ops=allowed_ops_for(False),
            max_steps=20,
        )
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=not headed)
            try:
                page = browser.new_context(service_workers="block").new_page()
                result = run_task(
                    page,
                    task_id="goal",
                    instruction=goal,
                    start_url=url,
                    inputs=inputs,
                    policy=policy,
                    llm=llm,
                    settings=settings,
                    escalate=cli_escalation,
                    trace=trace,
                )
                if answer:
                    found = extract_answer(goal, extract_snapshot(page, settings), llm)
                    print(f"answer: {found if found is not None else '(not found on the page)'}")
            finally:
                browser.close()
    finally:
        llm.close()
    _write_report(report, events, "Janus run", goal)
    print(f"{result.status}  ({result.steps_run} steps, {result.replans} replans)")
    return 0 if result.status == "completed" else 1


def run(task_path: Path, report: Path | None = None) -> int:
    task = load_task_file(task_path)
    settings = get_settings()
    llm = LLMClient(settings)
    events, trace = _recorder(report)
    policy = Policy(
        allowed_origins=frozenset({origin_of(task.start_url)}),
        allowed_ops=allowed_ops_for(task.allow_navigate),
        max_steps=20,
        sensitive_fields=frozenset(task.sensitive_field_names),
    )
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_context(service_workers="block").new_page()
                result = run_task(
                    page,
                    task_id=task.id,
                    instruction=task.instruction.en,
                    start_url=task.start_url,
                    inputs=task.inputs,
                    policy=policy,
                    llm=llm,
                    settings=settings,
                    approvals=task.approvals,
                    escalate=cli_escalation,
                    trace=trace,
                )
            finally:
                browser.close()
    finally:
        llm.close()

    _write_report(report, events, task.id, task.instruction.en)
    print(f"{result.status}  ({result.steps_run} steps, {result.replans} replans)")
    return 0 if result.status == "completed" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="janus")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="check Ollama and required models")
    run_parser = sub.add_parser("run", help="run a task end to end")
    source = run_parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--task", type=Path, help="path to a task YAML file")
    source.add_argument("--goal", help="what to do, in English or Nepali (needs --url)")
    run_parser.add_argument("--url", help="start URL for --goal; also its only allowed origin")
    run_parser.add_argument("--headed", action="store_true", help="show the browser window")
    run_parser.add_argument(
        "--answer", action="store_true", help="after the run, answer the goal from the page"
    )
    run_parser.add_argument(
        "--report", type=Path, help="write an HTML report of every decision to this file"
    )
    args = parser.parse_args(argv)
    if args.command == "doctor":
        return doctor()
    if args.command == "run":
        if args.goal is not None:
            if not args.url:
                parser.error("--goal needs --url")
            return run_goal(
                args.url,
                args.goal,
                headed=args.headed,
                answer=args.answer,
                report=args.report,
            )
        return run(args.task, report=args.report)
    return 2


if __name__ == "__main__":
    sys.exit(main())
