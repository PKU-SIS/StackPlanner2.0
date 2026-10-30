FinAuditing numerical checking task

Answer both questions for the requested financial element and period: extract its reported value from the instance document, then compute the corresponding actual value from the filing's calculation relationships. Read the supplied rule/reference files, consult the US GAAP taxonomy, and retrieve the relevant facts, contexts, units, and weighted calculation relationships with ragflow_retrieve.

Final output: only a JSON object with exactly these two fields. Both values must be strings:
{"extracted_value": "<reported numeric value>", "calculated_value": "<computed numeric value>"}

Keep the reported value's number formatting as it appears in the XBRL filing, and use the same formatting style for the calculated value. As specified by the original task instructions, use "0" for a reported value that is not found or an actual value that is not computable. Do not add explanations, citations, Markdown fences, or extra fields to the final answer.
