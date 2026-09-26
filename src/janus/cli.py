"""`janus` command line: doctor (M0); run arrives in M5."""

from __future__ import annotations

import argparse
import sys

from janus.config import get_settings
from janus.llm import LLMClient, LLMError


def has_model(installed: list[str], wanted: str) -> bool:
    names = set(installed)
    return wanted in names or f"{wanted}:latest" in names


def doctor() -> int:
    settings = get_settings()
    client = LLMClient(settings)
    try:
        installed = client.list_models()
    except LLMError as exc:
        print(f"FAIL  Ollama not reachable at {settings.ollama_base_url}: {exc}")
        return 1
    finally:
        client.close()
    print(f"ok    Ollama reachable at {settings.ollama_base_url}")
    missing = 0
    for model in settings.required_models:
        if has_model(installed, model):
            print(f"ok    model {model}")
        else:
            print(f"FAIL  model {model} missing")
            missing += 1
    return 1 if missing else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="janus")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="check Ollama and required models")
    args = parser.parse_args(argv)
    if args.command == "doctor":
        return doctor()
    return 2


if __name__ == "__main__":
    sys.exit(main())
