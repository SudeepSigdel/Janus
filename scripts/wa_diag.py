"""Read-only diagnostics for the WebArena forum container (docs/PLAN.md B3).

Prints the container state, its recent logs, the image, and what the env-ctrl and site
endpoints answer on 127.0.0.1 and localhost. Changes nothing.
"""

from __future__ import annotations

import subprocess
import urllib.request

NAME = "webarena-verified-reddit"


def sh(*args: str) -> None:
    print(f"$ {' '.join(args)}")
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=60)
        print((out.stdout + out.stderr)[-4000:])
    except Exception as exc:  # noqa: BLE001
        print(f"  failed: {exc}")


def get(url: str) -> None:
    try:
        with urllib.request.urlopen(url, timeout=8) as r:
            print(f"GET {url} -> {r.status} {r.read(300)!r}")
    except Exception as exc:  # noqa: BLE001
        print(f"GET {url} -> {type(exc).__name__}: {exc}")


sh(
    "docker",
    "ps",
    "-a",
    "--filter",
    f"name={NAME}",
    "--format",
    "{{.Names}} | {{.Status}} | {{.Ports}}",
)
sh("docker", "image", "ls", "am1n3e/webarena-verified-reddit")
sh("docker", "logs", "--tail", "60", NAME)
for host in ("127.0.0.1", "localhost"):
    get(f"http://{host}:9998/status")
    get(f"http://{host}:9999/login")
sh("docker", "exec", NAME, "env-ctrl", "status")
