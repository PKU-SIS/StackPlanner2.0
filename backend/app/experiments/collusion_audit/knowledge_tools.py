"""Knowledge/review tools operate only inside the trusted project snapshot."""

import json

from langchain_core.tools import StructuredTool

from .knowledge import RagClient


def build_knowledge_tools(adapter):
    def make(name, fn, description):
        kwargs = {}
        if adapter.quality_controls and name in {"ragflow_retrieve", "knowledge_read"}:
            from typing import Literal

            from pydantic import Field, create_model

            ids = Literal.__getitem__(tuple(adapter.knowledge.index["documents"]))
            fields = {"document_id": (ids, Field(description="清单逻辑ID dXXX；不是远端RAGflow UUID"))}
            if name == "ragflow_retrieve":
                fields.update(question=(str, ...), page=(int, 1), page_size=(int, 5))
            else:
                fields.update(offset=(int, 0), limit=(int, 5))
            kwargs["args_schema"] = create_model("Scoped" + name, **fields)
        return StructuredTool.from_function(coroutine=fn, name=name, description=description, **kwargs)

    async def ragflow_retrieve(document_id: str, question: str, page: int = 1, page_size: int = 5) -> str:
        def call():
            from .settings import ragflow_settings

            c = ragflow_settings()
            client = RagClient(c["RAGFLOW_API_URL"], c["RAGFLOW_API_KEY"])
            try:
                return client.retrieve(adapter.knowledge, document_id, question, page, page_size)
            finally:
                client.session.close()

        return await adapter.recorded("ragflow_retrieve", locals_args(document_id=document_id, question=question, page=page, page_size=page_size), call)

    async def knowledge_read(document_id: str, offset: int = 0, limit: int = 5) -> str:
        return await adapter.recorded("knowledge_read", locals_args(document_id=document_id, offset=offset, limit=limit), lambda: adapter.knowledge.read(document_id, offset, limit))

    async def audit_review_units(offset: int = 0, limit: int = 10) -> str:
        return await adapter.recorded("audit_review_units", locals_args(offset=offset, limit=limit), lambda: adapter.ledger.page(offset, limit))

    async def audit_record_reviews(records: list[dict], replace: bool = False) -> str:
        return await adapter.recorded("audit_record_reviews", {"records": records, "replace": replace}, lambda: adapter.ledger.commit(records, replace))

    async def audit_add_review_units(document_id: str, items: list[dict]) -> str:
        def call():
            if adapter.rule_id != "M04":
                raise ValueError("Source parameter additions currently apply to M04")
            manifest = json.loads((adapter.project / "manifest.json").read_text())
            ids = adapter.ledger.add_discovered(document_id, items, adapter.knowledge, manifest)
            return {"unit_ids": ids, "status": adapter.ledger.status()}

        return await adapter.recorded("audit_add_review_units", {"document_id": document_id, "items": items}, call)

    async def audit_review_status() -> str:
        return await adapter.recorded("audit_review_status", {}, lambda: adapter.ledger.status())

    tools = [
        make("audit_add_review_units", audit_add_review_units, "登记模型从当前已读OCR原文补查出的遗漏参数；items含requirement,response,source_refs（dXXX:chunk_id）。只登记原文，不作判断；返回真实unit_id后另行提交判断，不得自造ID。"),
        make("ragflow_retrieve", ragflow_retrieve, "检索当前项目指定PDF的独立MinerU知识库；document_id必须来自清单，不能指定任意知识库。返回真实chunk、位置、相似度。"),
        make("knowledge_read", knowledge_read, "按原文顺序连续读指定PDF的持久化MinerU/OCR片段；offset从0开始，limit1..20，next_offset非空时可继续。"),
        make("audit_review_units", audit_review_units, "读取本次规则的待审核对象及真实要求/响应。读取不算语义复核。offset0起，limit最多20。"),
        make(
            "audit_record_reviews",
            audit_record_reviews,
            "保存非空的实际审核判断列表（提交量不受读取分页限制）：unit_id, judgement, comparison, evidence_refs。参数项judgement为满足/负偏离/不适用/无法判断；"
            "候选项为风险线索/排除/不适用/无法判断；文档范围项为已核对/无法判断。证据必须已读取，comparison写具体比较与缺口。冲突修订显式replace=true。",
        ),
        make("audit_review_status", audit_review_status, "查看去重审核覆盖、判断数量和未完成对象；与原文读取进度严格分开。"),
    ]
    return tools if adapter.ledger is not None else [tool for tool in tools if tool.name in {"ragflow_retrieve", "knowledge_read"}]


def locals_args(**kwargs):
    return kwargs
