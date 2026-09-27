"""bind_value: the only part of executor.py testable without a live page."""

from __future__ import annotations

from janus.executor.executor import bind_value


def test_input_ref_binds_to_task_input() -> None:
    assert bind_value("$inputs.phone", {"phone": "9841234567"}) == "9841234567"


def test_literal_value_passes_through_unchanged() -> None:
    assert bind_value("5", {"phone": "9841234567"}) == "5"
