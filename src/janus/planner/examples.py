"""Q3 (docs/PLAN.md): one conditional worked example, shown only on legs whose page
matches a ShareSewa-shaped form.

E7 (docs/EXPERIMENTS.md) found that a hand-authored "login -> list -> form -> review
-> submit" example, sent on *every* leg, took all six dev ShareSewa apply tasks from
the long-standing capability collapse to 3/3 -- but the same commit also sent a
second, unrelated example on every leg, which regressed nagarpalika (a combobox role
mismatch set the retry ceiling, P0's diagnosed false-block chain) and blew the token
budget. Q1's `validator/plan.py::repair_roles` already closes the role-mismatch
mechanism; this module closes the token/regression mechanism instead, by sending the
one example that actually helped only on the pages shaped like the ones it teaches.

`select_example` reads only trusted structure that already exists on `PageSnapshot`
and `Policy` -- element roles, `name_attr`, per-role counts, and
`policy.sensitive_fields` -- never `untrusted_text` or the URL, so it adds no new
observer field and never lets page content choose what the model is shown (invariant
1 still holds: this only changes which *developer-authored* text precedes the
page's own outline).

Security note (Q3): a hostile page could still shape its trusted structure (element
roles/counts) to look login- or PIN-form-shaped and trigger the example on a page it
doesn't belong to. The only effect is extra planning-guidance text in the prompt --
the example grants no capability, binds no value, and is never treated as an
instruction to execute; `validate_plan`/`authorize_action` are unchanged and don't
know the example was shown. See docs/ARCHITECTURE.md.
"""

from __future__ import annotations

from dataclasses import dataclass

from janus.observer.snapshot import PageSnapshot
from janus.validator.consequential import matches_consequential_keyword
from janus.validator.policy import Policy


@dataclass(frozen=True)
class Example:
    """A worked outline -> plan demonstration, as a sequence of chat turns."""

    messages: tuple[dict[str, str], ...]


# Fictional flow: login, an issue list, a multi-field apply form (a combobox plus
# several textboxes, one of them sensitive), and a review page whose only action is
# to submit. Field names (`boid`, `password`, `bank`, `kitta`, `crn`, `pin`) and
# labels match the real ShareSewa templates (static UI chrome, not task data); the
# issue name is a placeholder that doesn't collide with any seeded issue. Every bound
# value is `$inputs.<key>` -- never a literal -- so no task's real input values ever
# appear here regardless of which task is running.
_EXAMPLE = Example(
    messages=(
        {
            "role": "user",
            "content": (
                '{"task_id": "example", "instruction": "Example flow for reference '
                'only -- not the current task.", "input_keys": ["boid", "password", '
                '"bank", "kitta", "crn", "pin"], "completed_ops": [], "page_url": '
                '"https://example.invalid/login", "page_title": "लगइन / Login", '
                '"elements": ['
                '{"ref": "e0", "role": "textbox", "label": "वीओआईडी / BOID"}, '
                '{"ref": "e1", "role": "textbox", "label": "पासवर्ड / Password"}, '
                '{"ref": "e2", "role": "button", "label": "लगइन / Login"}]}'
            ),
        },
        {
            "role": "assistant",
            "content": (
                '{"task_id": "example", "steps": ['
                '{"op": "FILL_FORM", "fields": ['
                '{"ref": "e0", "value": "$inputs.boid"}, '
                '{"ref": "e1", "value": "$inputs.password"}]}, '
                '{"op": "CLICK", "ref": "e2"}]}'
            ),
        },
        {
            "role": "user",
            "content": (
                '{"task_id": "example", "instruction": "Example flow for reference '
                'only -- not the current task.", "input_keys": ["boid", "password", '
                '"bank", "kitta", "crn", "pin"], "completed_ops": ["CLICK"], '
                '"page_url": "https://example.invalid/issues", "page_title": '
                '"खुल्ला निष्कासन / Open issues", "elements": ['
                '{"ref": "e0", "role": "link", '
                '"label": "आवेदन / Apply -- उदाहरण जलविद्युत् आईपीओ / Example Hydro IPO"}]}'
            ),
        },
        {
            "role": "assistant",
            "content": '{"task_id": "example", "steps": [{"op": "CLICK", "ref": "e0"}]}',
        },
        {
            "role": "user",
            "content": (
                '{"task_id": "example", "instruction": "Example flow for reference '
                'only -- not the current task.", "input_keys": ["boid", "password", '
                '"bank", "kitta", "crn", "pin"], "completed_ops": ["CLICK", "CLICK"], '
                '"page_url": "https://example.invalid/apply/example-hydro-ipo", '
                '"page_title": "आवेदन / Apply", "elements": ['
                '{"ref": "e0", "role": "combobox", "label": "बैंक / Bank"}, '
                '{"ref": "e1", "role": "textbox", "label": "कित्ता संख्या / Kitta"}, '
                '{"ref": "e2", "role": "textbox", "label": "सीआरएन / CRN"}, '
                '{"ref": "e3", "role": "textbox", "label": "ट्रान्जेक्सन पिन / Transaction PIN"}, '
                '{"ref": "e4", "role": "button", "label": "अर्को / Next: review"}]}'
            ),
        },
        {
            "role": "assistant",
            "content": (
                '{"task_id": "example", "steps": ['
                '{"op": "FILL_FORM", "fields": ['
                '{"ref": "e1", "value": "$inputs.kitta"}, '
                '{"ref": "e2", "value": "$inputs.crn"}, '
                '{"ref": "e3", "value": "$inputs.pin"}]}, '
                '{"op": "SELECT", "ref": "e0", "value": "$inputs.bank"}, '
                '{"op": "CLICK", "ref": "e4"}]}'
            ),
        },
        {
            "role": "user",
            "content": (
                '{"task_id": "example", "instruction": "Example flow for reference '
                'only -- not the current task.", "input_keys": ["boid", "password", '
                '"bank", "kitta", "crn", "pin"], "completed_ops": ["CLICK", "CLICK", '
                '"FILL_FORM", "SELECT", "CLICK"], "page_url": '
                '"https://example.invalid/apply/example-hydro-ipo/review", '
                '"page_title": "समीक्षा / Review", "elements": ['
                '{"ref": "e0", "role": "button", "label": "आवेदन पेश गर्नुहोस् / Submit application"}]}'
            ),
        },
        {
            "role": "assistant",
            "content": '{"task_id": "example", "steps": [{"op": "SUBMIT", "ref": "e0"}]}',
        },
    )
)


def _role_counts(snapshot: PageSnapshot) -> tuple[list, list]:
    textboxes = [e for e in snapshot.elements if e.role == "textbox"]
    comboboxes = [e for e in snapshot.elements if e.role == "combobox"]
    return textboxes, comboboxes


def _is_login_shaped(snapshot: PageSnapshot) -> bool:
    """Exactly two textboxes, no combobox, and no button whose accessible name
    matches the deterministic consequential-keyword list -- the real ShareSewa login
    page's shape (`boid` + `password`, a "Login"/"लगइन" button that matches no
    keyword) and, checked against the goldens, no other current page's."""
    textboxes, comboboxes = _role_counts(snapshot)
    if len(textboxes) != 2 or comboboxes:
        return False
    buttons = [e for e in snapshot.elements if e.role == "button"]
    return not any(matches_consequential_keyword(e.accessible_name) for e in buttons)


def _has_sensitive_textbox(snapshot: PageSnapshot, policy: Policy) -> bool:
    """A textbox whose `name_attr` is one of the task's declared sensitive fields --
    the real ShareSewa apply form's `pin` field, gated by the task's own policy
    rather than by anything page-specific, so it never fires on a page whose fields
    happen to look similar but aren't marked sensitive for this task."""
    textboxes, _ = _role_counts(snapshot)
    return any(
        e.name_attr is not None and e.name_attr in policy.sensitive_fields for e in textboxes
    )


def select_example(snapshot: PageSnapshot, policy: Policy) -> Example | None:
    """The one worked example to show on this leg, or None.

    Fires on a page shaped like ShareSewa's login form, or holding a textbox the
    task's policy marks sensitive (docs/PLAN.md Q3's two candidate shapes). Reads
    only element roles/`name_attr` and `policy.sensitive_fields` -- trusted
    structure, never `untrusted_text` or the URL (invariant 1).
    """
    if _is_login_shaped(snapshot):
        return _EXAMPLE
    if _has_sensitive_textbox(snapshot, policy):
        return _EXAMPLE
    return None
