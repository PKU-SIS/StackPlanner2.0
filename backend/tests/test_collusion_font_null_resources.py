"""Malformed optional PDF resources must not mask valid font facts or errors."""

import asyncio
import json
from pathlib import Path

import pytest

from app.experiments.collusion_audit.tools import AuditTools
from app.experiments.tender_audit.observability import AuditTrace


@pytest.fixture
def vendor(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "vendor/bid_audit_tools"))
    from bid_audit import pdf_rules

    return pdf_rules


class Ref:
    def __init__(self, obj, idnum=1):
        self.obj, self.idnum, self.generation = obj, idnum, 0

    def get_object(self):
        return self.obj


@pytest.mark.parametrize("null_value", [None, "pypdf-null"])
def test_font_scan_skips_null_xobjects_and_retains_valid_nested_font(vendor, null_value):
    from pypdf.generic import NullObject

    null_value = NullObject() if null_value == "pypdf-null" else null_value
    font1, font2 = {"/BaseFont": "/AAAAAA+Arial"}, {"/BaseFont": "/BBBBBB+Song"}
    form = {"/Subtype": "/Form", "/Resources": {"/Font": {"/F2": Ref(font2)}}}
    resources = {"/Font": {"/F1": Ref(font1)}, "/XObject": {"/bad": Ref(null_value), "/good": Ref(form, 2)}}
    form["/Resources"]["/XObject"] = {"/cycle": Ref(form, 2)}
    assert list(vendor.resource_fonts(resources)) == [("/F1", font1), ("/F2", font2)]


@pytest.mark.parametrize("rule_mode", [False, True])
def test_failed_calculation_preserves_original_error_and_never_packs_missing_sources(tmp_path, monkeypatch, vendor, rule_mode):
    import bid_audit.runner

    error = {"run_id": "M11_20261107T000000_0123456789", "status": "error", "error": "AttributeError: original resource failure"}
    monkeypatch.setattr(bid_audit.runner, "run_rule", lambda *a, **kw: dict(error))
    tools = AuditTools("M11", tmp_path, tmp_path / "outputs", tmp_path / "cache", AuditTrace(tmp_path / "trace"), rule_level_review=rule_mode)
    tool = next(t for t in tools.build() if t.name == "audit_m11")
    result = json.loads(asyncio.run(tool.ainvoke({})))
    assert result["error"] == error["error"]
    assert result["run_id"] == error["run_id"]
    assert "complete_code_result" not in result and "read_file_pages" not in result
    records = [json.loads(line) for line in (tmp_path / "trace/tool_results.jsonl").read_text().splitlines()]
    assert records[-1]["status"] == "failed"


def test_null_resource_gap_is_preserved_on_font_cache_hit(tmp_path, monkeypatch, vendor):
    from types import SimpleNamespace

    from pypdf.generic import DictionaryObject, NameObject

    font = DictionaryObject({NameObject("/BaseFont"): NameObject("/AAAAAA+Arial")})
    page = {"/Resources": {"/Font": {"/F1": Ref(font)}, "/XObject": {"/missing": Ref(None)}}}
    monkeypatch.setattr(vendor, "PdfReader", lambda path: SimpleNamespace(pages=[page]))
    doc = {"id": "d001", "sha256": "source", "_path": tmp_path / "original.pdf", "bidder": "A", "round": "1", "lot": "1"}
    cache = tmp_path / "cache"
    cache.mkdir()

    def context():
        return SimpleNamespace(cache=cache, warnings=[], evidence=lambda d, page, **kw: {"document_id": d["id"], "page": page, **kw})

    fresh, cached = context(), context()
    first = vendor.font_records(fresh, doc)
    assert first == vendor.font_records(cached, doc)
    assert len(first) == 1
    assert fresh.warnings and cached.warnings == fresh.warnings
    assert "d001" in fresh.warnings[0] and "XObject" in fresh.warnings[0]
