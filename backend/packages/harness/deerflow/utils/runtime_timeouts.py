"""Opt-in process-local scaling of infrastructure deadlines, defaulting to one."""

import math
import os


def scaled_runtime_timeout(seconds: float) -> float:
    try:
        multiplier = float(os.getenv("DEER_FLOW_RUNTIME_TIMEOUT_MULTIPLIER", "1"))
    except ValueError:
        multiplier = 1.0
    if not math.isfinite(multiplier) or multiplier <= 0:
        multiplier = 1.0
    return seconds * multiplier
