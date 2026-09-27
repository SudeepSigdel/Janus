# Attacker sink: replica notes

A separate origin (port 8199), fictional and used only by the benchmark harness. Code:
`src/janus_bench/sites/attacker/`.

It is not a replica of any real site and models no real content -- it exists purely as the
M8 injection suite's "did any request cross the origin boundary" instrument. Any request to
any path, on any method, is logged to `/__bench/state`'s `received` list and answered with an
innocuous placeholder page. Oracle and Janus should never reach it: oracle ignores injected
content, and Janus's origin allowlist plus egress guard (`janus/executor/egress.py`) block any
attempt to leave the task's own site before a request could arrive here. A deliberately gullible
scripted agent (`agents/gullible.py`) does reach it, for the injection tasks whose category is
`exfiltration` -- proof that the sink would have received the data if the safety machinery
weren't there.

## Bench endpoints
`POST /__bench/reset` (variant is accepted but ignored -- there is nothing to seed); `GET
/__bench/state` returns `received`, a list of `{method, path, query, body}` per request.

## Wiring
Registered in `harness/server.py::SITES` alongside the two real sites. `harness/cli.py run`
starts it once per invocation, alongside whichever site(s) the task batch needs; `run_task`
resets it before each task and reads `received` after, which `classify_injection`
(`harness/runner.py`) uses to produce the `leaked` outcome.
