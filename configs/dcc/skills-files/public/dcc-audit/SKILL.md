---
name: dcc-audit
description: Inspect sample-local XML or text evidence with native file search and reading tools.
---

Read dcc-context.json and the listed rule/reference files. Inspect only the
evidence uploaded for this sample using native grep, glob and read_file tools.
Grep searches directories; read the original matching text rather than relying
on truncated previews. Use local Python for precise XML/text relationships or
arithmetic when useful. Preserve all original identifiers, namespaces, units,
periods and values; do not repair truncated source fragments or invent evidence.

This is data analysis, not a software patch. Keep command status observable with
one command per bash call or &&, and return the complete audit answer in the
original task's exact format. Intermediate excerpts/scripts are working evidence.
