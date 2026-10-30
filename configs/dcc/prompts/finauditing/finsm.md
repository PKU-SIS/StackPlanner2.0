FinAuditing semantic checking task

Given the question, identify erroneous concepts or axis-member pairs in the filing. Read the supplied rule/reference files and use the US GAAP taxonomy to check relevant elements across the filing's schema, presentation, calculation, definition, label, and instance documents. Use ragflow_retrieve to inspect the required evidence and apply the question's conditions and exceptions.

Final output: only a JSON array of strings. Each string is one erroneous concept identifier or the axis-member pair requested by the question. Preserve exact identifiers and pair notation. Example:
["us-gaap:Revenues", "us-gaap:OperatingIncomeLoss"]

Do not add explanations, citations, Markdown fences, or a wrapping object to the final answer. Keep supporting evidence in working notes.
