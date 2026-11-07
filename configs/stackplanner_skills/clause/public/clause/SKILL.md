---
name: clause
description: Task scope, evidence boundaries and final JSON requirements for CLAUSE EVAL3 in-text review.
---

Review the entire uploaded contract using the five categories in the task. Report incompatible obligations or readings, and unresolved terms, omissions or broken references that have a substantive effect. Respect express exceptions. Ignore capitalization, synonyms and different names for the same document when their meaning and effect agree.

Quote the affected passage verbatim in `text`, including its printed leading clause number or list marker. In `explanation`, explain in English which provisions conflict or what is missing and why it matters. Tool line numbers are separate from contract numbering.

Deliver one UTF-8 JSON array in the assigned file. Each object has exactly the two fields `text` and `explanation`. Use `[]` only after a completed review with no findings.
