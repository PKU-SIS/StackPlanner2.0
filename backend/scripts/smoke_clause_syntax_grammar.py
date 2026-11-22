"""Explicit real-service probe of the native CLAUSE syntax-repair graph.

The malformed candidate is a public synthetic fixture, not a production model
sample. The service generates every subsequent response; the host never repairs
or substitutes response bytes. No endpoint is contacted until run_probe/main.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from deerflow.agents.thread_state import ThreadState
from deerflow.models.vllm_provider import VllmChatModel
from deerflow.sp.evidence_quotes import collect_quote_review_diagnostics
from deerflow.sp.public_format_repair import PublicArtifactFormatRepairMiddleware
from deerflow.sp.semantic_review import collect_semantic_review_diagnostics
from deerflow.sp.source_prefetch import SourcePrefetchSettings, SourcePrefetchVisibilityMiddleware
from deerflow.sp.subagents.adapter import SPSubagentTask
from deerflow.sp.subagents.dr2_adapter import normalize_dr2_subagent_result, render_sp_subagent_prompt
from deerflow.sp.syntax_grammar import collect_syntax_grammar_diagnostics

SOURCE_PATH = "/mnt/user-data/uploads/document.txt"
SOURCE = 'The definition says "Information");tion");\nPayment is due on 1 January.'
MALFORMED = json.dumps([{"text": SOURCE, "explanation": "The definition contains an incomplete fragment."}]).replace(';tion\\");', ';tion");')


async def run_probe(*, base_url, model_name, tokenizer, max_tokens=1024, mode="repair", context_tokens=65536, timeout=180, syntax_grammar_backend="sglang", http_async_client=None):
    if os.environ.get("SP_CLAUSE_SYNTAX_GRAMMAR") != "1":
        raise ValueError("Set SP_CLAUSE_SYNTAX_GRAMMAR=1 explicitly for this diagnostic")
    public = {
        "delivery_mode": "artifact",
        "output_parse_mode": "json",
        "output_schema": {"type": "array", "items": {"type": "object", "properties": {"text": {"type": "string"}, "explanation": {"type": "string"}}, "required": ["text", "explanation"], "additionalProperties": False}},
        "source_requirements": {"audit_agent": "reporter", "files": [{"path": SOURCE_PATH, "total_lines": 2}], "document_ids": []},
    }
    task_text = "Audit the complete synthetic document for supported issues; return the exact public schema."
    if mode == "partial":
        task_text += " A second agreement required for the substantive comparison is unavailable. Use the ordinary honest native partial/blocked envelope with that concrete gap."
    elif mode == "length":
        task_text += " Preserve the complete original quoted passage in any supported finding. This diagnostic intentionally supplies a very small existing response budget."
    task = SPSubagentTask(action_id="syntax-service-probe", subagent_type="reporter", task=task_text, description="Public synthetic transport diagnostic", context_refs={"task_contract": public}, metadata={"skill_names": ["clause"]})

    @tool
    def read_file(path: str) -> str:
        """Read the unchanged public synthetic source."""
        if path != SOURCE_PATH:
            return "Error: no such synthetic source"
        return SOURCE

    call = {"name": "read_file", "args": {"path": SOURCE_PATH}, "id": "synthetic-source-read", "type": "tool_call"}
    source_pair = [AIMessage(content="", tool_calls=[call]), read_file.invoke(call)]
    candidate = AIMessage(content=MALFORMED, id="synthetic-malformed-final")
    captured = [message.model_dump() for message in [*source_pair, candidate]]
    # Turn on the existing independent reviews only while constructing this
    # standalone probe; preserve the caller's environment on exit.
    review_flags = ("SP_CLAUSE_SEMANTIC_REVIEW", "SP_CLAUSE_QUOTE_REVIEW")
    saved = {name: os.environ.get(name) for name in review_flags}
    for name in review_flags:
        os.environ[name] = "1"
    try:
        check = PublicArtifactFormatRepairMiddleware(public, action_id=task.action_id, auditor_role="reporter", skill_names=["clause"], captured_sources=lambda: captured)
        seed = {"messages": [HumanMessage(content=render_sp_subagent_prompt(task)), *source_pair, candidate]}
        update = check.after_model(seed, None)
        if not update or update.get("jump_to") != "model":
            raise RuntimeError("Synthetic fixture did not enter the ordinary native format-repair budget")
        seed["messages"][-1] = update["messages"][0]
        response_count = 0

        class Capture(AgentMiddleware):
            def after_model(self, state, runtime):
                nonlocal response_count
                response_count += 1
                captured.append(state["messages"][-1].model_dump())

            async def aafter_model(self, state, runtime):
                self.after_model(state, runtime)

        kwargs = {"http_async_client": http_async_client} if http_async_client is not None else {}
        model = VllmChatModel(
            model=model_name,
            base_url=base_url,
            api_key=os.environ.get("OPENAI_API_KEY", "EMPTY"),
            max_tokens=max_tokens,
            temperature=0.0,
            top_p=1.0,
            timeout=timeout,
            max_retries=0,
            streaming=False,
            syntax_grammar_backend=syntax_grammar_backend,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            **kwargs,
        )
        visibility = SourcePrefetchVisibilityMiddleware(
            captured, contract=public, settings=SourcePrefetchSettings(tokenizer=tokenizer, context_tokens=context_tokens, response_tokens=max_tokens), auditor_role="reporter", skill_names=["clause"], syntax_scope=task.action_id
        )
        graph = create_agent(model=model, tools=[read_file], middleware=[check, Capture(), visibility], state_schema=ThreadState)
        state = await graph.ainvoke(seed)
        raw = state["messages"][-1].content
        normalized = normalize_dr2_subagent_result(SimpleNamespace(status="completed", result=raw, ai_messages=captured), task=task)
        return {
            "fixture": "public synthetic malformed candidate, not a production sample",
            "mode": mode,
            "service_model_responses": response_count,
            "raw_final": raw,
            "completion_status": normalized.artifact_metadata.get("completion_status"),
            "stop_reason": normalized.stop_reason,
            "finish_reason": state["messages"][-1].response_metadata.get("finish_reason"),
            "syntax_grammar": collect_syntax_grammar_diagnostics(captured),
            "semantic_review": collect_semantic_review_diagnostics(captured, finalize=True),
            "quote_review": collect_quote_review_diagnostics(captured),
        }
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--tokenizer", required=True, help="Local tokenizer used to verify exact request context headroom")
    parser.add_argument("--syntax-grammar-backend", choices=["sglang"], required=True)
    parser.add_argument("--mode", choices=["repair", "partial", "length"], default="repair")
    parser.add_argument("--max-tokens", type=int, default=1024)
    parser.add_argument("--context-tokens", type=int, default=65536)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(
        run_probe(
            base_url=args.base_url, model_name=args.model, tokenizer=args.tokenizer, syntax_grammar_backend=args.syntax_grammar_backend, mode=args.mode, max_tokens=args.max_tokens, context_tokens=args.context_tokens, timeout=args.timeout
        )
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "final.txt").write_bytes(report.pop("raw_final").encode("utf-8"))
    (args.output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))
    if args.mode == "length" and report["stop_reason"] != "token_capped":
        raise SystemExit("The service did not reach length in this probe; no retry was made")


if __name__ == "__main__":
    main()
