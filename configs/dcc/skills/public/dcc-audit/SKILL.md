---
name: dcc-audit
description: Check a document sample against its supplied rule files using scoped RAGFlow evidence retrieval.
---

Read the listed uploaded rule/reference files with the native file tools. The
current question and document inventory come from the task prompt. Use
`ragflow_retrieve` to inspect relevant evidence; its document scope is selected
automatically for the current user and thread. Preserve the identifiers and
values needed by the rules and retain chunk references in working notes.

Query relevant concepts and relationships iteratively. Retrieved snippets may
omit important evidence. Do not equate an empty result with proof of absence.
Record incomplete coverage honestly. Apply the task-specific output contract
from the rule files. Return the required auditing answer to the CentralAgent
for final delivery using normal SP orchestration.
