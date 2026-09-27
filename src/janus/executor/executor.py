"""Execute one already-validated, already-authorized Step against a live page.

`execute_step` never validates or authorizes anything itself (`janus.validator`
already did that). Its only job: resolve the target by fingerprint immediately
before acting (invariant 5), perform the Playwright action, and report what
happened so the verifier can judge the outcome.
"""

from __future__ import annotations

from dataclasses import dataclass

from playwright.sync_api import ElementHandle, Page

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


@dataclass(frozen=True)
class StepOutcome:
    ok: bool
    blocked: bool = False
    reason: str = ""
    extracted_value: str | None = None


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


def execute_step(
    page: Page, step: Step, snapshot: PageSnapshot, inputs: dict[str, str]
) -> StepOutcome:
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
            handle.fill(bind_value(field_value.value, inputs))
        return StepOutcome(ok=True)

    if isinstance(step, SelectStep):
        element = _element_by_ref(snapshot, step.ref)
        if element is None:
            return StepOutcome(ok=False, reason=f"unknown element ref {step.ref!r}")
        handle, blocked = _resolve_or_block(page, element)
        if blocked is not None:
            return blocked
        handle.select_option(bind_value(step.value, inputs))
        return StepOutcome(ok=True)

    if isinstance(step, (ClickStep, SubmitStep)):
        element = _element_by_ref(snapshot, step.ref)
        if element is None:
            return StepOutcome(ok=False, reason=f"unknown element ref {step.ref!r}")
        handle, blocked = _resolve_or_block(page, element)
        if blocked is not None:
            return blocked
        handle.click()
        return StepOutcome(ok=True)

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
