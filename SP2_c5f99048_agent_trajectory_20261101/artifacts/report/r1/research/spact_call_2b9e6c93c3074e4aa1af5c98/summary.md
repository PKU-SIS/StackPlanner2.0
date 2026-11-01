# Research observation — gap g1 (report r1): MCP official definition, scope, positioning, design goals

## Assigned gap g1
Question: "按照 MCP 官方规范（Model Context Protocol），MCP 协议的定义、作用范围、定位（agent-to-tool 标准化）以及其与 A2A 的关系/连接表述是什么？"
Nodes: n4 (MCP scope), n5 (A2A scope), n9 (A2A innovation), n14 (summary).

## Newly saved materials (official first-party, both upstream_truncation=false)

### m2 / S2 — Model Context Protocol official documentation (Introduction)
- Path: /mnt/user-data/outputs/report/r1/materials/S2/17bf734c7cc2b4ce2385199657ef12dda5b0adae07c5aff1fb56499ae1b7d4f1.txt
- URL: https://modelcontextprotocol.io/
- Nature: first-party protocol documentation (site administered by the MCP Project / Anthropic). 63 lines, 3411 chars.
- Locatable key lines:
  - Lines 4: definition — "MCP (Model Context Protocol) is an open-source standard for connecting AI applications to external systems."
  - Lines 5-8: scope — via MCP, AI applications (Claude/ChatGPT) connect to data sources (local files, databases), tools (search engines, calculators) and workflows (specialized prompts), "enabling them to access key information and perform tasks."
  - Lines 10-12: positioning — "Think of MCP like a USB-C port for AI applications... standardized way to connect AI applications to external systems."
  - Lines 18-28: ecosystem benefits — developers (reduces development time/complexity), AI apps/agents (access to ecosystem of data sources/tools/apps), end-users; open protocol supported across clients/servers (Claude, ChatGPT, VS Code, Cursor).

### m3 / S3 — Model Context Protocol specification, Introduction (2025-06-18)
- Path: /mnt/user-data/outputs/report/r1/materials/S3/9b811c6b272f8d694496d290e78e6693def5a70c3bdbf04cb2a8d2e2f0e3baaf.txt
- URL: https://modelcontextprotocol.io/specification/2025-06-18
- Nature: first-party authoritative protocol specification (defines authoritative requirements from TypeScript schema in GitHub). 136 lines, 5501 chars.
- Locatable key lines:
  - Lines 4-8: definition/scope — "MCP is an open protocol that enables seamless integration between LLM applications and external data sources and tools... standardized way to connect LLMs with the context they need."
  - Lines 25-31 (Overview): design goals — MCP provides standardized way for applications to (1) share contextual information with language models, (2) expose tools and capabilities to AI systems, (3) build composable integrations and workflows.
  - Lines 32-38: architecture — uses JSON-RPC 2.0; Hosts (LLM applications initiating connections), Clients (connectors within the host), Servers (services providing context and capabilities).
  - Lines 40-45: design inspiration — takes inspiration from Language Server Protocol; "MCP standardizes how to integrate additional context and tools into the ecosystem of AI applications."
  - Lines 54-66 (Key Details / Features): Servers offer Resources (context/data), Prompts (templated messages/workflows), Tools (functions for the AI model to execute); Clients offer Sampling, Roots, Elicitation.

## Relationship to A2A (existing material m1)
- The two MCP official documents above do NOT mention A2A; the MCP↔A2A relationship framing comes from A2A's official doc (m1, https://a2a-protocol.org/latest/, lines 93-98 and ~126): "MCP and A2A are not competitors — they are highly complementary"; MCP is the agent→tool/data layer, A2A is the agent→agent layer. This framing is a first-party statement from the A2A project (S1), not from MCP's spec. Outline should present the complimentarity as jointly supported by m1 (A2A side) plus the MCP-native scope from m2/m3 (MCP covers agent-to-tool/resource, leaving agent-to-agent uncovered).

## Coverage vs stop condition
- MCP definition: m2 L4, m3 L4-8. ✓
- MCP scope/positioning (agent-to-tool standardization): m2 L5-12, m3 L4-8, L25-31. ✓
- MCP design goals: m3 L25-45. ✓
- Symmetric A2A-side positioning for comparison: m1 L8-11, L93-98, L126. ✓
- A2A's own statement of relationship to A2A (from A2A side): m1. MCP-side direct mention of A2A: not present in m2/m3 — noted as an open nuance, not a blocking gap for the symmetric comparison.

## Conflicts
- None among these official sources. m2 ('open-source standard') vs m3 ('open protocol') are consistent phrasing differences.

## Remaining gaps
- No MCP-official doc explicitly discusses its relationship to A2A in the fetched pages. If the report wants an MCP-side reference to A2A, a further search (e.g., Anthropic blog or MCP changelog mentioning A2A) could add it; not required by the stop condition, which asks for understandably the A2A-MCP connection, supported by A2A-side framing (m1).

## Source nature summary
- m2 (modelcontextprotocol.io intro): first-party documentation.
- m3 (specification 2025-06-18): first-party authoritative protocol specification.
- m1 (a2a-protocol.org/latest, from pre-research): first-party A2A project documentation (Google-donated, Linux Foundation).
