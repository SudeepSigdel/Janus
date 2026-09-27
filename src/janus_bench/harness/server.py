"""Run a replica site in a background uvicorn thread."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import httpx
import uvicorn
from fastapi import FastAPI

from janus_bench.sites.nagarpalika.app import PORT as NAGARPALIKA_PORT
from janus_bench.sites.nagarpalika.app import create_app as create_nagarpalika
from janus_bench.sites.sharesewa.app import PORT as SHARESEWA_PORT
from janus_bench.sites.sharesewa.app import create_app as create_sharesewa

SITES: dict[str, tuple[Callable[[], FastAPI], int]] = {
    "nagarpalika": (create_nagarpalika, NAGARPALIKA_PORT),
    "sharesewa": (create_sharesewa, SHARESEWA_PORT),
}
HOST = "127.0.0.1"


def origin(site: str) -> str:
    return f"http://{HOST}:{SITES[site][1]}"


@contextmanager
def running_site(site: str, timeout: float = 10.0) -> Iterator[str]:
    """Serve `site` on its fixed port; yield its origin."""
    factory, port = SITES[site]
    server = uvicorn.Server(uvicorn.Config(factory(), host=HOST, port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = origin(site)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            httpx.get(f"{base}/__bench/state", timeout=1.0).raise_for_status()
            break
        except httpx.HTTPError:
            time.sleep(0.1)
    else:
        server.should_exit = True
        raise RuntimeError(f"site {site} did not start on port {port}")
    try:
        yield base
    finally:
        server.should_exit = True
        thread.join(timeout=5)


def serve(site: str) -> None:
    factory, port = SITES[site]
    uvicorn.run(factory(), host=HOST, port=port, log_level="info")
