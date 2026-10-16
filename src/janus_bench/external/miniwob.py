"""MiniWoB++ for Janus (docs/PLAN.md B1).

The episode protocol (hide the human display, seed, start the episode, read the goal from
`core.getUtterance()`, score from `WOB_RAW_REWARD_GLOBAL`) is ported from BrowserGym's
`browsergym/miniwob/base.py` (Apache-2.0). BrowserGym itself is not a dependency: it pins
playwright==1.44, below Janus's own playwright requirement.

The HTML comes from a miniwob-plusplus checkout (default `external/miniwob-plusplus`,
gitignored) and is served from a local static server, so every task's origin is
http://127.0.0.1:<port> and Janus's normal egress guard applies unchanged.

Scoring is MiniWoB's own reward: success iff the first episode-ending event had raw
reward > 0 (BrowserGym's rule). No model judges anything.
"""

from __future__ import annotations

import functools
import json
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml
from playwright.sync_api import Page, sync_playwright

from janus.agent import run_task
from janus.config import Settings, get_settings
from janus.goal import extract_inputs, logging_approver
from janus.llm import LLMClient
from janus.validator.policy import Policy, allowed_ops_for

DEFAULT_ROOT = Path("external/miniwob-plusplus/miniwob/html")
SPLIT_FILE = Path("splits/miniwob.yaml")
MAX_STEPS = 20

# Ported from BrowserGym (see module docstring): removes the goal/timer/reward display from
# the DOM so the page doesn't show the instruction, and keeps core's hooks working.
_REMOVE_DISPLAY_JS = r"""
let __display_ids = ['reward-display', 'click-canvas', 'sync-task-cover'];
let __display_divs = {};
let __query_div_hidden_copy = null;
removeDisplay = function() {
  core.clearTimer();
  document.body.removeEventListener('click', core.canvasDrawClick);
  __query_div_hidden_copy = document.getElementById('query').cloneNode(true);
  document.getElementById('query').innerHTML = '';
  for (i in __display_ids) {
    elem = document.getElementById(__display_ids[i]);
    elem.remove();
    __display_divs[__display_ids[i]] = elem;
  }
};
bringBackDisplay = function() {
  document.getElementById('query').innerHTML = __query_div_hidden_copy.innerHTML;
  for (var elem_id in __display_divs) { document.body.appendChild(__display_divs[elem_id]); }
  core.createDisplay();
};
core.endEpisode_legacy = core.endEpisode;
core.startEpisodeReal_legacy = core.startEpisodeReal;
core.getUtterance_legacy = core.getUtterance;
core.getUtterance = function () {
  bringBackDisplay(); utterance = core.getUtterance_legacy(); removeDisplay(); return utterance;
};
core.endEpisode = function(reward, time_proportional, reason) {
  bringBackDisplay(); core.endEpisode_legacy(reward, time_proportional, reason); removeDisplay();
};
core.startEpisodeReal = function() {
  bringBackDisplay(); core.startEpisodeReal_legacy(); removeDisplay();
};
removeDisplay();
"""

_START_JS = """
Math.seedrandom({seed});
core.EPISODE_MAX_TIME = {max_ms};
core.startEpisodeReal();
"""

_STATE_JS = "() => [WOB_RAW_REWARD_GLOBAL, WOB_REWARD_REASON, WOB_DONE_GLOBAL]"


@dataclass
class MiniwobRecord:
    task: str
    agent: str
    repeat: int
    seed: int
    category: str
    success: bool
    raw_reward: float | None
    reason: str | None
    goal: str | None
    status: str | None = None
    steps: int | None = None
    approvals: int = 0
    chat_calls: int | None = None
    wall_time: float = 0.0
    error: str | None = None
    inputs: dict[str, str] = field(default_factory=dict)


def load_split(path: Path = SPLIT_FILE) -> tuple[list[str], dict[str, str]]:
    """(smoke task ids, {task id: category}) from splits/miniwob.yaml."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    category = {t: cat for cat, ids in data["categories"].items() for t in ids}
    return list(data["smoke"]), category


@contextmanager
def serve(root: Path) -> Iterator[str]:
    """Serve `root` on 127.0.0.1 (random free port); yields the base URL."""
    handler = functools.partial(_QuietHandler, directory=str(root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


def start_episode(page: Page, url: str, seed: int, max_ms: int = 1_000_000) -> str:
    """Load a task page, start a seeded episode, and return its goal text."""
    page.goto(url)
    page.evaluate(_REMOVE_DISPLAY_JS)
    page.evaluate(_START_JS.format(seed=seed, max_ms=max_ms))
    page.wait_for_function("() => WOB_TASK_READY")
    response = page.evaluate("() => core.getUtterance()")
    return response["utterance"] if isinstance(response, dict) else str(response)


def episode_state(page: Page) -> tuple[float | None, str | None, bool]:
    raw, reason, done = page.evaluate(_STATE_JS)
    return (None if raw is None else float(raw)), reason, bool(done)


def _trace_for(task: str, repeat: int):
    """A per-run JSON trace sink when JANUS_TRACE_DIR is set, else None."""
    directory = os.environ.get("JANUS_TRACE_DIR")
    if not directory:
        return None
    from janus_bench.harness.trace_sink import make_trace_sink

    return make_trace_sink(Path(directory) / f"{task}-{repeat}.json")


def run_one(
    page: Page,
    *,
    base_url: str,
    task: str,
    category: str,
    agent: str,
    repeat: int,
    settings: Settings,
) -> MiniwobRecord:
    started = time.monotonic()
    record = MiniwobRecord(
        task=task,
        agent=agent,
        repeat=repeat,
        seed=repeat,
        category=category,
        success=False,
        raw_reward=None,
        reason=None,
        goal=None,
    )
    try:
        url = f"{base_url}/miniwob/{task}.html"
        record.goal = start_episode(page, url, seed=repeat)
        if agent == "janus":
            approvals: list[dict[str, str]] = []
            llm = LLMClient(settings)
            try:
                inputs = extract_inputs(record.goal, llm)
                record.inputs = inputs
                result = run_task(
                    page,
                    task_id=f"miniwob.{task}",
                    instruction=record.goal,
                    start_url=url,
                    inputs=inputs,
                    policy=Policy(
                        allowed_origins=frozenset({base_url}),
                        allowed_ops=allowed_ops_for(False),
                        max_steps=MAX_STEPS,
                    ),
                    llm=llm,
                    settings=settings,
                    escalate=logging_approver(approvals),
                    navigate=False,
                    stop=lambda: episode_state(page)[2],
                    trace=_trace_for(task, repeat),
                )
            finally:
                llm.close()
            record.status = result.status
            record.steps = result.steps_run
            record.chat_calls = llm.chat_calls
            record.approvals = len(approvals)
        elif agent != "null":
            raise ValueError(f"unknown agent {agent!r} (expected janus or null)")
        raw, reason, done = episode_state(page)
        record.raw_reward = raw if done else None
        record.reason = reason if done else "not finished"
        record.success = bool(done and raw is not None and raw > 0)
    except Exception as exc:  # one broken task must not end the whole run
        record.error = f"{type(exc).__name__}: {exc}"[:300]
    record.wall_time = round(time.monotonic() - started, 2)
    return record


def run(
    *,
    agent: str,
    tasks: list[str],
    category: dict[str, str],
    repeats: int,
    out: Path,
    root: Path = DEFAULT_ROOT,
    settings: Settings | None = None,
) -> list[MiniwobRecord]:
    """Run `tasks` x `repeats`. Appends one JSON line per run to `out`, skipping
    (task, repeat) pairs already in it, so an interrupted run resumes."""
    if not (root / "miniwob").is_dir():
        raise FileNotFoundError(
            f"MiniWoB HTML not found under {root}. Clone it with: git clone --depth 1 "
            "https://github.com/Farama-Foundation/miniwob-plusplus external/miniwob-plusplus"
        )
    settings = settings or get_settings()
    done: set[tuple[str, int]] = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                done.add((r["task"], r["repeat"]))
    out.parent.mkdir(parents=True, exist_ok=True)
    records: list[MiniwobRecord] = []
    with serve(root) as base_url, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=os.environ.get("JANUS_HEADED") != "1")
        try:
            for task in tasks:
                for repeat in range(1, repeats + 1):
                    if (task, repeat) in done:
                        continue
                    context = browser.new_context(service_workers="block")
                    try:
                        record = run_one(
                            context.new_page(),
                            base_url=base_url,
                            task=task,
                            category=category.get(task, "?"),
                            agent=agent,
                            repeat=repeat,
                            settings=settings,
                        )
                    finally:
                        context.close()
                    records.append(record)
                    with out.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
                    mark = "PASS" if record.success else "FAIL"
                    print(
                        f"{mark}  {task:<28} #{repeat}  {record.wall_time:>6.1f}s  "
                        f"{record.reason or ''} {record.error or ''}".rstrip()
                    )
        finally:
            browser.close()
    return records


def summarize(records: list[dict]) -> str:
    """Success per category, as one printable block."""
    by: dict[str, list[int]] = {}
    for r in records:
        bucket = by.setdefault(r["category"], [0, 0])
        bucket[0] += bool(r["success"])
        bucket[1] += 1
    total = [sum(v[0] for v in by.values()), sum(v[1] for v in by.values())]
    lines = [f"{cat:<11} {ok}/{n} = {100 * ok / n:.1f}%" for cat, (ok, n) in sorted(by.items())]
    lines.append(f"{'TOTAL':<11} {total[0]}/{total[1]} = {100 * total[0] / max(total[1], 1):.1f}%")
    return "\n".join(lines)
