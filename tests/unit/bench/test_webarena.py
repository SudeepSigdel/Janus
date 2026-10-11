"""janus_bench.external.webarena (docs/PLAN.md B3): response shape, split, schema lookup."""

from __future__ import annotations

import json

import httpx

from janus.config import Settings
from janus.goal import classify_task, extract_data
from janus.llm import LLMClient
from janus.observer.snapshot import PageSnapshot
from janus_bench.external import webarena as wa


def _llm(content: dict) -> LLMClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(content)}}]}
        )

    return LLMClient(Settings(), transport=httpx.MockTransport(handler))


def test_split_ids() -> None:
    assert len(wa.load_split("reddit_hard")) == 42
    assert len(wa.load_split("reddit_all")) == 106
    assert set(wa.load_split("reddit_hard")) <= set(wa.load_split("reddit_all"))


def test_agent_response_shapes() -> None:
    assert wa.agent_response("MUTATE", "completed", None) == {
        "task_type": "MUTATE",
        "status": "SUCCESS",
        "retrieved_data": None,
        "error_details": None,
    }
    retrieve = wa.agent_response("RETRIEVE", "partial", [{"count": 3}])
    assert retrieve["retrieved_data"] == [{"count": 3}]
    blocked = wa.agent_response("MUTATE", "blocked", None)
    assert blocked["status"] == "ACTION_NOT_ALLOWED_ERROR" and blocked["error_details"]


def test_results_schema_comes_from_the_agent_response_evaluator() -> None:
    task = {
        "eval": [
            {"evaluator": "NetworkEventEvaluator"},
            {"evaluator": "AgentResponseEvaluator", "results_schema": {"type": "array"}},
        ]
    }
    assert wa.results_schema(task) == {"type": "array"}
    assert wa.results_schema({"eval": []}) is None


def test_classify_task_accepts_only_the_three_types() -> None:
    assert classify_task("Post a comment", _llm({"task_type": "MUTATE"})) == "MUTATE"
    assert classify_task("Post a comment", _llm({"task_type": "DELETE"})) is None


def test_extract_data_wraps_a_single_object_in_a_list() -> None:
    snap = PageSnapshot(url="http://127.0.0.1:9999/", title="t", elements=[], untrusted_text=[])
    out = extract_data("q", snap, _llm({"retrieved_data": {"count": 2}}), {"type": "array"})
    assert out == [{"count": 2}]
    assert extract_data("q", snap, _llm({"retrieved_data": None}), {"type": "array"}) is None
