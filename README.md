# Janus

Safety-first browser agent for Nepali/Hindi/bilingual portals, plus an offline benchmark that
measures it against an open baseline on fictional replica sites. See [janus.md](janus.md) for the
project vision and [docs/PLAN.md](docs/PLAN.md) for milestone status.

Janus plans before it reads untrusted page content, validates every step with deterministic code
(never the model) before acting, and re-verifies the page after acting. It runs entirely on local,
open-weight models through Ollama -- no proprietary API or SDK anywhere in the runtime
(`tests/unit/test_no_proprietary.py` enforces this).

## Setup

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/), and a local
[Ollama](https://ollama.com) install.

```bash
uv sync --all-extras
uv run playwright install chromium
ollama pull qwen3:8b
ollama pull qwen2.5:7b-instruct
ollama pull qwen3:4b
ollama pull bge-m3
ollama create janus-planner -f models/janus-planner.Modelfile
```

Check everything is reachable:

```bash
uv run janus doctor
```

## Quickstart: run one task

Janus drives a real (local, fictional) site, so start one of the replica sites first, then point
`janus run` at a task file:

```bash
uv run janus-bench serve --site nagarpalika
```

In a second terminal:

```bash
uv run janus run --task tasks/nagarpalika/t01.yaml
```

This runs the full loop (observe -> plan -> ground -> validate -> authorize -> execute -> verify)
against the live replica and prints a status line (`completed`/`partial`/`blocked`) plus step and
replan counts. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for what each stage does and why.

## Benchmark

```bash
uv run janus-bench run --agent <null|oracle|browser_use|janus> --tasks tasks/<site> --repeats N
uv run janus-bench report --tasks tasks --records <results.jsonl> [--records <more.jsonl> ...]
```

`null` (does nothing) and `oracle` (scripted Playwright, no model) are ground truth for task
correctness; `browser_use` is the open-source baseline agent; `janus` is this project's own agent.
All results are state-based (`/__bench/state`), never LLM-judged.

## Results (M10 full evaluation, 41 tasks, both sites)

Full tables, injection-category and difficulty-tag breakdowns, and the task appendix are in
[docs/results.md](docs/results.md). Headline:

| agent | overall | nagarpalika (20 tasks) | sharesewa (21 tasks) | mean wall time |
| --- | --- | --- | --- | ---: |
| browser_use (baseline) | 21/41 (51%) | 11/20 (55%) | 10/21 (48%) | 283.9s |
| janus | 40/123 (33%, N=3) | 34/60 (57%) | 6/63 (9.5%) | 11.9s |

Read the per-site split, not the blended overall number: Janus matches or beats the baseline on
nagarpalika and is ~24x faster, but collapses on ShareSewa's longer, PIN-gated flow -- a real,
unresolved capability gap (see [docs/LIMITATIONS.md](docs/LIMITATIONS.md)), not noise.

**Safety:** zero exfiltration leaks for either agent across all 16 exfiltration-case runs.
Injection resistance is not uniform across attack types or sites for either agent -- see
[docs/LIMITATIONS.md](docs/LIMITATIONS.md) for the one clear Janus-specific finding
(value-poisoning on ShareSewa's bank field, hijacked 3/3).

## Repo layout

- `src/janus/` -- the runtime (observer, planner, validator, executor, verifier, agent loop). Never
  imports `janus_bench` (test-enforced).
- `src/janus_bench/` -- the benchmark: replica sites (`sites/`), harness (`harness/`), agents
  (`agents/`).
- `tasks/<site>/` -- task YAML files with state-based success checks and injection variants.
- `docs/` -- [PLAN.md](docs/PLAN.md) (status), [ARCHITECTURE.md](docs/ARCHITECTURE.md),
  [LIMITATIONS.md](docs/LIMITATIONS.md), [results.md](docs/results.md), [pilot-report.md](docs/pilot-report.md).
- `AI_USAGE.md` -- every AI call site and exactly what constrains its output.

## Tests

```bash
uv run pytest                  # unit only, fast, no browser/model
uv run pytest -m browser       # Playwright against local replica sites
uv run pytest -m ollama        # needs local Ollama with models pulled
uv run ruff check . ; uv run ruff format --check .
```

## License

Apache 2.0. Fictional replica sites only -- Janus never automates a real government, bank, or
broker site.
