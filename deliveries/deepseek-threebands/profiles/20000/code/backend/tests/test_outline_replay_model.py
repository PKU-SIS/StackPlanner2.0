"""Replay is diagnostic only; it must stop at the recorded failure and unbind tools."""

import importlib.util
import json
from pathlib import Path

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import Field

SPEC = importlib.util.spec_from_file_location("outline_replay_model", Path(__file__).resolve().parents[1] / "scripts/outline_replay_model.py")
replay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(replay)


class Live(FakeMessagesListChatModel):
    bound_tools: list = Field(default_factory=list)

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        self.bound_tools.append([item.name for item in tools])
        return self


def test_replay_stops_at_original_bad_call_and_only_later_turns_are_live(tmp_path):
    steps = [
        {"attempt": 1, "type": "ai", "content": "Plan", "tool_calls": []},
        {"attempt": 1, "type": "ai", "content": "", "invalid_tool_calls": [{"name": "report_submit_outline", "args": "bad", "id": "bad", "error": "bad JSON"}]},
        {"attempt": 2, "type": "ai", "content": "Should not replay"},
    ]
    (tmp_path / "steps.jsonl").write_text("\n".join(json.dumps(row) for row in steps))
    messages = replay.load_failed_outline_turns(tmp_path)
    assert len(messages) == 2
    assert messages[-1].invalid_tool_calls[0]["args"] == "bad"
    live = Live(responses=[AIMessage(content="Live")])
    model = replay.ReplayThenLiveModel(responses=messages, live_model=live)
    request = [HumanMessage(content="Task")]
    assert model.invoke(request).content == "Plan"
    assert model.invoke(request).invalid_tool_calls
    assert model.invoke(request).content == "Live"
    assert model.position == 2
    assert model.live_turns == 1
