"""Playwright execution of validated, authorized plan steps (M4).

Nothing in this package validates or authorizes an action -- that already happened
in `janus.validator`. This package's job is: re-find the target by fingerprint
immediately before acting (`resolve.py`), perform the Playwright action
(`executor.py`), keep non-allowlisted network requests from ever leaving the page
(`egress.py`), and turn a task's declared approvals into granted op kinds
(`escalation.py`).
"""

from __future__ import annotations
