---
name: dcc-audit
description: Check a complete inline scenario against the supplied rule files.
---

The task prompt contains the complete scenario. Read the supplied rule/reference
files with native file tools. Apply the original task instructions, preserve the
scenario's numbers and operation order, and follow its required answer format.
Return the complete audit answer to the CentralAgent. A complete rule excerpt,
calculation script, or verification log completes only its delegated step;
the final delivery must answer the original question in its required format.

For local arithmetic and checks, use a single command per bash call or join
dependent steps with &&. Do not append exit-status echo commands with semicolons,
pipe checks through head, or mask errors with || true. A successful data audit
can report rule violations; these findings are not software test failures.
