"""Hard constraint: no proprietary APIs, SDKs, or keys anywhere in src/."""

from __future__ import annotations

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"

BANNED_HOSTS = re.compile(
    r"api\.openai\.com|api\.anthropic\.com|generativelanguage\.googleapis\.com"
    r"|api\.mistral\.ai|api\.cohere\.(com|ai)|api\.groq\.com|openrouter\.ai",
    re.IGNORECASE,
)
BANNED_ENV = re.compile(
    r"OPENAI_API_KEY|ANTHROPIC_API_KEY|GEMINI_API_KEY|GOOGLE_API_KEY"
    r"|MISTRAL_API_KEY|COHERE_API_KEY|GROQ_API_KEY|OPENROUTER_API_KEY",
)
BANNED_MODULES = {
    "openai",
    "anthropic",
    "google.generativeai",
    "google.genai",
    "mistralai",
    "cohere",
    "groq",
}


def _py_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _is_banned_module(name: str) -> bool:
    return any(name == m or name.startswith(m + ".") for m in BANNED_MODULES)


def test_src_has_files() -> None:
    assert _py_files()


def test_no_banned_hosts_or_env_names() -> None:
    offenders = []
    for path in _py_files():
        text = path.read_text(encoding="utf-8")
        if BANNED_HOSTS.search(text) or BANNED_ENV.search(text):
            offenders.append(str(path.relative_to(SRC)))
    assert not offenders, f"proprietary hosts/keys in: {offenders}"


def test_no_sdk_imports() -> None:
    offenders = []
    for path in _py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            offenders += [f"{path.relative_to(SRC)}: {n}" for n in names if _is_banned_module(n)]
    assert not offenders, f"proprietary SDK imports: {offenders}"


def test_scanner_detects_violations() -> None:
    assert BANNED_HOSTS.search("url = 'https://api.openai.com/v1'")
    assert BANNED_ENV.search("os.environ['ANTHROPIC_API_KEY']")
    assert _is_banned_module("openai.types")
    assert not _is_banned_module("openaiish")
