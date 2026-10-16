"""Execute one already-validated, already-authorized Step against a live page.

`execute_step` never validates or authorizes anything itself (`janus.validator`
already did that). Its only job: resolve the target by fingerprint immediately
before acting (invariant 5), perform the Playwright action, and report what
happened so the verifier can judge the outcome.
"""

from __future__ import annotations

from dataclasses import dataclass

from playwright.sync_api import ElementHandle, Page, Request
from playwright.sync_api import Error as PlaywrightError

from janus.executor.resolve import ResolutionError, resolve_element
from janus.observer.snapshot import Element, PageSnapshot
from janus.planner.ops import (
    ClickStep,
    ExtractStep,
    FillFormStep,
    NavigateStep,
    SelectStep,
    Step,
    SubmitStep,
    input_ref_key,
)
from janus.text.datetime_input import native_input_value


@dataclass(frozen=True)
class StepOutcome:
    ok: bool
    blocked: bool = False
    reason: str = ""
    extracted_value: str | None = None
    # A CLICK/SUBMIT caused a main-frame POST navigation (docs/PLAN.md P4: every
    # replica state change is a real form POST; a link like the cancel/withdraw
    # confirm-page navigation is a GET and never sets this). Unset (False) for
    # every other op kind.
    caused_post: bool = False


def bind_value(value: str, inputs: dict[str, str]) -> str:
    """Resolve a step's field value: `$inputs.<key>` binds to a task input, anything
    else is a literal (validator/plan.py already checked sensitive fields can't be)."""
    key = input_ref_key(value)
    return inputs[key] if key is not None else value


def _element_by_ref(snapshot: PageSnapshot, ref: str) -> Element | None:
    for element in snapshot.elements:
        if element.ref == ref:
            return element
    return None


def _resolve_or_block(
    page: Page, element: Element
) -> tuple[ElementHandle | None, StepOutcome | None]:
    try:
        return resolve_element(page, element), None
    except ResolutionError as exc:
        return None, StepOutcome(ok=False, blocked=True, reason=str(exc))


def _click_and_detect_post(page: Page, handle: ElementHandle) -> bool:
    """Click `handle` and report whether it caused a main-frame POST navigation
    (docs/PLAN.md P4's definition of a "commit"). A GET navigation (e.g. the
    cancel/withdraw link to a confirm page) or no navigation at all (a plain CLICK)
    both report False."""
    posts: list[bool] = []

    def _on_request(request: Request) -> None:
        if (
            request.method == "POST"
            and request.is_navigation_request()
            and request.frame == page.main_frame
        ):
            posts.append(True)

    page.on("request", _on_request)
    try:
        handle.click(timeout=ACTION_TIMEOUT_MS)
        page.wait_for_load_state("load")
    finally:
        page.remove_listener("request", _on_request)
    return bool(posts)


ACTION_TIMEOUT_MS = 5000


def execute_step(
    page: Page, step: Step, snapshot: PageSnapshot, inputs: dict[str, str]
) -> StepOutcome:
    """Run one step. A Playwright failure (element not editable, covered, detached, timed
    out) is a failed step the agent can replan from, not an exception that ends the run
    (docs/AUDIT.md R7). Egress-guard and fingerprint blocks keep their own outcomes."""
    try:
        return _execute(page, step, snapshot, inputs)
    except PlaywrightError as exc:
        first_line = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        return StepOutcome(ok=False, reason=f"action failed: {first_line[:200]}")


def _execute(page: Page, step: Step, snapshot: PageSnapshot, inputs: dict[str, str]) -> StepOutcome:
    if isinstance(step, NavigateStep):
        page.goto(step.url)
        return StepOutcome(ok=True)

    if isinstance(step, FillFormStep):
        for field_value in step.fields:
            element = _element_by_ref(snapshot, field_value.ref)
            if element is None:
                return StepOutcome(ok=False, reason=f"unknown element ref {field_value.ref!r}")
            handle, blocked = _resolve_or_block(page, element)
            if blocked is not None:
                return blocked
            value = bind_value(field_value.value, inputs)
            # B1b: native date/time inputs accept only ISO values; convert written forms.
            handle.fill(
                native_input_value(handle.get_attribute("type"), value), timeout=ACTION_TIMEOUT_MS
            )
        return StepOutcome(ok=True)

    if isinstance(step, SelectStep):
        element = _element_by_ref(snapshot, step.ref)
        if element is None:
            return StepOutcome(ok=False, reason=f"unknown element ref {step.ref!r}")
        handle, blocked = _resolve_or_block(page, element)
        if blocked is not None:
            return blocked
        handle.select_option(bind_value(step.value, inputs), timeout=ACTION_TIMEOUT_MS)
        return StepOutcome(ok=True)

    if isinstance(step, (ClickStep, SubmitStep)):
        element = _element_by_ref(snapshot, step.ref)
        if element is None:
            return StepOutcome(ok=False, reason=f"unknown element ref {step.ref!r}")
        handle, blocked = _resolve_or_block(page, element)
        if blocked is not None:
            return blocked
        caused_post = _click_and_detect_post(page, handle)
        return StepOutcome(ok=True, caused_post=caused_post)

    if isinstance(step, ExtractStep):
        element = _element_by_ref(snapshot, step.field)
        if element is None:
            return StepOutcome(ok=False, reason=f"unknown element ref {step.field!r}")
        handle, blocked = _resolve_or_block(page, element)
        if blocked is not None:
            return blocked
        if element.role in ("textbox", "combobox"):
            text = handle.input_value()
        else:
            text = handle.text_content() or ""
        return StepOutcome(ok=True, extracted_value=text)

    # DONE: nothing to execute; the verifier judges the run's outcome, not this step.
    return StepOutcome(ok=True)
