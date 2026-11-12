"""Process-scoped experiment deadlines; native defaults outside this CLI are intact."""

import math
import os
from contextlib import contextmanager

_KEY = "SP2_AUDIT_TIMEOUT_MULTIPLIER"


def timeout_multiplier():
    return float(os.getenv(_KEY, "1"))


def scaled_timeout(seconds):
    return seconds * timeout_multiplier()


def scale_config_timeouts(value):
    """Scale positive configured timeouts, retaining disabled/null settings."""
    if isinstance(value, dict):
        return {
            key: (int(scaled_timeout(item)) if isinstance(item, int) else scaled_timeout(item)) if "timeout" in key.lower() and isinstance(item, (int, float)) and not isinstance(item, bool) and item > 0 else scale_config_timeouts(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [scale_config_timeouts(item) for item in value]
    return value


@contextmanager
def timeout_multiplier_environment(multiplier):
    multiplier = float(multiplier)
    if not math.isfinite(multiplier) or multiplier <= 0:
        raise ValueError("Timeout multiplier must be finite and positive")
    stream_key = "LANGCHAIN_OPENAI_STREAM_CHUNK_TIMEOUT_S"
    stream_base_key = "SP2_AUDIT_BASE_STREAM_TIMEOUT_SECONDS"
    try:
        stream_base = float(os.getenv(stream_base_key, os.getenv(stream_key, "120")))
    except ValueError:
        stream_base = 120.0
    if not math.isfinite(stream_base) or stream_base < 0:
        stream_base = 120.0
    keys = (_KEY, "DEER_FLOW_RUNTIME_TIMEOUT_MULTIPLIER", stream_key, stream_base_key)
    previous = {key: os.environ.get(key) for key in keys}
    for key in (_KEY, "DEER_FLOW_RUNTIME_TIMEOUT_MULTIPLIER"):
        os.environ[key] = str(multiplier)
    os.environ[stream_base_key] = str(stream_base)
    os.environ[stream_key] = str(stream_base * multiplier)
    try:
        yield multiplier
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
