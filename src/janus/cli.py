"""`janus` command line: `doctor` (M0), `run` (M5)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import get_args

from playwright.sync_api import sync_playwright

from janus.agent import run_task
from janus.config import get_settings
from janus.executor.escalation import cli_escalation, make_granted_ops
from janus.llm import LLMClient, LLMError
from janus.planner.ops import OpKind
from janus.task import load_task_file
from janus.validator.policy import Policy, origin_of


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
    missing = 0
    for model in settings.required_models:
        if has_model(installed, model):
            print(f"ok    model {model}")
        else:
            print(f"FAIL  model {model} missing")
            missing += 1
    return 1 if missing else 0


def run(task_path: Path) -> int:
    task = load_task_file(task_path)
    settings = get_settings()
    llm = LLMClient(settings)
    policy = Policy(
        allowed_origins=frozenset({origin_of(task.start_url)}),
        allowed_ops=frozenset(get_args(OpKind)),
        max_steps=20,
    )
    granted_ops = make_granted_ops(task.approvals)

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page()
                result = run_task(
                    page,
                    task_id=task.id,
                    instruction=task.instruction.en,
                    start_url=task.start_url,
                    inputs=task.inputs,
                    policy=policy,
                    llm=llm,
                    settings=settings,
                    granted_ops=granted_ops,
                    escalate=cli_escalation,
                )
            finally:
                browser.close()
    finally:
        llm.close()

    print(f"{result.status}  ({result.steps_run} steps, {result.replans} replans)")
    return 0 if result.status == "completed" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="janus")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="check Ollama and required models")
    run_parser = sub.add_parser("run", help="run a task end to end")
    run_parser.add_argument("--task", required=True, type=Path, help="path to a task YAML file")
    args = parser.parse_args(argv)
    if args.command == "doctor":
        return doctor()
    if args.command == "run":
        return run(args.task)
    return 2


if __name__ == "__main__":
    sys.exit(main())
