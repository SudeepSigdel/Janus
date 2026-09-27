"""authorize_action: the final gate before an executor may run a step
(CLAUDE.md: "model output is consumed in ... janus/validator/action.py::authorize_action").

A non-consequential action is always allowed. A consequential action is allowed only
if its op kind is already in `granted_ops` -- the set of op kinds this run has been
given permission for. Populating that set (a CLI prompt for interactive use, or a
task's declared approvals in the benchmark's simulated user) is the escalation
interface's job (executor/escalation.py, M4), not this function's.
"""

from __future__ import annotations

from dataclasses import dataclass

from janus.planner.ops import OpKind


@dataclass(frozen=True)
class Authorization:
    allowed: bool
    consequential: bool
    reason: str


def authorize_action(
    op: OpKind, consequential: bool, granted_ops: frozenset[OpKind]
) -> Authorization:
    if not consequential:
        return Authorization(allowed=True, consequential=False, reason="not consequential")
    if op in granted_ops:
        return Authorization(
            allowed=True, consequential=True, reason=f"{op} is a granted consequential action"
        )
    return Authorization(
        allowed=False,
        consequential=True,
        reason=f"{op} is consequential and requires escalation (not in granted ops)",
    )
