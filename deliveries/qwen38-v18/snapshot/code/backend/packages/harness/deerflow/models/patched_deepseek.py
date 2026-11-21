"""Patched ChatDeepSeek that preserves reasoning_content in multi-turn conversations.

This module provides a patched version of ChatDeepSeek that properly handles
reasoning_content when sending messages back to the API. The original implementation
stores reasoning_content in additional_kwargs but doesn't include it when making
subsequent API calls, which causes errors with APIs that require reasoning_content
on all assistant messages when thinking mode is enabled.
"""

import json
from typing import Any

from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_deepseek import ChatDeepSeek

from deerflow.models.assistant_payload_replay import restore_assistant_payloads, restore_reasoning_content


def project_legacy_history(messages):
    """Request-only compatibility for history with unavailable provider fields.

    Real reasoning is replayed verbatim. Host-generated actions and legacy
    assistant turns with lost reasoning become labelled observations, never
    invented reasoning. The checkpoint/messages themselves are not changed.
    """
    projected = []
    historical_calls = set()
    for message in messages:
        if isinstance(message, AIMessage) and (message.additional_kwargs.get("reasoning_content") is None or message.additional_kwargs.get("stackplanner_generated")):
            historical_calls.update(call["id"] for call in message.tool_calls)
            observation = {"assistant_output": message.content, "tool_calls": message.tool_calls}
            projected.append(HumanMessage(content="[Runtime history observation, not a new user request; continue the original task.]\n" + json.dumps(observation, ensure_ascii=False)))
        elif isinstance(message, ToolMessage) and message.tool_call_id in historical_calls:
            projected.append(HumanMessage(content="[Historical tool result, not a new user request]\n" + json.dumps({"tool_call_id": message.tool_call_id, "content": message.content}, ensure_ascii=False)))
        else:
            projected.append(message)
    return projected


class PatchedChatDeepSeek(ChatDeepSeek):
    """ChatDeepSeek with proper reasoning_content preservation.

    When using thinking/reasoning enabled models, the API expects reasoning_content
    to be present on ALL assistant messages in multi-turn conversations. This patched
    version ensures reasoning_content from additional_kwargs is included in the
    request payload.
    """

    @classmethod
    def is_lc_serializable(cls) -> bool:
        return True

    @property
    def lc_secrets(self) -> dict[str, str]:
        return {"api_key": "DEEPSEEK_API_KEY", "openai_api_key": "DEEPSEEK_API_KEY"}

    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        """Get request payload with reasoning_content preserved.

        Overrides the parent method to inject reasoning_content from
        additional_kwargs into assistant messages in the payload.
        """
        # Get the original messages before conversion
        original_messages = self._convert_input(input_).to_messages()

        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        extra = payload.get("extra_body") or self.extra_body or {}
        if payload.get("tools") and (extra.get("thinking") or {}).get("type") == "enabled":
            original_messages = project_legacy_history(original_messages)
            payload = super()._get_request_payload(original_messages, stop=stop, **kwargs)

        restore_assistant_payloads(
            payload.get("messages", []),
            original_messages,
            restore_reasoning_content,
        )

        return payload
