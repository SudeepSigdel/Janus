"""Dev/test split loading (docs/PLAN.md, P-milestones: "tune ONLY on dev").

A split file just names which task ids belong to which set; it carries no policy of
its own. `janus_bench.harness.cli` is responsible for refusing `--set test` runs
that aren't explicitly labelled with a checkpoint.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

SetName = Literal["dev", "test"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Split(_Model):
    version: int
    dev: dict[str, dict[str, list[str]]]
    test: dict[str, dict[str, list[str]]]


def load_split(path: Path) -> Split:
    return Split.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def ids_for(split: Split, set_: SetName) -> set[str]:
    """Every task id in `split`'s dev or test set, across sites and categories."""
    sites = split.dev if set_ == "dev" else split.test
    return {
        task_id for categories in sites.values() for ids in categories.values() for task_id in ids
    }
