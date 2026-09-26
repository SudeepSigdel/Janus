# Janus

**A safety-first browser agent for the non-English web, and a benchmark that proves the gap it fills.**

Named for the two-faced Roman god of doorways and transitions — Janus looks both ways: at the untrusted page and at the user's intent, and lets neither one blindly control the other.

## The problem

Browser agents (Claude in Chrome, Comet, Gemini's auto browse, Browser Use, and others) are getting good at English-language, well-structured websites. They are measurably worse — both less capable and less secure — on:

- Non-English and bilingual sites (Nepali, Hindi, Devanagari script, mixed-script forms)
- Regional government portals, banks, and legacy systems that will never adopt emerging standards like WebMCP
- Prompt injection attacks written in the local language, which frontier agents are trained to resist mostly in English

Most agents are also architecturally exposed: they read a page and decide the next action in the same breath, so anything on that page can steer what the agent does next. Defenses are mostly probabilistic (model training, classifiers, "are you sure?" prompts), and published research shows these keep getting bypassed.

Nobody has built or measured either of these things for the South Asian web. That's the gap Janus fills.

## The idea, in one sentence

An open-source browser agent that plans before it reads untrusted page content, validates every step deterministically before acting, and is proven — via a purpose-built benchmark — to work better and resist injection better than existing agents on Nepali, Hindi, and bilingual portals, using only open-weight models.

## Two things being built (one project, two outputs)

### 1. The agent (the product)

A plan-then-execute browser automation runtime, evolved from the AegisWeb hackathon architecture:

- **Local content understanding.** A local model reads the page and extracts structured, bounded information — not raw HTML — before any plan is made.
- **Typed, task-level planning.** Instead of "click element #47," the model commits to a plan made of typed operations (e.g. `fill_application(name, citizenship_no, date)`), decided *before* it is exposed to the page's full untrusted content where possible. This is the core defense against hijacking-style prompt injection: injected text can influence values, but it can't add new actions or unlock capabilities the plan didn't already have.
- **Deterministic validation and capability policy.** Every proposed action is schema-checked, normalized, and matched against an explicit capability policy before anything touches the live page. The model is always an untrusted proposer; only code authorizes.
- **Controlled execution + verification.** A narrow executor performs only approved actions (CLICK, TYPE, SELECT, SCROLL, NAVIGATE, and typed site-specific operations where available). The page is re-observed afterward to confirm the action actually had the intended effect — not just assumed success.
- **Runs entirely on open, self-hostable models** (Ollama / vLLM / LM Studio, Qwen/Llama/Mistral-class models) — no proprietary API dependency, by design, not just by budget constraint.
- **Optional hybrid mode (future):** a stronger cloud planner can be used deliberately, but only over a privacy-abstracted view of the page (placeholders instead of raw PII), never the raw DOM — so "local-first" stays true even when a cloud model is in the loop.

**What it is not:** a general-purpose "browse anything" agent. It's scoped to the class of sites the benchmark covers — forms-heavy, bilingual, regional portals — because that's where the value and the evidence are.

### 2. The benchmark (the evidence)

A reproducible, offline benchmark that measures what nobody has measured:

- **Replica sites**, not real ones — locally hosted clones modeled on real portal patterns (share/IPO applications, bank-style transfers, tax/utility forms, municipal service requests), each replicating real difficulties: bilingual labels, Devanagari input, Bikram Sambat dates, mixed numerals, multi-page flows, messy legacy UI patterns. No real accounts, no live government or bank sites are ever touched.
- **Task suite** with deterministic, state-based success checks (not LLM-judged).
- **Security split**: injection attacks written in Nepali, Hindi, and English, testing whether an agent leaks data or takes unintended action.
- **Comparative evaluation**: open agents (Browser Use, Nanobrowser, BrowserOS) running open models, evaluated automatically; closed agents (Claude in Chrome, Comet) evaluated manually on a fixed subset as reference points; Janus evaluated on the same tasks.
- **Public leaderboard + repo**, so others can submit new agents, new sites, and new tasks over time.

The expected headline finding: existing agents' success rates drop and injection vulnerability rises on these sites, and Janus's architecture measurably closes part of that gap — particularly on hijacking-style attacks, less completely on value-poisoning attacks (this is the honest, falsifiable claim — see Open Questions).

## Why this, why now

- Consumer AI browsers (Atlas, Comet, Gemini, Claude in Chrome) have made agentic browsing a commodity feature — not a space a small team can compete in directly.
- Developer agent infrastructure (Browserbase, Firecrawl, Browser Use) is well-funded and crowded.
- Recent research (Piet et al., "Web Agents Should Adopt the Plan-Then-Execute Paradigm," UC Berkeley, 2026) argues the field's real bottleneck is the lack of typed, task-level interfaces — an infrastructure problem, not a modeling one. WebMCP is emerging to solve this, but only for sites that opt in, and regional government/bank portals won't be early adopters.
- Multilingual agent benchmarks (X-WebAgentBench, MAPS) show agents degrade sharply outside English, both in capability and security — but no one has built this for Nepali or Hindi.
- This is a gap a small, skilled, zero-budget team based in Nepal is unusually well-positioned to fill: local knowledge of the actual portals, an existing security-first architecture to build on, and open-weight models that make the whole pipeline reproducible by anyone.

## Long-term goals (not just Frogtoberfest)

1. **Open-source release** of the Janus runtime and benchmark, aimed at real reuse — clear docs, one-command setup, permissive license (MIT/Apache 2.0).
2. **A public benchmark and leaderboard** that becomes a reference point other researchers and agent builders cite and contribute to.
3. **A research paper** — realistically targeted at an ACL/NAACL/EMNLP student research workshop or an agents/multilingual-NLP workshop first, with ACL Findings or a NeurIPS Datasets & Benchmarks submission as a stretch goal after revision. arXiv preprint as soon as results are solid, to establish priority.
4. **Community growth** — good-first-issues for new replica sites, tasks, translations, and injection cases, so the benchmark grows through contributions rather than solo effort.
5. **Visibility over revenue** — this is explicitly not a commercial product. The goal is research credibility, open-source traction, and the kind of attention that helps with grad school, jobs, or being taken seriously in the agent-security community.

## Near-term milestone: Frogtoberfest 2026

Frogtoberfest (Leapfrog Technology, Oct 2026, "Build with AI," open-weight models only) is the forcing function for a first working slice:

- The Janus runtime: local open model → structured extraction → typed plan → deterministic validator → executor → verification. This is what makes it clearly pass the "AI output consumed programmatically" bar.
- ~1–3 replica Nepali/bilingual portals, ~30 tasks, a few injection cases.
- A live comparison: one open baseline agent (e.g. Browser Use) vs. Janus on the same tasks — ideally with a "baseline gets hijacked, Janus doesn't" moment for the demo.
- Full required docs: README, architecture overview, limitations, AI usage disclosure naming the exact validator function that consumes the model's plan, demo video.
- Constraint: open-weight models end-to-end, no proprietary APIs anywhere in the pipeline (this is a hard rule for the submission, not just a budget one).

Frogtoberfest output becomes the seed for the full benchmark and paper — not the finish line.

## Open questions / honest risks to track

- **Does the security architecture actually outperform others, or does it just feel like it should?** Needs measurement, split by attack type (hijacking vs. value-poisoning vs. exfiltration-via-allowed-action). Don't claim "secure" — claim specific, measured resistance.
- **Can open-weight models plan reliably enough, live, for a good demo?** This is the single biggest execution risk. Rehearse with the actual shipped model; have a backup recording.
- **Are the replica sites realistic enough to survive review?** Document how each one was modeled on a real site's structure (screenshots, structural notes), since reviewers will ask.
- **Scope creep.** One site done reliably beats three sites done shakily. Expand only after the first is solid.
- **Someone else may publish something similar first** — post to arXiv early once results exist, don't wait for the "final" version.

## Non-goals

- Not building a Chrome Web Store product or chasing users.
- Not automating real, credentialed accounts on live government/bank/broker sites — replicas only.
- Not trying to be a general-purpose "agent for any website."
- Not monetizing.
