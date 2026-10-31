"""Ordered, strictly substituted prompt layers, with no access to private gold."""

from __future__ import annotations

from pathlib import Path
from string import Template

PROMPTS = Path(__file__).resolve().parents[2] / "configs" / "dcc" / "prompts"
DEFAULT_LAYERS = [str(PROMPTS / name) for name in ("common.md", "inputs.md", "retrieval.md", "question.md")]
DEFAULT_INLINE_LAYERS = [str(PROMPTS / name) for name in ("common.md", "inline-protocol.md", "inline-inputs.md", "question.md")]


def compose(layers: list[str], variables: dict[str, str]) -> str:
    fragments = []
    for layer in layers:
        content = layer.removeprefix("literal:") if layer.startswith("literal:") else Path(layer).read_text(encoding="utf-8")
        try:
            fragments.append(Template(content).substitute(variables).strip())
        except (KeyError, ValueError) as exc:
            raise ValueError(f"prompt layer {layer!r}: missing or invalid template variable: {exc}") from None
    return "\n\n".join(fragment for fragment in fragments if fragment)
