"""Replay recorded assistant turns locally, then use the configured real model.

Historical usage reproduces the original budget progress; only live turns make
API requests. Tool calls execute normally against the cloned saved evidence.
"""

import json
from pathlib import Path
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field


def load_failed_outline_turns(case: Path) -> list[AIMessage]:
    messages = []
    for line in (case / "steps.jsonl").read_text().splitlines():
        step = json.loads(line)
        if step.get("attempt") != 1 or step.get("type") != "ai":
            continue
        messages.append(AIMessage(content=step.get("content") or "", tool_calls=step.get("tool_calls") or [], invalid_tool_calls=step.get("invalid_tool_calls") or [], usage_metadata=step.get("usage_metadata")))
        if any(item.get("name") == "report_submit_outline" for item in step.get("invalid_tool_calls", [])):
            return messages
    raise ValueError("No original failed report_submit_outline call found")


class ReplayThenLiveModel(BaseChatModel):
    responses: list[AIMessage]
    live_model: Any = Field(exclude=True)
    position: int = 0
    live_turns: int = 0

    @property
    def _llm_type(self):
        return "outline-replay-then-live"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self.bind(replay_tools=tools, replay_tool_choice=tool_choice)

    def _replayed(self):
        if self.position < len(self.responses):
            message = self.responses[self.position].model_copy(deep=True)
            self.position += 1
            return message
        return None

    def _live(self, kwargs):
        tools = kwargs.pop("replay_tools", None)
        choice = kwargs.pop("replay_tool_choice", None)
        return self.live_model.bind_tools(tools, tool_choice=choice) if tools else self.live_model

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        response = self._replayed()
        if response is None:
            self.live_turns += 1
            response = self._live(kwargs).invoke(messages, config={"callbacks": []}, stop=stop, **kwargs)
        return ChatResult(generations=[ChatGeneration(message=response)])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        response = self._replayed()
        if response is None:
            self.live_turns += 1
            response = await self._live(kwargs).ainvoke(messages, config={"callbacks": []}, stop=stop, **kwargs)
        return ChatResult(generations=[ChatGeneration(message=response)])
