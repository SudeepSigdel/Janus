"""Plan-commit: task + trusted inputs + structural outline -> a schema-constrained Plan.

Security invariant 1 (CLAUDE.md): the planner never sees page body text. It only
sees the snapshot's trusted structural outline (element ref, role, short accessible
name), the task's own instruction (developer-authored, not page content), and the
*names* of the task's `$inputs` keys -- never their values, so the model never
handles the input data it's meant to bind by reference.

If the produced Plan fails `validate_plan`, the errors (plain strings, already safe
to show the model -- they never quote page text) are fed back and the model gets
another attempt, up to `max_retries` times.

Capability monotonicity (invariant 2) is enforced *within* that retry loop, not
across pages: a caller doesn't have to pass `committed_capabilities` at all for a
plan against a page it hasn't committed anything for yet (agent.py never does --
each new page's plan starts fresh, since the planner only ever sees one page and
can't have declared a ceiling for a page it hasn't seen). But once a first attempt
on *this* page has been proposed, retries "fixing" a validator rejection are held to
that first attempt's own capability envelope, so a retry can't quietly expand scope
under the guise of a fix. A caller that already has a real cross-call ceiling (none
exists yet in this codebase) may still pass `committed_capabilities` to have every
attempt checked against it from the start.
"""

from __future__ import annotations

import json

from pydantic import ValidationError

from janus.llm import LLMClient, LLMError
from janus.observer.snapshot import PageSnapshot
from janus.planner.ops import Plan
from janus.validator.plan import ValidationResult, validate_plan
from janus.validator.policy import Capability, Policy

_SYSTEM_PROMPT = (
    "You are a browser automation planner for a safety-first browser agent. You are "
    "shown one page at a time as a structural outline: each visible element's ref, "
    "role, and a short label -- never the page's body text, and never elements from "
    "another page. Output a JSON plan of the step(s) to perform on THIS page only.\n"
    "Rules:\n"
    "- Use element refs exactly as given in the outline; never invent a ref, and "
    "never target an element that isn't listed.\n"
    "- A CLICK/SUBMIT/NAVIGATE step can change the page. Never plan a step after one "
    "of those in the same response -- the elements you'd target next don't exist "
    "yet, since they belong to whatever page comes after. Stop the plan there; "
    "you'll see the new page's outline next and can continue from it.\n"
    "- Before advancing past a page that has textbox/combobox fields matching a task "
    "input, fill in ALL of them first: one FILL_FORM step for the textboxes (never "
    "put a combobox's ref in FILL_FORM.fields) and a separate SELECT step for each "
    'combobox. Bind every value as "$inputs.<key>" -- never write the value '
    "yourself, and never leave a relevant field empty just because the page doesn't "
    "reject it.\n"
    "- Only after every relevant field on the current page is filled, click or "
    "submit to advance.\n"
    "- If finishing the task needs a page that hasn't loaded yet, output only the "
    "step(s) that get you there (plus any fields on the CURRENT page) and nothing "
    "more -- do not guess at steps for a page you haven't seen; its outline will be "
    "shown to you afterward and you can continue the task then.\n"
    "- You are told which op kinds you already completed earlier in this same task "
    "(completed_ops -- just the kind, e.g. SUBMIT, not what they touched). If the "
    "task's real-world action is in there (e.g. SUBMIT for a task about submitting "
    "something), it already happened -- prefer DONE completed over clicking around "
    "further just because this page happens to offer more links.\n"
    "- Only output a DONE step (status completed/blocked/partial) when the task is "
    "truly finished, or you are genuinely unable to proceed at all -- never as a "
    "placeholder for 'more steps needed later'.\n"
    "- Leave consequential_hint false on every step: submit/confirm/cancel/delete/"
    "pay actions are already detected automatically. Only set it true for an action "
    "with a real irreversible effect that nothing about its label would suggest."
)


def _outline(snapshot: PageSnapshot) -> list[dict[str, str | None]]:
    """The trusted structural outline the planner is allowed to see (invariant 1)."""
    return [{"ref": e.ref, "role": e.role, "label": e.accessible_name} for e in snapshot.elements]


def _plan_schema() -> dict:
    """`Plan.model_json_schema()`, with `op` forced `required` on every step type.

    Pydantic's schema generation drops a field from `required` once it has a default
    (every step's `op: Literal[...] = "..."` has one, for callers that construct
    steps in Python without repeating it -- see e.g. `tests/integration/test_executor_browser.py`).
    But `op` is also the discriminator `Plan` parses steps by, and a discriminator
    left optional in the *schema* means a constrained-decoding model can (and did,
    against the real planner model) emit a step missing it entirely, which
    `Plan.model_validate` then rejects outright. Only the schema sent to the model
    needs `op` required; `ops.py`'s Python-side default is untouched.
    """
    schema = Plan.model_json_schema()
    for definition in schema.get("$defs", {}).values():
        if "op" in definition.get("properties", {}) and "op" not in definition.get("required", []):
            definition.setdefault("required", []).append("op")
    return schema


def _user_message(
    task_id: str,
    instruction: str,
    input_keys: list[str],
    snapshot: PageSnapshot,
    completed_ops: list[str],
) -> str:
    return json.dumps(
        {
            "task_id": task_id,
            "instruction": instruction,
            "input_keys": input_keys,
            "completed_ops": completed_ops,
            "page_url": snapshot.url,
            "page_title": snapshot.title,
            "elements": _outline(snapshot),
        },
        ensure_ascii=False,
    )


class PlanningError(RuntimeError):
    """No plan passed `validate_plan` within the retry budget."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(f"planning failed after retries: {errors}")


def commit_plan(
    *,
    task_id: str,
    instruction: str,
    inputs: dict[str, str],
    snapshot: PageSnapshot,
    policy: Policy,
    llm: LLMClient,
    max_retries: int,
    completed_ops: list[str] | None = None,
    committed_capabilities: frozenset[Capability] | None = None,
) -> tuple[Plan, ValidationResult]:
    """Commit a Plan for the current `snapshot`, retrying validator rejections.

    `completed_ops` is the op-kind-only history of steps already executed earlier in
    this same task run (across prior pages) -- the sole memory the planner gets of
    its own past, since each call is otherwise a fresh conversation (deliberately: a
    growing transcript across many pages would blow the model's context budget, and
    every page's own elements/decisions are already re-validated fresh regardless).
    It carries no page content, so it doesn't relax invariant 1.

    See the module docstring for why `committed_capabilities` (invariant 2) is
    enforced across *retries of this call*, not across separate calls/pages.
    """
    schema = _plan_schema()
    messages: list[dict[str, str]] = [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {
            "role": "user",
            "content": _user_message(
                task_id, instruction, list(inputs), snapshot, completed_ops or []
            ),
        },
    ]

    errors: list[str] = []
    ceiling = committed_capabilities
    for _attempt in range(max_retries + 1):
        if errors:
            messages.append(
                {
                    "role": "user",
                    "content": "That plan was rejected for these reasons:\n"
                    + "\n".join(errors)
                    + "\nOutput a corrected plan.",
                }
            )
        try:
            raw = llm.chat_json(messages, schema, schema_name="plan")
            plan = Plan.model_validate(raw)
        except (LLMError, ValidationError) as exc:
            errors = [f"the plan JSON was invalid: {exc}"]
            continue

        result = validate_plan(plan, policy, snapshot, inputs, ceiling)
        if result.ok:
            return plan, result
        errors = result.errors
        if ceiling is None:
            # Lock in this first (rejected) attempt's own scope: a retry may fix
            # what was wrong, but may not use the fix as cover to ask for more.
            ceiling = result.capabilities

    raise PlanningError(errors)
