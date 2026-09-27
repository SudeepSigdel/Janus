"""TaskFile: `janus run`'s minimal, independent re-parse of a task YAML (see
task.py's docstring for why it doesn't share a schema with janus_bench.harness.taskspec).
"""

from __future__ import annotations

from pathlib import Path

from janus.task import load_task_file

TASKS_DIR = Path(__file__).resolve().parents[2] / "tasks" / "nagarpalika"


def test_load_task_file_reads_t01() -> None:
    task = load_task_file(TASKS_DIR / "t01.yaml")
    assert task.id == "nag-01"
    assert task.site == "nagarpalika"
    assert task.start_url == "http://127.0.0.1:8101/services"
    assert task.inputs["ward"] == "5"
    assert task.approvals == ["submit_application"]
    assert "Sita Tamang" in task.instruction.en


def test_load_task_file_ignores_bench_only_fields() -> None:
    # t01.yaml also has a `success` block (janus_bench.harness.taskspec.TaskSpec's own
    # success oracle) that TaskFile has no field for; it must be tolerated, not rejected.
    task = load_task_file(TASKS_DIR / "t01.yaml")
    assert not hasattr(task, "success")


def test_load_task_file_defaults_missing_optional_fields(tmp_path: Path) -> None:
    path = tmp_path / "t.yaml"
    path.write_text(
        "id: t-min\n"
        "site: nagarpalika\n"
        "start_url: http://127.0.0.1:8101/services\n"
        "instruction:\n"
        '  en: "Do the thing."\n'
        '  ne: "काम गर्नुहोस्।"\n',
        encoding="utf-8",
    )
    task = load_task_file(path)
    assert task.inputs == {}
    assert task.approvals == []
    assert task.sensitive_field_names == []


def test_load_task_file_reads_sensitive_field_names() -> None:
    task = load_task_file(
        Path(__file__).resolve().parents[2] / "tasks" / "sharesewa" / "share-01.yaml"
    )
    assert task.sensitive_field_names == ["pin"]
