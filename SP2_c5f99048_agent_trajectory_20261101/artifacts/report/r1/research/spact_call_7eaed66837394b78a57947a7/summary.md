# Pre-research orientation — A2A vs MCP source-cited report (report_id r1)

## Overview
Orientation pass before Outline planning. Centered the reported goal: differences & connections between Google's A2A and Anthropic's MCP; A2A's innovation aspects; specific problems A2A addresses. Source-cited research report delivery.

## Saved material

### m1 / S1 — A2A Protocol official documentation
- Path: /mnt/user-data/outputs/report/r1/materials/S1/22e2e93233e2f45b593661bd228bb9fb3f41841a34829ab5f1cf65624130ea0a.txt
- URL: https://a2a-protocol.org/latest/ (A2A Project / Linux Foundation administration)
- Nature: **first-party protocol specification/documentation**, maintained by the A2A open project that Google donated to the Linux Foundation.
- Upstream truncation: false; 141 lines, ~7390 chars.

#### Key orientation facts (locatable)
- Lines 4-6: official positioning — "An open standard enabling users to connect agents to tools and data through MCP, and to other agents through A2A"; "developers to expose portable tools through MCP and interoperable agents through A2A."
- Line 8-11: "What is A2A?" — open standard for communication/collaboration between AI agents; "definitive common language for agent interoperability."
- Lines 20-29 (Key Features): Interoperability (LangGraph, CrewAI, Semantic Kernel, custom); Complex Workflows (delegate sub-tasks, exchange info, coordinate); Secure & Opaque (agents interact without sharing internal memory/tools/proprietary logic); Extensible (protocol extensions/custom bindings).
- Lines 64-84 ("How A2A Works with MCP"): OFFICIAL COMPLEMENTARITY FRAMING — "MCP and A2A are not competitors — they are highly complementary." MCP is for agent-to-tool communication; A2A is for agent-to-agent communication (discover, delegate tasks, share results). Includes diagram (client agent reaches remote tools via MCP and remote agents via A2A).
- Lines 86-88: "A2A and MCP — deeper dive" subtopic exists (topics/a2a-and-mcp/).
- Lines 91-97 ("What A2A Is Not"): NOT an agent dev kit; NOT a sub-agent or tool-call protocol; NOT a replacement for MCP; NOT an interactive messaging app.
- Lines 99-105 (Governance): "originally developed by Google and donated to the Linux Foundation"; Technical Steering Committee with AWS, Cisco, Google, IBM Research, Microsoft, Salesforce, SAP, ServiceNow.
- Lines 107-109: Apache License 2.0.

## Additional official sources located (search-verified, not yet fetched in this pass)
- **Google Developers Blog — "Announcing the Agent2Agent Protocol (A2A): A new era of agent interoperability"** — https://developers.googleblog.com/en/a2a-a-new-era-of-agent-interoperability/ (official first-party announcement, Apr 2025; also confirmed Japanese mirror https://developers.googleblog.com/ja/a2a-a-new-era-of-agent-interoperability/). FETCH TIMED OUT this pass (technical_error) — should be retried in Outline phase.
- **Model Context Protocol official spec** — https://modelcontextprotocol.io/ and dated spec https://modelcontextprotocol.io/specification/2026-07-28 (first-party Anthropic spec; open source MIT).

## Gaps / open needs for Outline
- Google Developers Blog English announcement not yet saved (fetch timeout). The official announcement framing (complement to MCP, 50+ partners incl. Atlassian/Box/Cohere/Intuit/LangChain/MongoDB/Salesforce/SAP/ServiceNow/UKG/Workday) is corroborated by third-party SiliconANGLE coverage but the first-party text should be fetched.
- MCP official spec content not yet saved; deeper comparison sections (A2A vs MCP framing, A2A innovation/problems) will need it plus secondary analysis.
- Prior completed version of this report exists (see task memory; /mnt/user-data/outputs/A2A与MCP协议研究报告.md) and should be treated as regeneration reference, not as a searchable source.
- No assigned gaps for this pre-research pass; gap_attempts omitted accordingly (no gaps bound to this action).
