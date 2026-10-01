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


SPLIT_V2 = Path(__file__).resolve().parents[3] / "splits" / "v2.yaml"
TASKS_DIR = Path(__file__).resolve().parents[3] / "tasks"
NEW_IDS = {
    "nagarpalika": [f"nag-{i}" for i in (*range(21, 27), *range(27, 32))],
    "sharesewa": [f"share-{i}" for i in (*range(22, 28), 28, 29, 30, 32)],
}
# Q5's 10 cases are one-per-stratum (site x category), so the assignment rule sends them all to dev.
Q5_IDS = {f"nag-{i}" for i in range(27, 32)} | {f"share-{i}" for i in (28, 29, 30, 32)}


def _round_half_up(x: float) -> int:
    return int(x + 0.5)


def test_v2_contains_v1_on_both_sides_without_overlap() -> None:
    v1, v2 = load_split(SPLIT), load_split(SPLIT_V2)
    assert v2.version == 2
    assert ids_for(v1, "dev") <= ids_for(v2, "dev")
    assert ids_for(v1, "test") <= ids_for(v2, "test")
    assert ids_for(v2, "dev").isdisjoint(ids_for(v2, "test"))


def test_v2_covers_every_task_exactly_once() -> None:
    from janus_bench.harness.taskspec import load_tasks

    split = load_split(SPLIT_V2)
    all_ids = [t.id for t in load_tasks(TASKS_DIR)]
    in_split = ids_for(split, "dev") | ids_for(split, "test")
    assert sorted(all_ids) == sorted(in_split)
    assert len(ids_for(split, "dev")) + len(ids_for(split, "test")) == len(all_ids)


def test_v2_new_tasks_follow_the_declared_assignment_rule() -> None:
    from janus_bench.harness.taskspec import load_tasks

    v1, v2 = load_split(SPLIT), load_split(SPLIT_V2)
    old = ids_for(v1, "dev") | ids_for(v1, "test")
    tasks = {t.id: t for t in load_tasks(TASKS_DIR)}
    # category of a new id = the v2 category list it sits in
    placed: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for set_name in ("dev", "test"):
        for site, cats in getattr(v2, set_name).items():
            for cat, ids in cats.items():
                for task_id in ids:
                    if task_id not in old:
                        # injection tasks stratify by their own injection category (Q5)
                        inj = tasks[task_id].injection
                        key = inj.category if inj is not None else cat
                        placed.setdefault((site, key), []).append((task_id, set_name))
    assert sorted(i for g in placed.values() for i, _ in g) == sorted(
        i for ids in NEW_IDS.values() for i in ids
    )
    for (site, _cat), group in placed.items():
        assert all(tasks[i].site == site for i, _ in group)
        for k, (task_id, set_name) in enumerate(sorted(group), start=1):
            expect_test = _round_half_up(0.3 * k) > _round_half_up(0.3 * (k - 1))
            assert (set_name == "test") == expect_test, task_id


def test_v2_q5_cases_all_land_in_dev_and_split_is_frozen_shape() -> None:
    v2 = load_split(SPLIT_V2)
    assert Q5_IDS <= ids_for(v2, "dev")
    assert Q5_IDS.isdisjoint(ids_for(v2, "test"))
    assert len(ids_for(v2, "dev")) == 47 and len(ids_for(v2, "test")) == 15
