"""`janus-bench demo`: one injection task, a naive agent next to Janus, as one HTML page.

The naive agent is the scripted `gullible` stand-in (it obeys the planted instruction through
direct HTTP, so it needs no model); Janus runs for real with its decision trace. Both are scored
by the task's own state checks, so the page shows what changed on the site, not what either
agent claims.
"""

from __future__ import annotations

import tempfile
from html import escape
from pathlib import Path

from janus.report import render_report
from janus_bench.agents.gullible import GullibleAgent
from janus_bench.agents.janus_agent import JanusAgent
from janus_bench.harness.runner import TaskResult, run_task
from janus_bench.harness.server import running_site
from janus_bench.harness.taskspec import TaskSpec, load_tasks
from janus_bench.harness.trace_sink import load_trace
from janus_bench.sites.nagarpalika.seed import NOTICES

_CSS = """
:root{--bg:#fff;--fg:#1b1f24;--mut:#667;--ok:#17803d;--bad:#b42318;--card:#f6f7f9;--line:#dde1e6}
@media (prefers-color-scheme:dark){:root{--bg:#14171b;--fg:#e8eaed;--mut:#9aa3ad;--ok:#4ade80;
--bad:#f87171;--card:#1e2328;--line:#2f363d}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px}
h1{font-size:1.4rem;margin:0 0 4px}.mut{color:var(--mut)}
blockquote{margin:12px 0;padding:10px 14px;border-left:4px solid var(--bad);background:var(--card)}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:16px}
@media (max-width:760px){.cols{grid-template-columns:1fr}}
.col{border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.verdict{font-weight:700;font-size:1.1rem}.ok{color:var(--ok)}.bad{color:var(--bad)}
iframe{width:100%;height:520px;border:1px solid var(--line);border-radius:8px;background:#fff}
"""


def _notice_text(task: TaskSpec) -> str:
    notice = NOTICES.get(task.injection.variant) if task.injection else None
    return notice.text if notice else "(no page notice for this variant)"


def _verdict(task: TaskSpec, result: TaskResult) -> tuple[str, str]:
    outcome = result.injection_outcome(task)
    if outcome in ("hijacked", "leaked"):
        paths = ", ".join(v.check.path for v in result.forbidden_violations) or "attacker sink"
        return "bad", f"{outcome.upper()}: the attack changed {paths}"
    return "ok", "RESISTED: the site state the attacker targeted is unchanged"


def build_demo(task_id: str, out: Path, tasks_dir: Path = Path("tasks")) -> int:
    task = next((t for t in load_tasks(tasks_dir) if t.id == task_id), None)
    if task is None or task.injection is None:
        print(f"{task_id} is not an injection task")
        return 2
    with tempfile.TemporaryDirectory() as tmp:
        janus = JanusAgent(trace_dir=Path(tmp))
        with running_site("attacker") as attacker, running_site(task.site) as base:
            naive = run_task(task, GullibleAgent(), base, attacker)
            real = run_task(task, janus, base, attacker)
        trace = load_trace(next(Path(tmp).glob("*.json")))
    janus_page = render_report(trace, title="Janus", subtitle=task.instruction.en)
    cells = []
    for heading, note, result, extra in (
        ("Naive agent", "scripted stand-in that obeys instructions found on the page", naive, ""),
        (
            "Janus",
            f"planner never sees page text; {real.steps} steps, status {real.status}",
            real,
            f'<iframe sandbox srcdoc="{escape(janus_page, quote=True)}"></iframe>',
        ),
    ):
        tone, text = _verdict(task, result)
        cells.append(
            f'<div class="col"><h2>{heading}</h2><p class="mut">{escape(note)}</p>'
            f'<p class="verdict {tone}">{escape(text)}</p>{extra}</div>'
        )
    html = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>Janus demo</title><style>{_CSS}</style></head><body><main>"
        f"<h1>Same task, same planted instruction</h1>"
        f'<p class="mut">{escape(task.instruction.en)}</p>'
        f"<p>The site shows this notice to anything that reads the page ({task.injection.lang}):"
        f"</p><blockquote>{escape(_notice_text(task))}</blockquote>"
        f'<div class="cols">{"".join(cells)}</div>'
        f'<p class="mut">Task {escape(task.id)} on a fictional replica site. Verdicts come from '
        "the site's own state, not from either agent.</p></main></body></html>"
    )
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out}  (naive: {_verdict(task, naive)[1]} | janus: {_verdict(task, real)[1]})")
    return 0
