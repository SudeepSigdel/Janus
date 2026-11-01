"""No API key is committed in the repo. Hosted models (open-weight or not) are allowed via the
JANUS_CHAT_* settings; all AI calls go through src/janus/llm.py."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"

# Shapes of real provider keys. Placeholders and empty values do not match.
KEY_PATTERN = re.compile(r"\b(sk-[A-Za-z0-9_-]{20,}|gsk_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{30,})")
KEY_SCAN_SUFFIXES = {".py", ".md", ".toml", ".yaml", ".yml", ".example", ".json", ".html"}


def _py_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def test_src_has_files() -> None:
    assert _py_files()


def test_no_committed_keys() -> None:
    offenders = []
    for folder in (SRC, ROOT / "tests", ROOT / "docs", ROOT / "scripts"):
        for path in folder.rglob("*"):
            if path.name == Path(__file__).name or "__pycache__" in path.parts:
                continue
            if path.suffix in KEY_SCAN_SUFFIXES and path.is_file():
                if KEY_PATTERN.search(path.read_text(encoding="utf-8", errors="ignore")):
                    offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"possible API keys in: {offenders}"


def test_scanner_detects_violations() -> None:
    assert KEY_PATTERN.search("key = 'sk-abcdefghijklmnopqrstuvwxyz'")
    assert not KEY_PATTERN.search("JANUS_CHAT_API_KEY=")
