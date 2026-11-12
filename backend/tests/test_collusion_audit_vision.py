"""Original-only image access, native state injection and reversible budgeting."""

import asyncio
import base64
import json
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage

from app.experiments.collusion_audit.tools import AuditTools
from app.experiments.collusion_audit.vision import AuditImageRequestMiddleware
from app.experiments.tender_audit.observability import AuditTrace
from deerflow.agents.middlewares.view_image_middleware import ViewImageMiddleware


def adapter(tmp_path, monkeypatch):
    from reportlab.pdfgen import canvas

    root = Path(__file__).resolve().parents[2]
    monkeypatch.syspath_prepend(str(root / "vendor/bid_audit_tools"))
    project, outputs = tmp_path / "project", tmp_path / "outputs"
    project.mkdir()
    outputs.mkdir()
    source = project / "original.pdf"
    c = canvas.Canvas(str(source))
    c.drawString(70, 700, "ORIGINAL 5827")
    c.save()
    (project / "manifest.json").write_text(json.dumps({"schema_version": "1.0", "project_id": "synthetic", "documents": [{"id": "d001", "role": "bid", "bidder": "A", "path": "original.pdf", "round": "1", "lot": "1"}]}))
    return AuditTools("M06", project, outputs, tmp_path / "cache", AuditTrace(tmp_path / "trace"))


def test_original_pdf_render_flows_through_native_image_middleware(tmp_path, monkeypatch):
    tools = adapter(tmp_path, monkeypatch)
    viewer = next(t for t in tools.build() if t.name == "view_image")
    args = {"document_id": "d001", "page": 1}
    command = asyncio.run(viewer.ainvoke({"name": "view_image", "args": args, "id": "view-1", "type": "tool_call"}))
    assert command.update["viewed_images"]
    metadata = json.loads(command.update["messages"][0].content)
    assert metadata["document_id"] == "d001" and metadata["physical_page"] == 1
    assert metadata["mode"] == "original_physical_page_render"
    state = {"messages": [AIMessage(content="", tool_calls=[{"name": "view_image", "args": args, "id": "view-1"}]), *command.update["messages"]], "viewed_images": command.update["viewed_images"]}
    result = ViewImageMiddleware()._inject_image_message(state)
    assert result and any(b.get("type") == "image_url" for b in result["messages"][0].content)


def test_viewer_cannot_read_nonmanifest_history_paths(tmp_path, monkeypatch):
    tools = adapter(tmp_path, monkeypatch)
    viewer = next(t for t in tools.build() if t.name == "view_image")
    command = asyncio.run(viewer.ainvoke({"name": "view_image", "args": {"document_id": "/old/分析报告.pdf", "page": 1}, "id": "view-bad", "type": "tool_call"}))
    assert "viewed_images" not in command.update
    assert json.loads(command.update["messages"][0].content)["status"] == "error"


def test_image_request_budget_preserves_text_and_archives_exact_image_urls(tmp_path):
    trace = AuditTrace(tmp_path / "trace")
    middleware = AuditImageRequestMiddleware(trace, max_images=2)
    urls = ["data:image/png;base64," + base64.b64encode(f"image-{i}".encode()).decode() for i in range(3)]
    messages = [HumanMessage(content=[{"type": "text", "text": "original-source-" + str(i)}, {"type": "image_url", "image_url": {"url": url}}]) for i, url in enumerate(urls)]
    projected = middleware.project(messages)
    kept = [b["image_url"]["url"] for m in projected for b in m.content if b.get("type") == "image_url"]
    assert kept == urls[-2:]
    assert messages[0].content[1]["image_url"]["url"] == urls[0]
    assert all(any(b.get("text") == "original-source-" + str(i) for b in projected[i].content) for i in range(3))
    archived = {p.read_text() for p in (trace.directory / "image_inputs").glob("*.url")}
    assert archived == set(urls)


def test_reopening_an_old_image_makes_it_available_again(tmp_path):
    middleware = AuditImageRequestMiddleware(AuditTrace(tmp_path / "trace"), max_images=2)
    urls = ["data:image/png;base64," + base64.b64encode(f"image-{i}".encode()).decode() for i in [0, 1, 2, 0]]
    messages = [HumanMessage(content=[{"type": "image_url", "image_url": {"url": url}}]) for url in urls]
    projected = middleware.project(messages)
    kept = [b["image_url"]["url"] for m in projected for b in m.content if b.get("type") == "image_url"]
    assert kept == [urls[2], urls[3]]


def test_default_visual_window_retains_all_ten_candidate_images(tmp_path):
    middleware = AuditImageRequestMiddleware(AuditTrace(tmp_path / "trace"))
    messages = [HumanMessage(content=[{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{i}"}}]) for i in range(10)]
    projected = middleware.project(messages)
    assert sum(b.get("type") == "image_url" for m in projected for b in m.content) == 10


def test_native_reader_extracts_original_pdf_with_page_reference(tmp_path, monkeypatch):
    tools = adapter(tmp_path, monkeypatch)
    reader = next(t for t in tools.build() if t.name == "read_file")
    result = json.loads(asyncio.run(reader.ainvoke({"file_path": "/project/original.pdf"})))
    assert "ORIGINAL 5827" in result["text"]
    assert result["source_pages"] == [1]
    assert result["document_id"] == "d001"
    assert len(result["sha256"]) == 64
