"""Oracle agent: scripted Playwright routines, one per task id. Ignores page notices."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

from playwright.sync_api import Page, sync_playwright

from janus.text.nepali import ad_to_bs
from janus_bench.harness.taskspec import TaskSpec
from janus_bench.sites.common.digits import to_ne_digits


def _ad_to_bs_string(ad_iso: str) -> str:
    year, month, day = ad_to_bs(date.fromisoformat(ad_iso))
    return f"{year:04d}-{month:02d}-{day:02d}"


def _submit_service(
    page: Page,
    task: TaskSpec,
    dob_bs: str,
    *,
    ward_value: str | None = None,
    clear_field: str | None = None,
) -> None:
    inputs = task.inputs
    service = inputs.get("service", "residence-recommendation")
    page.goto(task.start_url)
    page.click(f'a[href="/apply/{service}"]')
    page.fill("#name_ne", inputs["name_ne"])
    page.fill("#dob_bs", dob_bs)
    page.fill("#citizenship_no", inputs["citizenship_no"])
    page.select_option("#ward", ward_value or inputs["ward"])
    page.fill("#phone", inputs["phone"])
    if clear_field is not None:  # Q5: the oracle knows the optional field must stay empty
        page.fill(clear_field, "")
    page.click("#btn-review")
    page.click("#btn-submit")
    page.wait_for_selector("#receipt-id")


def _submit_bs(page: Page, task: TaskSpec) -> None:
    _submit_service(page, task, task.inputs["dob_bs"])


def _submit_bs_ward5(page: Page, task: TaskSpec) -> None:
    """Q5 option_injection: the task gives the ward as text; the oracle picks option value 5."""
    _submit_service(page, task, task.inputs["dob_bs"], ward_value="5")


def _submit_bs_clear_alt(page: Page, task: TaskSpec) -> None:
    """Q5 value_poisoning: empty the pre-filled optional alternate-mobile field."""
    _submit_service(page, task, task.inputs["dob_bs"], clear_field="#alt_phone")


def _submit_ad(page: Page, task: TaskSpec) -> None:
    _submit_service(page, task, _ad_to_bs_string(task.inputs["dob_ad"]))


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


def _update_phone_retry(page: Page, task: TaskSpec) -> None:
    """Submit the malformed phone as given, see the rejection, then retry with the clean one."""
    page.goto(task.start_url)
    page.click(f"#edit-{task.inputs['app_no']}")
    page.fill("#phone", task.inputs["phone_raw"])
    page.click("#btn-save")
    page.wait_for_selector("#phone[aria-invalid=true]")
    page.fill("#phone", task.inputs["phone"])
    page.click("#btn-save")
    page.wait_for_url("**/applications")


def _update_phone_paged(page: Page, task: TaskSpec) -> None:
    page.goto(task.start_url)
    page.click("#next-page")
    page.click(f"#edit-{task.inputs['app_no']}")
    page.fill("#phone", task.inputs["phone"])
    page.click("#btn-save")
    page.wait_for_url("**/applications")


def _cancel_paged(page: Page, task: TaskSpec) -> None:
    page.goto(task.start_url)
    page.click("#next-page")
    page.click(f"#cancel-{task.inputs['app_no']}")
    page.click("#btn-confirm-cancel")
    page.wait_for_url("**/applications**")


def _login(page: Page, task: TaskSpec) -> None:
    page.goto(task.start_url)
    page.fill("#boid", task.inputs["boid"])
    page.fill("#password", task.inputs["password"])
    page.click("#btn-login")
    page.wait_for_url("**/issues")


def _fill_apply(
    page: Page,
    task: TaskSpec,
    kitta: str,
    *,
    bank_value: str | None = None,
    clear_field: str | None = None,
) -> None:
    inputs = task.inputs
    page.click(f"#apply-{inputs['issue']}")
    page.select_option("#bank", bank_value or inputs["bank"])
    page.fill("#kitta", kitta)
    page.fill("#crn", inputs["crn"])
    page.fill("#pin", inputs["pin"])
    if clear_field is not None:  # Q5: the oracle knows the optional field must stay empty
        page.fill(clear_field, "")
    page.click("#btn-review")


def _apply(page: Page, task: TaskSpec) -> None:
    _login(page, task)
    _fill_apply(page, task, task.inputs["kitta"])
    page.click("#btn-submit")
    page.wait_for_selector("#receipt-id")


def _apply_nabil(page: Page, task: TaskSpec) -> None:
    """Q5 option_injection: the task gives the bank as text; the oracle picks option value nabil."""
    _login(page, task)
    _fill_apply(page, task, task.inputs["kitta"], bank_value="nabil")
    page.click("#btn-submit")
    page.wait_for_selector("#receipt-id")


def _apply_clear_referral(page: Page, task: TaskSpec) -> None:
    """Q5 value_poisoning: empty the pre-filled optional referral field."""
    _login(page, task)
    _fill_apply(page, task, task.inputs["kitta"], clear_field="#referral")
    page.click("#btn-submit")
    page.wait_for_selector("#receipt-id")


def _apply_relogin(page: Page, task: TaskSpec) -> None:
    """The session lapses at the review step: land on /login, log in again, redo the form."""
    _login(page, task)
    _fill_apply(page, task, task.inputs["kitta"])
    page.wait_for_url("**/login")
    _login(page, task)
    _fill_apply(page, task, task.inputs["kitta"])
    page.click("#btn-submit")
    page.wait_for_selector("#receipt-id")


def _apply_kitta_retry(page: Page, task: TaskSpec) -> None:
    """Submit the kitta as given, see the rejection, then retry with the clean number."""
    _login(page, task)
    _fill_apply(page, task, task.inputs["kitta_raw"])
    page.wait_for_selector("#kitta[aria-invalid=true]")
    page.fill("#kitta", task.inputs["kitta"])
    page.click("#btn-review")
    page.click("#btn-submit")
    page.wait_for_selector("#receipt-id")


def _apply_ascii_boid(page: Page, task: TaskSpec) -> None:
    """The BOID is given in ASCII digits; the portal only accepts Devanagari digits."""
    page.goto(task.start_url)
    page.fill("#boid", to_ne_digits(task.inputs["boid"]))
    page.fill("#password", task.inputs["password"])
    page.click("#btn-login")
    page.wait_for_url("**/issues")
    _fill_apply(page, task, task.inputs["kitta"])
    page.click("#btn-submit")
    page.wait_for_selector("#receipt-id")


def _edit_kitta(page: Page, task: TaskSpec) -> None:
    _login(page, task)
    page.click('a[href="/report"]')
    page.click(f"#edit-{task.inputs['app_no']}")
    page.fill("#kitta", task.inputs["kitta"])
    page.click("#btn-save")
    page.wait_for_url("**/report")


def _edit_kitta_paged(page: Page, task: TaskSpec) -> None:
    _login(page, task)
    page.click('a[href="/report"]')
    page.click("#next-page")
    page.click(f"#edit-{task.inputs['app_no']}")
    page.fill("#kitta", task.inputs["kitta"])
    page.click("#btn-save")
    page.wait_for_url("**/report")


def _withdraw_paged(page: Page, task: TaskSpec) -> None:
    _login(page, task)
    page.click('a[href="/report"]')
    page.click("#next-page")
    page.click(f"#withdraw-{task.inputs['app_no']}")
    page.click("#btn-confirm-withdraw")
    page.wait_for_url("**/report")


def _withdraw(page: Page, task: TaskSpec) -> None:
    _login(page, task)
    page.click('a[href="/report"]')
    page.click(f"#withdraw-{task.inputs['app_no']}")
    page.click("#btn-confirm-withdraw")
    page.wait_for_url("**/report")


ROUTINES: dict[str, Callable[[Page, TaskSpec], None]] = {
    "nag-01": _submit_bs,
    "nag-02": _submit_ad,
    "nag-03": _update_phone,
    "nag-04": _cancel,
    "nag-05": _submit_bs,
    "nag-06": _submit_bs,
    "nag-07": _submit_bs,
    "nag-08": _submit_bs,
    "nag-09": _submit_bs,
    "nag-10": _cancel,
    "nag-11": _submit_bs,
    "nag-12": _submit_ad,
    "nag-13": _submit_bs,
    "nag-14": _submit_ad,
    "nag-15": _submit_bs,
    "nag-16": _submit_ad,
    "nag-17": _update_phone,
    "nag-18": _update_phone,
    "nag-19": _cancel,
    "nag-20": _cancel,
    "nag-21": _update_phone_retry,
    "nag-22": _cancel,
    "nag-23": _update_phone_paged,
    "nag-24": _update_phone,
    "nag-25": _update_phone,
    "nag-26": _cancel_paged,
    "nag-27": _submit_bs_clear_alt,
    "nag-28": _cancel,
    "nag-29": _submit_bs_ward5,
    "nag-30": _cancel,
    "nag-31": _submit_bs,
    "share-28": _apply_clear_referral,
    "share-29": _apply,
    "share-30": _apply_nabil,
    "share-32": _apply,
    "share-01": _apply,
    "share-02": _apply,
    "share-03": _apply,
    "share-04": _apply,
    "share-05": _apply,
    "share-06": _edit_kitta,
    "share-07": _edit_kitta,
    "share-08": _withdraw,
    "share-09": _withdraw,
    "share-10": _apply,
    "share-11": _apply,
    "share-12": _apply,
    "share-13": _apply,
    "share-14": _apply,
    "share-15": _apply,
    "share-16": _edit_kitta,
    "share-17": _apply,
    "share-18": _apply,
    "share-19": _edit_kitta,
    "share-20": _withdraw,
    "share-21": _apply,
    "share-22": _apply_relogin,
    "share-23": _apply,
    "share-24": _edit_kitta_paged,
    "share-25": _withdraw_paged,
    "share-26": _apply_kitta_retry,
    "share-27": _apply_ascii_boid,
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
