"""Per-op postconditions and run classification (`DoneStep.status`'s vocabulary).

Checks operate on observable page state only -- never `/__bench/state`, which is
the benchmark harness's own success oracle and off limits to the runtime (CLAUDE.md's
import boundary: `janus` must never import `janus_bench`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from playwright.sync_api import Page

from janus.executor.executor import StepOutcome, bind_value
from janus.executor.resolve import ResolutionError, resolve_element
from janus.observer.snapshot import Element, PageSnapshot
from janus.planner.ops import FillFormStep, SelectStep, Step

Status = Literal["completed", "blocked", "partial"]


@dataclass(frozen=True)
class StepCheck:
    ok: bool
    reason: str = ""


def _element_by_ref(snapshot: PageSnapshot, ref: str) -> Element | None:
    for element in snapshot.elements:
        if element.ref == ref:
            return element
    return None


def verify_step(
    page: Page, step: Step, snapshot: PageSnapshot, inputs: dict[str, str]
) -> StepCheck:
    """Confirm a step actually took effect, beyond "the Playwright call didn't raise".

    Only FILL_FORM and SELECT have a checkable postcondition here (the field's value
    round-trips); other ops' effects are page transitions the next leg's snapshot
    either confirms or contradicts by what elements are actually there to act on.
    """
    if isinstance(step, FillFormStep):
        for field_value in step.fields:
            element = _element_by_ref(snapshot, field_value.ref)
            if element is None:
                return StepCheck(False, f"unknown element ref {field_value.ref!r}")
            try:
                handle = resolve_element(page, element)
            except ResolutionError as exc:
                return StepCheck(False, str(exc))
            expected = bind_value(field_value.value, inputs)
            if handle.input_value() != expected:
                return StepCheck(False, f"field {field_value.ref!r} did not take its value")
        return StepCheck(True)

    if isinstance(step, SelectStep):
        element = _element_by_ref(snapshot, step.ref)
        if element is None:
            return StepCheck(False, f"unknown element ref {step.ref!r}")
        try:
            handle = resolve_element(page, element)
        except ResolutionError as exc:
            return StepCheck(False, str(exc))
        expected = bind_value(step.value, inputs)
        if handle.input_value() != expected:
            return StepCheck(False, f"select {step.ref!r} did not take its value")
        return StepCheck(True)

    return StepCheck(True)


def classify_run(outcomes: list[StepOutcome], checks: list[StepCheck], claimed: Status) -> Status:
    """The run's real status: a blocked or failed step overrides whatever a DONE step claims."""
    if any(outcome.blocked for outcome in outcomes):
        return "blocked"
    if any(not outcome.ok for outcome in outcomes) or any(not check.ok for check in checks):
        return "partial"
    return claimed
