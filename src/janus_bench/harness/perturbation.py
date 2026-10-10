"""`--perturbation`: run tasks against a surface-perturbed deployment (docs/AUDIT.md §4).

The site side lives in `sites/common/perturb.py` (labels, control names, field order,
layout). This module only adapts a `TaskSpec` to that deployment: approval names and
sensitive field names are written against the deployed site's labels and control names, so
they are rewritten through the same maps. Instructions, inputs and success checks are
unchanged -- a perturbation must not change what the task asks for or how it is scored.
"""

from __future__ import annotations

from janus_bench.harness.taskspec import TaskSpec
from janus_bench.sites.common.perturb import (
    PERTURBATIONS,
    perturb_approval,
    perturbed_sensitive_fields,
)


def perturb_task(task: TaskSpec, name: str | None) -> TaskSpec:
    if name is None:
        return task
    p = PERTURBATIONS[name]
    return task.model_copy(
        update={
            "approvals": [perturb_approval(a, p) for a in task.approvals],
            "sensitive_field_names": perturbed_sensitive_fields(task.sensitive_field_names, p),
        }
    )
