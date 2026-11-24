"""Shared recognition of plain, line-qualified and Markdown-escaped source markers."""

import re

# Escapes must be paired; do not alter ordinary math/Markdown backslashes.
CITATION_RE = re.compile(r"(?<!\\)(?P<escape>\\?)\[(?P<source_id>S\d+)(?P<locator>:[1-9]\d*(?:-[1-9]\d*)?)?(?P=escape)\]")
