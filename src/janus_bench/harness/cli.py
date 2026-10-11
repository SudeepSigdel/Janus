"""`janus-bench` command line: serve, run."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from janus.config import Settings
from janus_bench.agents.base import Agent
from janus_bench.harness import metrics
from janus_bench.harness.perturbation import perturb_task
from janus_bench.harness.results import (
    RunRecord,
    append_record,
    asr_table,
    category_table,
    load_records,
    overall_table,
    summary_table,
    tag_table,
)
from janus_bench.harness.runner import run_task
from janus_bench.harness.server import SITES, running_site, serve
from janus_bench.harness.split import ids_for, load_split
from janus_bench.harness.taskspec import TaskSpec, load_tasks
from janus_bench.sites.common.perturb import PERTURBATIONS

AVAILABLE_AGENTS = ("null", "oracle", "browser_use", "janus")
CHECKPOINTS = ("CP1", "CP2", "CP3", "CP4")


def make_agent(name: str, trace_dir: Path | None = None, model: str | None = None) -> Agent:
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

        settings = Settings(planner_model=model) if model else None
        return JanusAgent(settings=settings, trace_dir=trace_dir)
    raise SystemExit(
        f"agent '{name}' is not available yet (available: {', '.join(AVAILABLE_AGENTS)})"
    )


def _select_tasks(
    tasks_dir: Path, split: Path | None, set_: str | None, checkpoint: str | None
) -> list[TaskSpec]:
    tasks = load_tasks(tasks_dir)
    if split is None:
        if set_ is not None:
            raise SystemExit("--set requires --split")
        return tasks
    if set_ is None:
        raise SystemExit("--set is required with --split")
    if set_ == "test" and checkpoint is None:
        raise SystemExit(
            "--set test requires --checkpoint <CPn> (docs/PLAN.md: test runs only at "
            "milestone checkpoints, deliberately and labelled)"
        )
    wanted = ids_for(load_split(split), set_)
    return [t for t in tasks if t.id in wanted]


def run(
    agent_name: str,
    tasks_dir: Path,
    repeats: int,
    out: Path | None = None,
    split: Path | None = None,
    set_: str | None = None,
    checkpoint: str | None = None,
    trace: bool = False,
    model: str | None = None,
    perturbation: str | None = None,
) -> int:
    tasks = _select_tasks(tasks_dir, split, set_, checkpoint)
    # docs/AUDIT.md §4: approvals/sensitive field names follow the perturbed site; checks don't.
    tasks = [perturb_task(t, perturbation) for t in tasks]
    if perturbation is not None and agent_name == "janus":
        print("note: false-block clause 2 matches unperturbed oracle labels; read it as n/a")
    if not tasks:
        print(f"no tasks found in {tasks_dir}")
        return 2
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = out or Path("results") / f"{agent_name}-{tasks_dir.name}-{stamp}.jsonl"
    trace_dir = out.parent / f"{out.stem}-traces" if trace else None
    if trace and agent_name != "janus":
        print(f"note: --trace has no effect for agent '{agent_name}' (janus-only)")
    if model and agent_name != "janus":
        print(f"note: --model has no effect for agent '{agent_name}' (janus-only)")
    agent = make_agent(agent_name, trace_dir=trace_dir, model=model)
    records: list[RunRecord] = []
    with running_site("attacker") as attacker_url:
        for site in sorted({t.site for t in tasks}):
            with running_site(site) as base_url:
                for task in (t for t in tasks if t.site == site):
                    for repeat in range(1, repeats + 1):
                        result = run_task(task, agent, base_url, attacker_url, perturbation)
                        record = RunRecord(
                            task=task.id,
                            agent=agent.name,
                            repeat=repeat,
                            success=result.success,
                            steps=result.steps,
                            wall_time=round(result.wall_time, 2),
                            injection_outcome=result.injection_outcome(task),
                            error=result.error,
                            status=result.status,
                            chat_calls=result.chat_calls,
                            prompt_tokens=result.prompt_tokens,
                            completion_tokens=result.completion_tokens,
                            llm_time=result.llm_time,
                            gate_block=result.gate_block,
                            false_block=result.false_block,
                            over_action_count=result.over_action_count,
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


def report(tasks_dir: Path, records_paths: list[Path]) -> int:
    """Regenerate the markdown breakdown tables from committed JSONL result files."""
    tasks = load_tasks(tasks_dir)
    records: list[RunRecord] = []
    for path in records_paths:
        records.extend(load_records(path))
    if not records:
        print("no records found")
        return 2
    print("## Overall")
    print(overall_table(records))
    print()
    print("## By injection category")
    print(category_table(records, tasks))
    print()
    print("## Attack success rate by category (exercised rate in parentheses)")
    print(asr_table(records, tasks))
    print()
    print("## By difficulty tag")
    print(tag_table(records, tasks))
    return 0


def analyze(
    tasks_dir: Path, records_paths: list[Path], split: Path | None = None, set_: str | None = None
) -> int:
    """Print the docs/EXPERIMENTS.md metrics row for one or more committed JSONL
    results files, optionally narrowed to a split's dev/test set."""
    tasks = load_tasks(tasks_dir)
    records: list[RunRecord] = []
    for path in records_paths:
        records.extend(load_records(path))
    if split is not None:
        if set_ is None:
            raise SystemExit("--set is required with --split")
        wanted = ids_for(load_split(split), set_)
        tasks = [t for t in tasks if t.id in wanted]
        records = [r for r in records if r.task in wanted]
    if not records:
        print("no records found")
        return 2
    trace_dirs = [p.parent / f"{p.stem}-traces" for p in records_paths]
    print(metrics.experiment_row_markdown(records, tasks, trace_dirs))
    return 0


def miniwob_cmd(agent: str, split: str, only: str | None, repeats: int, out: Path) -> int:
    from janus_bench.external import miniwob

    smoke, category = miniwob.load_split()
    if only:
        tasks = [t.strip() for t in only.split(",") if t.strip()]
    else:
        tasks = smoke if split == "smoke" else list(category)
    miniwob.run(agent=agent, tasks=tasks, category=category, repeats=repeats, out=out)
    records = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line]
    print(miniwob.summarize([r for r in records if r["task"] in set(tasks)]))
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
    run_p.add_argument("--split", type=Path, default=None, help="a splits/*.yaml file")
    run_p.add_argument("--set", dest="set_", choices=("dev", "test"), default=None)
    run_p.add_argument(
        "--checkpoint",
        choices=CHECKPOINTS,
        default=None,
        help="required with --set test, so a test run is deliberate and labelled",
    )
    run_p.add_argument(
        "--trace", action="store_true", help="capture per-run JSON traces (janus agent only)"
    )
    run_p.add_argument(
        "--model",
        default=None,
        help=(
            "override the janus-planner Ollama model tag for this run (agent=janus only; "
            "docs/PLAN.md P9 model-selection experiments)"
        ),
    )
    run_p.add_argument(
        "--perturbation",
        choices=sorted(PERTURBATIONS),
        default=None,
        help="serve surface-perturbed sites (docs/AUDIT.md section 4)",
    )
    report_p = sub.add_parser("report", help="print breakdown tables from committed JSONL results")
    report_p.add_argument("--tasks", type=Path, required=True)
    report_p.add_argument(
        "--records",
        type=Path,
        action="append",
        required=True,
        help="repeatable; one or more JSONL files",
    )
    analyze_p = sub.add_parser("analyze", help="print the EXPERIMENTS.md metrics row")
    analyze_p.add_argument("--tasks", type=Path, default=Path("tasks"))
    analyze_p.add_argument(
        "--records",
        type=Path,
        action="append",
        required=True,
        help="repeatable; one or more JSONL files",
    )
    analyze_p.add_argument("--split", type=Path, default=None)
    analyze_p.add_argument("--set", dest="set_", choices=("dev", "test"), default=None)
    mw_p = sub.add_parser("miniwob", help="run an agent on MiniWoB++ (docs/PLAN.md B1)")
    mw_p.add_argument("--agent", required=True, choices=("janus", "null"))
    mw_p.add_argument("--split", choices=("smoke", "dev"), default="smoke")
    mw_p.add_argument("--only", default=None, help="comma-separated task ids (overrides --split)")
    mw_p.add_argument("--repeats", type=int, default=1)
    mw_p.add_argument("--out", type=Path, required=True, help="JSONL results path (resumable)")
    wa_p = sub.add_parser("webarena", help="run Janus on WebArena-Verified (docs/PLAN.md B3)")
    wa_p.add_argument("--site", choices=("reddit",), default="reddit")
    wa_p.add_argument("--split", default="reddit_hard", help="a key of splits/webarena.yaml")
    wa_p.add_argument("--only", default=None, help="comma-separated task ids (overrides --split)")
    wa_p.add_argument("--out-dir", type=Path, required=True, help="run directory (resumable)")
    wa_p.add_argument(
        "--no-reset", action="store_true", help="reuse the running container between tasks"
    )
    ws_p = sub.add_parser("wasp", help="WASP injection scenarios on the forum (PLAN.md B4)")
    ws_p.add_argument("--out", type=Path, required=True, help="JSONL results (resumable)")
    ws_p.add_argument("--only", default=None, help="comma-separated scenario ids")
    args = parser.parse_args(argv)
    if args.command == "wasp":
        from janus_bench.external import wasp

        only = [s.strip() for s in args.only.split(",")] if args.only else None
        wasp.run(out=args.out, only=only)
        records = [json.loads(x) for x in args.out.read_text("utf-8").splitlines() if x]
        print(wasp.summarize(records))
        return 0
    if args.command == "webarena":
        from janus_bench.external import webarena

        ids = (
            [int(t) for t in args.only.split(",") if t.strip()]
            if args.only
            else webarena.load_split(args.split)
        )
        webarena.run(
            site_name=args.site, task_ids=ids, out_dir=args.out_dir, reset=not args.no_reset
        )
        print(
            f"next: uv tool run webarena-verified eval-tasks --output-dir {args.out_dir} "
            f"--config {args.out_dir / 'eval_config.json'}"
        )
        return 0
    if args.command == "miniwob":
        return miniwob_cmd(args.agent, args.split, args.only, args.repeats, args.out)
    if args.command == "serve":
        serve(args.site)
        return 0
    if args.command == "report":
        return report(args.tasks, args.records)
    if args.command == "analyze":
        return analyze(args.tasks, args.records, args.split, args.set_)
    return run(
        args.agent,
        args.tasks,
        args.repeats,
        args.out,
        args.split,
        args.set_,
        args.checkpoint,
        args.trace,
        args.model,
        args.perturbation,
    )


if __name__ == "__main__":
    sys.exit(main())
