"""Task files for `janus run`: the subset of a task YAML's fields the runtime needs.

Task YAML files live under `tasks/<site>/` and are the same files the benchmark's
`janus_bench.harness.taskspec.TaskSpec` loads -- but `janus` (runtime) must never
import `janus_bench` (CLAUDE.md's import boundary, test-enforced). Rather than share
a schema module across that boundary, `TaskFile` independently re-parses just the
fields `janus run` needs and ignores the rest (`success`, `injection`: the
benchmark's own success oracle, off limits to the runtime per verifier/verify.py).
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict


class Instruction(BaseModel):
    model_config = ConfigDict(extra="ignore")

    en: str
    ne: str


class TaskFile(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    site: str
    start_url: str
    instruction: Instruction
    inputs: dict[str, str] = {}
    approvals: list[str] = []
    sensitive_field_names: list[str] = []


def load_task_file(path: Path) -> TaskFile:
    return TaskFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
