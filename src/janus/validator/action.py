"""authorize_action: the final gate before an executor may run a step
(CLAUDE.md: "model output is consumed in ... janus/validator/action.py::authorize_action").

A non-consequential action is always allowed. A consequential action is allowed if
either:
  - its op kind is already in `granted_ops` -- a live, run-wide grant from an
    interactive human approving a step at `escalate`'s prompt (executor/escalation.py,
    M4); or
  - it matches one of `approvals` (docs/PLAN.md Q2): a declared `ApprovalTarget`
    whose op kind, target accessible name, and `id`/`path` binding all match this
    step -- see `janus.policy.approval_matches`. The caller is expected to pass only
    *remaining* (not yet consumed) approvals; `agent.py` owns removing a matched
    approval once the step it authorized actually commits.

Populating `granted_ops`/`approvals` is the escalation interface's job (a CLI prompt
for interactive use, or a task's declared approvals in the benchmark's simulated
user), not this function's.
"""

from __future__ import annotations

from dataclasses import dataclass

from janus.planner.ops import OpKind
from janus.policy import ApprovalTarget, approval_matches


@dataclass(frozen=True)
class Authorization:
    allowed: bool
    consequential: bool
    reason: str
    matched: ApprovalTarget | None = None


def authorize_action(
    op: OpKind,
    consequential: bool,
    accessible_name: str | None,
    row_key: str | None,
    page_url: str,
    approvals: list[ApprovalTarget],
    granted_ops: frozenset[OpKind] = frozenset(),
) -> Authorization:
    if not consequential:
        return Authorization(allowed=True, consequential=False, reason="not consequential")
    if op in granted_ops:
        return Authorization(
            allowed=True,
            consequential=True,
            reason=f"{op} is a granted consequential action",
        )
    for approval in approvals:
        if approval_matches(approval, op, accessible_name, row_key, page_url):
            return Authorization(
                allowed=True,
                consequential=True,
                reason=f"{op} matches approval {approval.action!r}",
                matched=approval,
            )
    return Authorization(
        allowed=False,
        consequential=True,
        reason=f"{op} is consequential and matches no remaining approval",
    )
