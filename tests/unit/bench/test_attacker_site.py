from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from janus_bench.sites.attacker.app import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(), follow_redirects=False)


def state(client: TestClient) -> dict:
    return client.get("/__bench/state").json()


def test_starts_empty(client: TestClient) -> None:
    assert state(client) == {"received": [], "variant": None, "events": []}


def test_any_path_and_method_is_logged(client: TestClient) -> None:
    client.get("/copy", params={"app": "047"})
    client.post("/verify", data={"citizenship_no": "२७-०१-७६-०१२३४"})
    received = state(client)["received"]
    assert len(received) == 2
    assert received[0]["method"] == "GET" and received[0]["path"] == "/copy"
    assert received[0]["query"] == {"app": "047"}
    assert received[1]["method"] == "POST" and received[1]["path"] == "/verify"
    assert "citizenship_no" in received[1]["body"]


def test_reset_clears_the_log(client: TestClient) -> None:
    client.get("/copy")
    assert state(client)["received"]
    client.post("/__bench/reset")
    assert state(client)["received"] == []
