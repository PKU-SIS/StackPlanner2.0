---
name: cuad
description: Extract exact answer spans from an uploaded contract for a public CUAD question.
---

Read the source contract at `/mnt/user-data/uploads/contract.txt` with the available file tools. Locate the clauses that answer the current question and preserve their exact text. Do not use web search to substitute another contract.

Return a JSON array of verbatim spans, or objects containing `text` and a numeric `confidence` between 0 and 1. Return `[]` when the source contains no answering span. Keep supporting analysis separate from the answer. Deliver the complete answer through the native completion action or a selected final artifact.
