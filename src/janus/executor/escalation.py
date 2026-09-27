"""Escalation: how a consequential action gets into `granted_ops`
(validator/action.py::authorize_action expects that set already built).

Two interfaces:
  - `cli_escalation`: an interactive human approves or denies one action at a time.
  - `make_granted_ops`: a deterministic benchmark "simulated user" that grants
    exactly what a task's declared `approvals` allow, before any step runs.

Approval labels are free-form strings, optionally suffixed `:<target>` (e.g.
"cancel_application:045"). Only the prefix maps to op kinds here -- Policy's
capability model is (op, origin, form_id), with no per-record target, and
nagarpalika's forms set no form_id at all, so nothing downstream of this function
can currently tell "cancel 045" from "cancel 046" apart. The suffix is parsed and
available for tests/logging, but is not enforced by authorize_action. That gap is
a known M4 limitation (see docs/PLAN.md), not something this module papers over.

`submit_application` grants both SUBMIT and CLICK (not SUBMIT alone): nagarpalika's
final submit control is an ordinary `<button type="submit">`, and nothing forces the
planner to name that verb SUBMIT rather than CLICK -- validator/consequential.py's
keyword match ("submit" / "पेश") flags it consequential either way. Running the real
planner against the harness's deterministic simulated user (M6) showed it reliably
plans that button as CLICK, so SUBMIT-only left every submit-flow task blocked on its
last step. Same rationale as `cancel_application` below, just discovered later.
"""

from __future__ import annotations

from janus.planner.ops import OpKind, Step

_APPROVAL_OP_KINDS: dict[str, frozenset[OpKind]] = {
    "submit_application": frozenset({"SUBMIT", "CLICK"}),
    # Cancelling is a link click (navigates to a confirmation page) followed by a
    # form submit; both trip the "cancel" consequential keyword (validator/consequential.py).
    "cancel_application": frozenset({"CLICK", "SUBMIT"}),
}


def parse_approval(label: str) -> tuple[str, str | None]:
    """Split "cancel_application:045" into ("cancel_application", "045")."""
    action, _, target = label.partition(":")
    return action, target or None


def make_granted_ops(approvals: list[str]) -> frozenset[OpKind]:
    """The op kinds a benchmark task's declared approvals grant, for the whole run."""
    granted: set[OpKind] = set()
    for label in approvals:
        action, _ = parse_approval(label)
        granted |= _APPROVAL_OP_KINDS.get(action, frozenset())
    return frozenset(granted)


def cli_escalation(step: Step, reason: str) -> bool:
    """Prompt a human on stdin/stdout to approve one consequential action."""
    target = getattr(step, "ref", None) or getattr(step, "url", "")
    answer = input(f"Approve {step.op} on {target!r}? [{reason}] (y/N): ")
    return answer.strip().lower() in ("y", "yes")
