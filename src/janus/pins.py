"""Pinned versions of the external benchmarks, checked before they are used.

The pins live in one file, `external-pins.yaml` at the repo root. A git checkout is pinned by its
HEAD commit, a single data file by its sha256. `janus doctor` reports every pin, and each
`janus-bench` command that uses an external benchmark calls `require` first, refusing to run on a
missing or different checkout unless the caller deliberately allows it.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

PINS_FILE = Path("external-pins.yaml")

Status = Literal["ok", "missing", "mismatch"]


@dataclass(frozen=True)
class Pin:
    name: str
    path: Path
    kind: Literal["git", "sha256"]
    expected: str
    source: str


@dataclass(frozen=True)
class PinCheck:
    pin: Pin
    status: Status
    actual: str | None


class UnpinnedError(RuntimeError):
    """An external benchmark is missing or not at its pinned version."""


def load_pins(path: Path = PINS_FILE) -> dict[str, Pin]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    pins: dict[str, Pin] = {}
    for name, entry in data.items():
        kind: Literal["git", "sha256"] = "git" if "git" in entry else "sha256"
        pins[name] = Pin(
            name=name,
            path=Path(entry["path"]),
            kind=kind,
            expected=str(entry[kind]).strip().lower(),
            source=" ".join(str(entry.get("source", "")).split()),
        )
    return pins


def _git_head(path: Path) -> str | None:
    if not (path / ".git").exists():
        return None
    try:
        out = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip().lower() or None


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def check(pin: Pin, root: Path = Path(".")) -> PinCheck:
    target = root / pin.path
    actual = _git_head(target) if pin.kind == "git" else _sha256(target)
    if actual is None:
        return PinCheck(pin, "missing", None)
    return PinCheck(pin, "ok" if actual == pin.expected else "mismatch", actual)


def describe(result: PinCheck) -> str:
    pin = result.pin
    if result.status == "ok":
        return f"ok    {pin.name} at {pin.kind} {pin.expected[:12]}"
    if result.status == "missing":
        return f"--    {pin.name} not installed ({pin.path}); get it with: {pin.source}"
    return (
        f"FAIL  {pin.name} is at {result.actual[:12] if result.actual else '?'}, pinned "
        f"{pin.expected[:12]} ({pin.path})"
    )


def require(
    names: list[str],
    allow_unpinned: bool = False,
    pins_file: Path = PINS_FILE,
    root: Path = Path("."),
) -> list[PinCheck]:
    """Check the named pins. Raise `UnpinnedError` on any missing or different checkout, unless
    `allow_unpinned` (a deliberate upgrade) is set, in which case only a missing one raises."""
    pins = load_pins(pins_file)
    results = [check(pins[name], root) for name in names]
    problems = [
        r
        for r in results
        if r.status == "missing" or (r.status == "mismatch" and not allow_unpinned)
    ]
    if problems:
        lines = "\n".join(describe(r) for r in problems)
        hint = (
            ""
            if allow_unpinned
            else (
                "\nRe-fetch the pinned version, or pass --allow-unpinned for a deliberate upgrade "
                f"(then update {pins_file} and docs/EXPERIMENTS.md)."
            )
        )
        raise UnpinnedError(lines + hint)
    return results
