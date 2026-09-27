"""Grounding: repair a planner-produced element ref or `<select>` value against the
live page (docs/PLAN.md M5: "step -> element ref").

The planner is handed exact element refs in its structural outline (invariant 1)
and is expected to use them as-is. Small local models don't always: they copy a
label instead of a ref, or guess at a `<select>` option's value the observer never
showed them (the bounded `PageSnapshot` doesn't enumerate `<option>`s -- M2's known
gap). Grounding repairs both cases through the same three-tier funnel, in order:

  1. deterministic: an exact match (Devanagari-digit- and whitespace-normalized)
     against the real ref/value or its label -- the common case, no model call.
  2. bge-m3 embedding cosine similarity, above `threshold`.
  3. an LLM call whose answer is schema-constrained to an enum of the real
     candidates, so it can never invent a target that isn't actually there.

`GroundingError` is raised if nothing resolves, or if the value needing repair
belongs to a sensitive field (invariant 3 forbids turning a `$inputs` reference on
a sensitive field into an invented literal, even one grounding is confident about).

This module is a repair pass, not a new authority: everything it produces still
passes through `validate_plan` exactly as an untouched model output would.
"""

from __future__ import annotations

from playwright.sync_api import ElementHandle, Page

from janus.executor.executor import bind_value
from janus.executor.resolve import ResolutionError, resolve_element
from janus.llm import LLMClient, LLMError
from janus.observer.extract import extract_select_options
from janus.observer.snapshot import Element, PageSnapshot, SelectOption
from janus.planner.ops import ClickStep, FillFormStep, Plan, SelectStep, Step, SubmitStep
from janus.text.nepali import normalize_text, to_ascii_digits


class GroundingError(RuntimeError):
    """A step's ref or select value could not be resolved to something on the page."""


def _norm(value: str) -> str:
    return to_ascii_digits(normalize_text(value))


def _exact_match(desired: str, candidates: dict[str, str]) -> str | None:
    """`candidates` maps key (ref or option value) -> label. An exact key match wins
    outright; otherwise a unique normalized match against either key or label."""
    if desired in candidates:
        return desired
    target = _norm(desired)
    matches = [key for key, label in candidates.items() if target in (_norm(key), _norm(label))]
    return matches[0] if len(matches) == 1 else None


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def _best_by_embedding(
    desired: str, candidates: dict[str, str], llm: LLMClient, threshold: float
) -> str | None:
    if not candidates:
        return None
    keys = list(candidates)
    try:
        target, *vectors = llm.embed([desired, *(candidates[k] for k in keys)])
    except LLMError:
        return None
    best_key: str | None = None
    best_score = threshold
    for key, vector in zip(keys, vectors, strict=True):
        score = _cosine(target, vector)
        if score >= best_score:
            best_key, best_score = key, score
    return best_key


def _best_by_llm(desired: str, candidates: dict[str, str], llm: LLMClient, kind: str) -> str | None:
    if not candidates:
        return None
    keys = list(candidates)
    schema = {
        "type": "object",
        "properties": {"choice": {"type": "string", "enum": keys}},
        "required": ["choice"],
        "additionalProperties": False,
    }
    listing = "\n".join(f"- {key}: {label}" for key, label in candidates.items())
    messages = [
        {
            "role": "system",
            "content": f"Pick the {kind} that best matches the target. Answer only from the list.",
        },
        {"role": "user", "content": f"Target: {desired!r}\nOptions:\n{listing}"},
    ]
    try:
        raw = llm.chat_json(messages, schema, schema_name="grounding")
    except LLMError:
        return None
    choice = raw.get("choice") if isinstance(raw, dict) else None
    return choice if choice in candidates else None


def _resolve(
    desired: str, candidates: dict[str, str], llm: LLMClient, threshold: float, kind: str
) -> str:
    for resolver in (
        lambda: _exact_match(desired, candidates),
        lambda: _best_by_embedding(desired, candidates, llm, threshold),
        lambda: _best_by_llm(desired, candidates, llm, kind),
    ):
        found = resolver()
        if found is not None:
            return found
    raise GroundingError(f"could not ground {kind} {desired!r} against {sorted(candidates)}")


def ground_ref(
    desired_ref: str, elements: list[Element], llm: LLMClient, *, threshold: float
) -> str:
    """Resolve a step's declared ref to a real element ref on `elements`."""
    candidates = {e.ref: e.accessible_name for e in elements}
    return _resolve(desired_ref, candidates, llm, threshold, "element")


def ground_select_value(
    step_value: str,
    inputs: dict[str, str],
    options: list[SelectOption],
    llm: LLMClient,
    *,
    sensitive: bool,
    threshold: float,
) -> str:
    """Resolve a SELECT step's value to a real `<option>` value.

    If `step_value` (resolved through `$inputs` if it's a reference) already
    matches an option exactly, it's returned unchanged -- grounding never rewrites
    a step that was already correct, so a `$inputs` reference stays a reference.
    """
    resolved = bind_value(step_value, inputs)
    candidates = {o.value: o.label for o in options}
    if _exact_match(resolved, candidates) is not None:
        return step_value
    if sensitive:
        raise GroundingError(
            "select value for a sensitive field matches no option; refusing to "
            "rewrite it to an invented literal"
        )
    return _resolve(resolved, candidates, llm, threshold, "option")


def _select_options(page: Page, element: Element) -> list[SelectOption]:
    try:
        handle: ElementHandle = resolve_element(page, element)
    except ResolutionError as exc:
        raise GroundingError(str(exc)) from exc
    return extract_select_options(handle)


def ground_plan(
    plan: Plan,
    snapshot: PageSnapshot,
    page: Page,
    inputs: dict[str, str],
    llm: LLMClient,
    *,
    sensitive_fields: frozenset[str],
    threshold: float,
) -> Plan:
    """Repair every step's ref (and a SELECT step's value) in place.

    Only reads `<select>` options live, and only for a SELECT step whose ref
    resolved to one -- never eagerly for the whole page.
    """
    grounded_steps: list[Step] = []
    for step in plan.steps:
        if isinstance(step, (ClickStep, SubmitStep)):
            ref = ground_ref(step.ref, snapshot.elements, llm, threshold=threshold)
            step = step.model_copy(update={"ref": ref})
        elif isinstance(step, FillFormStep):
            fields = [
                fv.model_copy(
                    update={"ref": ground_ref(fv.ref, snapshot.elements, llm, threshold=threshold)}
                )
                for fv in step.fields
            ]
            step = step.model_copy(update={"fields": fields})
        elif isinstance(step, SelectStep):
            ref = ground_ref(step.ref, snapshot.elements, llm, threshold=threshold)
            element = next(e for e in snapshot.elements if e.ref == ref)
            options = _select_options(page, element)
            value = ground_select_value(
                step.value,
                inputs,
                options,
                llm,
                sensitive=element.name_attr in sensitive_fields,
                threshold=threshold,
            )
            step = step.model_copy(update={"ref": ref, "value": value})
        grounded_steps.append(step)
    return plan.model_copy(update={"steps": grounded_steps})
