"""ApprovalTarget: what a task's declared approval authorizes, matched per-target
and per-use (docs/PLAN.md Q2).

A new top-level module, not `janus.validator.policy` (that module's `Policy`/
`Capability` are invariant-2 machinery: what a plan is allowed to ask for). This one
is a task-authoring shape: both `janus.task.TaskFile` and
`janus_bench.harness.taskspec.TaskSpec` need to parse it from task YAML, and
`janus_bench` may import from `janus` but never the reverse (CLAUDE.md's import
boundary) -- a top-level module keeps it reachable from both without living under
`validator/`, which is about authorization decisions, not the shape of what's
declared.

Before Q2, an approval label like "cancel_application:045" granted its op kinds
(`_APPROVAL_OP_KINDS` below) for the rest of the run, with no check on *which*
control caused the POST -- `docs/PLAN.md`'s own Q2 session-start trace
(`results/e5-dev-traces/share-01-1.json`) shows the exact failure mode: a login
SUBMIT, authorized because `apply_issue` grants `{SUBMIT, CLICK}` for the whole run,
consumed the task's one declared approval and ended it `completed` after 2 steps,
before the real apply flow ever started. `approval_matches` closes that: an approval
now authorizes a step only if its op kind, its target's accessible name, and a
target binding (`id` -- an application/row number, or `path` -- a URL glob) all
match.
"""

from __future__ import annotations

import fnmatch
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, model_validator

from janus.planner.ops import OpKind

# Which op kinds an approval's action name grants, once its target also matches.
# Two kinds each, not one: nothing forces the planner to name a `<button
# type="submit">` SUBMIT rather than CLICK (M6/M7's own finding -- both trip
# validator/consequential.py's keyword match identically), so an approval must cover
# whichever the model actually picks.
_APPROVAL_OP_KINDS: dict[str, frozenset[OpKind]] = {
    "submit_application": frozenset({"SUBMIT", "CLICK"}),
    "cancel_application": frozenset({"CLICK", "SUBMIT"}),
    "apply_issue": frozenset({"SUBMIT", "CLICK"}),
    "withdraw_application": frozenset({"CLICK", "SUBMIT"}),
}


class ApprovalTarget(BaseModel):
    """One declared approval: `action` (an `_APPROVAL_OP_KINDS` key), the exact
    accessible names of the control(s) it covers (copied verbatim from the site
    templates -- see docs/PLAN.md Q2's migration), and exactly one of `id` (an
    application/row number, matched against the target's `row_key` on a list page or
    a whole path segment of the page URL on a confirm page) or `path` (a URL glob,
    for an approval with no existing row to bind to, e.g. a new submission)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: str
    names: list[str]
    id: str | None = None
    path: str | None = None

    @model_validator(mode="after")
    def _check_target(self) -> ApprovalTarget:
        if not self.names:
            raise ValueError("approval names must not be empty")
        if (self.id is None) == (self.path is None):
            raise ValueError("approval must set exactly one of id or path, not both or neither")
        return self


def op_kinds_for(action: str) -> frozenset[OpKind]:
    """Every op kind `action` grants once its target also matches -- empty for an
    unrecognized action name, so an unknown label authorizes nothing."""
    return _APPROVAL_OP_KINDS.get(action, frozenset())


def _id_matches(target_id: str, row_key: str | None, page_url: str) -> bool:
    if row_key == target_id:
        return True
    segments = urlparse(page_url).path.strip("/").split("/")
    return target_id in segments


def approval_matches(
    approval: ApprovalTarget,
    op: OpKind,
    accessible_name: str | None,
    row_key: str | None,
    page_url: str,
) -> bool:
    """True if `approval` authorizes this specific step: its op kind is one
    `approval.action` grants, its target's accessible name is in `approval.names`
    exactly, and the `id`/`path` binding holds against `row_key`/`page_url`."""
    if op not in op_kinds_for(approval.action):
        return False
    if accessible_name is None or accessible_name not in approval.names:
        return False
    if approval.id is not None:
        return _id_matches(approval.id, row_key, page_url)
    assert approval.path is not None  # the model validator guarantees exactly one
    return fnmatch.fnmatch(urlparse(page_url).path, approval.path)
