"""Auditable source/reading/judgement ledger; never infer a judgement."""

import json
import threading
from collections import Counter
from pathlib import Path

from .knowledge import atomic, fingerprint


class ReviewLedger:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.units = {}
        self.records = {}
        self.seen = set()
        self.lock = threading.RLock()
        self.calculation_registered = False
        self.save()

    def add_unit(self, unit_id, data):
        with self.lock:
            if unit_id in self.units and self.units[unit_id] != data:
                raise ValueError("Conflicting review unit")
            self.units[unit_id] = data

    def mark_seen(self, refs):
        with self.lock:
            self.seen.update(refs)

    def page(self, offset=0, limit=10):
        if offset < 0 or not 1 <= limit <= 20:
            raise ValueError("Invalid review pagination")
        pairs = list(self.units.items())
        rows = [{"unit_id": k, **v, "reviewed": k in self.records} for k, v in pairs[offset : offset + limit]]
        for row in rows:
            if row["kind"] == "source_scope":
                row["source_ref_count"] = len(row["source_refs"])
                row["source_refs"] = row["source_refs"][:3]
        self.mark_seen(ref for row in rows for ref in row.get("source_refs", []) if row.get("kind") != "source_scope")
        return {"total": len(pairs), "offset": offset, "items": rows, "next_offset": offset + len(rows) if offset + len(rows) < len(pairs) else None}

    def commit(self, records, replace=False):
        if not isinstance(records, list) or not records:
            raise ValueError("Supply a non-empty list of actual judgement records")
        with self.lock:
            validated = {}
            for r in records:
                if not isinstance(r, dict) or not {"unit_id", "judgement", "comparison", "evidence_refs"} <= r.keys():
                    raise ValueError("Missing review fields; no repair")
                unit = self.units.get(r["unit_id"])
                if unit is None:
                    raise ValueError("Unknown review unit")
                allowed = {"满足", "负偏离", "不适用", "无法判断"} if unit["kind"] == "parameter" else {"风险线索", "排除", "不适用", "无法判断", "已核对"}
                if r["judgement"] not in allowed or not isinstance(r["comparison"], str) or len(r["comparison"].strip()) < 5:
                    raise ValueError("Explicit judgement and substantive comparison required")
                refs = r["evidence_refs"]
                if not isinstance(refs, list) or not refs or any(not isinstance(x, str) or x not in self.seen for x in refs):
                    raise ValueError("Evidence has not been returned by a reading tool")
                if not set(refs) & set(unit.get("source_refs", [])):
                    raise ValueError("Evidence does not belong to review unit")
                item = {k: r[k] for k in ["unit_id", "judgement", "comparison", "evidence_refs"]}
                if r["unit_id"] in validated and validated[r["unit_id"]] != item:
                    raise ValueError("Conflicting duplicate in one batch")
                old = self.records.get(r["unit_id"])
                if old and old != item and not replace:
                    raise ValueError("Conflicting review requires explicit replace=true")
                validated[r["unit_id"]] = item
            added = sum(k not in self.records for k in validated)
            self.records.update(validated)
            self.save()
            return {"added": added, "status": self.status()}

    def status(self):
        with self.lock:
            pending = [k for k in self.units if k not in self.records]
            return {
                "expected": len(self.units),
                "reviewed": len(self.records),
                "unreviewed": len(pending),
                "complete": bool(self.units) and not pending,
                "judgements": dict(Counter(r["judgement"] for r in self.records.values())),
                "pending_ids": pending[:30],
                "first_unreviewed_offset": next((i for i, k in enumerate(self.units) if k not in self.records), None),
                "unit_set_sha256": fingerprint(self.units),
                "note": "Tool reads are not counted as semantic reviews; judgement correctness is not established by this ledger.",
            }

    def save(self):
        with self.lock:
            atomic(self.root / "status.json", self.status())
            atomic(self.root / "units.json", self.units)
            atomic(self.root / "records.json", self.records)

    def register_calculation(self, result, output_root, knowledge=None, manifest=None):
        if self.calculation_registered:
            return  # Same immutable run can be accessed again without resetting review scope.
        root = Path(output_root) / "bid_audit" / result["run_id"]
        evidence = {}
        f = root / "evidence.jsonl"
        if f.exists():
            evidence = {r["evidence_id"]: r for r in map(json.loads, f.read_text().splitlines())}
        collection = "review_rows" if result["rule_id"] == "M04" else "findings"
        rows = [json.loads(s) for s in (root / (collection + ".jsonl")).read_text().splitlines()]
        for i, row in enumerate(rows):
            refs = []

            def walk(v):
                if isinstance(v, dict):
                    for k, x in v.items():
                        if k.endswith("_refs") and isinstance(x, list):
                            refs.extend(y for y in x if y in evidence)
                        else:
                            walk(x)
                elif isinstance(v, list):
                    for x in v:
                        walk(x)

            walk(row)
            self.add_unit(f"{collection}:{i}", {"kind": "parameter" if collection == "review_rows" else "candidate", "data": row, "source_refs": list(dict.fromkeys(refs))})
        if not rows and result["rule_id"] != "M04":
            ref = "calculation:" + result["run_id"]
            self.add_unit(
                "scope:calculation",
                {
                    "kind": "candidate",
                    "data": {"status": result.get("status"), "metrics": result.get("metrics"), "limitations": result.get("limitations"), "instruction": "核对零候选或不适用的范围与数据缺口，不能直接判无风险。"},
                    "source_refs": [ref],
                },
            )
            self.mark_seen([ref])
        if result["rule_id"] == "M04" and knowledge and manifest:
            self.register_ocr_tables(knowledge, manifest)
        self.calculation_registered = True
        self.save()
        folder = self.root / "pages"
        folder.mkdir(exist_ok=True)
        for offset in range(0, len(self.units), 10):
            # Exporting pages does not itself constitute model reading.
            items = [{"unit_id": k, **v} for k, v in list(self.units.items())[offset : offset + 10]]
            for item in items:
                if item["kind"] == "source_scope":
                    item["source_ref_count"] = len(item["source_refs"])
                    item["source_refs"] = item["source_refs"][:3]
                    item["source_refs_preview"] = True
            atomic(folder / f"offset_{offset:06d}.json", {"offset": offset, "total": len(self.units), "items": items})

    def register_ocr_tables(self, knowledge, manifest):
        from bid_audit.core import norm
        from bs4 import BeautifulSoup

        from .table_grid import table_grid

        known = set()
        # Only exact source/requirement/response identity may collapse duplicate
        # extraction. Ambiguous OCR variants remain separately reviewable.
        for unit in self.units.values():
            row = unit.get("data", {})
            known.add((row.get("bidder"), row.get("round"), norm(row.get("requirement", "")), norm(row.get("response", ""))))
        for doc in manifest["documents"]:
            if doc["role"] != "bid" or doc["id"] not in knowledge.index["documents"]:
                continue
            chunks = knowledge.rows(doc["id"])
            refs = [doc["id"] + ":" + r["id"] for r in chunks]
            self.add_unit(
                "scope:" + doc["id"],
                {"kind": "source_scope", "document_id": doc["id"], "bidder": doc["bidder"], "round": doc["round"], "instruction": "核对参数响应区段及遗漏；说明已定位区段、扫描/提取缺口，不把缺失当满足。", "source_refs": refs},
            )
            active = None
            for chunk in chunks:
                soup = BeautifulSoup(chunk["content"], "html.parser")
                for ti, table in enumerate(soup.find_all("table")):
                    table_has_header = False
                    for ri, cells in enumerate(table_grid(table)):
                        header = [norm(x) for x in cells]
                        q = next((i for i, x in enumerate(header) if "采购文件规定" in x or "招标要求" in x), None)
                        a = next((i for i, x in enumerate(header) if "投标文件响应" in x or "实际响应" in x), None)
                        if q is not None and a is not None:
                            deviation = next((i for i, x in enumerate(header) if "偏离" in x), None)
                            active = (q, a, deviation, len(cells))
                            table_has_header = True
                            continue
                        if active is None or len(cells) != active[3]:
                            continue
                        # Carry column positions only across recognisable response
                        # rows, never all later same-width certificate/price tables.
                        if not table_has_header and (active[2] is None or norm(cells[active[2]]).strip("。.;；") not in {"无偏离", "正偏离", "负偏离"}):
                            continue
                        requirement, response = cells[active[0]], cells[active[1]]
                        if not requirement or not response:
                            continue
                        key = (doc["bidder"], doc["round"], norm(requirement), norm(response))
                        if key in known:
                            continue
                        known.add(key)
                        self.add_unit(
                            f"ocr:{doc['id']}:{chunk['id']}:{ti}:{ri}",
                            {"kind": "parameter", "data": {"requirement": requirement, "response": response, "bidder": doc["bidder"], "round": doc["round"], "positions": chunk["positions"]}, "source_refs": [doc["id"] + ":" + chunk["id"]]},
                        )

    def add_discovered(self, document_id, items, knowledge, manifest):
        if not self.calculation_registered:
            raise ValueError("Run the current rule calculator first")
        doc = next((d for d in manifest["documents"] if d["id"] == document_id and d["role"] == "bid"), None)
        if doc is None or not isinstance(items, list) or not 1 <= len(items) <= 20:
            raise ValueError("Current bid document and1..20 extracted source rows required")
        chunks = {document_id + ":" + r["id"]: r for r in knowledge.rows(document_id)}
        added = []
        pending = []
        for item in items:
            if not isinstance(item, dict) or not {"requirement", "response", "source_refs"} <= item.keys():
                raise ValueError("Missing extraction fields; no repair")
            if any(not isinstance(item[k], str) or not item[k].strip() for k in ["requirement", "response"]):
                raise ValueError("Nonempty source requirement and response required")
            refs = item["source_refs"]
            if not isinstance(refs, list) or not refs or any(r not in chunks or r not in self.seen for r in refs):
                raise ValueError("Extraction must cite current, actually read knowledge chunks")
            unit_id = "discovered:" + document_id + ":" + fingerprint(item)[:16]
            pending.append(
                (
                    unit_id,
                    {
                        "kind": "parameter",
                        "data": {"requirement": item["requirement"], "response": item["response"], "bidder": doc["bidder"], "round": doc["round"], "positions": [p for ref in refs for p in chunks[ref]["positions"]]},
                        "source_refs": refs,
                    },
                )
            )
            added.append(unit_id)
        with self.lock:
            for unit_id, data in pending:
                self.add_unit(unit_id, data)
            self.save()
            folder = self.root / "pages"
            folder.mkdir(exist_ok=True)
            for offset in range(0, len(self.units), 10):
                # New source extraction extends the immutable-id scope, not the judgement map.
                rows = [{"unit_id": k, **v} for k, v in list(self.units.items())[offset : offset + 10]]
                for row in rows:
                    if row["kind"] == "source_scope":
                        row["source_ref_count"] = len(row["source_refs"])
                        row["source_refs"] = row["source_refs"][:3]
                        row["source_refs_preview"] = True
                atomic(folder / f"offset_{offset:06d}.json", {"offset": offset, "total": len(self.units), "items": rows})
        return added

    def finish_error(self):
        s = self.status()
        if not self.calculation_registered:
            return "Business audit incomplete: current rule calculator has not run."
        if not s["complete"]:
            return "Business audit incomplete: " + json.dumps(s, ensure_ascii=False) + ". Continue using native decisions; inspect audit_review_units/audit_review_status and return judgement records. Reading alone cannot pass."
        return None
