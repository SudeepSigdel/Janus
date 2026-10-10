"""Property-based tests (hypothesis) for deterministic security/text logic (docs/AUDIT.md §3).

BS<->AD round-trips, `normalize_row_key`'s closed character class, and `validate_plan`'s
rejection of out-of-policy plans over randomly generated plans and snapshots.
"""

from __future__ import annotations

import unicodedata
from datetime import date, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from janus.observer.snapshot import Element, Fingerprint, PageSnapshot
from janus.planner.ops import (
    ClickStep,
    DoneStep,
    ExtractStep,
    FieldValue,
    FillFormStep,
    NavigateStep,
    Plan,
    SelectStep,
    SubmitStep,
)
from janus.text.nepali import (
    _MAX_BS_YEAR,
    _MONTH_LENGTHS,
    _REFERENCE_AD,
    ad_to_bs,
    bs_to_ad,
    normalize_row_key,
    to_ne_digits,
)
from janus.validator.plan import repair_roles, validate_plan
from janus.validator.policy import Policy, capabilities_of

# --- BS <-> AD --------------------------------------------------------------------------------

_LAST_AD = bs_to_ad(_MAX_BS_YEAR, 12, _MONTH_LENGTHS[_MAX_BS_YEAR][11])
ad_dates = st.dates(min_value=_REFERENCE_AD, max_value=_LAST_AD)


@st.composite
def bs_dates(draw: st.DrawFn) -> tuple[int, int, int]:
    year = draw(st.sampled_from(sorted(_MONTH_LENGTHS)))
    month = draw(st.integers(1, 12))
    day = draw(st.integers(1, _MONTH_LENGTHS[year][month - 1]))
    return year, month, day


@given(ad_dates)
def test_ad_bs_ad_round_trip(d: date) -> None:
    assert bs_to_ad(*ad_to_bs(d)) == d


@given(bs_dates())
def test_bs_ad_bs_round_trip(bs: tuple[int, int, int]) -> None:
    assert ad_to_bs(bs_to_ad(*bs)) == bs


@given(st.dates(min_value=_REFERENCE_AD, max_value=_LAST_AD - timedelta(days=1)))
def test_consecutive_ad_days_are_consecutive_bs_days(d: date) -> None:
    y, m, dd = ad_to_bs(d)
    ny, nm, nd = ad_to_bs(d + timedelta(days=1))
    if dd < _MONTH_LENGTHS[y][m - 1]:
        assert (ny, nm, nd) == (y, m, dd + 1)
    elif m < 12:
        assert (ny, nm, nd) == (y, m + 1, 1)
    else:
        assert (ny, nm, nd) == (y + 1, 1, 1)


# --- normalize_row_key -------------------------------------------------------------------------

_ID_ALPHABET = "0123456789०१२३४५६७८९-"


@given(st.text())
def test_row_key_output_is_ascii_digits_and_hyphens_or_none(text: str) -> None:
    key = normalize_row_key(text)
    assert key is None or (1 <= len(key) <= 12 and set(key) <= set("0123456789-"))


@given(st.text())
def test_row_key_never_admits_a_letter_of_any_script(text: str) -> None:
    if any(unicodedata.category(ch).startswith(("L", "M")) for ch in text):
        assert normalize_row_key(text) is None


@given(st.text())
def test_row_key_is_idempotent(text: str) -> None:
    key = normalize_row_key(text)
    assert normalize_row_key(key) == key


@given(st.text(alphabet="0123456789-", min_size=1, max_size=12))
def test_row_key_devanagari_and_ascii_digits_normalize_alike(text: str) -> None:
    # By design (P5). This is also why two rows "045" and "०४५" collide: AUDIT.md S10.
    assert normalize_row_key(to_ne_digits(text)) == normalize_row_key(text) == text


@given(st.text(alphabet=_ID_ALPHABET, min_size=13, max_size=40))
def test_row_key_rejects_over_twelve_characters(text: str) -> None:
    assert normalize_row_key(text) is None


# --- validate_plan -----------------------------------------------------------------------------

ORIGIN = "http://127.0.0.1:8101"
ROLES = ["textbox", "combobox", "button", "link", "checkbox"]
NAMES = ["Submit", "Save", "Cancel", "Next", "Name", "Phone", "पेश गर्नुहोस्", "रद्द", "Help"]
NAME_ATTRS = [None, "phone", "pin", "ward", "name_ne"]
SENSITIVE = frozenset({"pin"})


@st.composite
def snapshots(draw: st.DrawFn) -> PageSnapshot:
    n = draw(st.integers(0, 6))
    elements = []
    for i in range(n):
        role = draw(st.sampled_from(ROLES))
        name = draw(st.sampled_from(NAMES))
        attr = draw(st.sampled_from(NAME_ATTRS))
        fp = Fingerprint(role=role, accessible_name=name, name_attr=attr, form_id=None, tag="x")
        elements.append(
            Element(
                ref=f"e{i}",
                tag="x",
                role=role,
                accessible_name=name,
                name_attr=attr,
                form_id=None,
                fingerprint=fp,
            )
        )
    return PageSnapshot(url=f"{ORIGIN}/p", title="t", elements=elements, untrusted_text=[])


refs = st.sampled_from(["e0", "e1", "e2", "e3", "e4", "e5", "e9", "bogus"])
values = st.sampled_from(["$inputs.pin", "$inputs.phone", "1234", "literal", ""])
hints = st.booleans()
steps = st.one_of(
    st.builds(
        NavigateStep,
        url=st.sampled_from([f"{ORIGIN}/x", "http://evil.test/x"]),
        consequential_hint=hints,
    ),
    st.builds(
        FillFormStep,
        fields=st.lists(st.builds(FieldValue, ref=refs, value=values), min_size=1, max_size=3),
        consequential_hint=hints,
    ),
    st.builds(SelectStep, ref=refs, value=values, consequential_hint=hints),
    st.builds(ClickStep, ref=refs, consequential_hint=hints),
    st.builds(SubmitStep, ref=refs, consequential_hint=hints),
    st.builds(ExtractStep, field=refs, consequential_hint=hints),
    st.builds(
        DoneStep,
        status=st.sampled_from(["completed", "partial", "blocked"]),
        consequential_hint=hints,
    ),
)
plans = st.builds(Plan, task_id=st.just("t"), steps=st.lists(steps, max_size=6))
op_sets = st.frozensets(
    st.sampled_from(["NAVIGATE", "FILL_FORM", "SELECT", "CLICK", "SUBMIT", "EXTRACT", "DONE"]),
    min_size=1,
)


def _policy(ops: frozenset, max_steps: int) -> Policy:
    return Policy(
        allowed_origins=frozenset({ORIGIN}),
        allowed_ops=ops,
        max_steps=max_steps,
        sensitive_fields=SENSITIVE,
    )


def _targets(step) -> list[str]:
    if isinstance(step, FillFormStep):
        return [f.ref for f in step.fields]
    return [step.ref] if isinstance(step, (SelectStep, ClickStep, SubmitStep)) else []


@settings(max_examples=400)
@given(plans, snapshots(), op_sets, st.integers(1, 6))
def test_out_of_policy_plans_are_always_rejected(plan, snap, ops, max_steps) -> None:
    result = validate_plan(plan, _policy(ops, max_steps), snap, {})
    by_ref = {e.ref: e for e in snap.elements}
    violations = []
    if len(plan.steps) > max_steps:
        violations.append("max_steps")
    for step in plan.steps:
        if step.op not in ops:
            violations.append("op")
        if isinstance(step, NavigateStep) and not step.url.startswith(ORIGIN):
            violations.append("origin")
        for ref in _targets(step):
            el = by_ref.get(ref)
            if el is None:
                violations.append("unknown ref")
            elif isinstance(step, FillFormStep) and el.role != "textbox":
                violations.append("role")
            elif isinstance(step, SelectStep) and el.role != "combobox":
                violations.append("role")
        pairs = (
            [(f.ref, f.value) for f in step.fields]
            if isinstance(step, FillFormStep)
            else [(step.ref, step.value)]
            if isinstance(step, SelectStep)
            else []
        )
        for ref, value in pairs:
            el = by_ref.get(ref)
            if el is not None and el.name_attr in SENSITIVE and not value.startswith("$inputs."):
                violations.append("sensitive literal")
    if violations:
        assert not result.ok, violations


@settings(max_examples=400)
@given(plans, snapshots())
def test_consequential_is_never_downgraded(plan, snap) -> None:
    result = validate_plan(
        plan,
        _policy(
            frozenset(["NAVIGATE", "FILL_FORM", "SELECT", "CLICK", "SUBMIT", "EXTRACT", "DONE"]), 20
        ),
        snap,
        {},
    )
    # Only accepted plans' decisions are ever used. (A SUBMIT on an *unknown* ref is decided
    # non-consequential, but the plan is rejected for the unknown ref: AUDIT.md T1, harmless.)
    if not result.ok:
        return
    for step, decision in zip(plan.steps, result.decisions, strict=True):
        if step.consequential_hint or isinstance(step, SubmitStep):
            assert decision.consequential


@settings(max_examples=400)
@given(plans, snapshots(), op_sets)
def test_replan_outside_the_ceiling_is_rejected(plan, snap, ops) -> None:
    other = Plan(task_id="t", steps=[ClickStep(ref="e0")])
    ceiling = capabilities_of(other, snap)
    result = validate_plan(plan, _policy(ops, 20), snap, {}, ceiling)
    if not capabilities_of(plan, snap) <= ceiling:
        assert not result.ok


@settings(max_examples=400)
@given(plans, snapshots(), op_sets)
def test_repair_never_changes_refs_or_values_or_escapes_the_ceiling(plan, snap, ops) -> None:
    ceiling = capabilities_of(plan, snap) if plan.steps else None
    repaired, _ = repair_roles(plan, snap, _policy(ops, 20), ceiling)

    def bindings(p: Plan) -> list[tuple[str, str]]:
        out = []
        for s in p.steps:
            if isinstance(s, FillFormStep):
                out += [(f.ref, f.value) for f in s.fields]
            elif isinstance(s, SelectStep):
                out.append((s.ref, s.value))
        return sorted(out)

    assert bindings(repaired) == bindings(plan)
    if ceiling is not None and repaired is not plan:
        assert capabilities_of(repaired, snap) <= ceiling | capabilities_of(plan, snap)
