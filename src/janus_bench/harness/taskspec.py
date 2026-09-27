"""Task specification: what to do, what the user approves, and how success is checked."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Instruction(_Model):
    en: str
    ne: str


class Check(_Model):
    """A state-based check against /__bench/state. `path` is dotted (dict keys, list indices)."""

    path: str
    op: Literal["equals", "contains", "count"]
    value: Any


class Injection(_Model):
    variant: str
    category: Literal["hijack", "value_poisoning", "exfiltration"]
    lang: Literal["en", "ne", "hi"]
    forbidden: list[Check]


class TaskSpec(_Model):
    id: str
    site: str
    start_url: str
    instruction: Instruction
    inputs: dict[str, str] = {}
    approvals: list[str] = []
    sensitive_field_names: list[str] = []
    success: list[Check]
    injection: Injection | None = None


def load_task(path: Path) -> TaskSpec:
    return TaskSpec.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def load_tasks(directory: Path) -> list[TaskSpec]:
    return [load_task(p) for p in sorted(directory.glob("*.yaml"))]
