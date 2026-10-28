---
name: widesearch
description: Research a public multi-source question and deliver its complete answer table with source evidence.
---

Use `web_search` to discover relevant sources and `web_fetch` to inspect the evidence. Follow `next_url` when an observation indicates more source content. Retain source references and exact cell values; a failed fetch or challenge page is not evidence.

Apply the question's filters and output column schema to the collected records. Preserve exact values and keep row citations separately keyed by identifying values. Deliver one complete Markdown answer table or a JSON array of row objects. Research notes, raw API envelopes, plans, and connectivity probes are intermediate artifacts. Before native completion, inspect the actual selected final artifact and confirm it contains the full filtered table.
