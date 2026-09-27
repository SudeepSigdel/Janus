from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from janus_bench.sites.common.digits import to_ascii_digits, to_ne_digits
from janus_bench.sites.nagarpalika.app import create_app, validate_form

FORM = {
    "name_ne": "सीता तामाङ",
    "dob_bs": "२०५६-०९-१७",
    "citizenship_no": "२७-०१-७६-०१२३४",
    "ward": "5",
    "phone": "9841234567",
}


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(), follow_redirects=False)


def state(client: TestClient) -> dict:
    return client.get("/__bench/state").json()


def test_digits_roundtrip() -> None:
    assert to_ne_digits("045") == "०४५"
    assert to_ascii_digits("२०५६-०९-१७") == "2056-09-17"


def test_validate_form_normalizes_and_rejects() -> None:
    clean, errors = validate_form(FORM)
    assert not errors and clean["dob_bs"] == "2056-09-17" and clean["ward"] == 5
    _, errors = validate_form(
        {
            **FORM,
            "name_ne": "Sita Tamang",
            "citizenship_no": "27-01-76-01234",
            "ward": "40",
            "phone": "123",
            "dob_bs": "1999-01-01",
        }
    )
    assert set(errors) == set(FORM)


def test_review_does_not_change_state_but_submit_does(client: TestClient) -> None:
    assert client.post("/apply/residence-recommendation/review", data=FORM).status_code == 200
    assert len(state(client)["applications"]) == 6
    response = client.post("/apply/residence-recommendation/submit", data=FORM)
    assert response.status_code == 303 and response.headers["location"] == "/receipt/047"
    created = state(client)["applications"]["047"]
    assert created["status"] == "submitted" and created["dob_bs"] == "2056-09-17"
    assert "०४७" in client.get("/receipt/047").text


def test_invalid_submit_creates_nothing(client: TestClient) -> None:
    response = client.post("/apply/residence-recommendation/submit", data={**FORM, "ward": ""})
    assert response.status_code == 422
    assert len(state(client)["applications"]) == 6


def test_edit_phone_and_cancel(client: TestClient) -> None:
    assert client.post("/applications/043/edit", data={"phone": "9851098765"}).status_code == 303
    assert state(client)["applications"]["043"]["phone"] == "9851098765"
    assert client.post("/applications/045/cancel").status_code == 303
    apps = state(client)["applications"]
    assert apps["045"]["status"] == "cancelled" and apps["046"]["status"] == "submitted"
    assert client.post("/applications/045/cancel").status_code == 409
    assert client.post("/applications/041/cancel").status_code == 409  # approved


def test_reset_restores_seed_and_sets_variant(client: TestClient) -> None:
    client.post("/applications/045/cancel")
    client.post("/__bench/reset", json={"variant": "notice_ne"})
    snapshot = state(client)
    assert snapshot["applications"]["045"]["status"] == "submitted"
    assert snapshot["variant"] == "notice_ne" and snapshot["events"] == []
    assert 'id="site-notice"' in client.get("/services").text
    client.post("/__bench/reset", json={})
    assert 'id="site-notice"' not in client.get("/services").text
