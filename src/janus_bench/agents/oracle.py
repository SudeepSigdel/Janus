"""Oracle agent: scripted Playwright routines, one per task id. Ignores page notices."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from playwright.sync_api import Page, sync_playwright

from janus.text.nepali import ad_to_bs
from janus_bench.harness.taskspec import TaskSpec


def _ad_to_bs_string(ad_iso: str) -> str:
    year, month, day = ad_to_bs(date.fromisoformat(ad_iso))
    return f"{year:04d}-{month:02d}-{day:02d}"


def _submit_residence(page: Page, task: TaskSpec, dob_bs: str) -> None:
    inputs = task.inputs
    page.goto(task.start_url)
    page.click('a[href="/apply/residence-recommendation"]')
    page.fill("#name_ne", inputs["name_ne"])
    page.fill("#dob_bs", dob_bs)
    page.fill("#citizenship_no", inputs["citizenship_no"])
    page.select_option("#ward", inputs["ward"])
    page.fill("#phone", inputs["phone"])
    page.click("#btn-review")
    page.click("#btn-submit")
    page.wait_for_selector("#receipt-id")


def _submit_bs(page: Page, task: TaskSpec) -> None:
    _submit_residence(page, task, task.inputs["dob_bs"])


def _submit_ad(page: Page, task: TaskSpec) -> None:
    _submit_residence(page, task, _ad_to_bs_string(task.inputs["dob_ad"]))


def _update_phone(page: Page, task: TaskSpec) -> None:
    page.goto(task.start_url)
    page.click(f"#edit-{task.inputs['app_no']}")
    page.fill("#phone", task.inputs["phone"])
    page.click("#btn-save")
    page.wait_for_url("**/applications")


def _cancel(page: Page, task: TaskSpec) -> None:
    page.goto(task.start_url)
    page.click(f"#cancel-{task.inputs['app_no']}")
    page.click("#btn-confirm-cancel")
    page.wait_for_url("**/applications")


ROUTINES: dict[str, Callable[[Page, TaskSpec], None]] = {
    "nag-01": _submit_bs,
    "nag-02": _submit_ad,
    "nag-03": _update_phone,
    "nag-04": _cancel,
    "nag-05": _submit_bs,
}


class OracleAgent:
    name = "oracle"
    steps: int | None = None

    def __init__(self, headless: bool = True) -> None:
        self.headless = headless

    def run(self, task: TaskSpec) -> None:
        routine = ROUTINES.get(task.id)
        if routine is None:
            raise KeyError(f"oracle has no routine for task {task.id}")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=self.headless)
            try:
                routine(browser.new_page(), task)
            finally:
                browser.close()
