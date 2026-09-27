"""`janus-bench` command line: serve, run."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from janus_bench.agents.base import Agent
from janus_bench.harness.results import RunRecord, append_record, summary_table
from janus_bench.harness.runner import run_task
from janus_bench.harness.server import SITES, running_site, serve
from janus_bench.harness.taskspec import load_tasks

AVAILABLE_AGENTS = ("null", "oracle", "browser_use", "janus")


def make_agent(name: str) -> Agent:
    if name == "null":
        from janus_bench.agents.null import NullAgent

        return NullAgent()
    if name == "oracle":
        from janus_bench.agents.oracle import OracleAgent

        return OracleAgent()
    if name == "browser_use":
        from janus_bench.agents.browser_use_agent import BrowserUseAgent

        return BrowserUseAgent()
    if name == "janus":
        from janus_bench.agents.janus_agent import JanusAgent

        return JanusAgent()
    raise SystemExit(
        f"agent '{name}' is not available yet (available: {', '.join(AVAILABLE_AGENTS)})"
    )


def run(agent_name: str, tasks_dir: Path, repeats: int, out: Path | None = None) -> int:
    agent = make_agent(agent_name)
    tasks = load_tasks(tasks_dir)
    if not tasks:
        print(f"no tasks found in {tasks_dir}")
        return 2
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = out or Path("results") / f"{agent.name}-{tasks_dir.name}-{stamp}.jsonl"
    records: list[RunRecord] = []
    for site in sorted({t.site for t in tasks}):
        with running_site(site) as base_url:
            for task in (t for t in tasks if t.site == site):
                for repeat in range(1, repeats + 1):
                    result = run_task(task, agent, base_url)
                    record = RunRecord(
                        task=task.id,
                        agent=agent.name,
                        repeat=repeat,
                        success=result.success,
                        steps=result.steps,
                        wall_time=round(result.wall_time, 2),
                        injection_outcome=result.injection_outcome(task),
                        error=result.error,
                    )
                    records.append(record)
                    append_record(out, record)
                    print(f"{'PASS' if result.success else 'FAIL'}  {task.id}  #{repeat}")
                    for failed in result.failed_checks:
                        print(
                            f"      check {failed.check.path} {failed.check.op} "
                            f"{failed.check.value!r}: got {failed.actual!r}"
                        )
                    for violated in result.forbidden_violations:
                        print(f"      FORBIDDEN violated: {violated.check.path}")
                    if result.error:
                        print(f"      error: {result.error}")
    print()
    print(summary_table(records))
    passed = sum(r.success for r in records)
    print(f"{agent.name}: {passed}/{len(records)} passed  (records: {out})")
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):  # Windows consoles default to a non-UTF-8 codepage
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="janus-bench")
    sub = parser.add_subparsers(dest="command", required=True)
    serve_p = sub.add_parser("serve", help="serve a replica site in the foreground")
    serve_p.add_argument("--site", choices=sorted(SITES), default="nagarpalika")
    run_p = sub.add_parser("run", help="run an agent over a task directory")
    run_p.add_argument("--agent", required=True)
    run_p.add_argument("--tasks", type=Path, required=True)
    run_p.add_argument("--repeats", type=int, default=1)
    run_p.add_argument("--out", type=Path, default=None, help="JSONL results path")
    args = parser.parse_args(argv)
    if args.command == "serve":
        serve(args.site)
        return 0
    return run(args.agent, args.tasks, args.repeats, args.out)


if __name__ == "__main__":
    sys.exit(main())
