"""Network-free token estimates for mixed Latin and CJK agent messages."""

import math
import re

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.messages.utils import count_tokens_approximately

from deerflow.utils.messages import message_content_to_text

_CJK = re.compile(r"[\u2e80-\ua4cf\uac00-\ud7af\uf900-\ufaff\U00020000-\U000323af]")


def estimate_message_tokens(messages: list[BaseMessage], *, use_usage_metadata_scaling: bool = False) -> int:
    """Keep Latin/image overhead estimates and charge at least one token per CJK character.

    This remains an estimate, not a model tokenizer. Provider-reported usage
    takes precedence for billing; this counter is a context/budget backstop.
    Image data is excluded from the character correction.
    """
    messages = list(messages)
    approximate = count_tokens_approximately(messages)
    cjk_count = 0
    for message in messages:
        cjk_count += len(_CJK.findall(message_content_to_text(message.content)))
        if isinstance(message, AIMessage) and not isinstance(message.content, list) and message.tool_calls:
            cjk_count += len(_CJK.findall(repr(message.tool_calls)))
    corrected = approximate + math.ceil(cjk_count * 0.75)
    if use_usage_metadata_scaling:
        corrected = max(corrected, count_tokens_approximately(messages, use_usage_metadata_scaling=True))
    return corrected
