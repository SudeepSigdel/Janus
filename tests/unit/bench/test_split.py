from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from janus_bench.harness.split import Split, ids_for, load_split

SPLIT = Path(__file__).resolve().parents[3] / "splits" / "v1.yaml"


def test_v1_loads_and_has_no_overlap() -> None:
    split = load_split(SPLIT)
    dev_ids = ids_for(split, "dev")
    test_ids = ids_for(split, "test")
    assert len(dev_ids) == 29
    assert len(test_ids) == 12
    assert dev_ids.isdisjoint(test_ids)


def test_known_dev_ids_present() -> None:
    dev_ids = ids_for(load_split(SPLIT), "dev")
    assert {"nag-13", "share-13"} <= dev_ids


def test_forbids_extra_fields(tmp_path: Path) -> None:
    data = {"version": 1, "dev": {}, "test": {}, "surprise": 1}
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_split(path)


def test_ids_for_flattens_categories(tmp_path: Path) -> None:
    split = Split(
        version=1,
        dev={"site_a": {"submit": ["a-01", "a-02"], "edit": ["a-03"]}},
        test={"site_a": {"submit": ["a-04"]}},
    )
    assert ids_for(split, "dev") == {"a-01", "a-02", "a-03"}
    assert ids_for(split, "test") == {"a-04"}
