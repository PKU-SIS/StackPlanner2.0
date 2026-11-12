"""Single cloud preflight; fail once and stop, with independent trace/costs."""

import argparse
import asyncio
import base64
import io
import json
import time
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from PIL import Image

from .runner import setup_env, write


async def probe(directory, model="aliyun-qwen3.8-27b", extra_env=None, reporter_delivery=False):
    setup_env(directory, extra_env)
    from app.experiments.tender_audit.observability import AuditTrace
    from app.experiments.tender_audit.runner import experiment_config, request_deadline_environment
    from app.experiments.tender_audit.timing import timeout_multiplier_environment
    from deerflow.models import create_chat_model

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    checks = []
    with timeout_multiplier_environment(2), request_deadline_environment(1920, no_progress_timeout=2040):
        cfg = experiment_config(model, max_tokens=8192)
        secret = str(cfg.models[0].model_extra.get("api_key", ""))
        trace = AuditTrace(directory, secrets=(secret,))

        def client(thinking, tokens=8192, timeout=240):
            if reporter_delivery and thinking:
                import httpx

                local = cfg.model_copy(deep=True)
                local.models[0] = local.models[0].model_copy(update={"use": "app.experiments.collusion_audit.reporter_stream:StreamingReporterModel", "normal_output_timeout": 300, "stream_chunk_timeout": 300})
                local = type(local).model_validate(local.model_dump())
                return create_chat_model(name=model, thinking_enabled=False, app_config=local, attach_tracing=False, max_tokens=tokens, timeout=httpx.Timeout(connect=10, read=330, write=60, pool=30))
            return create_chat_model(name=model, thinking_enabled=thinking, app_config=cfg, attach_tracing=False, max_tokens=tokens, timeout=timeout)

        async def call(label, model_client, messages, timeout=300):
            t = time.monotonic()
            from .reporter_delivery import DELIVERY_CONTEXT

            token = DELIVERY_CONTEXT.set({"trace": trace, "action_id": "preflight-" + label, "normal_chars": 0}) if reporter_delivery and label.startswith("reporter_") else None
            try:
                response = await asyncio.wait_for(model_client.ainvoke(messages, config={"callbacks": [trace], "run_name": label}), timeout)
            finally:
                if token is not None:
                    DELIVERY_CONTEXT.reset(token)
            usage = response.usage_metadata or {}
            checks.append(
                {
                    "check": label,
                    "seconds": time.monotonic() - t,
                    "usage": usage,
                    "visible_chars": len(response.content),
                    "tool_calls": [t["name"] for t in response.tool_calls],
                    "returned_model": response.response_metadata.get("model_name"),
                    "finish_reason": response.response_metadata.get("finish_reason"),
                }
            )
            write(directory / "checks.json", checks)
            return response

        try:
            text = await call("identity", client(False, 64), [HumanMessage("17加25等于多少？只输出数字。")])
            assert text.content.strip() == "42", "Plain response did not match the probe"
            assert "qwen3.8-27b" in str(text.response_metadata.get("model_name", "")).lower(), "Unexpected cloud model identity"

            def cloud_probe_echo(value: str) -> str:
                """Return a harmless compatibility value unchanged."""
                return value

            echo = StructuredTool.from_function(cloud_probe_echo)
            researcher = client(False, 16384).bind_tools([echo])
            messages = [
                SystemMessage("你是工具兼容性探针。调用工具读取结果后只返回一行原始JSON对象，禁止代码围栏、前后说明或工具标记文本。"),
                HumanMessage(
                    "请调用cloud_probe_echo，value填写cloud_ok。工具返回后返回summary为cloud_ok，artifact_type为research_observation，artifact_content为实际读取结果，artifact_metadata中completion_status为complete、evidence_gaps为空数组的JSON。"
                ),
            ]
            first = await call("researcher_tool", researcher, messages)
            assert len(first.tool_calls) == 1 and first.tool_calls[0]["name"] == echo.name, "Missing actual structured tool call"
            tc = first.tool_calls[0]
            value = await echo.ainvoke(tc["args"])
            final = await call("researcher_stage", researcher, [*messages, first, ToolMessage(content=value, tool_call_id=tc["id"])])
            parsed = json.loads(final.content)
            assert parsed["summary"] == "cloud_ok" and parsed["artifact_metadata"]["completion_status"] == "complete"

            def cloud_probe_write(content: str) -> str:
                """Write the complete self-contained HTML probe into the private probe folder."""
                if not content.lstrip().lower().startswith("<!doctype html>") or not content.rstrip().lower().endswith("</html>"):
                    raise ValueError("Incomplete probe HTML")
                path = directory / "probe-report.html"
                with path.open("x") as f:
                    f.write(content)
                return "Probe HTML created"

            writer = StructuredTool.from_function(cloud_probe_write)
            report = await call(
                "reporter_nothinking_stream_write" if reporter_delivery else "reporter_thinking_write",
                client(True, 32768, 1920).bind_tools([writer]),
                [
                    SystemMessage("这是合成文档工具兼容测试，无真实项目或风险结论。请直接调用写文件工具，不输出报告草稿。"),
                    HumanMessage(
                        ("\n".join(f"合成材料{i:04d}：某供应商提供一段待核描述，只可标为事实或未核，不得编造结论。" for i in range(600)) + "\n" if reporter_delivery else "")
                        + "调用cloud_probe_write写入独立中文HTML5，内联简单CSS，正文约"
                        + ("1000" if reporter_delivery else "200")
                        + "汉字，主题为如何区分事实、推断与尚未核实事项。不要占位符，不要外部链接。"
                    ),
                ],
                timeout=900 if reporter_delivery else 1980,
            )
            assert len(report.tool_calls) == 1 and report.tool_calls[0]["name"] == writer.name, "Reporter did not issue a structured write"
            await writer.ainvoke(report.tool_calls[0]["args"])
            stream = io.BytesIO()
            Image.new("RGB", (64, 64), (255, 0, 0)).save(stream, format="PNG")
            vision = await call(
                "vision",
                client(False, 128),
                [HumanMessage(content=[{"type": "text", "text": "图片是什么颜色？只回答颜色。"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(stream.getvalue()).decode()}}])],
            )
            assert "红" in vision.content, "Image input was not interpreted correctly"
            data = "\n".join(f"材料{i:04d}：这是合成速度测试文本，需要区分事实、推断、来源和未核范围。不得编造证据。" for i in range(500))
            await call("long_input_speed", client(False, 512), [HumanMessage(data + "\n请直接写约300字的文档核对建议。")])
            result = {"passed": True, "model": "qwen3.8-27b", "checks": checks, "no_json_repair": True}
        except Exception as exc:
            result = {"passed": False, "model": "qwen3.8-27b", "error": trace.safe(type(exc).__name__ + ": " + str(exc)), "checks": checks, "no_json_repair": True}
        result["duration_seconds"] = time.monotonic() - started
        result["metrics"] = trace.metrics(result["duration_seconds"])
        write(directory / "preflight.json", result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return result


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--model", default="aliyun-qwen3.8-27b")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--reporter-delivery", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(probe(args.directory, args.model, args.env_file, args.reporter_delivery))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
