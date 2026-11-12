"""Original-source image viewing through native ViewImageMiddleware."""

import ast
import asyncio
import base64
import hashlib
import json
import time
import uuid
from typing import Annotated

from langchain.agents.middleware import AgentMiddleware
from langchain.tools import InjectedToolCallId, tool
from langchain_core.messages import ToolMessage
from langgraph.types import Command


def render_original(adapter, document_id, page, image_name):
    doc = adapter.context().by_id[document_id]
    source = doc["_path"]
    if source.suffix.lower() != ".pdf" or page < 1:
        raise ValueError("Only original PDF physical pages are viewable; use project_read for DOCX")
    if hashlib.sha256(source.read_bytes()).hexdigest() != doc["sha256"]:
        raise ValueError("Original file hash changed")
    metadata = {"status": "ok", "document_id": document_id, "source_path": doc["path"], "source_sha256": doc["sha256"], "bidder": doc.get("bidder"), "round": doc["round"], "physical_page": page, "image_name": image_name}
    if image_name:
        from pypdf import PdfReader

        with source.open("rb") as stream:
            reader = PdfReader(stream)
            if page > len(reader.pages):
                raise ValueError("Physical page outside original PDF")
            key = ast.literal_eval(image_name) if image_name.startswith(("[", "(")) else image_name
            if isinstance(key, tuple):
                key = list(key)
            item = reader.pages[page - 1].images[key]
            image = item.image.convert("RGB")
            metadata["encoded_sha256"] = hashlib.sha256(item.data).hexdigest()
            metadata["original_pixel_sha256"] = hashlib.sha256(str(image.size).encode() + image.tobytes()).hexdigest()
            metadata["mode"] = "original_embedded_image"
    else:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(str(source))
        if page > len(pdf):
            pdf.close()
            raise ValueError("Physical page outside original PDF")
        pdf_page = pdf.get_page(page - 1)
        bitmap = pdf_page.render(scale=1.5)
        image = bitmap.to_pil().convert("RGB").copy()
        bitmap.close()
        pdf_page.close()
        pdf.close()
        metadata["mode"] = "original_physical_page_render"
    metadata["original_dimensions"] = list(image.size)
    if max(image.size) > 2200:
        image.thumbnail((2200, 2200))
    metadata["display_dimensions"] = list(image.size)
    name = document_id + "-p" + str(page) + "-" + uuid.uuid4().hex + ".png"
    directory = adapter.outputs / "visual_sources"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    image.save(target, format="PNG")
    image.close()
    metadata["image_path"] = "/mnt/user-data/outputs/visual_sources/" + name
    image_bytes = target.read_bytes()
    metadata["display_png_sha256"] = hashlib.sha256(image_bytes).hexdigest()
    return metadata, base64.b64encode(image_bytes).decode()


def build_view_tool(adapter):
    @tool(
        "view_image",
        description="查看清洁项目原始PDF的物理页或M06证据中的嵌入图。参数为document_id、page；image_name留空查看整页，或填M06返回的image_name查看该图。仅限清单原件；无需image_path或宿主机路径。每次模型请求可保留最近12种不同图像，超过时需重新查看；不要声称核对未实际显示的图片。",
    )
    async def view_image(document_id: str, page: int, tool_call_id: Annotated[str, InjectedToolCallId], image_name: str = "") -> Command:
        started = time.monotonic()
        arguments = {"document_id": document_id, "page": page, "image_name": image_name}
        adapter.trace.event("tool.started", tool="view_image", tool_call_id=tool_call_id, arguments=arguments)
        try:
            metadata, encoded = await asyncio.to_thread(adapter.guarded, lambda: render_original(adapter, document_id, page, image_name))
            if adapter.quality_controls:
                with adapter._cache_lock:
                    adapter.register_rendered_image(metadata)
            record = {"tool": "view_image", "tool_call_id": tool_call_id, "arguments": arguments, "status": "completed", "duration_ms": (time.monotonic() - started) * 1000}
            update = {"viewed_images": {metadata["image_path"]: {"base64": encoded, "mime_type": "image/png"}}, "messages": [ToolMessage(json.dumps(metadata, ensure_ascii=False), tool_call_id=tool_call_id)]}
        except Exception as exc:
            metadata = {"status": "error", "error": type(exc).__name__ + ": " + str(exc)}
            record = {"tool": "view_image", "tool_call_id": tool_call_id, "arguments": arguments, "status": "failed", "duration_ms": (time.monotonic() - started) * 1000}
            update = {"messages": [ToolMessage(json.dumps(metadata, ensure_ascii=False), tool_call_id=tool_call_id)]}
        adapter.trace.record_tool(record, metadata)
        adapter.trace.append("tool_results.jsonl", {**record, "result": metadata})
        return Command(update=update)

    return view_image


class AuditImageRequestMiddleware(AgentMiddleware):
    """Keep at most twelve distinct original images in each model request.

    Native image state and original PNGs remain intact. Exact removed data URLs
    are archived once, and both request views record reversible references.
    Roles, stages, tool decisions and text evidence are unchanged.
    """

    def __init__(self, trace, max_images=12, adapter=None):
        super().__init__()
        self.trace, self.max_images = trace, max_images
        self.adapter = adapter

    def archived(self, messages):
        values = self.trace.safe(messages)
        directory = self.trace.directory / "image_inputs"
        for message in values:
            if not isinstance(message, dict) or not isinstance(message.get("content"), list):
                continue
            for block in message["content"]:
                if not isinstance(block, dict) or block.get("type") != "image_url":
                    continue
                url = block["image_url"]["url"]
                key = hashlib.sha256(url.encode()).hexdigest()
                directory.mkdir(exist_ok=True)
                path = directory / (key + ".url")
                try:
                    with path.open("x") as stream:
                        stream.write(url)
                except FileExistsError:
                    pass
                block["image_url"]["url"] = {"archive_path": str(path), "data_url_sha256": key}
        return values

    def project(self, messages):
        positions = []
        for index, message in enumerate(messages):
            if isinstance(getattr(message, "content", None), list):
                for block_index, block in enumerate(message.content):
                    if isinstance(block, dict) and block.get("type") == "image_url":
                        positions.append((index, block_index, block["image_url"]["url"]))
        seen, keep = set(), set()
        for index, block_index, url in reversed(positions):
            if url not in seen and len(seen) < self.max_images:
                seen.add(url)
                keep.add((index, block_index))
        if len(keep) == len(positions):
            return messages
        projected = []
        for index, message in enumerate(messages):
            if not isinstance(getattr(message, "content", None), list):
                projected.append(message)
                continue
            blocks = []
            for block_index, block in enumerate(message.content):
                if isinstance(block, dict) and block.get("type") == "image_url" and (index, block_index) not in keep:
                    blocks.append({"type": "text", "text": f"[原图以来源位置保留；本请求的视觉窗口仅保留最近{self.max_images}种图像。需要核对这张原图时，请再次调用view_image。]"})
                else:
                    blocks.append(block)
            if index == max((position[0] for position in keep), default=-1):
                blocks.append({"type": "text", "text": f"视觉窗口说明：本次请求只展示上方实际保留的最多{self.max_images}种图像内容，重复图片仅展示一份；保留全部来源元数据。未展示的新图片请重新调用view_image，不得声称已核对未见原图。"})
            projected.append(message.model_copy(update={"content": blocks}))
        self.trace.append(
            "model_image_projections.jsonl",
            {"before_image_occurrences": len(positions), "retained_distinct_images": len(keep), "before": self.archived(messages), "after": self.archived(projected), "original_images_and_state_unchanged": True},
        )
        return projected

    def wrap_model_call(self, request, handler):
        messages = self.project(request.messages)
        if self.adapter is not None:
            self.adapter.register_submitted_images(messages)
            if self.adapter.rule_id == "M06" and self.adapter.ocr_receipts is not None:
                self.adapter.ocr_receipts.observe_model_input(messages)
        return handler(request.override(messages=messages))

    async def awrap_model_call(self, request, handler):
        messages = await asyncio.to_thread(self.project, request.messages)
        if self.adapter is not None:
            self.adapter.register_submitted_images(messages)
            if self.adapter.rule_id == "M06" and self.adapter.ocr_receipts is not None:
                self.adapter.ocr_receipts.observe_model_input(messages)
        return await handler(request.override(messages=messages))


def build_pair_view_tool(adapter):
    @tool("view_image_pair", description="按当前M06代码候选offset同时查看两端原图，工具自动使用真实证据ID/文档/物理页/image_name。一个候选是一对图；无需手工拼接位置。run_id必须是真实audit_run_id。")
    async def view_image_pair(run_id: str, candidate_offset: int, tool_call_id: Annotated[str, InjectedToolCallId]) -> Command:
        from .fact_index import pair_sources
        from .full_results import pack_complete_result

        started = time.monotonic()
        arguments = {"run_id": run_id, "candidate_offset": candidate_offset}
        adapter.trace.event("tool.started", tool="view_image_pair", tool_call_id=tool_call_id, arguments=arguments)

        def render():
            if adapter.rule_id != "M06":
                raise ValueError("Pair viewing is available only for the current image rule")
            endpoints = pair_sources(pack_complete_result(adapter.result_root(run_id)), candidate_offset)
            return [(endpoint, *render_original(adapter, endpoint["document_id"], endpoint["page"], endpoint.get("image_name", ""))) for endpoint in endpoints]

        try:
            images = await asyncio.to_thread(adapter.guarded, render)
            receipt = {"status": "ok", "audit_run_id": run_id, "candidate_offset": candidate_offset, "endpoints": [{**metadata, "evidence_id": endpoint["evidence_id"]} for endpoint, metadata, _ in images]}
            with adapter._cache_lock:
                for _, metadata, _ in images:
                    adapter.register_rendered_image(metadata)
            update = {
                "viewed_images": {metadata["image_path"]: {"base64": encoded, "mime_type": "image/png"} for _, metadata, encoded in images},
                "messages": [ToolMessage(json.dumps(receipt, ensure_ascii=False), tool_call_id=tool_call_id)],
            }
            status = "completed"
        except Exception as exc:
            receipt = {"status": "error", "error": type(exc).__name__ + ": " + str(exc)}
            update = {"messages": [ToolMessage(json.dumps(receipt, ensure_ascii=False), tool_call_id=tool_call_id)]}
            status = "failed"
        record = {"tool": "view_image_pair", "tool_call_id": tool_call_id, "arguments": arguments, "status": status, "duration_ms": (time.monotonic() - started) * 1000}
        adapter.trace.record_tool(record, receipt)
        adapter.trace.append("tool_results.jsonl", {**record, "result": receipt})
        return Command(update=update)

    return view_image_pair
