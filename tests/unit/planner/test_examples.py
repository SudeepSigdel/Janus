"""`planner/examples.py::select_example` (docs/PLAN.md Q3): the selector must not
fire on any existing nagarpalika page, must fire on both ShareSewa candidate shapes,
and the example text itself must never leak anything about a held-out test task.

Loading `janus_bench.harness.taskspec`/`splits/v1.yaml` here (test-only, not
`src/janus`) is fine: `tests/unit/test_import_boundary.py` only forbids the
*runtime* (`src/janus`) importing `janus_bench`.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from janus.observer.snapshot import Element, Fingerprint, PageSnapshot
from janus.planner.examples import _EXAMPLE, select_example
from janus.validator.policy import Policy
from janus_bench.harness.taskspec import TaskSpec, load_tasks

GOLDEN_DIR = Path(__file__).resolve().parents[2] / "integration" / "golden"
REPO_ROOT = Path(__file__).resolve().parents[3]


def _golden_snapshot(name: str) -> PageSnapshot:
    return PageSnapshot.model_validate(json.loads((GOLDEN_DIR / name).read_text(encoding="utf-8")))


def _no_sensitive_fields() -> Policy:
    return Policy(
        allowed_origins=frozenset({"http://127.0.0.1:8101"}),
        allowed_ops=frozenset({"CLICK", "FILL_FORM", "SELECT", "DONE"}),
        max_steps=10,
    )


def test_does_not_fire_on_nagarpalika_services() -> None:
    snapshot = _golden_snapshot("nagarpalika_services.json")
    assert select_example(snapshot, _no_sensitive_fields()) is None


def test_does_not_fire_on_nagarpalika_form() -> None:
    """The one page with a combobox (ward) among the three goldens -- proves the
    login-shaped predicate isn't just "no combobox"."""
    assert select_example(_golden_snapshot("nagarpalika_form.json"), _no_sensitive_fields()) is None


def test_does_not_fire_on_nagarpalika_applications() -> None:
    assert (
        select_example(_golden_snapshot("nagarpalika_applications.json"), _no_sensitive_fields())
        is None
    )


def test_fires_on_the_real_sharesewa_login_page() -> None:
    snapshot = _golden_snapshot("sharesewa_login.json")
    assert select_example(snapshot, _no_sensitive_fields()) is _EXAMPLE


def _apply_form_snapshot() -> PageSnapshot:
    """Not a captured golden: this test is about the `policy.sensitive_fields` gate,
    not the observer's extraction, so a synthetic snapshot shaped like the real
    apply form's PIN field is enough (see `test_plan.py`'s equivalent fixture)."""
    pin = Element(
        ref="e0",
        tag="input",
        role="textbox",
        accessible_name="Transaction PIN",
        name_attr="pin",
        form_id=None,
        fingerprint=Fingerprint(
            role="textbox",
            accessible_name="Transaction PIN",
            name_attr="pin",
            form_id=None,
            tag="input",
        ),
    )
    bank = Element(
        ref="e1",
        tag="select",
        role="combobox",
        accessible_name="Bank",
        name_attr="bank",
        form_id=None,
        fingerprint=Fingerprint(
            role="combobox", accessible_name="Bank", name_attr="bank", form_id=None, tag="select"
        ),
    )
    return PageSnapshot(
        url="http://127.0.0.1:8102/apply/example-issue",
        title="Apply",
        elements=[pin, bank],
        untrusted_text=[],
    )


def test_fires_when_a_textbox_is_declared_sensitive() -> None:
    policy = Policy(
        allowed_origins=frozenset({"http://127.0.0.1:8102"}),
        allowed_ops=frozenset({"FILL_FORM", "SELECT", "DONE"}),
        max_steps=10,
        sensitive_fields=frozenset({"pin"}),
    )
    assert select_example(_apply_form_snapshot(), policy) is _EXAMPLE


def test_does_not_fire_when_no_declared_field_is_sensitive() -> None:
    """Same page, no field marked sensitive by this task's policy -- the predicate is
    policy-driven, not page-shape-driven, so a task that never marks anything
    sensitive never gets the example from this branch."""
    assert select_example(_apply_form_snapshot(), _no_sensitive_fields()) is None


def test_login_shape_requires_exactly_two_textboxes() -> None:
    """docs/PLAN.md Q3 session-start note: ShareSewa's edit-kitta page (one textbox,
    no combobox, a "Save" button that also matches no consequential keyword) must not
    accidentally match the login predicate -- only the real login page's exact
    two-textbox shape should."""
    kitta = Element(
        ref="e0",
        tag="input",
        role="textbox",
        accessible_name="Kitta",
        name_attr="kitta",
        form_id=None,
        fingerprint=Fingerprint(
            role="textbox", accessible_name="Kitta", name_attr="kitta", form_id=None, tag="input"
        ),
    )
    save = Element(
        ref="e1",
        tag="button",
        role="button",
        accessible_name="Save",
        name_attr=None,
        form_id=None,
        fingerprint=Fingerprint(
            role="button", accessible_name="Save", name_attr=None, form_id=None, tag="button"
        ),
    )
    snapshot = PageSnapshot(
        url="http://127.0.0.1:8102/applications/046/edit",
        title="Edit",
        elements=[kitta, save],
        untrusted_text=[],
    )
    assert select_example(snapshot, _no_sensitive_fields()) is None


def test_login_shape_requires_no_consequential_button() -> None:
    """A two-textbox, no-combobox page whose button reads as consequential (e.g. a
    poisoned/spoofed "Confirm" label) must not match -- the real login page's button
    never does (M7: "Login"/"लगइन" matches no keyword)."""
    fields = [
        Element(
            ref=f"e{i}",
            tag="input",
            role="textbox",
            accessible_name=name,
            name_attr=name,
            form_id=None,
            fingerprint=Fingerprint(
                role="textbox", accessible_name=name, name_attr=name, form_id=None, tag="input"
            ),
        )
        for i, name in enumerate(["a", "b"])
    ]
    confirm = Element(
        ref="e2",
        tag="button",
        role="button",
        accessible_name="Confirm",
        name_attr=None,
        form_id=None,
        fingerprint=Fingerprint(
            role="button", accessible_name="Confirm", name_attr=None, form_id=None, tag="button"
        ),
    )
    snapshot = PageSnapshot(
        url="http://127.0.0.1:8102/some-form",
        title="Form",
        elements=[*fields, confirm],
        untrusted_text=[],
    )
    assert select_example(snapshot, _no_sensitive_fields()) is None


def _example_text() -> str:
    return " ".join(m["content"] for m in _EXAMPLE.messages)


def _test_split_task_ids() -> list[str]:
    split = yaml.safe_load((REPO_ROOT / "splits" / "v1.yaml").read_text(encoding="utf-8"))
    ids: list[str] = []
    for site in split["test"].values():
        for group in site.values():
            ids.extend(group)
    return ids


def _test_tasks() -> list[TaskSpec]:
    all_tasks = {t.id: t for t in load_tasks(REPO_ROOT / "tasks")}
    return [all_tasks[task_id] for task_id in _test_split_task_ids()]


def test_example_leaks_no_test_task_id_instruction_input_value_or_target() -> None:
    text = _example_text()
    for task in _test_tasks():
        assert task.id not in text, task.id
        assert task.instruction.en not in text, task.id
        assert task.instruction.ne not in text, task.id
        for key, value in task.inputs.items():
            # $inputs.<key> references are fine (they're generic across every task);
            # the *value* itself must never appear.
            assert value not in text, (task.id, key, value)


def test_example_binds_every_value_by_reference_never_a_literal() -> None:
    """Structural guarantee behind the leakage test above: since nothing in the
    example is ever a literal task value, no future task's input value can leak
    through it either."""
    for message in _EXAMPLE.messages:
        if message["role"] != "assistant":
            continue
        plan = json.loads(message["content"])
        for step in plan["steps"]:
            if step["op"] == "FILL_FORM":
                for field in step["fields"]:
                    assert field["value"].startswith("$inputs.")
            elif step["op"] == "SELECT":
                assert step["value"].startswith("$inputs.")
