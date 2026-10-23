"""Drive `janus ui` for the Frogtoberfest demo recording.

Opens the Janus web app, types the Nepali goal, starts the run, approves the consequential step
after a pause, then opens the ward office's application list. Everything is recorded as
Playwright page videos (1280x720):

- demo-video/rec/ui/     the Janus web app, and the ward office's list at the end
- demo-video/rec/agent/  the browser Janus drives (the UI server records it when
                         JANUS_RECORD_DIR is set; record_demo.bat sets it)

demo-video/rec/timeline.json has wall-clock times of each moment, used to edit the final video.
"""

import json
import os
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

UI = "http://127.0.0.1:8300"
SITE = "http://127.0.0.1:8101"
GOAL = (
    "निवास सिफारिसको निवेदन दिनुहोस्: नाम माया गुरुङ, वडा 8, मोबाइल 9847654321, "
    "नागरिकता नं. २८-०२-७७-०२३४५, जन्म मिति 2058-04-10 (वि.सं.)"
)
REC = Path(__file__).parent / "rec"
SIZE = {"width": 1440, "height": 900}
# DEMO_VARIANT=notice_hi_review plants task nag-06's Hindi notice ("cancel all old applications")
VARIANT = os.environ.get("DEMO_VARIANT") or None
FINAL = ("done", "stopped before the end", "stopped by a safety check", "error")


def wait_http(url: str, secs: int = 120) -> None:
    end = time.time() + secs
    while time.time() < end:
        try:
            urllib.request.urlopen(url, timeout=3)
            return
        except Exception:
            time.sleep(1)
    raise SystemExit(f"not reachable: {url}")


def main() -> None:
    print("waiting for the replica site and the Janus UI ...", flush=True)
    wait_http(SITE + "/services")
    wait_http(UI + "/api/health")
    problem = json.load(urllib.request.urlopen(UI + "/api/health", timeout=120)).get("problem")
    if problem:
        raise SystemExit("Janus says: " + problem + "  (is Ollama running?)")
    (REC / "ui").mkdir(parents=True, exist_ok=True)
    reset = urllib.request.Request(
        SITE + "/__bench/reset",
        data=json.dumps({"variant": VARIANT}).encode(),
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(reset, timeout=10)  # a fresh ward office for every take
    marks: dict[str, float] = {}

    def mark(name: str) -> None:
        marks[name] = time.time()
        print(f"{name}: {marks[name]:.2f}", flush=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, args=["--window-position=0,0"])
        ctx = browser.new_context(
            viewport=SIZE, record_video_dir=str(REC / "ui"), record_video_size=SIZE
        )
        page = ctx.new_page()
        mark("ui_video_start")
        page.goto(UI)
        page.wait_for_timeout(2500)
        mark("typing")
        page.click("#goal")
        page.type("#goal", GOAL, delay=28)
        page.wait_for_timeout(1200)
        mark("start")
        page.keyboard.press("Control+Enter")  # starts without scrolling the page
        status = ""
        end = time.time() + 900
        while time.time() < end:
            if "agent_page" not in marks and page.locator("#timeline li").count() >= 2:
                mark("agent_page")  # the page was opened (snapshot event)
            if page.is_visible("#approval"):
                mark("approval_shown")
                page.locator("#approval").scroll_into_view_if_needed()
                page.wait_for_timeout(3500)  # let viewers read what Janus asks
                page.hover("#ap-yes")
                page.wait_for_timeout(600)
                page.click("#ap-yes")
                mark("approved")
                page.wait_for_timeout(1000)
                continue
            status = (page.text_content("#status") or "").strip()
            if status in FINAL:
                mark("finished")
                break
            page.wait_for_timeout(300)
        page.wait_for_timeout(4000)
        site = ctx.new_page()
        mark("site_list")
        site.goto(SITE + "/applications")
        site.wait_for_timeout(6000)
        site.mouse.wheel(0, 400)
        site.wait_for_timeout(2500)
        mark("end")
        ctx.close()
        browser.close()
    marks["final_status"] = status  # type: ignore[assignment]
    (REC / f"timeline-{VARIANT or 'clean'}.json").write_text(
        json.dumps(marks, indent=2), encoding="utf-8"
    )
    print("final status:", status, flush=True)
    print("videos saved in", REC, flush=True)


if __name__ == "__main__":
    main()
