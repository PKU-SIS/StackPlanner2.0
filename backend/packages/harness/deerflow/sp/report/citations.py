"""Shared recognition of plain and Markdown-escaped report source markers."""

import re

# Escapes must be paired; do not alter ordinary math/Markdown backslashes.
CITATION_RE = re.compile(r"(?<!\\)(?P<escape>\\?)\[(?P<source_id>S\d+)(?P=escape)\]")
