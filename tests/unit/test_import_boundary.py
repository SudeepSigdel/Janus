"""Hard constraint: the runtime (janus) never imports janus_bench."""

from __future__ import annotations

import ast
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[2] / "src" / "janus"


def imports_bench(source: str) -> bool:
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            if any(a.name.split(".")[0] == "janus_bench" for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] == "janus_bench":
                return True
    return False


def test_runtime_does_not_import_bench() -> None:
    offenders = [
        str(p.relative_to(RUNTIME))
        for p in RUNTIME.rglob("*.py")
        if imports_bench(p.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"janus imports janus_bench in: {offenders}"


def test_detector_catches_imports() -> None:
    assert imports_bench("import janus_bench")
    assert imports_bench("from janus_bench.sites import x")
    assert imports_bench("def f():\n    import janus_bench.harness")
    assert not imports_bench("import janus\nfrom janus.llm import LLMClient")
