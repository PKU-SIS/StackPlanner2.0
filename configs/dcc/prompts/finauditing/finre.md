FinAuditing relationship checking task

Given the two concepts in the question, check their relationship in the filing against the supplied US GAAP taxonomy. Read the rule/reference files and use ragflow_retrieve to examine the relevant schema, presentation, calculation, definition, label, and instance evidence.

Choose exactly one erroneous relationship type:
- Reversal: a sibling relationship in the US GAAP taxonomy is redefined as parent-child in the filing's extension taxonomy, or vice versa.
- Inappropriateness: an element is placed under an abstract category where it does not belong according to the US GAAP taxonomy.
- CombinationErr: an axis is associated with inappropriate members that the US GAAP taxonomy does not permit.

Final output: exactly one unquoted label: Reversal, Inappropriateness, or CombinationErr. Do not add an explanation, Markdown fence, JSON wrapper, or other text. Keep evidence references in working notes rather than the final answer.
