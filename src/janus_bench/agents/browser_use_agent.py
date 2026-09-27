"""Browser Use baseline: vision off, same local `janus-planner` model via Ollama.

Not part of the runtime; requires the optional `baseline` extra.
No escalation step (see LIMITATIONS.md).
"""

from __future__ import annotations

import asyncio
import os

from janus.config import Settings, get_settings
from janus.llm import assert_local_url
from janus_bench.harness.taskspec import TaskSpec

MAX_STEPS = 20
_LOCAL_PLACEHOLDER_KEY = "local-ollama"  # the client requires a non-empty value; Ollama ignores it


def build_prompt(task: TaskSpec) -> str:
    lines = [f"Start at {task.start_url}.", task.instruction.en]
    if task.inputs:
        lines.append("Inputs: " + "; ".join(f"{k}={v}" for k, v in task.inputs.items()))
    return "\n".join(lines)


class BrowserUseAgent:
    name = "browser_use"

    def __init__(self, settings: Settings | None = None, max_steps: int = MAX_STEPS) -> None:
        self.settings = settings or get_settings()
        assert_local_url(self.settings.ollama_base_url)
        self.max_steps = max_steps
        self.steps: int | None = None
        try:
            import browser_use  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "browser_use agent needs the baseline extra: uv sync --all-extras"
            ) from exc

    def run(self, task: TaskSpec) -> None:
        self.steps = None
        asyncio.run(self._run(task))

    async def _run(self, task: TaskSpec) -> None:
        os.environ["ANONYMIZED_TELEMETRY"] = "false"
        os.environ["BROWSER_USE_CLOUD_SYNC"] = "false"
        from browser_use import Agent, BrowserProfile, BrowserSession
        from browser_use.llm import ChatOpenAI

        llm = ChatOpenAI(
            model=self.settings.planner_model,
            base_url=f"{self.settings.ollama_base_url}/v1",
            api_key=_LOCAL_PLACEHOLDER_KEY,
            temperature=0,
            reasoning_effort="none",
        )
        session = BrowserSession(browser_profile=BrowserProfile(headless=True))
        agent = Agent(
            task=build_prompt(task),
            llm=llm,
            browser_session=session,
            use_vision=False,
            use_judge=False,
            enable_signal_handler=False,
        )
        try:
            history = await agent.run(max_steps=self.max_steps)
            self.steps = len(history.history)
        finally:
            await session.kill()
