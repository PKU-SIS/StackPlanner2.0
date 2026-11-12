"""Whitelist-only rule export and original-document staging."""

import hashlib
import json
import re
import shutil
from pathlib import Path

allowed_rule_fields = ("合并规则ID", "规则名称", "通用规则内容", "通用判断方法", "法律来源", "通用适用条件", "通用排除条件", "证据等级", "通用备注")
HISTORY_DIRS = {"分析报告", "分析结果", "历史分析报告_原三家目录", "validation", "__MACOSX"}


def clean_rules(rows):
    return [{key: row.get(key) for key in allowed_rule_fields} for row in rows if re.fullmatch(r"M\d{2}", str(row.get("合并规则ID", "")))]


def read_rules(path):
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=True)
    sheet = workbook["合并规则总表"]
    rows = sheet.iter_rows(values_only=True)
    headers = next(rows)
    rules = clean_rules(dict(zip(headers, row)) for row in rows)
    assert [r["合并规则ID"] for r in rules] == [f"M{i:02d}" for i in range(1, 16)]
    workbook.close()
    return rules


def safe_document(path):
    path = Path(path)
    if path.suffix.lower() not in {".pdf", ".docx", ".txt"}:
        return False
    if any(part in HISTORY_DIRS or part.startswith("历史分析") for part in path.parts):
        return False
    return not any(word in path.name for word in ("风险初筛", "审核报告", "核查报告", "比对报告", "分析报告"))


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_manifest(project_root, files):
    documents = []
    project_root = Path(project_root)
    for path in sorted(files):
        relative = path.relative_to(project_root)
        if not safe_document(relative):
            raise ValueError("Historical/unsupported input rejected")
        text = str(relative)
        if "招标文件" in path.name:
            role, bidder = "tender", None
        else:
            if "采购包" not in text or "(包" not in path.parent.name:
                raise ValueError("Unclassified document must not be auto-admitted: " + text)
            role = "quote_detail" if "分项报价表" in path.name else "quote_opening" if "开标一览表" in path.name else "bid"
            bidder = re.sub(r"[（(]包\d+[）)]$", "", path.parent.name)
        document = {"id": f"d{len(documents) + 1:03d}", "path": text, "role": role, "round": "2" if "第二次开标" in text else "1", "lot": "1", "expected_sha256": sha256(path)}
        if bidder:
            document["bidder"] = bidder
        documents.append(document)
    rounds = sorted({document["round"] for document in documents})
    missing_baselines = [round_id for round_id in rounds if not any(document["role"] in {"tender", "public_template"} and document["round"] == round_id for document in documents)]
    scope = "仅原始招投标材料，不提供历史审核结论。"
    scope += ("缺少独立招标基线的轮次：" + ",".join(missing_baselines)) if missing_baselines else "每轮均提供独立招标文件；跨轮同一性及可比性仍需核验。"
    return {
        "schema_version": "1.0",
        "project_id": "chengyi_rounds",
        "project_name": project_root.name,
        "data_root": ".",
        "documents": documents,
        "parameters": {},
        "scope_note": scope,
    }


def stage_project(source, destination):
    source, destination = Path(source), Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    candidates = [p for p in source.rglob("*") if p.is_file() and safe_document(p.relative_to(source))]
    accepted = [p for p in candidates if "招标文件" in p.name or ("采购包" in str(p.relative_to(source)) and "(包" in p.parent.name)]
    manifest = build_manifest(source, accepted)
    manifest["project_name"] = source.name
    for item in manifest["documents"]:
        target = destination / item["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / item["path"], target)
        assert sha256(target) == item["expected_sha256"]
    (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest
