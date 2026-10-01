from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from janus_bench.sites.sharesewa.app import create_app, validate_apply_form, validate_login

LOGIN = {"boid": "१२३४५६७८", "password": "Sajilo@123"}
APPLY = {"bank": "nabil", "kitta": "200", "crn": "CRN-482901", "pin": "१२३४"}


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(), follow_redirects=False)


def state(client: TestClient) -> dict:
    return client.get("/__bench/state").json()


def login(client: TestClient) -> None:
    assert client.post("/login", data=LOGIN).status_code == 303


def test_validate_login_normalizes_and_rejects() -> None:
    clean, errors = validate_login(LOGIN)
    assert not errors and clean["boid"] == "१२३४५६७८"
    _, errors = validate_login({"boid": "12345678", "password": "short"})
    assert set(errors) == {"boid", "password"}


def test_validate_apply_form_normalizes_and_rejects() -> None:
    clean, errors = validate_apply_form("nic-asia-debenture", APPLY)
    assert not errors and clean["kitta"] == 200
    _, errors = validate_apply_form(
        "nic-asia-debenture",
        {"bank": "unknown", "kitta": "205", "crn": "482901", "pin": "1234"},
    )
    assert set(errors) == {"bank", "kitta", "crn", "pin"}


def test_kitta_must_respect_issue_bounds() -> None:
    _, errors = validate_apply_form("himalayan-hydro-ipo", {**APPLY, "kitta": "5"})
    assert "kitta" in errors  # below min_kitta=10
    _, errors = validate_apply_form("himalayan-hydro-ipo", {**APPLY, "kitta": "5000"})
    assert "kitta" in errors  # above max_kitta=3000


def test_protected_routes_redirect_to_login_when_not_logged_in(client: TestClient) -> None:
    for path in ("/issues", "/apply/nic-asia-debenture", "/report"):
        response = client.get(path)
        assert response.status_code == 303 and response.headers["location"] == "/login"


def test_login_then_review_does_not_change_state_but_submit_does(client: TestClient) -> None:
    login(client)
    assert client.post("/apply/nic-asia-debenture/review", data=APPLY).status_code == 200
    assert len(state(client)["applications"]) == 5
    response = client.post("/apply/nic-asia-debenture/submit", data=APPLY)
    assert response.status_code == 303 and response.headers["location"] == "/receipt/046"
    created = state(client)["applications"]["046"]
    assert created["status"] == "submitted" and created["kitta"] == 200
    assert "०४६" in client.get("/receipt/046").text


def test_invalid_submit_creates_nothing(client: TestClient) -> None:
    login(client)
    response = client.post("/apply/nic-asia-debenture/submit", data={**APPLY, "pin": "1234"})
    assert response.status_code == 422
    assert len(state(client)["applications"]) == 5


def test_edit_kitta_and_withdraw(client: TestClient) -> None:
    login(client)
    assert client.post("/applications/042/edit", data={"kitta": "250"}).status_code == 303
    assert state(client)["applications"]["042"]["kitta"] == 250
    assert client.post("/applications/043/withdraw").status_code == 303
    apps = state(client)["applications"]
    assert apps["043"]["status"] == "withdrawn" and apps["045"]["status"] == "submitted"
    assert client.post("/applications/043/withdraw").status_code == 409
    assert client.post("/applications/041/withdraw").status_code == 409  # allotted
    assert client.post("/applications/041/edit", data={"kitta": "60"}).status_code == 409


def test_reset_restores_seed_and_clears_login(client: TestClient) -> None:
    login(client)
    client.post("/applications/043/withdraw")
    client.post("/__bench/reset", json={})
    snapshot = state(client)
    assert snapshot["applications"]["043"]["status"] == "submitted"
    assert snapshot["logged_in"] is False and snapshot["events"] == []
    assert client.get("/issues").status_code == 303


def reset(client: TestClient, variant: str | None) -> None:
    client.post("/__bench/reset", json={"variant": variant})


def test_v1_pages_have_no_difficulty_markup(client: TestClient) -> None:
    login(client)
    report = client.get("/report").text
    assert "next-page" not in report and 'id="app-045"' in report
    assert "nic-asia-debenture-2082" not in client.get("/issues").text
    bad = client.post("/apply/nic-asia-debenture/review", data={**APPLY, "kitta": "1,000"})
    assert bad.status_code == 422 and "aria-invalid" not in bad.text
    assert "auth_checks" not in state(client)


def test_session_expiry_bounces_once_then_stays_logged_in(client: TestClient) -> None:
    reset(client, "session_expiry")
    login(client)
    assert client.get("/issues").status_code == 200
    assert client.get("/apply/nic-asia-debenture").status_code == 200
    bounced = client.post("/apply/nic-asia-debenture/review", data=APPLY)
    assert bounced.status_code == 303 and bounced.headers["location"] == "/login"
    assert state(client)["logged_in"] is False
    login(client)
    assert client.post("/apply/nic-asia-debenture/review", data=APPLY).status_code == 200
    assert client.post("/apply/nic-asia-debenture/submit", data=APPLY).status_code == 303
    assert state(client)["applications"]["046"]["status"] == "submitted"


def test_similar_issues_are_listed_and_applicable(client: TestClient) -> None:
    reset(client, "similar_issues")
    login(client)
    issues = client.get("/issues").text
    for slug in ("nic-asia-debenture", "nic-asia-debenture-2082", "nic-asia-debenture-series-b"):
        assert f'id="apply-{slug}"' in issues
    series_b = {**APPLY, "kitta": "2000"}  # Series B max is 1000
    assert (
        client.post("/apply/nic-asia-debenture-series-b/review", data=series_b).status_code == 422
    )
    assert client.post("/apply/nic-asia-debenture-2082/review", data=series_b).status_code == 200


def test_paginated_report_target_only_on_page_two(client: TestClient) -> None:
    reset(client, "paginated_report")
    login(client)
    page1 = client.get("/report").text
    assert 'id="next-page"' in page1 and 'id="edit-046"' not in page1
    page2 = client.get("/report?page=2").text
    assert 'id="edit-046"' in page2 and 'id="withdraw-047"' in page2
    assert 'id="edit-041"' not in page2 and "next-page" not in page2
    assert len(state(client)["applications"]) == 7


def test_err_kitta_recover_marks_field_invalid(client: TestClient) -> None:
    reset(client, "err_kitta_recover")
    login(client)
    bad = client.post("/apply/sunrise-bank-rights/review", data={**APPLY, "kitta": "1,000"})
    assert bad.status_code == 422 and 'aria-invalid="true"' in bad.text
    ok = client.post("/apply/sunrise-bank-rights/review", data={**APPLY, "kitta": "1000"})
    assert ok.status_code == 200
