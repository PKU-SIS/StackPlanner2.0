"""Probe the actual configured Qwen client, including a tool round-trip."""

import asyncio
import json
import time
from pathlib import Path

from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool

from .runner import HOME, setup_env, write


async def probe(directory, model="qwen3-8-27b", extra_env=None):
    setup_env(directory, extra_env)
    from app.experiments.tender_audit.observability import AuditTrace
    from app.experiments.tender_audit.runner import experiment_config, request_deadline_environment
    from app.experiments.tender_audit.timing import timeout_multiplier_environment
    from deerflow.models import create_chat_model

    started = time.monotonic()
    with timeout_multiplier_environment(2), request_deadline_environment(300, no_progress_timeout=600):
        config = experiment_config(model, max_tokens=1024)
        trace = AuditTrace(directory, secrets=(str(config.models[0].model_extra.get("api_key", "")),))
        model_client = create_chat_model(name=model, thinking_enabled=True, app_config=config, attach_tracing=False, max_tokens=1024)
        try:
            plain = await asyncio.wait_for(model_client.ainvoke([HumanMessage(content="连通性测试，请只回复OK。")], config={"callbacks": [trace]}), timeout=300)
            write(Path(directory) / "text.json", trace.safe(plain))

            def echo(value: str) -> str:
                return value

            tool = StructuredTool.from_function(echo, name="collusion_probe", description="Connectivity probe: return value unchanged.")
            client = model_client.bind_tools([tool])
            prompt = [HumanMessage(content="工具连通性测试。必须调用collusion_probe，value填ok。")]
            first = await asyncio.wait_for(client.ainvoke(prompt, config={"callbacks": [trace]}), timeout=300)
            assert first.tool_calls and first.tool_calls[0]["name"] == "collusion_probe", "No valid tool call returned"
            answer = ToolMessage(content="ok", tool_call_id=first.tool_calls[0]["id"])
            follow = await asyncio.wait_for(client.ainvoke([*prompt, first, answer], config={"callbacks": [trace]}), timeout=300)
            write(Path(directory) / "tool_roundtrip.json", trace.safe({"request": prompt, "tool_call": first, "tool_result": answer, "response": follow}))
            status = {"passed": True, "model": model, "duration_seconds": time.monotonic() - started, "metrics": trace.metrics(time.monotonic() - started)}
        except Exception as exc:
            status = {"passed": False, "model": model, "error": trace.safe(type(exc).__name__ + ": " + str(exc)), "duration_seconds": time.monotonic() - started}
        write(Path(directory) / "preflight.json", status)
        return status


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--directory", type=Path, default=HOME / "native-preflight")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--model", default="qwen3-8-27b")
    args = parser.parse_args()
    result = asyncio.run(probe(args.directory, args.model, args.env_file))
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result["passed"] else 1)
