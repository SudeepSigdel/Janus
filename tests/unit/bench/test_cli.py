"""janus_bench.harness.cli: split/set task selection and the `analyze` command's
argument wiring. Not the full `run`/`analyze` bodies -- those need real sites/agents
and are exercised by `-m browser` and the milestone Accept commands."""

from __future__ import annotations

from pathlib import Path

import pytest

from janus_bench.harness import cli
from janus_bench.harness.results import RunRecord, append_record

TASKS = Path(__file__).resolve().parents[3] / "tasks"
SPLIT = Path(__file__).resolve().parents[3] / "splits" / "v1.yaml"


def test_select_tasks_without_split_returns_everything_under_tasks_dir() -> None:
    tasks = cli._select_tasks(TASKS / "nagarpalika", split=None, set_=None, checkpoint=None)
    assert len(tasks) == 20


def test_select_tasks_dev_set_is_29() -> None:
    tasks = cli._select_tasks(TASKS, split=SPLIT, set_="dev", checkpoint=None)
    assert len(tasks) == 29


def test_select_tasks_test_set_requires_a_checkpoint() -> None:
    with pytest.raises(SystemExit):
        cli._select_tasks(TASKS, split=SPLIT, set_="test", checkpoint=None)


def test_select_tasks_test_set_with_checkpoint_is_12() -> None:
    tasks = cli._select_tasks(TASKS, split=SPLIT, set_="test", checkpoint="CP1")
    assert len(tasks) == 12


def test_select_tasks_split_without_set_is_an_error() -> None:
    with pytest.raises(SystemExit):
        cli._select_tasks(TASKS, split=SPLIT, set_=None, checkpoint=None)


def test_select_tasks_set_without_split_is_an_error() -> None:
    with pytest.raises(SystemExit):
        cli._select_tasks(TASKS, split=None, set_="dev", checkpoint=None)


def test_analyze_prints_a_row_and_returns_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    records_path = tmp_path / "r.jsonl"
    append_record(
        records_path,
        RunRecord(
            task="nag-01",
            agent="janus",
            repeat=1,
            success=True,
            steps=3,
            wall_time=1.0,
            injection_outcome="n/a",
            status="completed",
        ),
    )
    assert cli.analyze(TASKS, [records_path]) == 0
    assert "1/1" in capsys.readouterr().out


def test_analyze_with_no_records_returns_2(tmp_path: Path) -> None:
    records_path = tmp_path / "empty.jsonl"
    records_path.write_text("", encoding="utf-8")
    assert cli.analyze(TASKS, [records_path]) == 2


def test_analyze_narrows_to_a_split_set(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    records_path = tmp_path / "r.jsonl"
    append_record(
        records_path,
        RunRecord(
            task="nag-01",
            agent="janus",
            repeat=1,
            success=True,
            steps=3,
            wall_time=1.0,
            injection_outcome="n/a",
        ),
    )
    append_record(
        records_path,
        RunRecord(
            # nag-12 is a test-only id in splits/v1.yaml, so a dev-set analyze must drop it
            task="nag-12",
            agent="janus",
            repeat=1,
            success=False,
            steps=3,
            wall_time=1.0,
            injection_outcome="n/a",
        ),
    )
    assert cli.analyze(TASKS, [records_path], split=SPLIT, set_="dev") == 0
    assert "1/1" in capsys.readouterr().out


def test_analyze_with_split_but_no_set_is_an_error(tmp_path: Path) -> None:
    records_path = tmp_path / "r.jsonl"
    append_record(
        records_path,
        RunRecord(
            task="nag-01",
            agent="janus",
            repeat=1,
            success=True,
            steps=3,
            wall_time=1.0,
            injection_outcome="n/a",
        ),
    )
    with pytest.raises(SystemExit):
        cli.analyze(TASKS, [records_path], split=SPLIT, set_=None)


def test_main_dispatches_analyze(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, object] = {}

    def fake_analyze(tasks_dir, records_paths, split=None, set_=None):
        called["args"] = (tasks_dir, records_paths, split, set_)
        return 0

    monkeypatch.setattr(cli, "analyze", fake_analyze)
    assert cli.main(["analyze", "--records", "r.jsonl", "--split", "s.yaml", "--set", "dev"]) == 0
    assert called["args"] == (Path("tasks"), [Path("r.jsonl")], Path("s.yaml"), "dev")


def test_main_dispatches_run_with_split_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, object] = {}

    def fake_run(agent, tasks_dir, repeats, out, split, set_, checkpoint, trace, model):
        called["args"] = (agent, tasks_dir, repeats, out, split, set_, checkpoint, trace, model)
        return 0

    monkeypatch.setattr(cli, "run", fake_run)
    rc = cli.main(
        [
            "run",
            "--agent",
            "janus",
            "--tasks",
            "tasks",
            "--split",
            "splits/v1.yaml",
            "--set",
            "test",
            "--checkpoint",
            "CP1",
            "--trace",
        ]
    )
    assert rc == 0
    assert called["args"] == (
        "janus",
        Path("tasks"),
        1,
        None,
        Path("splits/v1.yaml"),
        "test",
        "CP1",
        True,
        None,
    )


def test_main_dispatches_run_with_model_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, object] = {}

    def fake_run(agent, tasks_dir, repeats, out, split, set_, checkpoint, trace, model):
        called["args"] = (agent, tasks_dir, repeats, out, split, set_, checkpoint, trace, model)
        return 0

    monkeypatch.setattr(cli, "run", fake_run)
    rc = cli.main(["run", "--agent", "janus", "--tasks", "tasks", "--model", "janus-planner-4b"])
    assert rc == 0
    assert called["args"] == (
        "janus",
        Path("tasks"),
        1,
        None,
        None,
        None,
        None,
        False,
        "janus-planner-4b",
    )


def test_make_agent_janus_with_model_overrides_planner_model() -> None:
    agent = cli.make_agent("janus", model="janus-planner-4b")
    assert agent.settings.planner_model == "janus-planner-4b"


def test_make_agent_janus_without_model_keeps_default() -> None:
    agent = cli.make_agent("janus")
    assert agent.settings.planner_model == "janus-planner"
