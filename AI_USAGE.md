# AI usage

Every AI call in this repo goes through `src/janus/llm.py::LLMClient`. Embeddings and
local chat require a loopback Ollama (`assert_local_url`). Chat may optionally go to a hosted
OpenAI-compatible endpoint (`JANUS_CHAT_BASE_URL`/`_MODEL`/`_API_KEY`), but only for
open-weight model families (`assert_open_weight`); the key comes from the environment. There is no
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

## Model record: exact models, licenses, hosting

| Model | Where it runs | License (check the model card for the exact file you pull) | Used for |
|---|---|---|---|
| `qwen3:8b`, Q4_K_M via Ollama, wrapped as `janus-planner` (`num_ctx 8192`, temperature 0, thinking off) | local Ollama on loopback | Apache 2.0 | planner, quarantined reads, goal-input extraction |
| `bge-m3` | local Ollama on loopback | MIT | grounding similarity |
| `qwen2.5:7b-instruct`, `qwen3:4b` | local Ollama | Apache 2.0 | fallbacks and the M0 smoke test only |
| `openai/gpt-oss-20b`, `qwen/qwen3-32b` | optional hosted OpenAI-compatible endpoint (Groq free tier) if `JANUS_CHAT_*` is set | Apache 2.0 | used once for a smoke run on 2026-10-09; not part of any reported result |

All reported benchmark numbers come from the local `janus-planner` (qwen3:8b). No proprietary model
is called anywhere in `src/` (`tests/unit/test_no_proprietary.py`). The optional `baseline` extra
installs `browser-use` (MIT), which pulls in the `openai` Python client package as a dependency; it
is pointed only at the local Ollama and is used for the comparison agent, not by Janus itself.

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

- **`read.py::quarantined_read`** (the `READ` op, off unless a caller passes
  `allowed_ops_for(..., allow_read=True)`: `janus run --goal` and the MiniWoB adapter) -- calls
  `LLMClient.chat_json` over the current page's text (untrusted) with the response constrained to
  `{answer: string | null}`. The planner supplies only a name, a question and a kind; it never
  sees the page text. The answer passes a deterministic shape check (`check_shape`: a number, one
  word, or a short phrase with no URL, newline or `$inputs`) and is stored as the input
  `page_<name>`. `validate_plan` forbids binding a `page_*` value to a sensitive field, a READ
  must be the only step in its plan, and the value is only ever typed into a field: it cannot
  become an operation, ref or URL. The remaining risk is a poisoned typed value, listed in
  `docs/LIMITATIONS.md`.

- **`goal.py::extract_inputs`** (docs/PLAN.md B0, goal-only runs) -- calls
  `LLMClient.chat_json` on the *user's goal text* only (trusted, never page content), with the
  response constrained to `{inputs: [{key, value}]}` (`extra="forbid"`). Deterministic checks
  then drop any key that isn't short snake_case, any duplicate, and any value that does not occur
  verbatim in the goal, so the model can name values the user gave but never invent one. The
  result is used as the run's `inputs`, bound by reference (`$inputs.<key>`) exactly like
  task-YAML inputs, and every plan using them still passes `validate_plan`.

- **`goal.py::extract_answer`** (B0, the quarantined read) -- calls `LLMClient.chat_json` on
  the final page's text (untrusted) with the response constrained to `{answer: string|null}`.
  The answer is printed to the user or returned to the benchmark harness and **never** reaches
  the planner, a gate, or the executor, so page text still cannot add or change an operation
  (invariant 1).

- **`goal.py::classify_task`** (B3, benchmarks) -- `LLMClient.chat_json` on the *goal text*
  only, constrained to an enum (`RETRIEVE`/`MUTATE`/`NAVIGATE`); anything else becomes `None`.
  It only labels the benchmark's structured response (`janus_bench/external/webarena.py`); no
  gate, planner or executor reads it.

- **`goal.py::extract_data`** (B3, the quarantined structured read) -- like `extract_answer`,
  but the response is constrained to the task's own JSON schema for its answer. The result is
  written to the benchmark's `agent_response.json` and never reaches the planner or a gate.

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
