"""External benchmark pins: one file, checked by commit or sha256, refused on mismatch."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from janus.pins import PINS_FILE, UnpinnedError, check, load_pins, require


def _git_repo(path: Path) -> str:
    path.mkdir(parents=True)
    run = lambda *a: subprocess.run(["git", "-C", str(path), *a], check=True, capture_output=True)  # noqa: E731
    run("init", "-q")
    (path / "f.txt").write_text("x", encoding="utf-8")
    run("add", "f.txt")
    run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "c")
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()


def _pins(tmp: Path, commit: str, sha: str) -> Path:
    pins = tmp / "pins.yaml"
    pins.write_text(
        f"bench:\n  path: repo\n  git: {commit}\n  source: clone it\n"
        f"data:\n  path: data.json\n  sha256: {sha}\n  source: download it\n",
        encoding="utf-8",
    )
    return pins


def test_the_repo_pins_file_names_every_external_benchmark() -> None:
    pins = load_pins(PINS_FILE)
    assert set(pins) == {"miniwob", "wasp", "webgames", "webarena"}
    for pin in pins.values():
        assert len(pin.expected) == (40 if pin.kind == "git" else 64)
        assert all(c in "0123456789abcdef" for c in pin.expected)
        assert str(pin.path).startswith("external")


def test_matching_checkouts_pass(tmp_path: Path) -> None:
    commit = _git_repo(tmp_path / "repo")
    (tmp_path / "data.json").write_bytes(b"{}")
    sha = hashlib.sha256(b"{}").hexdigest()
    results = require(["bench", "data"], pins_file=_pins(tmp_path, commit, sha), root=tmp_path)
    assert [r.status for r in results] == ["ok", "ok"]


def test_a_different_version_is_refused_unless_explicitly_allowed(tmp_path: Path) -> None:
    _git_repo(tmp_path / "repo")
    (tmp_path / "data.json").write_bytes(b"{}")
    pins = _pins(tmp_path, "0" * 40, "f" * 64)
    with pytest.raises(UnpinnedError, match="--allow-unpinned"):
        require(["bench"], pins_file=pins, root=tmp_path)
    with pytest.raises(UnpinnedError):
        require(["data"], pins_file=pins, root=tmp_path)
    allowed = require(["bench", "data"], allow_unpinned=True, pins_file=pins, root=tmp_path)
    assert [r.status for r in allowed] == ["mismatch", "mismatch"]


def test_a_missing_checkout_is_refused_even_when_unpinned_is_allowed(tmp_path: Path) -> None:
    pins = _pins(tmp_path, "0" * 40, "f" * 64)
    assert check(load_pins(pins)["bench"], tmp_path).status == "missing"
    with pytest.raises(UnpinnedError, match="clone it"):
        require(["bench"], allow_unpinned=True, pins_file=pins, root=tmp_path)
