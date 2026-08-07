# SA 报告流水线失败诊断与 Bocha 检索恢复方案

日期：2026-08-07

## 1. 目标链路

当前希望 StackPlanner2 在 SA 式深度研究报告任务中稳定形成三段流水线：

```text
researcher -> Research Summary artifact
outline    -> ScopeTree outline artifact
reporter   -> final report_revision artifact
```

每一步都必须产出可被下一步机器消费的结构化 artifact，而不是只依赖自然语言状态。

## 2. 本次失败的真实断点

这次“国际综合实力前十保险公司”任务没有失败在报告写作本身，而是失败在 reporter 之前。

实际链路变成：

```text
researcher -> partial research_observation
outline    -> No response generated / 未落盘 outline artifact
reporter   -> 因缺少 outline artifact 无法进入标准 SA 报告路径
```

### 2.1 researcher 的问题

researcher 需要采集强实时、一手来源数据：

- 公司年报 / IR 页面；
- 评级机构信息；
- 发债、资本充足率、偿付能力、再保险安排；
- 股息、五年资产 / 保费 / 营收 / 净利润增长；
- 在华布局与监管准入。

本次在线检索和网页抓取不稳定，搜索预算被耗尽，Jina fetch 多次失败，最终只收集到排名类证据。Research Summary 因此是 `partial`，不能完整支撑最终报告。

### 2.2 outline 的问题

outline 被要求输出：

```json
{
  "artifact_type": "outline",
  "artifact_content": "{ScopeTree JSON}",
  "artifact_metadata": {
    "evidence_map": {},
    "agm_state": {}
  }
}
```

但本次 outline 子任务返回 `No response generated`。当前 runtime 会把这种情况包装成 completed，而不是硬失败。由于没有 `artifact_content`，delegate 层不会写 outline artifact。

这是最关键的链路断点：**outline artifact contract 没有被 runtime 强制执行**。

### 2.3 reporter 的情况

V1.2 的 reporter guard 要求先存在 current outline artifact，并且 metadata 中有 `evidence_map` 和 `agm_state`。这次 reporter 被挡住是正确行为。否则 reporter 会基于 partial summary 和模型记忆硬写报告，产生“看似完成、实际没走 SA 链路”的假成功。

## 3. 必须修的产物验收规则

### 3.0 当前日志能看到什么、看不到什么

目前并不是完全没有日志，但日志粒度不够解释 `No response generated` 的根因。

现有链路会记录：

- subagent executor logger warning：
  - `Subagent {name} no final state`
  - `Subagent {name} final messages count: N`
  - `Subagent {name} no AIMessage found`
- delegate observe memory：
  - `stop_reason`
  - `completion_status`
  - `evidence_gaps`
  - `target_agent`
  - `task_id`
- run_events：
  - `sp.delegate.completed`
  - `sp.delegate.partial`
  - `sp.delegate.failed`
  - `sp.artifact.created`
  - `sp.artifact.current_changed`

但是当前缺少一个结构化的“空输出诊断对象”。也就是说，系统知道最后展示了 `No response generated`，但没有把下面这些分支稳定落到 run_events / memory metadata：

```text
missing_final_state
empty_messages
missing_ai_message
empty_ai_content
message_content_to_text_empty
token_capped
turn_capped
loop_capped
tool_call_only_final_message
artifact_contract_missing
```

因此现在很难区分：

- 是输出太长被截断；
- 是模型只发了 tool call 没有最终正文；
- 是 LangGraph 没产出 final_state；
- 是 JSON 输出被解析/抽取为空；
- 是工具不可用导致 agent 没收束。

需要补的不是更多自然语言日志，而是一个机器可查的字段，例如：

```json
{
  "subagent_output_diagnostics": {
    "empty_result_reason": "empty_ai_content",
    "final_state_present": true,
    "message_count": 4,
    "ai_message_count": 1,
    "last_message_type": "AIMessage",
    "last_ai_content_chars": 0,
    "last_ai_tool_call_count": 1,
    "stop_reason": null,
    "token_usage": {
      "input_tokens": 0,
      "output_tokens": 0,
      "total_tokens": 0
    }
  }
}
```

这个诊断对象应该进入：

- `SPSubagentResult.artifact_metadata["output_diagnostics"]`；
- delegate observe memory metadata；
- `sp.delegate.completed` / `sp.delegate.partial` / `sp.delegate.failed` event payload。

### 3.1 researcher artifact 验收

researcher 可以 partial，但必须结构化 partial，不能只给一段说明。

最低要求：

- `artifact_type="research_observation"`；
- `artifact_content` 包含：
  - compact Research Summary；
  - Evidence Ledger；
  - Numeric Claim Map；
  - seed evidence；
- `artifact_metadata` 包含：
  - `completion_status`；
  - `source_refs`；
  - `evidence_ledger`；
  - `numeric_claim_map`；
  - `evidence_gaps`；
  - `pending_queries`。

如果搜索失败，必须把缺口落在 `evidence_gaps` / `pending_queries`，而不是让下一步猜。

### 3.2 outline artifact 验收

outline 没有生成有效 artifact 时必须 `error_recoverable`，不能按 completed 继续。

最低检查：

- `result.artifact_content` 非空；
- `result.artifact_type == "outline"`；
- `artifact_metadata.evidence_map` 是非空 mapping；
- `artifact_metadata.agm_state` 是非空 mapping；
- `artifact_content` 可解析为 ScopeTree JSON，或者至少包含明确 ScopeTree 顶层结构。

失败时返回 recoverable error，并写入 stack memory：

```text
Outline delegate completed without a valid outline artifact. Retry outline with raw JSON output contract.
```

推荐错误 metadata：

```json
{
  "contract": "outline_artifact_required",
  "missing": ["artifact_content", "evidence_map", "agm_state"],
  "raw_result": "No response generated",
  "output_diagnostics": {
    "empty_result_reason": "empty_ai_content"
  }
}
```

### 3.3 reporter artifact 验收

reporter 必须只在以下条件满足时运行：

- current `research_observation` 存在；
- current `outline` 存在；
- outline metadata 有 `evidence_map` / `agm_state`；
- reporter input_refs 包含 research + outline + evidence bodies；
- first draft 不加载 `scaffold-quality-gate`。

报告为空、partial 或无 artifact 时，不重复 full-report。应进入 section fallback：

```text
section_draft per first-level ScopeTree section -> report_merge
```

## 4. Bocha / LangSearch 检索恢复方案

### 4.1 现有可用代码

`scaffold_agent_lite` 中已经有轻量 Bocha retriever：

```text
scaffold_agent_lite/src/scaffold_agent/retriever.py
```

核心类：

```python
BochaWebRetriever
```

它调用：

```text
https://api.langsearch.com/v1/web-search
```

输入：

```json
{
  "query": "...",
  "freshness": "noLimit",
  "summary": true,
  "count": 5,
  "page": 1
}
```

输出会转成 `ResearchDoc`，包含 title、url、summary/snippet、retrieved_by。

### 4.2 本地连通性测试结论

直接使用当前 shell 环境代理时，Bocha 请求失败：

```text
ProxyError: Tunnel connection failed: 500 Internal Server Error
```

但使用 `requests.Session().trust_env = False` 绕过环境代理后，请求成功：

```text
status 200
count 3
```

示例 query：

```text
Allianz 2024 annual report dividend solvency ratio
```

返回了 Allianz SFCR 2024 等长 summary 结果。

结论：**Bocha/LangSearch API 本身可用；当前失败来自环境代理，而不是 Bocha 不可用。**

### 4.3 SP2 最小接入方式

推荐不要把整个 `scaffold_agent_lite` pipeline 搬进 SP2。最小改法是在 DeerFlow 增加一个 Bocha community provider：

```text
backend/packages/harness/deerflow/community/bocha/tools.py
backend/packages/harness/deerflow/community/bocha/__init__.py
backend/tests/test_bocha_tools.py
```

工具名仍然暴露为：

```text
web_search
```

这样 researcher 不需要改调用习惯，只需要修改 config：

```yaml
tools:
  - name: web_search
    group: web
    display_name: Web Search
    use: deerflow.community.bocha.tools:web_search_tool
    api_key: $BOCHA_API_KEY
    max_results: 5
    trust_env: false
```

`trust_env: false` 是关键，避免再次走坏代理。

### 4.4 Bocha provider 行为要求

Bocha provider 应返回 JSON 字符串，结构尽量接近现有 Serper / Brave provider：

```json
{
  "query": "...",
  "results": [
    {
      "title": "",
      "url": "",
      "snippet": "",
      "summary": "",
      "site_name": "",
      "date_published": ""
    }
  ]
}
```

要求：

- 从 config 或 `BOCHA_API_KEY` 读取密钥；
- 支持 `max_results`；
- 支持 `trust_env`；
- HTTP 错误返回结构化 error JSON；
- 对 URL 做基本 public URL 清洗；
- 不泄漏 API key；
- 不把 “no results” 伪装成成功证据。

### 4.5 Bocha provider 代码草案

实现风格应对齐现有 provider：

- Serper: `backend/packages/harness/deerflow/community/serper/tools.py`
- Brave: `backend/packages/harness/deerflow/community/brave/tools.py`
- Lite Bocha retriever: `scaffold_agent_lite/src/scaffold_agent/retriever.py`

建议新增：

```text
backend/packages/harness/deerflow/community/bocha/__init__.py
backend/packages/harness/deerflow/community/bocha/tools.py
backend/tests/test_bocha_tools.py
```

`__init__.py`：

```python
from .tools import web_search_tool

__all__ = ["web_search_tool"]
```

`tools.py` 最小实现草案：

```python
"""Web search tool powered by Bocha / LangSearch."""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx
from langchain.tools import tool

from deerflow.config import get_app_config

logger = logging.getLogger(__name__)

_BOCHA_ENDPOINT = "https://api.langsearch.com/v1/web-search"
_DEFAULT_MAX_RESULTS = 5
_MAX_RESULTS = 10
_api_key_warned = False


def _tool_config_extra(tool_name: str = "web_search") -> dict[str, Any]:
    config = get_app_config().get_tool_config(tool_name)
    return dict(config.model_extra or {}) if config is not None else {}


def _get_api_key(tool_name: str = "web_search") -> str | None:
    extra = _tool_config_extra(tool_name)
    value = extra.get("api_key")
    if isinstance(value, str) and value.strip():
        return value.strip()
    value = os.getenv("BOCHA_API_KEY") or os.getenv("LANGSEARCH_API_KEY")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _coerce_max_results(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return _DEFAULT_MAX_RESULTS
    return max(1, min(parsed, _MAX_RESULTS))


def _coerce_bool(value: object, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _clean_query(query: str) -> str:
    query = query.strip()
    if len(query) > 500:
        query = query[:500]
    return query


def _missing_key_error(query: str) -> str:
    global _api_key_warned
    if not _api_key_warned:
        _api_key_warned = True
        logger.warning(
            "Bocha/LangSearch API key is not set. Set BOCHA_API_KEY or provide api_key in config.yaml."
        )
    return json.dumps(
        {"error": "BOCHA_API_KEY is not configured", "query": query},
        ensure_ascii=False,
    )


def _request_error(query: str, message: str) -> str:
    return json.dumps({"error": message, "query": query}, ensure_ascii=False)


def _normalize_items(data: dict[str, Any], max_results: int) -> list[dict[str, str]]:
    raw_items = (
        ((data.get("data") or {}).get("webPages") or {}).get("value")
        if isinstance(data.get("data"), dict)
        else None
    )
    if not isinstance(raw_items, list):
        return []
    results: list[dict[str, str]] = []
    for item in raw_items[:max_results]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("name") or item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        snippet = str(item.get("snippet") or "").strip()
        summary = str(item.get("summary") or "").strip()
        if not title and not url and not snippet and not summary:
            continue
        results.append(
            {
                "title": title,
                "url": url,
                "content": summary or snippet,
                "snippet": snippet,
                "summary": summary,
                "site_name": str(item.get("siteName") or "").strip(),
                "date_published": str(
                    item.get("datePublished") or item.get("dateLastCrawled") or ""
                ).strip(),
            }
        )
    return results


@tool("web_search", parse_docstring=True)
def web_search_tool(query: str, max_results: int = _DEFAULT_MAX_RESULTS) -> str:
    """Search the web using Bocha / LangSearch.

    Args:
        query: Search keywords describing what you want to find.
        max_results: Maximum number of search results to return. Default is 5, capped at 10.
    """
    extra = _tool_config_extra("web_search")
    if "max_results" in extra:
        max_results = extra.get("max_results", max_results)
    max_results = _coerce_max_results(max_results)
    query = _clean_query(query)

    api_key = _get_api_key("web_search")
    if not api_key:
        return _missing_key_error(query)

    timeout = float(extra.get("timeout", 60.0) or 60.0)
    # Important: local environment proxy can break LangSearch with HTTP 500.
    # Default false so deployments must explicitly opt into ambient proxies.
    trust_env = _coerce_bool(extra.get("trust_env"), False)
    freshness = str(extra.get("freshness") or "noLimit")

    payload = {
        "query": query,
        "freshness": freshness,
        "summary": True,
        "count": max_results,
        "page": 1,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=timeout, trust_env=trust_env) as client:
            response = client.post(_BOCHA_ENDPOINT, headers=headers, json=payload)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Bocha/LangSearch API returned HTTP %s: %s",
            exc.response.status_code,
            (exc.response.text or "")[:500],
        )
        return _request_error(
            query,
            f"Bocha/LangSearch API error: HTTP {exc.response.status_code}",
        )
    except Exception as exc:
        logger.error("Bocha/LangSearch request failed: %s: %s", type(exc).__name__, str(exc)[:500])
        return _request_error(query, f"{type(exc).__name__}: {str(exc)[:500]}")

    if not isinstance(data, dict):
        return _request_error(query, "Bocha/LangSearch returned an unexpected response format")

    results = _normalize_items(data, max_results)
    if not results:
        return json.dumps({"error": "No results found", "query": query}, ensure_ascii=False)

    return json.dumps(
        {
            "query": query,
            "total_results": len(results),
            "results": results,
        },
        indent=2,
        ensure_ascii=False,
    )
```

实现注意点：

- 不要复用 `scaffold_agent_lite` 的 `ResearchDoc` dataclass，DeerFlow tool 只需要返回 JSON 字符串。
- 默认 `trust_env=False`，因为本地已验证环境代理会导致 `Tunnel connection failed: 500 Internal Server Error`。
- Bocha 返回的 `summary` 可能很长。先保留完整 summary，后续如果 tool output 过大，再由已有 `ToolOutputBudgetMiddleware` 截断。
- `content` 字段放 `summary or snippet`，兼容 researcher 已习惯的 web_search result 格式。
- API key 只从 config/env 读取，错误日志不能输出 key。

### 4.6 Bocha provider 配置修改

当前 `StackPlanner2/config.yaml` 使用：

```yaml
tools:
  - name: web_search
    group: web
    display_name: Web Search
    use: deerflow.community.ddg_search.tools:web_search_tool
    max_results: 5

  - name: web_fetch
    group: web
    display_name: Web Fetch
    use: deerflow.community.jina_ai.tools:web_fetch_tool
```

建议改为：

```yaml
tools:
  - name: web_search
    group: web
    display_name: Web Search
    use: deerflow.community.bocha.tools:web_search_tool
    api_key: $BOCHA_API_KEY
    max_results: 5
    timeout: 60.0
    trust_env: false
    freshness: noLimit

  - name: web_fetch
    group: web
    display_name: Web Fetch
    use: deerflow.community.direct_fetch.tools:web_fetch_tool
    timeout: 30.0
    max_chars: 30000
    trust_env: false
```

说明：

- `web_search` 切 Bocha，解决 DDG/Jina 不稳定时 researcher 没有足够搜索结果的问题。
- `web_fetch` 建议从 Jina 切到 direct_fetch 做官方 PDF/IR 页面兜底。Jina 可以后续作为另一个 fetch provider，但本次失败里 Jina 是明显不稳定点。
- 如果仍希望保留 Jina，可先只改 `web_search`，但 researcher 要被提示：Bocha summary 已足够时优先用 search result summary 建 Evidence Ledger，不要强依赖 fetch。

### 4.7 Bocha provider 测试建议

新增 `backend/tests/test_bocha_tools.py`：

```python
import json

import httpx
import pytest


def test_bocha_web_search_returns_results(monkeypatch):
    from deerflow.community.bocha import tools

    class DummyClient:
        def __init__(self, *, timeout, trust_env):
            assert trust_env is False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, url, headers, json):
            assert url == tools._BOCHA_ENDPOINT
            assert headers["Authorization"].startswith("Bearer ")
            assert json["summary"] is True
            return httpx.Response(
                200,
                json={
                    "data": {
                        "webPages": {
                            "value": [
                                {
                                    "name": "Allianz Group SFCR 2024",
                                    "url": "https://www.allianz.com/example.pdf",
                                    "snippet": "Solvency report",
                                    "summary": "Long summary",
                                    "siteName": "Allianz",
                                    "datePublished": "2025-03-01",
                                }
                            ]
                        }
                    }
                },
            )

    monkeypatch.setenv("BOCHA_API_KEY", "test-key")
    monkeypatch.setattr(httpx, "Client", DummyClient)

    result = json.loads(tools.web_search_tool.invoke({"query": "Allianz 2024 SFCR"}))

    assert result["total_results"] == 1
    assert result["results"][0]["title"] == "Allianz Group SFCR 2024"
    assert result["results"][0]["content"] == "Long summary"


def test_bocha_web_search_missing_key(monkeypatch):
    from deerflow.community.bocha import tools

    monkeypatch.delenv("BOCHA_API_KEY", raising=False)
    monkeypatch.delenv("LANGSEARCH_API_KEY", raising=False)

    result = json.loads(tools.web_search_tool.invoke({"query": "test"}))

    assert result["error"] == "BOCHA_API_KEY is not configured"


def test_bocha_web_search_no_results(monkeypatch):
    # mock HTTP 200 with empty webPages.value and assert {"error": "No results found"}
    ...


def test_bocha_web_search_http_error(monkeypatch):
    # mock response.raise_for_status() raising HTTPStatusError
    # assert structured error JSON, no exception escapes
    ...
```

测试重点：

- `trust_env` 默认是 false；
- 成功响应结构能标准化为 `query/total_results/results`；
- missing key / no results / HTTP error 都返回 JSON error；
- 不把 API key 写进 output。

### 4.8 researcher 使用 Bocha 后的输出要求

Bocha 搜索返回长 summary 后，researcher 应直接把结果整理进 Evidence Ledger：

```json
{
  "source_id": 1,
  "citation": "[1]",
  "title": "Allianz Group Solvency and Financial Condition Report 2024",
  "url": "https://...",
  "publisher": "Allianz",
  "published_at": "2025",
  "source_type": "official",
  "claim_summary": "Includes solvency and capital information for FY2024.",
  "used_for": ["capital adequacy", "financing", "credibility"]
}
```

关键点：

- researcher 不需要等 `web_fetch` 成功才产 Summary；Bocha summary 足够时可以先形成 partial Evidence Ledger。
- 对年报 / SFCR / IR 页面这类结果，优先标为 `official`。
- 对媒体/二级来源，标为 `media` 或 `other`，不能和官方来源同权重。
- 如果 Bocha search 有结果但 fetch 失败，`evidence_gaps` 应写成“未抓取全文”，而不是“无证据”。

## 5. 让 researcher 生成 Summary 的实际策略

仅换搜索源不够，researcher prompt / skill 也要把 Bocha 结果压成 SA 可用的事实层。

建议 researcher 对复杂报告任务按固定 query families 检索：

```text
1. ranking basis:
   "largest global insurance companies 2024 total assets premiums market cap ranking"

2. company annual reports:
   "{company} 2024 annual report total assets premiums revenue net income dividend"

3. capital / solvency:
   "{company} 2024 solvency financial condition report capital ratio debt issuance"

4. credit rating:
   "{company} financial strength rating S&P Moody's Fitch AM Best 2024 2025"

5. China footprint:
   "{company} China insurance subsidiary joint venture license 2024 2025"

6. dividend:
   "{company} dividend per share 2024 2025 investor relations"
```

输出必须固定为：

```json
{
  "research_summary": "...",
  "evidence_ledger": [],
  "numeric_claim_map": [],
  "seed_evidence": [],
  "evidence_gaps": [],
  "pending_queries": []
}
```

如果某家公司某个维度没证据，不要补模型记忆；写入缺口，例如：

```json
{
  "company": "AXA",
  "metric": "2021-2025 net income CAGR",
  "gap": "No verified company annual-report series collected",
  "pending_query": "AXA annual report 2021 2022 2023 2024 net income dividend"
}
```

## 6. 让 outline 生成大纲的实际策略

outline 只吃 researcher 的 artifact，不搜索。

输入：

- user request；
- Research Summary；
- Evidence Ledger；
- Numeric Claim Map；
- evidence_gaps；
- pending_queries。

输出：

```json
{
  "artifact_type": "outline",
  "artifact_content": {
    "scope_tree": {
      "id": "root",
      "title": "...",
      "children": []
    }
  },
  "artifact_metadata": {
    "completion_status": "complete|partial|blocked",
    "format": "scope_tree_json",
    "evidence_map": {},
    "evidence_gaps": [],
    "agm_state": {
      "active_nodes": [],
      "evidence_gaps": [],
      "pending_queries": [],
      "expansion_targets": [],
      "utility_signals": []
    }
  }
}
```

对证据缺失的叶子，outline 应在 `evidence_map` 或 `evidence_gaps` 中明确标记：

```json
{
  "leaf_id": "comparison.dividend.allianz",
  "company": "Allianz",
  "metric": "dividend",
  "status": "gap",
  "node_path": "横向比较 / 实际分红 / Allianz"
}
```

## 7. 让 reporter 生成报告的实际策略

reporter 只在 artifact guard 通过后运行。

输入必须包含：

- research_observation ref；
- outline ref；
- Evidence Ledger；
- Numeric Claim Map；
- materialized evidence bodies。

first draft skill：

```json
["stackplanner-reporting", "scaffold-reporting"]
```

不要加载：

```json
["scaffold-quality-gate"]
```

报告规则：

- 使用 `[n]` 稳定引用；
- 不引用 ScopeTree 当事实来源；
- 所有数字必须能回到 Numeric Claim Map；
- 对缺证维度用限制说明，不编造；
- 如果 full-report 空输出，进入 section fallback。

## 8. 建议实施顺序

### P0：防止假成功

1. 给 outline delegate 加 acceptance check。
2. `No response generated` 对 outline/reporter 不能算 complete。
3. outline 缺 `artifact_content` / `evidence_map` / `agm_state` 时返回 `error_recoverable`。
4. 增加测试覆盖。

代码建议：

```text
backend/packages/harness/deerflow/subagents/executor.py
  - _extract_final_result 不只返回字符串，还生成 output_diagnostics
  - 或新增 _extract_final_result_with_diagnostics

backend/packages/harness/deerflow/sp/subagents/dr2_adapter.py
  - normalize_dr2_subagent_result 把 output_diagnostics 放入 artifact_metadata
  - raw_result == "No response generated" 时 completion_status 不能默认为 complete

backend/packages/harness/deerflow/sp/actions/handlers/delegate.py
  - outline acceptance check
  - reporter no-artifact check 已有，扩展到 outline
  - run_events 加 output_diagnostics
```

最小实现可以先不改 executor 返回类型，先在 `normalize_dr2_subagent_result` 做兜底：

```text
if raw_result == "No response generated":
  artifact_metadata["completion_status"] = "blocked"
  artifact_metadata["evidence_gaps"].append("Subagent produced no final textual result.")
  artifact_metadata["output_diagnostics"]["empty_result_reason"] = "no_response_generated_sentinel"
```

但这只能说明“抽取为空”，不能区分为空的具体分支。完整实现还是应在 executor 抽取 final_state 时生成 diagnostics。

### P1：接入 Bocha 搜索

1. 新增 `deerflow.community.bocha.tools:web_search_tool`。
2. 支持 `BOCHA_API_KEY` 和 `trust_env=false`。
3. 把 `config.yaml` 的 `web_search` provider 从 DDG 改成 Bocha。
4. 用保险公司任务跑 researcher，只检查 Research Summary artifact，不进入 outline。

### P1：强化 researcher 输出 contract

1. Evidence Ledger 使用 `[1]`, `[2]`, `[3]`。
2. Numeric Claim Map 记录关键数字。
3. 缺口进入 `evidence_gaps` 和 `pending_queries`。

### P2：runtime 化 section fallback

1. full-report 失败后自动按 ScopeTree section 生成 `section_draft`。
2. section draft 完成后自动 `report_merge`。
3. 不完全依赖 Central prompt 自觉执行。

## 9. 验收标准

用同一个保险公司任务验收：

```text
收集整理目前国际综合实力前十的保险公司的相关资料...
```

必须看到：

```text
researcher:
  artifact_type=research_observation
  artifact_content has research_summary/evidence_ledger/numeric_claim_map

outline:
  artifact_type=outline
  artifact_content has ScopeTree JSON
  artifact_metadata has evidence_map + agm_state

reporter:
  artifact_type=report_revision
  input_refs include research_observation + outline
  metadata.source_artifact_ids is non-empty
```

失败时也必须可诊断：

```text
No response generated -> error_recoverable
missing evidence_map -> error_recoverable
search provider failure -> structured evidence_gaps + pending_queries
```

新增测试建议：

```text
test_outline_delegate_no_response_is_recoverable_error
  输入：SPSubagentResult(status=COMPLETED, result="No response generated")
  期望：next_step="error_recoverable"，无 outline artifact，run_event=sp.delegate.failed 或 sp.delegate.contract_failed

test_outline_delegate_missing_metadata_is_recoverable_error
  输入：artifact_type="outline"，artifact_content 有内容，但 metadata 缺 evidence_map/ag m_state
  期望：error_recoverable，missing 字段可查

test_no_response_generated_surfaces_output_diagnostics
  输入：executor final_state=None / empty AIMessage
  期望：artifact_metadata.output_diagnostics.empty_result_reason 有具体值

test_bocha_web_search_trust_env_false
  输入：config trust_env=false
  期望：httpx/requests 不读取环境代理，返回结构化 results 或结构化 error
```

稳定性验收不应只看最终用户有没有看到报告，而应看每一步 artifact ref：

```text
assert current_refs["research_observation"]
assert current_refs["outline"]
assert current_refs["report_revision"]
assert current_refs["outline"]["metadata"]["evidence_map"]
assert current_refs["outline"]["metadata"]["agm_state"]
```

## 9.1 稳定输出的 prompt / artifact 限制

为降低 outline 因 JSON 太长而截断的概率，outline 不应把所有公司 x 所有指标展开成超大树。

建议约束：

```text
first-level sections: 4-5
leaf_count: <= 10
per-leaf evidence refs: <= 8
per-leaf gap entries: <= 5
company x metric 明细放在 node instruction / evidence requirement 摘要中，不展开成 10 x 6 = 60 个叶子
```

对“前十保险公司 x 六维度”的任务，推荐 ScopeTree 结构：

```text
1. 前十名单与排名依据
2. 六维横向比较矩阵
3. 中国发展潜力
4. 未来资产排名展望
5. 结论与推荐 2-3 家
```

叶子里记录矩阵字段和证据要求，而不是为每家公司每个指标建一个叶子。这样 outline artifact 可以稳定落盘，reporter 再用 Evidence Ledger / Numeric Claim Map 写完整表格。

## 10. 结论

当前三步流程方向是对的，问题在于：

1. researcher 的检索源不稳定；
2. outline 的 artifact contract 没有硬验收；
3. runtime 把 `No response generated` 当 completed；
4. reporter guard 生效后暴露了前序 artifact 不稳定。

最小可行修复是：

```text
先修 outline/reporter 不能假成功
再接 Bocha web_search provider
再强化 researcher 的 Evidence Ledger / Numeric Claim Map
最后把 section fallback 从 prompt 迁到 runtime
```
