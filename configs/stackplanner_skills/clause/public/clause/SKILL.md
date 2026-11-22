---
name: clause
description: Task scope, evidence boundaries and final JSON requirements for CLAUSE EVAL3 in-text review.
---

Review the entire uploaded contract using the five categories in the task. Report incompatible obligations or readings, and unresolved terms, omissions or broken references that have a substantive effect. Respect express exceptions. Ignore capitalization, synonyms and different names for the same document when their meaning and effect agree.

Quote the affected passage verbatim in `text`, including its printed leading clause number or list marker. In `explanation`, explain in English the document-grounded defect and why it matters, using evidence appropriate to ambiguity, inconsistency, terminology, omission or structural issues. Only inconsistency requires comparing incompatible statements; for ambiguity identify the competing supported readings, for terminology identify the contextually material defect, for omission identify the internally evidenced requirement and its missing content, and for structural issues identify the broken organization or reference. Tool line numbers are separate from contract numbering.

Deliver one UTF-8 JSON array in the assigned file. Each object has exactly the two fields `text` and `explanation`. Use `[]` only after a completed review with no findings.
