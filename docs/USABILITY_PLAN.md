# Janus: usability and test-load plan (proposal, 2026-10-10)

Status: proposal. Nothing here is built yet. Once agreed, the chosen items move into
docs/PLAN.md as milestones. The hard constraints in CLAUDE.md stay as they are.

## 1. Where things stand

- **Research side is strong.** There are 62 tasks across 2 replica sites, v2 splits, an adaptive
  injection suite, a fair baseline (B1) and an audit with 19 xfail findings. The latest numbers are
  E14 at 83% on v1 dev and E18 at 74.5% on v2 dev.
- **Product side is thin.** The only entry point is `janus run --task <yaml>`:
  - It needs a hand-written task YAML (inputs, approvals, sensitive fields).
  - It runs headless.
  - It uses only `instruction.en`, so the Nepali instruction is ignored at runtime.
  - It prints one status line. When a run blocks, the user can't see why without reading JSONL traces.
- **Backend switching is fragile.** Switching from hosted to local means editing `.env` by hand.
  `JANUS_CHAT_REASONING_EFFORT=low` is meant for gpt-oss, and it would leak into the local qwen3
  run if left set. This was set back to local on 2026-10-10: the hosted lines in `.env` are
  commented out, and `.env.*` is now gitignored.
- **The test queue is the bottleneck.** Still open: Q6/B2, CP4, R1 (rest), R2–R10 and M13. Each R
  milestone has its own Janus dev-N3 run (about 35 min), and 4 milestones need overnight
  browser_use runs. browser_use takes about 290 s per run (median), so B2 on v2 dev (141 runs) is
  about 11 h. The first attempt died of `WinError 1455`, and the harness has no resume, so a crash
  loses the whole night.
- **Repo hygiene:**
  - PLAN.md is 150 KB and CLAUDE.md says to read it every session.
  - The working tree has about 22 files of real uncommitted changes (R1 egress code, hosted chat)
    hidden among about 30 files of CRLF-only noise.
  - The README results table predates E18.
- **Deadline:** M13 (submission) is still `todo`. Frogtoberfest ends Oct 31.

## 2. Direction: real sites, not only replicas (decided 2026-10-10)

Janus should run on real sites. The benchmark stays on replicas, because that is what makes the
results reproducible and safe to publish. The runtime gets a "live mode" with these rules:

- **Visible browser.** The run is headed, so the user watches every step.
- **Human approval.** Every consequential action needs an interactive human "yes". There is no
  declared-approval auto-grant on live sites.
- **The user's own account.** Credentials are typed at runtime or read from the OS keyring, and
  never sent to the planner. CAPTCHA and OTP steps are handed to the human: the run pauses and
  resumes when they are done.
- **No live sites in tests.** No live site is ever touched by CI or by an unattended benchmark run.

This requires rewriting the CLAUDE.md hard constraint "Replica sites only". Proposed wording:
"The benchmark and CI use replica sites only. The runtime may drive live sites only in live mode
(headed, human approval per consequential action, the user's own account)."

**What breaks on real sites today, and the milestone that fixes it:**

| blocker | why it matters on real sites | fix |
|---|---|---|
| Commits are detected only as main-frame POST navigations (AUDIT S9) | Most modern portals (SPAs, Angular/React) submit with fetch/XHR. Approvals and the "committed" stop would never fire. | R4 |
| The egress guard blocks every non-allowlisted origin | Real pages load CDNs, fonts and analytics, so pages break. | Split the guard: page-initiated subresource GETs are allowed. *Agent-caused* navigations, form posts and popups stay allowlisted, because that is the exfiltration channel an injection controls. |
| No timeouts, dialog handler or settle wait (AUDIT R2–R6) | Real pages are slow, show `alert()` boxes and render late. | R7 |
| Tasks need a hand-written YAML | Real users type a goal. | U2: free-form goal. A schema-constrained call extracts `inputs` from the *user's* message, which is trusted, so invariant 1 is unaffected. |
| Outline caps (200 elements, 80-char labels); no iframes or shadow DOM | Real DOMs are bigger and nested. | The observer walks iframes and open shadow roots. Raise the caps per profile. |
| No CAPTCHA/OTP handling | These block every gov/bank login. | Human hand-off step (pause, then resume). |

**Measuring real-site capability (the research side):**

- Keep the replicas as the controlled benchmark.
- Add a generalization track on established self-hosted benchmarks of real web apps, such as a
  WebArena subset (GitLab, shopping admin and similar sites run locally). This gives numbers
  reviewers already trust.
- Add a small, manual, read-only live checklist (public lookup pages, no account), reported
  separately and never automated in CI.

## 3. Proposed product changes (U-milestones)

| id | change | why |
|---|---|---|
| U1 | **Backend profiles.** `JANUS_BACKEND=local\|hosted` (or `--backend`). Each profile carries its own model, reasoning effort and strict flag. `doctor` checks only the active profile. Local is the default. | Switching becomes one variable, with no hand-editing of `.env` and no settings leaking between profiles. |
| U2 | **Free-form `janus run`.** `janus run --url URL --goal "..." [--input k=v] [--allow-origin O] [--headed] [--lang en\|ne]`. `--task` keeps working. | Users don't have to write YAML, and the Nepali goal is actually used. Allowed origins default to the start URL's origin, which is already the code path. |
| U3 | **Interactive approvals done right.** Do this together with R5: the prompt shows the label, row, URL and form fields; a "yes" approves that one action only; "always for this run" is an explicit choice. | A human approving every consequential action is how a real user stays safe on an unscripted site. |
| U4 | **Run report.** `--report run.html` is built from the existing `trace` callback. It shows the committed plan, each validator or authorizer decision with its reason, blocked actions, and before/after screenshots. | This is the missing piece for every audience: it helps users debug, gives the demo something to show (M12 was cut), and gives the paper its qualitative figures. |
| U5 | **Site profiles.** `profiles/<name>.yaml` holds allowed origins, sensitive fields and approval targets. A task file then only needs the goal and inputs. Add a "how to add a site" page. | One place to configure a site, and a contribution path matching janus.md goal 4. |
| Later | Docker compose (replicas plus Ollama), `pip install janus` docs, a Python API (`janus.Agent`). | Useful, but not needed for submission. |

## 3b. Why Janus still loses to browser_use on some tasks, and how to beat it

**Where it loses.** Janus scores lower than browser_use (B1) on exactly 3 v1 dev tasks, both at E14
and at E18. All three are ShareSewa row actions done after logging in:

| task | browser_use | Janus | root cause (from the E18 traces) |
|---|---|---|---|
| share-06 (edit kitta) | 3/3 | 0/3 | **Crash.** The Q3 worked example is an *apply* flow, and it fires on every login-shaped page (`examples.select_example`). The model follows it into the apply form and copies `$inputs.bank`. `validate_plan` never checks that an `$inputs` key exists, so `executor.py` raises `KeyError: 'bank'`. |
| share-08, share-20 (withdraw) | 1/3, 3/3 | 0/3 | **Navigation loop.** After login, Janus clicks something on `/issues` six times, the URL never changes, and the leg budget runs out without it ever reaching "My Report". The planner sees `completed_ops` as op kinds only, so it doesn't know it already clicked that element with no effect. Nothing detects the loop. |

Other v2 failures where browser_use has no row yet:
- **nag-21:** cannot read the server's validation error (the invariant-1 cost).
- **nag-27, share-28:** hijacked through value poisoning.
- **nag-30, share-25:** false blocks in `authorize_action`.
- **share-24, share-27:** pagination and digit-script issues.

**Why browser_use wins there.** It sees the whole page and keeps a step history with element text
and results, so it can explore a site it has no flow knowledge of. Janus trades that for safety and
gets nothing back in return. The plan is to give Janus memory and site knowledge from **trusted**
sources only:

1. **Fix the two bugs** (deterministic, small):
   - `validate_plan` rejects an unknown `$inputs` key, and the error goes back to the planner as
     feedback.
   - Select the worked example by *task intent*, not page shape. Intent (apply, edit, withdraw,
     cancel, submit) is classified from the trusted instruction.
2. **Step history the planner can use:**
   - Feed it (op, target label, URL before → after, changed or unchanged) for previous steps.
     These are Janus's own actions, and the labels are the same short outline labels it already
     sees.
   - Add a deterministic loop guard: the same fingerprint clicked twice with no URL or DOM change
     is excluded from the next leg.
3. **Site memory ("workflow memory"):**
   - After a run that succeeds and verifies, store its abstract flow, for example
     `login → My Report → row[app_no] Withdraw → confirm`, as a per-origin skill keyed by intent.
   - Retrieve it on later runs.
   - The skill comes from Janus's own verified trace, not page text, so it is safe. It also
     generalizes the hand-written Q3 example and works on real sites after one supervised run.
4. **Safe reading of results:** schema-constrained extraction calls (enum outputs, so they can't
   add ops) read the confirmation and error text:
   - "did it succeed?" fixes the DONE gap;
   - "which field was rejected?" fixes nag-21 and validation-error recovery in general.
5. **Close the value-poisoning gap deterministically.** Before any SUBMIT, diff every non-empty
   form field against the plan's bindings. A pre-filled value the plan never touched is cleared or
   shown to the human. This turns the one real security loss (nag-27, share-28) into a headline
   win, because browser_use has no such check.
6. **Keep the existing edges and report them:**
   - about 3 LLM calls per task;
   - 10–20 s per run vs a 290 s median;
   - structural injection resistance;
   - deterministic Devanagari and BS-date handling.

   No other open agent has these. Report cost and latency next to success.

## 4. Cutting the test load

**The test ladder.** Each change runs the cheapest rung that can catch its failure.

| rung | what | cost | when |
|---|---|---|---|
| L0 | `uv run pytest`, ruff | < 1 min | every change |
| L1 | `uv run pytest -m browser` | minutes | when touching the observer, executor, sites or goldens |
| L2 | smoke: `splits/smoke.yaml`, Janus, N=1 | about 3 min (local) | the per-milestone gate for R3–R8 and the U-milestones |
| L3 | Janus dev-N3 (v2) | about 35 min (about 28 min after R9) | once per *batch* of hardening milestones, not once per milestone |
| L4 | browser_use, overnight | 4–11 h | once, as one resumable final run |

**Concrete changes:**

1. **Do R9 first, and add `--resume` to `janus-bench run`.** Resume skips (task, repeat) pairs
   already in `--out`. For browser_use, restart the browser per task. One crash then costs one
   task, not one night. R9's own estimate is about 17% faster runs.
2. **Batch the R3–R7 hardening milestones.** Accept each one on its xfail tests plus L2. Then run
   a single L3 under the hardening keep rule for the whole batch. If it regresses, bisect with L2.
   This saves about 4 dev-N3 runs. It does mean relaxing the CLAUDE.md "one milestone per session"
   rule to "one milestone per session; dev-N3 per batch".
3. **No per-milestone browser_use runs.** New cases from R1/R2 are proven exercisable by the
   oracle/gullible pair, which already exists for this. browser_use runs once, in a single
   resumable overnight script: B2 (v2 dev), then CP4 (v2 test), then R10 if time allows.
4. **Use the local planner for all reported numbers.** Every row from E0 to E18 is qwen3:8b on
   Ollama. Mixing in hosted gpt-oss rows would break comparability, and the Groq free tier
   (1000 req/day) can't finish a dev-N3 anyway. Keep hosted as a separate, labelled experiment
   for later.

## 5. Frogtoberfest plan (rules checked 2026-10-10 on frogtoberfest.lftechnology.com)

**Rules:**
- **Dates:** the challenge runs Oct 1–30. Week 4 is finalization and the top-5 shortlist. Demo Day
  is **Oct 30**, in person in Kathmandu valley.
- **Eligibility gate:**
  - a public repo;
  - a README, architecture overview, technology details and limitations/future work;
  - a working demo;
  - a demo video;
  - "Build with AI" (AI output drives the system);
  - an AI usage disclosure naming the file and function where model output is consumed.
- **Judged on:** innovation, technical excellence, open-source contribution and real-world impact
  for the Nepali community.
- **Open-weight only, everywhere,** including embeddings. Local Ollama satisfies this.
- **Exact submission cutoff:** not published. Ask events@lftechnology.com, and plan on the repo
  being final by **Oct 24**.

### Week A: Oct 10–15, win back the losses

1. Commit and push the current tree (R1 egress code, the backend switch, the CRLF fix).
2. Fix the two bugs:
   - unknown `$inputs` key gets rejected in `validate_plan`;
   - the worked example is chosen by task intent.
3. Add step history and the deterministic loop guard (share-08 and share-20).
4. Add the value-poisoning pre-submit diff (nag-27 and share-28).
5. Gate each fix on the L0 and L2 smoke runs. Run one overnight Janus v2 dev-N3 at the end of the
   week for the headline number.

### Week B: Oct 16–21, usable on real sites, and the demo

6. Free-form `janus run --url --goal "..." --headed`, accepting **Nepali goals**, with an
   interactive approval prompt that shows label, row and URL.
7. Minimal live mode:
   - an egress split (page subresources allowed; agent navigations and posts allowlisted);
   - timeouts and a dialog handler;
   - a pause for OTP/CAPTCHA.

   Prove it on one real site that is safe to use.
8. HTML run report: plan, validator decisions, blocked steps, screenshots. This is the demo's
   centrepiece.
9. Overnight: B2 browser_use, only if `--resume` landed. Otherwise B1 stays the comparison.

### Week C: Oct 22–24, submission package

10. Rewrite the README for judges:
    - the problem;
    - a GIF;
    - a 3-command quickstart;
    - one results table (success, ASR, wall time, LLM calls vs browser_use);
    - architecture;
    - limitations and future work;
    - models used.

    Re-verify AI_USAGE.md.
11. Demo video, about 3 min: problem → Nepali goal → Janus completes the flow → injection blocked,
    with the report shown → approval prompt → numbers.
12. Open-source signal:
    - CONTRIBUTING.md;
    - an "add a task" and "add a site" guide;
    - 5–8 good-first-issues;
    - tag `v0.1.0`.

### Oct 25–30: Demo Day prep

13. A 5-slide pitch, rehearsed 3 times on the demo laptop, **offline** (all-local is a selling
    point), with the video as backup.

**Deferred until after Oct 30** (listed under Limitations/Future in the README): R2–R10 except the
parts pulled in above, CP4, R10, WebArena, U5 site profiles, and the paper.

## 6. Open decisions

- Approve the CLAUDE.md constraint rewrite in section 2 (and the matching janus.md non-goal).
- Is it OK to drop the per-milestone browser_use runs (R1, R2) in favour of the single final run?
