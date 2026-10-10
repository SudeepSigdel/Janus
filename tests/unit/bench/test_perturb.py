"""Surface perturbations (docs/AUDIT.md §4): pure rewriting, task adaptation, and the site
middleware being a byte-for-byte no-op unless a perturbation is set."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from janus_bench.harness.perturbation import perturb_task
from janus_bench.harness.taskspec import load_task
from janus_bench.sites.common.perturb import PERTURBATIONS, rewrite_html, unrename_form
from janus_bench.sites.nagarpalika.app import create_app as create_nagarpalika
from janus_bench.sites.sharesewa.app import create_app as create_sharesewa

P1 = PERTURBATIONS["p1"]
TASKS = Path(__file__).resolve().parents[3] / "tasks"

FORM = """<nav><a href="/services">सेवाहरू / Services</a></nav><main>
<form method="post" action="/apply/x/review">
  <label for="name_ne">पूरा नाम (देवनागरी) / Full name (Devanagari)</label>
  <input id="name_ne" name="name_ne" value="">

  <label for="phone">मोबाइल नं. / Mobile number</label>
  <input id="phone" name="phone" value="">

  <button type="submit" id="btn-review">अर्को / Next: review</button>
</form></main>"""


def test_labels_are_reworded_and_ids_hrefs_kept() -> None:
    out = rewrite_html(FORM, P1)
    assert "Contact phone / सम्पर्क फोन" in out
    assert "Continue to review / समीक्षामा जानुहोस्" in out
    assert "Mobile number" not in out
    assert 'id="phone"' in out and 'id="btn-review"' in out and 'href="/services"' in out


def test_control_names_are_renamed_and_mapped_back() -> None:
    out = rewrite_html(FORM, P1)
    assert 'name="contact_mobile"' in out and 'name="phone"' not in out
    assert unrename_form(b"contact_mobile=98&applicant_name=x&other=1", P1) == (
        b"phone=98&name_ne=x&other=1"
    )


def test_fields_are_reordered_and_nav_moves_below_main() -> None:
    out = rewrite_html(FORM, P1)
    assert out.index('id="phone"') < out.index('id="name_ne"') < out.index('id="btn-review"')
    assert out.index("</main>") < out.index("<nav>")
    assert 'id="pert-help"' in out


def test_perturb_task_rewrites_approvals_and_sensitive_fields_only() -> None:
    task = load_task(TASKS / "sharesewa" / "share-01.yaml")
    perturbed = perturb_task(task, "p1")
    assert perturbed.sensitive_field_names == ["sec_pin"]
    names = [n for a in perturbed.approvals for n in a.names]
    assert "Confirm and submit / पुष्टि गरी पेश गर्नुहोस्" in names
    assert perturbed.instruction == task.instruction
    assert perturbed.inputs == task.inputs
    assert perturbed.success == task.success
    assert perturb_task(task, None) is task


@pytest.mark.parametrize(
    ("create_app", "path"),
    [(create_nagarpalika, "/apply/residence-recommendation"), (create_sharesewa, "/login")],
)
def test_middleware_is_a_no_op_without_a_perturbation(create_app, path: str) -> None:
    client = TestClient(create_app())
    before = client.get(path).text
    client.post("/__bench/reset", json={"variant": None})
    assert client.get(path).text == before
    assert "perturbation" not in client.get("/__bench/state").json()
    client.post("/__bench/reset", json={"variant": None, "perturbation": "p1"})
    assert client.get(path).text != before


def test_perturbed_nagarpalika_form_still_submits() -> None:
    client = TestClient(create_nagarpalika())
    client.post("/__bench/reset", json={"variant": None, "perturbation": "p1"})
    form = {
        "applicant_name": "राम बहादुर",
        "birth_date": "2050-01-01",
        "ctz_number": "१२-३४",
        "ward_no": "4",
        "contact_mobile": "9841234567",
    }
    resp = client.post("/apply/residence-recommendation/submit", data=form, follow_redirects=False)
    assert resp.status_code == 303
    apps = client.get("/__bench/state").json()["applications"]
    assert any(a.get("phone") == "9841234567" and a.get("ward") == 4 for a in apps.values())
