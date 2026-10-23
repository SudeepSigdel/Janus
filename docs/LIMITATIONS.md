# Limitations

What Janus cannot do, where its safety story has gaps, and how far the evidence goes. Current as of
the E30 build (2026-10-18). Every number comes from a run logged in
[EXPERIMENTS.md](EXPERIMENTS.md); the history of how each gap was found and narrowed is in
[PLAN.md](PLAN.md).

## The trade-off behind most of this

The planner never sees page body text. That is why injected text has not changed what Janus does
on any benchmark we ran, and it is also why Janus is weak at tasks where the right action depends on
reading the page. Most items below are one side or the other of that choice.

## Capability

### Tasks that need reading the page to decide

"Open the email from Evy", "pick the cheapest flight", "type the value in the Color row": the
deciding information is page text, which the planner does not see.

- MiniWoB++ read tasks: 4/38. WebArena-Verified forum Hard: 4/42 (earlier build).
- `READ` asks a separate, schema-constrained model call for one short value from the page (a
  number, a word, a short phrase). It works mechanically, but the 8B reader is often wrong or finds
  nothing, so it adds little.
- `PICK` (choose an element by the text around it) chose correctly when used but did not raise
  MiniWoB success (32/125 against 36/125) and was not kept (E31, branch `dev/pick`).

### No vision, pixels or drag

Janus acts through a closed set of operations (fill, select, click, submit, navigate). Canvas
games, drag-and-drop, timing and visual puzzles are out of reach: MiniWoB coordinate tasks 1/32,
WebGames 4/49 (frontier vision models score 41-50% there).

### A small planner that is sensitive to wording

The planner is Qwen3-8B (Q4) so it runs on an 8 GB GPU. A one-token change in its input can flip a
whole task: the outline label for a link to the current page decided share-08 and share-20 (3/3 or
0/3) and, in the opposite direction, nag-23 on the test split. Read any single-task movement at
N=3 as weak evidence; aggregate moves of 3 runs or fewer are within noise.

### Where Janus still fails on its own replica sites (E30, v2 dev, 0/3 each)

- **ShareSewa row actions after login** (share-06, 07, 08, 24, 25: edit kitta or withdraw a given
  application). After logging in, the planner often goes to the issue list instead of "My Report"
  or loops on navigation; it has no memory of where a site keeps existing records. A step history
  helped two of these but broke others (E22, reverted).
- **Recovering from a server-side validation error** (nag-21): the error message is page text, so
  the planner cannot see what to fix.
- **A login with ASCII digits where the site expects its own format** (share-27).
- **Safe refusals that cost the task** (nag-30, share-16, share-25): an injected lookalike or a
  planted step is correctly refused, and the run stops instead of finding the legitimate path.

### Photo to fill still misreads names and some digits

On 20 generated documents (74 fields), three local readers (two vision models and an OCR model)
got 66% of the suggested values right, and 34% of fields came pre-filled and right. Of the 26 fields
where two readers agreed, 1 was wrong without any flag (a name). Names are the weak spot (6 of 20
suggestions right); citizenship numbers are 4 of 8 and phone numbers 4 of 6; dates, CRNs and bank
names are mostly right. The readers give no sign when they misread, so every value is shown beside a
close-up of the photo and the person must confirm it; nothing is auto-accepted. The documents are
synthetic and drawn in one Windows font, so real cards in other fonts, creases and glare are
untested. A person who cannot read the digits cannot catch a wrong one: the close-up helps, but a
spoken read-back is not built. Voice input is not built. The first OCR use downloads about 15 MB of
open model files; after that it runs offline.

### Run-level limits

- Interactive runs end after 12 pages, benchmark runs after 7.
- A goal that needs two separate consequential actions (submit two forms) needs two runs, because
  a run ends after the first approved action goes through.
- The web app runs one task at a time.
- Controls with no text, label or title (icon-only spans) are invisible to the observer, and
  separately clickable controls inside one pointer-styled container (an email's Reply and Forward)
  are merged into one control. Splitting them was built and reverted (D5, EXPERIMENTS E32): on its
  own it only moved the failure to the next icon-only control (Send). Controls that only get a
  pointer cursor on hover are not detected.

## Security

Attack success was 0 on every measured suite: WASP 0/72 (0/72 even attempted), our injection
suite 0/54 on dev and 0/9 on test, and no exfiltration leak in any run. The gaps below are what
those suites do not cover.

### A wrong value inside an approved action

Approval covers an action (submit this form on this page), not every value typed on the way.
Values the user gave are bound by reference (`$inputs.ward`), so the model cannot retype them, and
fields the run never touched are checked: optional text fields, page-chosen dropdown options and
pre-ticked boxes are listed at the approval prompt (`janus run`, `janus ui`) or cleared in
unattended runs. Not covered: a poisoned default in a required field or a radio button, and a
literal value the model writes into a non-sensitive field (allowed for fields that are not marked
sensitive).

### Values read from the page can be wrong

A `READ` value comes from page text, so a hostile page can make it wrong. It is shape-checked (a
number, a word or a short phrase; no URL, newline or `$inputs`), can only be typed into a field,
and can never go into a sensitive field, a password field or a one-time-code field. It cannot
change which operation runs.

### Approvals match controls by their visible name

A declared approval binds an action to the exact control name and a row id or URL path. A page that
gives a different control the same name and row id would still match. The lookalike-row case
(nag-30) was stopped by the path binding in 3/3 runs, and spoofed-label cases are covered by unit
tests rather than agent runs.

### Photos of documents

A photo is personal data. It is read only by local models (the vision call refuses any non-loopback
client, even when a hosted chat endpoint is configured), is re-encoded on upload so EXIF data is
dropped, is held in memory only, and is deleted when its values are used, when the person discards
it, or after 30 minutes; its values are never traced, logged or put in the report (only the keys).
Not covered: the web page is served on `127.0.0.1` without authentication, so anyone with access to
the machine's loopback can read an uploaded photo during its 30 minutes; the confirmed values are
typed into a form like any input, so a form that echoes them puts them on the page.

### Logins and one-time codes

Passwords and PINs given in the web app or at a hidden prompt never reach a model, may only be
typed into password fields, and password fields only take them. One-time-code fields only take an
answer the user gave, and answers are never logged. Limits: one replica site has a second factor,
with a fixed code; there is no handling for resending or expired codes; CAPTCHAs are not solved
(in the web app's visible browser the user can type the answer when asked); saved logins are
stored in the user's profile (`~/.janus/sessions/`) unencrypted, and only help on real sites,
because the replicas keep login state on the server.

## Evidence

### Replica sites, not real portals or users

Results on Nepali portals come from two fictional replicas written by the same author as the agent.
Janus has not been tested on a real government, bank or broker site (by policy) or with real users.
The outside benchmarks (WASP, MiniWoB++, WebGames, WebArena-Verified) exist to check results
against code we did not write.

### The test split was used once for diagnosis

The v2 test split is run only at checkpoints and read as aggregates. One test task (nag-23) was
inspected to diagnose a regression caused by an outline change; that change was then kept for its
dev gain, and nag-23 is a known test loss (33/45 against 36/45).

### Baseline comparison

The `browser_use` baseline uses the same local model. It has no approval step, so comparisons on
consequential or injected actions measure "agent with an approval gate" against "agent without
one", not model ability alone. Earlier comparisons were on the v1 split; the v2 comparison is in
[EXPERIMENTS.md](EXPERIMENTS.md) once its run completes.

### Benchmarks not run on the latest build

WebArena-Verified was last run on build 5c3822c (its evaluator runs in a separate environment).
X-WebAgentBench, which would test Hindi tasks, has no public code or data that we could find.

## Not built

A third replica site, a hybrid cloud planner over a privacy-abstracted page view, more baselines,
vLLM or LM Studio backends, encrypted session storage, and per-site workflow memory are listed under
"Later" in [PLAN.md](PLAN.md).
