"""WebGames (Convergence, Apache-2.0) for Janus: 50 client-side web challenges.

Each challenge is a route of a single-page app that reveals a unique password once it is solved.
The app is built from a webgames checkout (default `external/webgames/webgames/dist`, gitignored)
and served from a local static server, so every task runs on http://127.0.0.1:<port> with
Janus's normal egress guard. Tasks and passwords come from the published Hugging Face dataset
(`convergence-ai/webgames`, test.jsonl, MIT), downloaded to `external/webgames/tasks-hf.jsonl`.

Scoring is the benchmark's own rule: success iff the task's password is visible on the page.
No model judges anything. A task whose password is already visible before the agent acts is
reported as invalid, not scored.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from janus.agent import run_task
from janus.config import Settings, get_settings
from janus.goal import extract_inputs, logging_approver
from janus.llm import LLMClient
from janus.validator.policy import Policy, allowed_ops_for

DEFAULT_DIST = Path("external/webgames/webgames/dist")
DEFAULT_TASKS = Path("external/webgames/tasks-hf.jsonl")
MAX_STEPS = 20


@dataclass
class WebgamesRecord:
    task: str
    agent: str
    repeat: int
    tags: list[str]
    success: bool
    goal: str
    status: str | None = None
    steps: int | None = None
    approvals: int = 0
    chat_calls: int | None = None
    wall_time: float = 0.0
    invalid: bool = False
    error: str | None = None


def load_tasks(path: Path = DEFAULT_TASKS) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


class _SpaHandler(SimpleHTTPRequestHandler):
    """Static files, with every unknown path answered by index.html (client-side routes)."""

    def __init__(self, *args, directory: str, **kwargs) -> None:
        super().__init__(*args, directory=directory, **kwargs)

    def send_head(self):  # type: ignore[override]
        path = Path(self.translate_path(self.path))
        if not path.exists():
            self.path = "/index.html"
        return super().send_head()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


@contextmanager
def serve(dist: Path) -> Iterator[str]:
    def handler(*args, **kwargs):
        return _SpaHandler(*args, directory=str(dist), **kwargs)

    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def solved(page: Page, password: str) -> bool:
    try:
        return password in page.evaluate("() => document.body ? document.body.innerText : ''")
    except Exception:  # the page may be navigating
        return False


def run_one(
    page: Page, *, base_url: str, task: dict, agent: str, repeat: int, settings: Settings
) -> WebgamesRecord:
    started = time.monotonic()
    goal = task["description"]
    record = WebgamesRecord(
        task=task["id"],
        agent=agent,
        repeat=repeat,
        tags=task.get("tags", []),
        success=False,
        goal=goal,
    )
    url = f"{base_url}/{task['path']}"
    try:
        page.goto(url)
        page.wait_for_timeout(500)
        if solved(page, task["password"]):
            record.invalid = True
            return record
        if agent == "janus":
            approvals: list[dict[str, str]] = []
            llm = LLMClient(settings)
            try:
                result = run_task(
                    page,
                    task_id=f"webgames.{task['id']}",
                    instruction=goal,
                    start_url=url,
                    inputs=extract_inputs(goal, llm),
                    policy=Policy(
                        allowed_origins=frozenset({base_url}),
                        allowed_ops=allowed_ops_for(False, allow_read=True),
                        max_steps=MAX_STEPS,
                    ),
                    llm=llm,
                    settings=settings,
                    escalate=logging_approver(approvals),
                    navigate=False,
                    stop=lambda: solved(page, task["password"]),
                )
            finally:
                llm.close()
            record.status = result.status
            record.steps = result.steps_run
            record.chat_calls = llm.chat_calls
            record.approvals = len(approvals)
        elif agent != "null":
            raise ValueError(f"unknown agent {agent!r} (expected janus or null)")
        record.success = solved(page, task["password"])
    except Exception as exc:  # one broken task must not end the whole run
        record.error = f"{type(exc).__name__}: {exc}"[:300]
    finally:
        record.wall_time = round(time.monotonic() - started, 2)
    return record


def run(
    *,
    agent: str,
    tasks: list[dict],
    repeats: int,
    out: Path,
    dist: Path = DEFAULT_DIST,
    settings: Settings | None = None,
) -> list[WebgamesRecord]:
    """Run `tasks` x `repeats`, appending one JSON line per run to `out` (resumable)."""
    if not (dist / "index.html").is_file():
        raise FileNotFoundError(
            f"WebGames build not found at {dist}. Clone github.com/convergence-ai/webgames into "
            "external/webgames, then in external/webgames/webgames run: "
            "npm install && npx vite build"
        )
    settings = settings or get_settings()
    done: set[tuple[str, int]] = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                done.add((r["task"], r["repeat"]))
    out.parent.mkdir(parents=True, exist_ok=True)
    records: list[WebgamesRecord] = []
    with serve(dist) as base_url, sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            for task in tasks:
                for repeat in range(1, repeats + 1):
                    if (task["id"], repeat) in done:
                        continue
                    context = browser.new_context(service_workers="block")
                    try:
                        record = run_one(
                            context.new_page(),
                            base_url=base_url,
                            task=task,
                            agent=agent,
                            repeat=repeat,
                            settings=settings,
                        )
                    finally:
                        context.close()
                    records.append(record)
                    with out.open("a", encoding="utf-8") as fh:
                        fh.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
                    mark = "INVALID" if record.invalid else ("PASS" if record.success else "FAIL")
                    note = record.error or ""
                    print(f"{mark:<7} {task['id']:<24} #{repeat} {record.wall_time:>6.1f}s {note}")
        finally:
            browser.close()
    return records


def summarize(records: list[dict]) -> str:
    valid = [r for r in records if not r.get("invalid")]
    passed = sum(r["success"] for r in valid)
    invalid = len(records) - len(valid)
    return f"WebGames: {passed}/{len(valid)} solved" + (f" ({invalid} invalid)" if invalid else "")
