"""Pure-logic checks for the egress allowlist and denied paths (AUDIT.md S2, S7)."""

from __future__ import annotations

import pytest

from janus.executor.egress import is_url_allowed

ALLOWED = frozenset({"http://127.0.0.1:8101"})


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://127.0.0.1:8101/services", True),
        ("ws://127.0.0.1:8101/live", True),
        ("http://127.0.0.1:8199/steal", False),
        ("ws://127.0.0.1:8199/ws", False),
        ("https://127.0.0.1:8101/services", False),
        ("http://127.0.0.1:8101/__bench/state", False),
        ("http://127.0.0.1:8101/__bench", False),
        ("http://127.0.0.1:8101/__benchmark", True),
        ("http://127.0.0.1:8101/a/__bench/state", True),
    ],
)
def test_is_url_allowed(url: str, expected: bool) -> None:
    assert is_url_allowed(url, ALLOWED) is expected
