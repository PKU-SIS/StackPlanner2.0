# Research observation — gap g2 (A2A innovation and problems)

Report r1, action spact_call_97936b7f7aeb45478f8bd318, gap g2. Question: A2A's innovative aspects relative to existing approaches (esp. MCP) and specific problems it is designed to solve (agent interconnectivity, interoperability, delegation, security non-transparency).

## Sources added this attempt

### m4 (S4) — A2A and MCP: deeper dive (A2A Protocol official spec)
- Path: /mnt/user-data/outputs/report/r1/materials/S4/d0f076ebe5bcec631a32a4273b7aaca1b2a937f22baa0c868462b902c9fbe906.txt
- URL: https://a2a-protocol.org/latest/topics/a2a-and-mcp/
- Nature: **first-party protocol specification** (A2A open project, Linux Foundation). Located from m1/S1's explicit reference to this subtopic (S1 line 86-88).
- Upstream_truncation: false; 147 lines, 8111 chars.
- Chinese/Japanese not provided (reused existing m1).

#### Innovation evidence (node n9) — locatable lines
- Lines 14-24: "MCP is vertical" (deepens a single agent); "A2A is horizontal" — connects agents across the boundary, other agent may belong to another team, department, or partner organization; your agent discovers them, negotiates, and exchanges information. → A2A innovates as the **horizontal agent-to-agent layer**.
- Line 33: "MCP gives each agent depth, and A2A gives your system reach."
- Lines 53-61: A2A capabilities: standardizes how **independent, often opaque, AI agents communicate and collaborate as peers**; gives agents an **application-level protocol** (discover each other, negotiate interactions, manage shared tasks, exchange conversational context and complex data).
- Lines 143-145: A2A's main strength is **flexible, stateful, collaborative interactions that go beyond a typical tool call**; "A2A is about agents *partnering* on tasks; MCP is more about agents *using* capabilities."
- Complementarity/division of labor for n6/n14: lines 86-94 ("A2A connects the agents to each other; MCP connects each agent to its own tools"), lines 69 (both essential; distinction depends on what an agent interacts with), lines 71-84 (tools are primitives with structured IO vs agents are autonomous, reasoned, stateful, multi-turn dialogue systems).

#### Problems A2A addresses (node n11) — locatable lines
- Lines 53-64: enables different agents to **collaborate to reach a common goal**; use cases: a customer-service agent delegates an inquiry to a billing agent; a travel agent coordinates with flight, hotel, and activity agents (interoperability across independently built agents).
- Lines 16-24: addresses the problem that agents live in different teams/departments/partner orgs and need to discover, negotiate, and exchange info across organizational boundaries.
- Lines 57-58: specifically supports **opaque** AI agents communicating as peers (security/non-transparency dimension — agents do not need to share internals).
- Lines 96-138 (auto-repair-shop worked example): Shows the concrete problem decomposition — user-to-agent via A2A; diagnostic conversation agent-to-agent via A2A; internal tool use via MCP; supplier agent-to-agent via A2A; order processing via A2A. A2A handles higher-level conversational task-oriented interactions linking agents; MCP handles per-agent structured tool use.

## Rationale / how this closes gap g2
m1/S1 (lines 27-38) already listed A2A Key Features (interoperability, complex workflows, secure & opaque, extensible) and lines 93-98 complementarity. m4/S4 now adds the authoritative **horizontal-vs-vertical** framing, the **problems/use-cases** (delegation, coordination, cross-org reach), the **opaque peer communication** motivation, and the worked delegation example. Together these support n9 (innovation), n11 (problems), and n14 (summary) with first-party locatable evidence.

## Reported technical failures (not evidence of source absence)
- developers.googleblog.com/en/a2a-a-new-era-of-agent-interoperability/ → ConnectError (fetch_failed). Also failed with timeout in pre-research pass.
- developers.googleblog.com/ja/... mirror → Fetch timed out after 20s.
- web_search → WEB_SEARCH_UNAVAILABLE (search backend transport error).
The Google announcement remains **not saved / not fetchable** this run due to persistent infrastructure issues; silently retried EN then attempted JA mirror. No material was saved from Google Blog; do not cite it as saved. The gap brief's stop condition (first-party locatable statement of A2A innovations and problems) is met by m4 (S4) plus m1 (S1), but whether this satisfies Outline's sufficiency (incl. the Google announcement) is Outline's decision.

## Remaining gaps / notes for Outline
- Google official announcement text not saved (technical errors). If Outline requires the announcement specifically, a later round should retry the fetch.
- Only the deep-dive page and main page from a2a-protocol.org were used; MCP official sources m2/S2, m3/S3 were saved by a concurrent action (not this gap's attribution).
