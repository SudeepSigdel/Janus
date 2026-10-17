"""Opt-in saved browser sessions, so a person who chose "stay logged in" is not asked to log in
(and for a one-time code) on every run.

A session is Playwright's storage state (cookies and local storage) for one origin, kept in a file
under `~/.janus/sessions/` on the person's own computer. Nothing is saved unless they ask, a
saved session is only loaded for the same origin, and `forget` deletes it.
"""

from __future__ import annotations

import re
from pathlib import Path

from playwright.sync_api import Browser, BrowserContext

from janus.validator.policy import origin_of

SESSION_DIR = Path.home() / ".janus" / "sessions"


def session_file(url: str, directory: Path = SESSION_DIR) -> Path:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", origin_of(url))
    return directory / f"{name}.json"


def open_context(
    browser: Browser, url: str, remember: bool, directory: Path = SESSION_DIR
) -> BrowserContext:
    """A new context; with `remember`, starting from the origin's saved session if there is one."""
    path = session_file(url, directory)
    state = str(path) if remember and path.is_file() else None
    return browser.new_context(service_workers="block", storage_state=state)


def save(context: BrowserContext, url: str, directory: Path = SESSION_DIR) -> Path:
    path = session_file(url, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(path))
    return path


def forget(url: str, directory: Path = SESSION_DIR) -> bool:
    path = session_file(url, directory)
    if path.is_file():
        path.unlink()
        return True
    return False
