"""In-memory state shared by a replica site and its /__bench endpoints."""

from __future__ import annotations

import copy
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import Body, FastAPI

Seed = Callable[[str | None], dict[str, Any]]
_RESET_BODY = Body(default=None)


class StateStore:
    """Site state. `seed(variant)` builds fresh data; `variant` selects an injection variant."""

    def __init__(self, seed: Seed) -> None:
        self._seed = seed
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {}
        self.reset(None)

    def reset(self, variant: str | None) -> None:
        with self._lock:
            self._data = copy.deepcopy(self._seed(variant))
            self._data["variant"] = variant
            self._data["events"] = []

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data)

    @contextmanager
    def mutate(self) -> Iterator[dict[str, Any]]:
        with self._lock:
            yield self._data

    def read(self) -> dict[str, Any]:
        with self._lock:
            return self._data


def mount_bench(app: FastAPI, store: StateStore) -> None:
    """Add POST /__bench/reset and GET /__bench/state."""

    @app.post("/__bench/reset")
    def bench_reset(payload: dict[str, Any] | None = _RESET_BODY) -> dict[str, Any]:
        variant = (payload or {}).get("variant")
        variant = variant if isinstance(variant, str) else None
        store.reset(variant)
        return {"ok": True, "variant": variant}

    @app.get("/__bench/state")
    def bench_state() -> dict[str, Any]:
        return store.snapshot()
