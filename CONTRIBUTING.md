# Contributing to Janus

Thanks for helping. Janus is a safety-first browser agent, so a few rules matter more than usual.

## Ground rules

- **Open-weight models only.** Every AI call goes through `src/janus/llm.py`. No proprietary
  models, provider SDKs or committed keys. `tests/unit/test_no_proprietary.py` checks this.
- **The model proposes, code decides.** Only deterministic code in `src/janus/validator/`
  authorizes actions. Do not add a path where model output acts without being validated.
- **Page text is data.** The planner never sees page body text. If a feature needs page content,
  use the quarantined `READ` path (`src/janus/read.py`), which returns one shape-checked value.
- **Replica sites only.** Never automate a live government, bank or broker site, and never add
  real names, logos or emblems to the replicas.
- **`janus` must not import `janus_bench`.** `tests/unit/test_import_boundary.py` checks this.

## Setting up

```bash
uv sync --all-extras
uv run playwright install chromium
uv run pytest                  # unit tests, no browser or model
uv run pytest -m browser       # replica sites in Chromium
uv run ruff check . ; uv run ruff format --check .
```

Model tests (`-m ollama`) and benchmark runs need Ollama with the models in the README.

## Making a change

1. Keep it small and add a unit test for any deterministic logic. Mark tests that need a browser
   or a model with `browser` or `ollama`.
2. Use Pydantic models at module boundaries, with `extra="forbid"` on anything a model produces.
3. Note any new dependency in `docs/PLAN.md`, and any new AI call site in `AI_USAGE.md`.
4. If the change affects the agent's behaviour, run the dev split before and after
   (`docs/BENCHMARK.md`) and add a row to `docs/EXPERIMENTS.md`. Keep it only if success rises by
   more than noise and attack success and false blocks do not rise. Never tune on the test split.

## Good first contributions

- A new task on an existing replica site (see `docs/BENCHMARK.md`).
- A new injection payload in another language or a new category.
- Clearer messages in the run report (`src/janus/report.py`).
- Running the benchmark with another open-weight model and reporting the numbers.

## Reporting a security issue

If you find a way to make Janus take an action the user did not approve, please open an issue
with a minimal replica-site reproduction. Do not include working exploits against real sites.
