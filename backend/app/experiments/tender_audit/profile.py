"""Run-local rules and capabilities, leaving all built-in role defaults intact."""

import asyncio
import hashlib
import json
from dataclasses import replace
from pathlib import Path, PurePosixPath

from langchain_core.tools import StructuredTool

from deerflow.sp.extensions import SPExecutionExtensions
from deerflow.sp.subagents import SPSubagentTask
from deerflow.subagents.config import SubagentConfig

from .cases import AuditCase
from .context_projection import AuditDelegationRequestMiddleware
from .observability import AuditTrace
from .ragflow import RagflowClient, visible_chunk
from .request_projection import AuditEvidenceRequestMiddleware

OUTPUT_PREFIX = "/mnt/user-data/outputs/"
REPORTER_EVIDENCE_MAX_CHARS = 96_000


class AuditProfile:
    def __init__(self, case: AuditCase, rag: RagflowClient, trace: AuditTrace, outputs: Path):
        self.case, self.rag, self.trace = case, rag, trace
        self.outputs = outputs.resolve()

    def prepare_task(self, task: SPSubagentTask) -> None:
        task.context_refs["audit_task_context"] = self.case.task_context()
        task.context_refs["audit_report_instructions"] = self.case.report_instructions()
        if task.subagent_type == "reporter":
            # Native DelegateHandler always adds read_file/write_file to the
            # reporter A2A allowlist. Narrow both representations before the
            # runtime validates them against this run's read-only role copy.
            task.allowed_tools = [name for name in task.allowed_tools if name != "write_file"]
            if isinstance(task.metadata.get("tool_names"), list):
                task.metadata["tool_names"] = [name for name in task.metadata["tool_names"] if name != "write_file"]
            with self.trace.lock:
                chunks = list(self.rag.chunks)
            unique = {}
            for chunk in chunks:
                chunk = visible_chunk(chunk)
                digest = hashlib.sha256(str(chunk.get("content", "")).encode()).hexdigest()
                key = (
                    str(chunk.get("id", "")),
                    str(chunk.get("document_id") or chunk.get("doc_id") or ""),
                    digest,
                )
                unique.setdefault(key, chunk)
            # Research/parent-report source references get first claim on the
            # bounded inline bundle. Every source stays readable case-locally.
            references = str(task.context_refs.get("artifact_bodies", ""))
            ordered = sorted(
                unique.items(),
                key=lambda pair: str(pair[1].get("id", "")) not in references,
            )
            items, manifest, size = [], [], 0
            for key, chunk in ordered:
                identity = hashlib.sha256(json.dumps(key).encode()).hexdigest()
                relative = f"audit-evidence/{identity}.json"
                cached = self._path(OUTPUT_PREFIX + relative)
                cached.parent.mkdir(parents=True, exist_ok=True)
                if not cached.exists():
                    with cached.open("x", encoding="utf-8") as output:
                        json.dump(chunk, output, ensure_ascii=False, indent=2)
                cost = len(json.dumps(chunk, ensure_ascii=False))
                included = size + cost <= REPORTER_EVIDENCE_MAX_CHARS
                if included:
                    size += cost
                    items.append(chunk)
                manifest.append(
                    {
                        "id": key[0],
                        "document_id": key[1],
                        "content_sha256": key[2],
                        "read_path": OUTPUT_PREFIX + relative,
                        "included": included,
                    }
                )
            bundle = {
                "items": items,
                "source_manifest": manifest,
                "total_retrieved_chunks": len(chunks),
                "unique_retrieved_chunks": len(unique),
                "truncated": len(items) < len(unique),
                "max_chars": REPORTER_EVIDENCE_MAX_CHARS,
            }
            task.context_refs["audit_retrieved_evidence"] = bundle
            self.trace.append(
                "reporter_evidence_inputs.jsonl",
                {"action_id": task.action_id, **bundle},
            )
        self.trace.event(
            "audit.task_prepared",
            role=task.subagent_type,
            action_id=task.action_id,
            rule_names=self.case.rule_names,
        )

    def configure_subagent(self, task: SPSubagentTask, config: SubagentConfig) -> SubagentConfig:
        role = task.subagent_type
        tools = ["local_search_tool"] if role == "researcher" else ["read_file", "write_file"] if role == "coder" else ["read_file"]
        instructions = """
<run_scoped_audit_requirements>
本次是封闭标书审核。context_refs.audit_task_context 包含本次完整规则，必须优先应用，不能因为委派摘要省略了规则而忽略它。
只有 researcher 的 local_search_tool 可以检索标书；禁止使用外网、宿主机文件或其他数据集作为证据。
其他通用 prompt 中关于网页 URL、互联网检索、图片的要求，在本次本地知识库任务中不适用。以真实 chunk ID、原文及位置作为来源。
所有结论都按本次给定规则判断，不添加法规或自行放宽规则。
保留原生 summary/artifact_content/artifact_type/artifact_metadata 协议；summary 保持简短。
完整交付契约见 context_refs.audit_report_instructions，所有委派和修订保持其字段及含义；score 是 0–1 自报置信度，不是采购评分或有待原文证明的事实数值。
确实缺少影响判断的关键证据时记录 needs_review；一般检索范围局限写入 reference，不把已经披露且不影响当前判断的理论性遗漏反复当成阻塞 evidence_gaps。真实阻塞项仍应保留，不能假装已解决。
</run_scoped_audit_requirements>
"""
        if role == "researcher":
            instructions += "\n将实际检索到的对比条款、chunk ID、原文和判断理由完整放进 research_observation 的 artifact_content，不能只给摘要。\n"
            instructions += (
                "付款方式核对适用对象、触发条件、期限、比例、金额、计算基数、扣除项目、退还条件和关键名词；不要只比较百分比。不自动把字面差异当成同义或擅自纠错，按本次规则判断并说明疑点。评分核对时分别计算满分列合计与详细描述最高分合计。\n"
            )
        if role == "memory_recaller":
            instructions += (
                "\n本次只召回当前任务上下文之外、确有来源的新增长期历史；不要把当前任务的审核规则、交付格式、工具限制或流程重新总结为记忆。"
                "若无新增可用长期记忆，简短返回summary说明无新增，items为空列表；有真实历史时保留相关偏好和硬约束。"
                "无历史不是审核证据缺口，不妨碍继续原生研究流程。\n"
            )
        if role == "reporter":
            instructions += f"""
<run_scoped_output_format>
本次明确要求机器可读的 JSON 审核结果，本节覆盖通用 role 中 Markdown 排版、标题、长报告文件名等格式要求。
完整规则见 context_refs.audit_task_context；格式见 context_refs.audit_report_instructions。
原始检索证据见 context_refs.audit_retrieved_evidence，研究结论见原生 artifact_bodies。source_manifest 给出全部去重来源的只读 read_path，必要时调用 read_file 读取未内联的完整 chunk。truncated=true 时不能声称已检查被省略的内容。
仍返回原生外层协议对象。artifact_content 必须放完整审核 JSON 对象，不再放嵌套 JSON 字符串；不要把外层协议误写进审核正文。
artifact_type 为 report_revision，artifact_metadata.filename 为 audit-case-{self.case.case_id:02d}.json。
artifact_metadata.created_paths 留空；直接返回完整 artifact_content，由 SP2 注册 JSON Artifact，无需调用 write_file。
只有完成指定审核任务时 completion_status 才为 complete。证据不足的判断可在 needs_review 中明确说明，不能假装明确通过。
summary 仅写简短完成说明；不要把完整审核正文放在 summary 中。
</run_scoped_output_format>
"""
        return replace(
            config,
            system_prompt=(config.system_prompt or "") + instructions,
            skills=[],
            tools=tools,
        )

    def _path(self, file_path: str) -> Path:
        if not file_path.startswith(OUTPUT_PREFIX) or ".." in PurePosixPath(file_path).parts:
            raise ValueError("Audit file tools only access this case's /mnt/user-data/outputs/")
        candidate = (self.outputs / file_path.removeprefix(OUTPUT_PREFIX)).resolve()
        if not candidate.is_relative_to(self.outputs):
            raise ValueError("Audit file path escaped this case's outputs")
        return candidate

    def tools(self):
        async def read_file(file_path: str) -> str:
            path = self._path(file_path)
            if path.stat().st_size > 4_000_000:
                raise ValueError("Artifact too large for a scoped file read")
            content = await asyncio.to_thread(path.read_text, encoding="utf-8")
            self.trace.event("audit.file_read", file_path=file_path, chars=len(content))
            return content

        async def write_file(file_path: str, content: str) -> str:
            path = self._path(file_path)
            if path.suffix.lower() not in {".json", ".md", ".txt"}:
                raise ValueError("Audit output must be .json, .md or .txt")

            def write():
                path.parent.mkdir(parents=True, exist_ok=True)
                # Never overwrite a prior version or follow a newly planted symlink.
                with path.open("x", encoding="utf-8") as output:
                    output.write(content)

            await asyncio.to_thread(write)
            self.trace.event("audit.file_written", file_path=file_path, chars=len(content))
            return f"Created {file_path}"

        return [
            self.rag.tool(),
            StructuredTool.from_function(
                coroutine=read_file,
                name="read_file",
                description="Read an existing artifact in this case's /mnt/user-data/outputs/ only.",
            ),
            StructuredTool.from_function(
                coroutine=write_file,
                name="write_file",
                description="Create a new .json/.md/.txt artifact in this case's /mnt/user-data/outputs/ only. Existing files cannot be overwritten.",
            ),
        ]

    def extensions(self) -> SPExecutionExtensions:
        return SPExecutionExtensions(
            tools=tuple(self.tools()),
            prepare_task=self.prepare_task,
            configure_subagent=self.configure_subagent,
            callbacks=(self.trace,),
            subagent_middlewares=lambda task: (
                AuditDelegationRequestMiddleware(action_id=task.action_id, role=task.subagent_type, trace=self.trace),
                *((AuditEvidenceRequestMiddleware(dataset_id=self.case.dataset_id, trace=self.trace),) if task.subagent_type == "researcher" else ()),
            ),
            skill_names=("stackplanner-long-task", "stackplanner-reporting"),
        )
