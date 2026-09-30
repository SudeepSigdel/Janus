# AI usage

Every AI call in this repo goes through `src/janus/llm.py::LLMClient`, which refuses
any endpoint that isn't a local loopback address (`assert_local_url`). There is no
other AI entry point, and no proprietary API or SDK anywhere in `src/`
(`tests/unit/test_no_proprietary.py` enforces this).

## Models

| Model | Tag (default) | Used for |
|---|---|---|
| Planner | `janus-planner` (from `qwen3:8b`, `models/janus-planner.Modelfile`) | plan-commit (`LLMClient.chat_json`) |
| Embeddings | `bge-m3` | grounding similarity (`LLMClient.embed`) |

`config.py::Settings.fallback_models` (`qwen2.5:7b-instruct`, `qwen3:4b`) are pulled
and checked by `janus doctor` but not used by default; see `docs/PLAN.md` M0's model
smoke test.

## Where model output enters the system, and what constrains it

Model output is **untrusted input**, never an authority (CLAUDE.md: "the model is an
untrusted proposer; only deterministic code in `janus/validator/` authorizes
actions"). Every call site below produces output that is either schema-constrained,
re-validated by deterministic code, or both, before anything downstream acts on it.

- **`planner/plan.py::commit_plan`** -- calls `LLMClient.chat_json` with the response
  constrained to `Plan.model_json_schema()` (closed op vocabulary, `extra="forbid"`,
  `planner/ops.py`). The parsed `Plan` is passed through
  `validator/plan.py::repair_roles` (docs/PLAN.md Q1 -- see below) and then to
  `validator/plan.py::validate_plan`; a rejection's plain-string errors are fed back
  to the model (up to `Settings.max_plan_retries` retries) rather than trusted. The
  planner is shown the task instruction, `$inputs` key *names* only (never values),
  and the current `PageSnapshot`'s trusted structural outline (element ref, role,
  short label) -- never `PageSnapshot.untrusted_text` (invariant 1).

- **`validator/plan.py::repair_roles`** (docs/PLAN.md Q1) -- a deterministic,
  model-free pre-pass `commit_plan` runs on every attempt's model output, before
  `validate_plan`. It only ever rewrites a FILL_FORM step's op to SELECT (or vice
  versa) when the model's own ref already names the right element but the wrong op
  for that element's role; it never changes what element, value, origin, or form
  the model chose, never invents a ref, and never widens what a locked retry
  ceiling already permits. The repaired plan is still fully re-validated by
  `validate_plan` exactly as an unrepaired one would be -- this is a correction of
  the proposer's output, not a second authority alongside it.

- **`planner/ground.py`** -- repairs a step's ref or `<select>` value that doesn't
  already match the live page, in two model-touching tiers:
  - `_best_by_embedding` calls `LLMClient.embed` (bge-m3) and picks the candidate
    with the highest cosine similarity above `Settings.grounding_similarity_threshold`;
    below threshold it falls through rather than trusting a weak match.
  - `_best_by_llm` calls `LLMClient.chat_json` with the response schema's `choice`
    field an `enum` of the actual candidate refs/option values -- the model cannot
    produce an answer that isn't already on the page, and the result is checked
    against the candidate set again before use (`choice in candidates`) regardless.
  - Whatever `ground_plan` produces is re-validated by `validate_plan` exactly as an
    ungrounded plan would be (`agent.py::run_task`); grounding is a repair pass, not
    a new authority.
  - A sensitive field's `<select>` value is never rewritten to an invented literal
    (`GroundingError` instead) -- see `ground_select_value`.

- **`validator/plan.py::validate_plan`** and **`validator/action.py::authorize_action`**
  -- the deterministic gates every model-produced `Plan`/`Step` above must pass
  before `executor/executor.py::execute_step` (or `agent.py`'s escalation path) ever
  touches a live page. `authorize_action` (docs/PLAN.md Q2) checks a consequential
  step's op kind, the model-chosen target's accessible name, and its `row_key`/page
  URL against the run's remaining declared `janus.policy.ApprovalTarget`s (or a live
  human grant) -- the model picks which element to act on; it never gets to invent
  what that action is authorized to do.

No other module calls `LLMClient`.

See `docs/ARCHITECTURE.md` for how these call sites fit into the full observe -> plan -> ground ->
validate -> authorize -> execute -> verify loop, and `docs/LIMITATIONS.md` for where model output
still slips past every gate above (value poisoning inside an already-approved field fill).
