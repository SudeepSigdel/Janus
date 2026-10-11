"""WebArena-Verified for Janus (docs/PLAN.md B3). Held-out: never tuned on.

Sites run from the WebArena-Verified Docker images (am1n3e/webarena-verified-<site>). For each
task the harness:
1. recreates the site container so the task starts from the original database (the
   WebArena-Verified way to reset; `docker rm -f` + `docker run`), unless `reset=False`;
2. opens a browser context that logs in with the image's auto-login header and records a HAR
   network trace;
3. runs Janus from the task's intent (goal-only, B0) with the logging approver;
4. writes `<out>/<task_id>/agent_response.json` and `network.har`, the layout the official
   evaluator (`webarena-verified eval-tasks`) reads.

Scoring is the official deterministic evaluator, run separately (`uv tool run
webarena-verified eval-tasks ...`), so its pinned dependencies never enter Janus's environment.
The dataset JSON (Apache-2.0) lives in the gitignored `external/webarena-verified/`.
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml
from playwright.sync_api import sync_playwright

from janus.agent import run_task
from janus.config import Settings, get_settings
from janus.goal import classify_task, extract_data, extract_inputs, logging_approver
from janus.llm import LLMClient
from janus.observer.extract import extract_snapshot
from janus.validator.policy import Policy, allowed_ops_for

DATASET = Path("external/webarena-verified/webarena-verified.json")
SPLIT_FILE = Path("splits/webarena.yaml")
MAX_STEPS = 20


@dataclass(frozen=True)
class Site:
    placeholder: str
    image: str
    container: str
    port: int
    ctrl_port: int
    login_header: str
    username: str
    password: str
    health_path: str

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


SITES = {
    "reddit": Site(
        placeholder="__REDDIT__",
        image="am1n3e/webarena-verified-reddit",
        container="webarena-verified-reddit",
        port=9999,
        ctrl_port=9998,
        login_header="X-Postmill-Auto-Login",
        username="MarvelsGrantMan136",
        password="test1234",
        health_path="/login",
    ),
}


@dataclass
class WebArenaRecord:
    task_id: int
    site: str
    task_type: str | None
    status: str | None
    steps: int | None = None
    approvals: int = 0
    chat_calls: int | None = None
    wall_time: float = 0.0
    reset_s: float = 0.0
    error: str | None = None
    inputs: dict[str, str] = field(default_factory=dict)


def load_tasks(path: Path = DATASET) -> dict[int, dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} not found. Export it with: uv tool run webarena-verified dataset-get "
            f"--output {path}"
        )
    return {t["task_id"]: t for t in json.loads(path.read_text(encoding="utf-8"))}


def load_split(name: str, path: Path = SPLIT_FILE) -> list[int]:
    return list(yaml.safe_load(path.read_text(encoding="utf-8"))[name])


def results_schema(task: dict[str, Any]) -> dict[str, Any] | None:
    for ev in task.get("eval", []):
        if ev.get("evaluator") == "AgentResponseEvaluator" and ev.get("results_schema"):
            return ev["results_schema"]
    return None


def _status_ok(site: Site) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{site.ctrl_port}/status", timeout=5) as r:
            if not json.loads(r.read().decode()).get("success"):
                return False
        # and the site's own health page answers: like the package's own check, any status
        # below 500 counts (the forum answers 403 to an anonymous request)
        with urllib.request.urlopen(f"{site.url}{site.health_path}", timeout=5) as r:
            return r.status < 500
    except urllib.error.HTTPError as exc:
        return exc.code < 500
    except Exception:  # noqa: BLE001 -- any failure means "not ready yet"
        return False


def reset_site(site: Site, timeout_s: float = 300.0) -> float:
    """Recreate the container (fresh database) and wait until env-ctrl reports ready."""
    started = time.monotonic()
    subprocess.run(["docker", "rm", "-f", site.container], capture_output=True, check=False)
    subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            site.container,
            "-p",
            f"{site.port}:80",
            "-p",
            f"{site.ctrl_port}:8877",
            # the image's entrypoint runs env-ctrl init with this as the site's public URL
            "-e",
            f"WA_ENV_CTRL_EXTERNAL_SITE_URL={site.url}",
            site.image,
        ],
        capture_output=True,
        check=True,
    )
    # time the readiness wait only: the first `docker run` may first pull the image
    ready_started = time.monotonic()
    while time.monotonic() - ready_started < timeout_s:
        if _status_ok(site):
            warm_site(site)
            return round(time.monotonic() - started, 1)
        time.sleep(2)
    raise TimeoutError(f"{site.container} not ready after {timeout_s:.0f}s")


WARM_PATHS = ("/", "/login", "/registration", "/submit", "/forums", "/messages")


def warm_site(site: Site, timeout_s: float = 180.0) -> None:
    """Request the main pages once, logged in, so the app's first-request cache build
    (about 34 s on the forum image) happens here and not inside a task's 30 s page load."""
    headers = {site.login_header: f"{site.username}:{site.password}"}
    for path in WARM_PATHS:
        req = urllib.request.Request(f"{site.url}{path}", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as r:
                r.read()
        except urllib.error.HTTPError:
            pass  # any answer means the page was built


def agent_response(task_type: str | None, status: str, data: list[Any] | None) -> dict[str, Any]:
    ok = status != "blocked"
    return {
        "task_type": task_type or "NAVIGATE",
        "status": "SUCCESS" if ok else "ACTION_NOT_ALLOWED_ERROR",
        "retrieved_data": data if task_type == "RETRIEVE" else None,
        "error_details": None if ok else "a Janus safety gate refused a step",
    }


def run_one(
    task: dict[str, Any], site: Site, out_dir: Path, settings: Settings, *, reset: bool
) -> WebArenaRecord:
    record = WebArenaRecord(task_id=task["task_id"], site="reddit", task_type=None, status=None)
    started = time.monotonic()
    task_dir = out_dir / str(task["task_id"])
    task_dir.mkdir(parents=True, exist_ok=True)
    try:
        if reset:
            record.reset_s = reset_site(site)
        start_url = task["start_urls"][0].replace(site.placeholder, site.url)
        llm = LLMClient(settings)
        approvals: list[dict[str, str]] = []
        data: list[Any] | None = None
        try:
            record.task_type = classify_task(task["intent"], llm)
            record.inputs = extract_inputs(task["intent"], llm)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                context = browser.new_context(
                    service_workers="block",
                    record_har_path=str(task_dir / "network.har"),
                    extra_http_headers={site.login_header: f"{site.username}:{site.password}"},
                )
                try:
                    page = context.new_page()
                    result = run_task(
                        page,
                        task_id=f"webarena.{task['task_id']}",
                        instruction=task["intent"],
                        start_url=start_url,
                        inputs=record.inputs,
                        policy=Policy(
                            allowed_origins=frozenset({site.url}),
                            allowed_ops=allowed_ops_for(False),
                            max_steps=MAX_STEPS,
                        ),
                        llm=llm,
                        settings=settings,
                        escalate=logging_approver(approvals),
                    )
                    schema = results_schema(task)
                    if record.task_type == "RETRIEVE" and schema is not None:
                        data = extract_data(
                            task["intent"], extract_snapshot(page, settings), llm, schema
                        )
                finally:
                    context.close()  # flushes the HAR
                    browser.close()
        finally:
            llm.close()
        record.status = result.status
        record.steps = result.steps_run
        record.chat_calls = llm.chat_calls
        record.approvals = len(approvals)
        response = agent_response(record.task_type, result.status, data)
    except Exception as exc:  # one broken task must not end the run
        record.error = f"{type(exc).__name__}: {exc}"[:300]
        response = agent_response(record.task_type, "blocked", None)
        response["status"] = "UNKNOWN_ERROR"
        response["error_details"] = record.error
    (task_dir / "agent_response.json").write_text(json.dumps(response, indent=1), "utf-8")
    record.wall_time = round(time.monotonic() - started, 1)
    return record


def run(
    *,
    site_name: str,
    task_ids: list[int],
    out_dir: Path,
    reset: bool = True,
    settings: Settings | None = None,
) -> list[WebArenaRecord]:
    """Run tasks; skips any task whose agent_response.json already exists (resumable)."""
    tasks = load_tasks()
    site = SITES[site_name]
    settings = settings or get_settings()
    out_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for task_id in task_ids:
        if (out_dir / str(task_id) / "agent_response.json").exists():
            continue
        record = run_one(tasks[task_id], site, out_dir, settings, reset=reset)
        records.append(record)
        with (out_dir / "janus_runs.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
        print(
            f"{task_id:>4}  {record.task_type or '?':<8} {record.status or 'error':<9} "
            f"{record.wall_time:>6.1f}s  reset {record.reset_s:>5.1f}s  {record.error or ''}"
        )
    config = {
        "environments": {
            site.placeholder: {
                "urls": [site.url],
                "credentials": {"username": site.username, "password": site.password},
            }
        }
    }
    (out_dir / "eval_config.json").write_text(json.dumps(config, indent=1), "utf-8")
    return records
