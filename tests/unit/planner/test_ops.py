"""Plan schema: closed op vocabulary, extra="forbid", $inputs reference parsing."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from janus.planner.ops import Plan, input_ref_key


def test_input_ref_key_parses_a_reference() -> None:
    assert input_ref_key("$inputs.name_ne") == "name_ne"


def test_input_ref_key_rejects_a_literal() -> None:
    assert input_ref_key("सीता तामाङ") is None
    assert input_ref_key("5") is None
    assert input_ref_key("inputs.name_ne") is None  # missing leading $


def test_plan_parses_one_of_each_op_kind() -> None:
    plan = Plan.model_validate(
        {
            "task_id": "nag-01",
            "steps": [
                {"op": "NAVIGATE", "url": "http://127.0.0.1:8101/services"},
                {
                    "op": "FILL_FORM",
                    "fields": [
                        {"ref": "e2", "value": "$inputs.name_ne"},
                        {"ref": "e5", "value": "5"},
                    ],
                },
                {"op": "SELECT", "ref": "e5", "value": "5"},
                {"op": "CLICK", "ref": "e7"},
                {"op": "SUBMIT", "ref": "e7"},
                {"op": "EXTRACT", "field": "receipt_id"},
                {"op": "DONE", "status": "completed"},
            ],
        }
    )
    assert [step.op for step in plan.steps] == [
        "NAVIGATE",
        "FILL_FORM",
        "SELECT",
        "CLICK",
        "SUBMIT",
        "EXTRACT",
        "DONE",
    ]


def test_unknown_op_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Plan.model_validate({"task_id": "t", "steps": [{"op": "DELETE_EVERYTHING", "ref": "e1"}]})


def test_extra_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Plan.model_validate(
            {
                "task_id": "t",
                "steps": [{"op": "CLICK", "ref": "e1", "double_click": True}],
            }
        )


def test_consequential_hint_defaults_false() -> None:
    plan = Plan.model_validate({"task_id": "t", "steps": [{"op": "CLICK", "ref": "e1"}]})
    assert plan.steps[0].consequential_hint is False
