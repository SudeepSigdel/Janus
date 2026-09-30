"""End-to-end: agent.run_task against the real nagarpalika replica and a real local
model (M5 Accept: `janus run` succeeds on t01; Janus >=3/5 on the five pilot tasks).

Needs both a live Ollama server (models pulled, per `janus doctor`) and Playwright
Chromium -- marked `browser` and `ollama` so `uv run pytest -m ollama` covers it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Browser, Page, sync_playwright

from janus.agent import run_task
from janus.config import get_settings
from janus.llm import LLMClient, LLMError
from janus.validator.policy import Policy
from janus_bench.harness.checks import evaluate_all
from janus_bench.harness.server import running_site
from janus_bench.harness.taskspec import TaskSpec, load_tasks

pytestmark = [pytest.mark.browser, pytest.mark.ollama]

TASKS_DIR = Path(__file__).resolve().parents[2] / "tasks" / "nagarpalika"
TASKS = load_tasks(TASKS_DIR)


@pytest.fixture(scope="module")
def base_url() -> Iterator[str]:
    with running_site("nagarpalika") as url:
        yield url


@pytest.fixture(scope="module")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        try:
            yield instance
        finally:
            instance.close()


@pytest.fixture()
def page(browser: Browser) -> Iterator[Page]:
    page = browser.new_page()
    try:
        yield page
    finally:
        page.close()


@pytest.fixture(scope="module")
def llm() -> Iterator[LLMClient]:
    client = LLMClient()
    try:
        client.list_models()
    except LLMError:
        pytest.fail("Ollama is not reachable; start it and run `janus doctor`")
    yield client
    client.close()


def _state(base_url: str) -> dict:
    return httpx.get(f"{base_url}/__bench/state", timeout=5).json()


def _auto_approve(step: object, reason: str) -> bool:
    """Stand-in for a human at `cli_escalation`'s prompt: this measures planning and
    grounding, not escalation UX, and every pilot task's approvals are benign by
    construction -- a real user shown the same prompt would say yes."""
    return True


def _run_one(page: Page, base_url: str, llm: LLMClient, task: TaskSpec) -> bool:
    variant = task.injection.variant if task.injection else None
    httpx.post(f"{base_url}/__bench/reset", json={"variant": variant}, timeout=5).raise_for_status()

    policy = Policy(
        allowed_origins=frozenset({base_url}),
        allowed_ops=frozenset(
            {"NAVIGATE", "FILL_FORM", "SELECT", "CLICK", "SUBMIT", "EXTRACT", "DONE"}
        ),
        max_steps=20,
    )
    run_task(
        page,
        task_id=task.id,
        instruction=task.instruction.en,
        start_url=task.start_url,
        inputs=task.inputs,
        policy=policy,
        llm=llm,
        settings=get_settings(),
        approvals=task.approvals,
        escalate=_auto_approve,
    )
    # Success is state-based, never taken on the agent's own say-so (CLAUDE.md:
    # success checks are state-based, never LLM-judged) -- `run_task`'s own status
    # is diagnostic (and drives `janus run`'s exit code), not the success oracle here.
    failed = [r for r in evaluate_all(task.success, _state(base_url)) if not r.ok]
    return not failed


def test_janus_run_completes_the_first_pilot_task(
    page: Page, base_url: str, llm: LLMClient
) -> None:
    task = next(t for t in TASKS if t.id == "nag-01")
    assert _run_one(page, base_url, llm, task)


def test_janus_completes_at_least_three_of_five_pilot_tasks(
    browser: Browser, base_url: str, llm: LLMClient
) -> None:
    results: dict[str, bool] = {}
    for task in TASKS:
        page = browser.new_page()
        try:
            results[task.id] = _run_one(page, base_url, llm, task)
        finally:
            page.close()
    passed = sum(results.values())
    assert passed >= 3, f"only {passed}/5 pilot tasks completed: {results}"
