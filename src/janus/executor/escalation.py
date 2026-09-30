"""Escalation: how a consequential action gets approved.

`cli_escalation` is the interactive channel -- a human approves or denies one action
at a time at a prompt; a yes grants that op kind for the rest of the run
(`agent.py`'s `granted_ops`). It's deliberately coarser than
`janus.policy.ApprovalTarget`'s per-target matching: the human *is* the per-target
check there (they see the actual step before answering), so nothing needs to
re-derive a target binding for them the way the benchmark's deterministic simulated
user does via a task's declared `approvals` (docs/PLAN.md Q2).

Before Q2, a task's declared approvals were turned into a flat `granted_ops` set via
`make_granted_ops`/`parse_approval` here -- op-kind-for-the-whole-run, with no check
on which control actually caused a step's POST. That let an unrelated authorized
step (e.g. ShareSewa's login SUBMIT, also granted by `apply_issue`) consume a task's
approval by accident (`docs/EXPERIMENTS.md`'s E6 row). Q2 replaces that with
`janus.policy.ApprovalTarget`, parsed straight from task YAML and passed to
`authorize_action`/`run_task` directly -- there is no longer a string label to parse
here.
"""

from __future__ import annotations

from janus.planner.ops import Step


def cli_escalation(step: Step, reason: str) -> bool:
    """Prompt a human on stdin/stdout to approve one consequential action."""
    target = getattr(step, "ref", None) or getattr(step, "url", "")
    answer = input(f"Approve {step.op} on {target!r}? [{reason}] (y/N): ")
    return answer.strip().lower() in ("y", "yes")
