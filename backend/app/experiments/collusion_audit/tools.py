"""Scoped adapters around the supplied evidence algorithms; no verdict injection."""

import asyncio
import json
import re
import threading
import time
import uuid
from collections import Counter
from pathlib import Path

from langchain_core.tools import StructuredTool


class AuditTools:
    def __init__(self, rule_id, project, outputs, cache, trace, *, knowledge=None, shared_cache=None, rule_level_review=False, quality_controls=False, report_format="markdown"):
        self.rule_id, self.project, self.outputs, self.cache, self.trace = rule_id, Path(project).resolve(), Path(outputs).resolve(), Path(cache), trace
        self.knowledge, self.shared_cache = knowledge, shared_cache
        self.report_format = report_format
        self.rule_level_review = rule_level_review
        self.quality_controls = quality_controls
        self.fact_indexes = {}
        self._calculation_source_files = {}
        self.rendered_images = {}
        self.submitted_images = set()
        self._scope_refs = {}
        self._source_reference_tokens = set()
        self.ledger = None
        if knowledge is not None and not rule_level_review:
            from .coverage import ReviewLedger

            self.ledger = ReviewLedger(self.outputs / "coverage")
        self.runs = []
        self._read_receipts = {}
        self._context = None
        self._cache_lock = threading.RLock()
        from .source_receipts import OCRReadReceipts

        self.ocr_receipts = OCRReadReceipts(knowledge) if quality_controls and knowledge is not None else None

    async def recorded(self, name, arguments, function):
        from .repetition import CURRENT_DELEGATION

        action_id = CURRENT_DELEGATION.get()
        call_id, started = uuid.uuid4().hex, time.monotonic()
        self.trace.event("tool.started", tool=name, tool_call_id=call_id, arguments=arguments)

        async def heartbeat():
            while True:
                await asyncio.sleep(5)
                self.trace.event("tool.progress", tool=name, tool_call_id=call_id, elapsed_seconds=time.monotonic() - started, detail="calculation still running")

        beat = asyncio.create_task(heartbeat())
        try:
            from .knowledge import atomic, fingerprint
            from .repetition import CURRENT_DELEGATION, READ_TOOLS

            action_id = CURRENT_DELEGATION.get()
            receipt_key = fingerprint({"action": action_id, "tool": name, "arguments": arguments}) if self.knowledge is not None and action_id and name in READ_TOOLS else None
            if name == "read_file" and "/coverage/" in str(arguments.get("file_path", "")):
                receipt_key = None  # Coverage changes after every committed batch.
            if receipt_key and receipt_key in self._read_receipts:
                result = {"status": "already_returned", "result_ref": self._read_receipts[receipt_key], "note": "同一委派已完整返回相同请求，复用先前内容；确需重读可用read_file读取result_ref。"}
            else:
                result = await asyncio.to_thread(self.guarded, function)
                if receipt_key and not (isinstance(result, dict) and result.get("status") == "error"):
                    relative = "read_receipts/" + receipt_key + ".json"
                    atomic(self.outputs / relative, result)
                    self._read_receipts[receipt_key] = "/mnt/user-data/outputs/" + relative
            failed = isinstance(result, dict) and result.get("status") == "error"
            record = {"tool": name, "tool_call_id": call_id, "arguments": arguments, "status": "failed" if failed else "completed", "duration_ms": (time.monotonic() - started) * 1000}
        except Exception as exc:
            result = {"status": "error", "error": type(exc).__name__ + ": " + str(exc)}
            record = {"tool": name, "tool_call_id": call_id, "arguments": arguments, "status": "failed", "duration_ms": (time.monotonic() - started) * 1000}
        finally:
            beat.cancel()
            await asyncio.gather(beat, return_exceptions=True)
        record["action_id"] = action_id
        record["deduplicated_read"] = isinstance(result, dict) and result.get("status") == "already_returned"
        if name == "ragflow_retrieve":
            chunks = result.get("chunks", []) if isinstance(result, dict) else []
            record["chunk_count"] = len(chunks)
            record["chunk_ids"] = [str(r.get("id", r.get("chunk_id"))) for r in chunks]
            record["http_request_attempted"] = not record["deduplicated_read"]
        if name == "audit_" + self.rule_id.lower() and isinstance(result, dict):
            record["calculation_cache_hit"] = result.get("cache", {}).get("hit", False)
        if record["status"] == "completed":
            from .identifier_provenance import source_reference_tokens

            with self._cache_lock:
                self._source_reference_tokens.update(source_reference_tokens(name, result, arguments))
        if record["status"] == "completed" and action_id:
            from .recovery import tool_scope_refs

            with self._cache_lock:
                self._scope_refs.setdefault(action_id, set()).update(tool_scope_refs(name, result, arguments))
        if self.ledger is not None and record["status"] == "completed":
            self.observe_read(name, result)
        if self.ocr_receipts is not None and record["status"] == "completed":
            with self._cache_lock:
                self.ocr_receipts.observe(name, result, arguments)
        self.trace.record_tool(record, result)
        self.trace.append("tool_results.jsonl", {**record, "result": result})
        visible = result
        if self.knowledge is not None and name == "ragflow_retrieve" and isinstance(result, dict) and "chunks" in result:
            from app.experiments.tender_audit.ragflow import visible_chunk

            visible = {**result, "chunks": [visible_chunk({**r, "content": r.get("content") or r.get("content_with_weight") or ""}) for r in result["chunks"]]}
        return json.dumps(visible, ensure_ascii=False)

    def register_calculation_sources(self, result):
        """Trust only producer-known immutable files of a registered code run."""
        import hashlib

        root = self.result_root(result["run_id"])
        names = {"result.json", "sources.json", "manifest.snapshot.json", "complete-code-result.json", "facts-index.json"}
        for meta in result.get("collections", {}).values():
            if isinstance(meta, dict) and meta.get("file"):
                name = str(meta["file"])
                if Path(name).name == name:
                    names.add(name)
        finding_meta = result.get("collections", {}).get("findings", {})
        count = finding_meta.get("count", 0) if isinstance(finding_meta, dict) else finding_meta
        names.add("candidates/index.json")
        names.add("candidates/response-pairs.json")
        for file in (root / "candidates").glob("C*.json"):
            match = re.fullmatch(r"C(\d{6})\.json", file.name)
            if match and 1 <= int(match[1]) <= count:
                names.add("candidates/" + file.name)
        if getattr(self, "resilient_delivery", False):
            names.add("m01_candidates/index.json")
            for file in (root / "m01_candidates").glob("C*.json"):
                match = re.fullmatch(r"C(\d{6})\.json", file.name)
                if match and 1 <= int(match[1]) <= count:
                    names.add("m01_candidates/" + file.name)
        for name in names:
            path = (root / name).resolve()
            if path.is_file() and path.is_relative_to(root):
                self._calculation_source_files[str(path)] = {
                    "source": "verified_current_calculation_source",
                    "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "audit_run_id": result["run_id"],
                    **({"source_candidate_id": path.stem} if path.parent.name == "m01_candidates" and re.fullmatch(r"C\d{6}", path.stem) else {}),
                }

    def read_window_length(self, text, offset, limit):
        if not getattr(self, "resilient_delivery", False):
            return limit
        window = text[offset : offset + limit]
        return len(window.encode("utf-8")[:512000].decode("utf-8", errors="ignore"))

    def register_rendered_image(self, metadata):
        self.rendered_images.setdefault(metadata["display_png_sha256"], set()).add((metadata["document_id"], metadata["physical_page"], metadata.get("image_name", "")))

    def register_submitted_images(self, messages):
        import base64
        import hashlib

        with self._cache_lock:
            for message in messages:
                blocks = getattr(message, "content", None)
                if not isinstance(blocks, list):
                    continue
                for block in blocks:
                    if isinstance(block, dict) and block.get("type") == "image_url":
                        url = block.get("image_url", {}).get("url", "")
                        if isinstance(url, str) and url.startswith("data:"):
                            sha = hashlib.sha256(base64.b64decode(url.split(",", 1)[1])).hexdigest()
                            if sha in self.rendered_images:
                                self.submitted_images.update(self.rendered_images[sha])

    def displayed_pairs(self):
        if self.rule_id != "M06":
            return set()
        with self._cache_lock:
            return {
                row["offset"]
                for index in self.fact_indexes.values()
                for row in index.get("findings", [])
                if len(row.get("pair", [])) == 2 and all((e["document_id"], e["page"], e.get("image_name", "")) in self.submitted_images for e in row["pair"])
            }

    def source_reference_tokens(self):
        with self._cache_lock:
            return tuple(sorted(self._source_reference_tokens))[:20000]

    def scope_refs(self, action_id):
        with self._cache_lock:
            return set(self._scope_refs.get(action_id, set()))

    def observe_read(self, name, result):
        if not isinstance(result, dict):
            return
        if name == "audit_read_result" and result.get("collection") in {"review_rows", "evidence", "findings"}:
            refs = []

            def visit(value):
                if isinstance(value, dict):
                    for key, child in value.items():
                        if key == "evidence_id" and isinstance(child, str):
                            refs.append(child)
                        elif key.endswith("_refs") and isinstance(child, list):
                            refs.extend(x for x in child if isinstance(x, str))
                        else:
                            visit(child)
                elif isinstance(value, list):
                    for child in value:
                        visit(child)

            visit(result.get("items", []))
            self.ledger.mark_seen(refs)
        if name in {"knowledge_read", "ragflow_retrieve"} and "document_id" in result:
            doc = result["document_id"]
            self.ledger.mark_seen(doc + ":" + str(r.get("id", r.get("chunk_id"))) for r in result.get("items", result.get("chunks", [])))
        path = str(result.get("file_path", ""))
        trusted_page = False
        prefix = "/mnt/user-data/outputs/coverage/pages/offset_"
        if path.startswith(prefix):
            suffix = path.removeprefix(prefix)
            if re.fullmatch(r"[0-9]{6}\.json", suffix):
                offset = int(suffix[:6])
                trusted_page = offset % 10 == 0 and offset < len(self.ledger.units) and (self.outputs / path.removeprefix("/mnt/user-data/outputs/")).is_file()
        if name == "read_file" and trusted_page and isinstance(result.get("text"), str):
            try:
                page = json.loads(result["text"])
            except ValueError:
                page = None
            if isinstance(page, dict):
                rows = page.get("items", [])
                self.ledger.mark_seen(ref for row in rows if isinstance(row, dict) and row.get("kind") != "source_scope" for ref in row.get("source_refs", []))
        self.ledger.save()

    def guarded(self, function):
        with self._cache_lock:
            return function()

    def context(self):
        if self._context is None:
            from bid_audit.core import Context

            self._context = Context(self.project / "manifest.json", self.cache, permitted_root=self.project)
        return self._context

    def result_root(self, run_id, *, preparing=False):
        if not re.fullmatch(self.rule_id + r"_\d{8}T\d{6}_[0-9a-f]{10}", run_id):
            raise ValueError("Only current rule output run IDs are readable. Use the audit calculation run_id, not the SP execution run_id. Known current audit run IDs: " + ", ".join(str(row["run_id"]) for row in self.runs))
        if not preparing and run_id not in {r["run_id"] for r in self.runs}:
            raise ValueError("Only registered current calculation run IDs are readable")
        path = (self.outputs / "bid_audit" / run_id).resolve()
        if not path.is_relative_to(self.outputs):
            raise ValueError("Output path escaped current rule")
        return path

    def materialize_read_pages(self, result):
        """Lossless pages of current raw collections for native read_file roles."""
        root = self.result_root(result["run_id"], preparing=True)
        virtual = "/mnt/user-data/outputs/bid_audit/" + result["run_id"]
        existing = root / "read_pages/index.json"
        if existing.exists():
            index = json.loads(existing.read_text())
            if index.get("audit_run_id") != result["run_id"]:
                raise ValueError("Calculation page index has wrong identity")
            overview = {name: {"total": v["total"], "page_count": len(v["pages"]), "path_pattern": virtual + "/read_pages/" + name + "/offset_<6位补零offset>.json"} for name, v in index["collections"].items()}
            return {"index_path": virtual + "/read_pages/index.json", "rows_per_page": 10, "collections": overview, "reused": True}
        index = {"audit_run_id": result["run_id"], "rows_per_page": 10, "collections": {}}
        overview = {}
        for name in result.get("collections", {}):
            if not re.fullmatch(r"[a-z_]+", name):
                raise ValueError("Invalid collection name")
            source = root / (name + ".jsonl")
            if not source.is_file():
                continue
            rows = [json.loads(line) for line in source.read_text().splitlines() if line.strip()]
            directory = root / "read_pages" / name
            directory.mkdir(parents=True, exist_ok=False)
            pages = []
            for start in range(0, len(rows), 10):
                filename = f"offset_{start:06d}.json"
                path = directory / filename
                items = rows[start : start + 10]
                page = {"audit_run_id": result["run_id"], "collection": name, "offset": start, "total": len(rows), "items": items}
                with path.open("x") as stream:
                    json.dump(page, stream, ensure_ascii=False, indent=2)
                pages.append({"offset": start, "count": len(items), "file_path": virtual + "/read_pages/" + name + "/" + filename})
            index["collections"][name] = {"total": len(rows), "pages": pages}
            overview[name] = {"total": len(rows), "page_count": len(pages), "path_pattern": virtual + "/read_pages/" + name + "/offset_<6位补零offset>.json"}
        (root / "read_pages").mkdir(exist_ok=True)
        with (root / "read_pages" / "index.json").open("x") as stream:
            json.dump(index, stream, ensure_ascii=False, indent=2)
        return {"index_path": virtual + "/read_pages/index.json", "rows_per_page": 10, "collections": overview, "note": "原始记录无损分页，无模型判断；用read_file读取索引和页面，若next_offset非空须继续读完字符窗口。"}

    def build(self):
        async def calculate(manifest_path: str = "/project/manifest.json") -> str:
            def invoke():
                if manifest_path not in {"/project/manifest.json", "project/manifest.json", "manifest.json"}:
                    raise ValueError("Only the supplied clean project manifest is available")
                from bid_audit.runner import run_rule

                from .m01_corpus import parsed_sources

                derived = parsed_sources(self.context(), self.knowledge) if self.knowledge is not None and hasattr(self.knowledge, "index") else None
                if self.shared_cache is not None:
                    from .cache import calculate_cached

                    result = calculate_cached(self.rule_id, self.project, self.outputs, self.cache, self.shared_cache, parsed_texts=derived)
                else:
                    result = run_rule(self.rule_id, self.project / "manifest.json", self.outputs / "bid_audit", permitted_root=self.project, cache_dir=self.cache, parsed_texts=derived)
                result["result_directory"] = "/mnt/user-data/outputs/bid_audit/" + result["run_id"]
                self.runs.append(result)
                if result.get("status") == "error":
                    return result  # Preserve the original tool error; no collections were produced.
                if self.rule_level_review:
                    from .full_results import pack_complete_result
                    from .knowledge import atomic

                    packet = pack_complete_result(self.result_root(result["run_id"]))
                    if self.quality_controls:
                        from .fact_index import build_fact_index, compact_facts

                        index = build_fact_index(packet)
                        self.fact_indexes[result["run_id"]] = index
                        result["fact_index_path"] = result["result_directory"] + "/facts-index.json"
                        result["code_facts"] = compact_facts(index)
                        atomic(self.result_root(result["run_id"]) / "facts-index.json", index)
                    if self.rule_id == "M01" and len(json.dumps(packet, ensure_ascii=False)) > 80000:
                        from .full_results import unpack_complete_result
                        from .m01_delivery import materialize_candidates

                        cols = unpack_complete_result(packet)["collections"]
                        result["m01_navigation"] = materialize_candidates(self.result_root(result["run_id"]), result["result_directory"], packet, cols.get("findings", []), cols.get("evidence", []))
                        result["code_result_delivery"] = "indexed_large_M01"
                    elif len(json.dumps(packet, ensure_ascii=False)) > 80000:
                        from .full_results import unpack_complete_result
                        from .general_delivery import materialize

                        cols = unpack_complete_result(packet)["collections"]
                        result["result_navigation"] = materialize(self.result_root(result["run_id"]), result["result_directory"], packet, cols.get("findings", []), cols.get("evidence", []), collections=cols)
                        result["code_result_delivery"] = "indexed_large_rule_result"
                        if self.quality_controls:
                            index["result_navigation"] = result["result_navigation"]
                            result["code_facts"] = compact_facts(index)
                            # The same full navigation is already a top-level tool field.
                            # Server-side pinned facts retain it for later handoff.
                            result["code_facts"].pop("result_navigation", None)
                            atomic(self.result_root(result["run_id"]) / "facts-index.json", index)
                    else:
                        result["complete_code_result"] = packet
                        result["code_result_delivery"] = "complete_grouped_records"
                    result["complete_result_path"] = result["result_directory"] + "/complete-code-result.json"
                    atomic(self.result_root(result["run_id"]) / "complete-code-result.json", packet)
                else:
                    result["read_file_pages"] = self.materialize_read_pages(result)
                if self.ledger is not None and result.get("status") != "error":
                    self.ledger.register_calculation(result, self.outputs, self.knowledge, json.loads((self.project / "manifest.json").read_text()))
                    result["review_coverage"] = self.ledger.status()
                    result["review_units_path"] = "/mnt/user-data/outputs/coverage/units.json"
                if self.quality_controls:
                    self.register_calculation_sources(result)
                return result

            return await self.recorded("audit_" + self.rule_id.lower(), {"manifest_path": manifest_path}, invoke)

        async def read_complete(run_id: str) -> str:
            from .full_results import pack_complete_result

            def invoke():
                known = next((r for r in self.runs if r["run_id"] == run_id), None)
                self.result_root(run_id)
                if known and known.get("m01_navigation"):
                    return {"complete": False, "delivery": "indexed_large_M01", "summary": {k: known.get(k) for k in ["rule_id", "run_id", "status", "collections"]}, **known["m01_navigation"]}
                if known and known.get("result_navigation"):
                    return {"complete": False, "summary": {k: known.get(k) for k in ["rule_id", "run_id", "status", "collections"]}, **known["result_navigation"]}
                return pack_complete_result(self.result_root(run_id))

            return await self.recorded("audit_read_complete", {"run_id": run_id}, invoke)

        async def read_result(run_id: str, collection: str = "findings", offset: int = 0, limit: int = 10) -> str:
            def invoke():
                from bid_audit.runner import read_result

                root = self.result_root(run_id)
                if self.quality_controls and self.rule_level_review:
                    from .bounded_records import read_bounded

                    return read_bounded(lambda identity, name, start, count: read_result(root, name, start, count), run_id, collection, offset, limit)
                return read_result(root, collection, offset, limit)

            return await self.recorded("audit_read_result", {"run_id": run_id, "collection": collection, "offset": offset, "limit": limit}, invoke)

        async def collection_overview(run_id: str, collection: str = "findings") -> str:
            def invoke():
                if not re.fullmatch(r"[a-z_]+", collection):
                    raise ValueError("Invalid collection name")
                root = self.result_root(run_id)
                summary = json.loads((root / "result.json").read_text())
                if collection not in summary["collections"]:
                    raise ValueError("Unknown collection")
                from .general_delivery import collection_statistics

                counts = Counter()
                rows = []
                total = 0
                for line in (root / (collection + ".jsonl")).read_text().splitlines():
                    value = json.loads(line)
                    rows.append(value)
                    counts[str(value.get("kind", "record"))] += 1
                    total += 1
                return {
                    "run_id": run_id,
                    "collection": collection,
                    "total": total,
                    "record_kinds": dict(counts),
                    **({"summary_statistics": collection_statistics(rows)} if self.quality_controls and collection == "findings" else {}),
                    "note": "全量记录分类计数，不是异常数量，不替代逐条语义复核；用 audit_read_result 分页追溯证据。",
                }

            return await self.recorded("audit_collection_overview", {"run_id": run_id, "collection": collection}, invoke)

        async def project_search(query: str, document_id: str = "", offset: int = 0, limit: int = 8, mode: str = "auto") -> str:
            def invoke():
                from .source_search import search_text

                if not query.strip() or not 1 <= limit <= 20 or offset < 0:
                    raise ValueError("Nonempty query, offset>=0 and limit 1..20 required")
                ctx = self.context()
                hits = []
                for doc in ctx.docs:
                    if document_id and doc["id"] != document_id:
                        continue
                    for page in ctx.pages(doc):
                        text = page["text"]
                        for match in search_text(text, query, mode):
                            hits.append(
                                {
                                    "document_id": doc["id"],
                                    "role": doc["role"],
                                    "bidder": doc.get("bidder"),
                                    "round": doc["round"],
                                    "lot": doc["lot"],
                                    "physical_page": page["page"],
                                    "sha256": doc["sha256"],
                                    "source_channel": "original_text",
                                    "matching_mode": match["mode"],
                                    "matched_terms": match["matched_terms"],
                                    "excerpt": text[max(0, match["start"] - 300) : match["end"] + 600],
                                }
                            )
                    if self.rule_id == "M01" and self.knowledge is not None and doc["id"] in self.knowledge.index["documents"]:
                        for row in self.knowledge.rows(doc["id"]):
                            for match in search_text(row["content"], query, mode):
                                pages = sorted({p[0] for p in row.get("positions", [])})
                                hits.append(
                                    {
                                        "document_id": doc["id"],
                                        "role": doc["role"],
                                        "bidder": doc.get("bidder"),
                                        "round": doc["round"],
                                        "lot": doc["lot"],
                                        "physical_page": pages[0] if len(pages) == 1 else None,
                                        "physical_pages": pages,
                                        "chunk_id": row["id"],
                                        "sha256": doc["sha256"],
                                        "source_channel": "ragflow_mineru",
                                        "matching_mode": match["mode"],
                                        "matched_terms": match["matched_terms"],
                                        "excerpt": row["content"][max(0, match["start"] - 300) : match["end"] + 600],
                                    }
                                )
                return {
                    "query": query,
                    "mode": mode,
                    "total": len(hits),
                    "items": hits[offset : offset + limit],
                    "next_offset": offset + limit if offset + limit < len(hits) else None,
                    "warnings": ctx.warnings,
                    "note": "auto:空格分词按AND检索，无空格按连续短语；literal模式仅连续短语。忽略换行/空白并做NFKC归一。零命中不是原件不可读。",
                }

            return await self.recorded("project_search", {"query": query, "document_id": document_id, "offset": offset, "limit": limit, "mode": mode}, invoke)

        async def project_read(document_id: str, page_start: int = 1, page_count: int = 1, text_offset: int = 0) -> str:
            def invoke():
                if page_start < 1 or not 1 <= page_count <= 5 or text_offset < 0:
                    raise ValueError("Valid physical page and page_count 1..5 required")
                ctx = self.context()
                doc = ctx.by_id[document_id]
                pages = ctx.pages(doc)
                chosen = [page for page in pages if page["page"] is None or page_start <= page["page"] < page_start + page_count]
                result = []
                for page in chosen:
                    text = page["text"]
                    result.append(
                        {
                            "physical_page": page["page"],
                            "text": text[text_offset : text_offset + 18000],
                            "text_offset": text_offset,
                            "text_chars": len(text),
                            "truncated": text_offset + 18000 < len(text),
                            "next_text_offset": text_offset + 18000 if text_offset + 18000 < len(text) else None,
                        }
                    )
                return {"document_id": document_id, "path": doc["path"], "bidder": doc.get("bidder"), "round": doc["round"], "sha256": doc["sha256"], "total_pages": len(pages), "pages": result, "warnings": sorted(set(ctx.warnings))}

            return await self.recorded("project_read", {"document_id": document_id, "page_start": page_start, "page_count": page_count, "text_offset": text_offset}, invoke)

        async def read_file(file_path: str, offset: int = 0, limit: int | None = None) -> str:
            def invoke():
                read_limit = limit if limit is not None else (512000 if getattr(self, "resilient_delivery", False) else 24000)
                if not 0 <= offset or not 1 <= read_limit <= (512000 if getattr(self, "resilient_delivery", False) else 48000):
                    raise ValueError("Invalid text window")
                if file_path.startswith("/knowledge/") and self.knowledge is not None:
                    relative = file_path.removeprefix("/knowledge/")
                    if self.quality_controls:
                        import re

                        page_match = re.fullmatch(r"(d\d{3})/pages/(\d+)(?:-(\d+))?\.md", relative)
                        if page_match:
                            page_data = self.knowledge.page_text(page_match[1], int(page_match[2]), int(page_match[3] or page_match[2]))
                            text = page_data.pop("text")
                            read_limit = self.read_window_length(text, offset, read_limit)
                            window = text[offset : offset + read_limit]
                            return {
                                **page_data,
                                "file_path": file_path,
                                "text": window,
                                "offset": offset,
                                "total_chars": len(text),
                                "next_offset": offset + read_limit if offset + read_limit < len(text) else None,
                                "source": "persistent_MinerU_snapshot",
                                "source_chunks": self.ocr_receipts.describe_window(file_path, offset, len(window)),
                            }
                    if relative == "index.json":
                        text = json.dumps(self.knowledge.context(logical_ids_only=True) if self.quality_controls else self.knowledge.context(), ensure_ascii=False, indent=2)
                    else:
                        parts = Path(relative).parts
                        if len(parts) != 2 or parts[1] not in {"parsed.md", "page_coverage.json"}:
                            raise ValueError("Only current knowledge parsed documents are readable")
                        entry = self.knowledge.document(parts[0])
                        text = (self.knowledge.root / entry["snapshot"] / parts[1]).read_text()
                        read_limit = self.read_window_length(text, offset, read_limit)
                        # Register only fully returned chunks, not truncated windows.
                        window = text[offset : offset + read_limit]
                        if self.ledger is not None:
                            self.ledger.mark_seen(parts[0] + ":" + r["id"] for r in self.knowledge.rows(parts[0]) if r["content"] and r["content"] in window)
                    read_limit = self.read_window_length(text, offset, read_limit)
                    return {
                        "file_path": file_path,
                        "text": text[offset : offset + read_limit],
                        "offset": offset,
                        "total_chars": len(text),
                        "next_offset": offset + read_limit if offset + read_limit < len(text) else None,
                        "source": "persistent_MinerU_snapshot",
                        **({"source_chunks": self.ocr_receipts.describe_window(file_path, offset, len(text[offset : offset + read_limit]))} if self.quality_controls and self.ocr_receipts is not None else {}),
                    }
                relative = file_path.removeprefix("/project/").removeprefix("./")
                if file_path not in {"/project/manifest.json", "manifest.json"} and not file_path.startswith("/mnt/user-data/outputs/"):
                    if Path(relative).is_absolute() or ".." in Path(relative).parts:
                        raise ValueError("Original path must exactly match the clean manifest")
                    ctx = self.context()
                    doc = next((item for item in ctx.docs if item["path"] == relative or item["id"] == relative), None)
                    if doc is None:
                        raise ValueError("Original path must exactly match the clean manifest")
                    pages = ctx.pages(doc)
                    segments, cursor, positions = [], 0, []
                    for page in pages:
                        label = f"[PDF物理页 {page['page']}]\n" if page["page"] is not None else "[无物理页码的原件正文]\n"
                        segment = label + page["text"] + "\n"
                        positions.append((cursor, cursor + len(segment), page["page"]))
                        cursor += len(segment)
                        segments.append(segment)
                    text = "".join(segments)
                    read_limit = self.read_window_length(text, offset, read_limit)
                    return {
                        "file_path": file_path,
                        "document_id": doc["id"],
                        "path": doc["path"],
                        "sha256": doc["sha256"],
                        "bidder": doc.get("bidder"),
                        "round": doc["round"],
                        "text": text[offset : offset + read_limit],
                        "offset": offset,
                        "total_chars": len(text),
                        "next_offset": offset + read_limit if offset + read_limit < len(text) else None,
                        "source_pages": [page for start, end, page in positions if start < offset + read_limit and end > offset],
                        "warnings": sorted(set(ctx.warnings)),
                        "note": "原件提取文本；扫描/表格局限需原图核验；DOCX/TXT不编造物理页码。",
                    }
                if file_path in {"/project/manifest.json", "manifest.json"}:
                    path = self.project / "manifest.json"
                else:
                    prefix = "/mnt/user-data/outputs/"
                    if not file_path.startswith(prefix):
                        raise ValueError("Only current outputs and /project/manifest.json are readable")
                    path = (self.outputs / file_path.removeprefix(prefix)).resolve()
                    if not path.is_relative_to(self.outputs):
                        raise ValueError("Path escaped current outputs")
                if path.suffix.lower() not in ({".json", ".jsonl", ".md", ".txt", ".html"} if self.report_format == "html" else {".json", ".jsonl", ".md", ".txt"}):
                    raise ValueError("Use project_read for original PDF/DOCX text")
                text = path.read_text()
                read_limit = self.read_window_length(text, offset, read_limit)
                provenance = {}
                known = self._calculation_source_files.get(str(path.resolve()))
                if self.quality_controls and known:
                    import hashlib

                    if hashlib.sha256(path.read_bytes()).hexdigest() == known["source_sha256"]:
                        provenance = known
                return {**provenance, "file_path": file_path, "text": text[offset : offset + read_limit], "offset": offset, "total_chars": len(text), "next_offset": offset + read_limit if offset + read_limit < len(text) else None}

            return await self.recorded("read_file", {"file_path": file_path, "offset": offset, "limit": limit}, invoke)

        async def write_file(path: str, content: str) -> str:
            file_path = path

            def invoke():
                prefix = "/mnt/user-data/outputs/"
                if not file_path.startswith(prefix):
                    raise ValueError("Only current outputs are writable")
                path = (self.outputs / file_path.removeprefix(prefix)).resolve()
                if not path.is_relative_to(self.outputs) or path.suffix.lower() not in ({".md", ".json", ".txt", ".html"} if self.report_format == "html" else {".md", ".json", ".txt"}):
                    raise ValueError("Invalid output artifact path")
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("x", encoding="utf-8") as stream:
                    stream.write(content)
                return {"created": file_path}

            return await self.recorded("write_file", {"path": file_path, "content": content}, invoke)

        def make(name, coroutine, description):
            return StructuredTool.from_function(coroutine=coroutine, name=name, description=description)

        from .vision import build_view_tool

        extra = []
        if self.knowledge is not None:
            from .knowledge_tools import build_knowledge_tools

            extra = build_knowledge_tools(self)
        if self.rule_level_review:
            extra.append(make("audit_read_complete", read_complete, "小结果一次返回全部代码集合；common+row恢复原记录。M01大结果明确返回索引和完整持久化路径，可读任意候选；不要重复申请超出上下文的整包。仅当前注册run_id可读。"))
        if self.rule_id == "M01" and self.rule_level_review:
            from .m01_delivery import build_candidate_tool

            extra.append(build_candidate_tool(self))
            if self.report_format == "html":
                from .m01_delivery import build_candidate_index_tool

                extra.append(build_candidate_index_tool(self))
        if self.quality_controls and self.rule_id == "M06":
            from .vision import build_pair_view_tool

            extra.append(build_pair_view_tool(self))
        return extra + [
            build_view_tool(self),
            make(
                "audit_" + self.rule_id.lower(),
                calculate,
                self.rule_id
                + (
                    " 对清洁原件执行指定规则代码，一次返回全部结果与全部集合，complete_code_result内common+row即原记录，含全部字段和正文；不作模型风险判定。"
                    if self.rule_level_review
                    else " 对清洁项目原件执行指定规则代码，返回计算范围、状态、候选集合和run_id；不作风险判定。请先执行此工具，再读取结果、复核原件。"
                ),
            ),
            make(
                "audit_read_result",
                read_result,
                (
                    "读取当前规则结果集合；offset从0开始，limit1..200。小集合可用limit=total一次读取；每次最多60000字符且每条记录完整，next_offset非空须按实际游标继续；证据E000001对应evidence offset=0。"
                    if self.quality_controls and self.rule_level_review
                    else "分页读取当前规则运行的 findings/evidence/records/review_rows 等集合；offset从0开始，limit最多20；证据E000001对应evidence offset=0。"
                ),
            ),
            make("audit_collection_overview", collection_overview, "全量扫描集合，提供记录类别计数以规划复核范围；不标注异常，不替代原件复核。"),
            make("project_search", project_search, "清洁原件检索；auto模式空格词按AND，无空格按短语；literal连续原句，terms按AND。忽略换行/空白与全半角差异。M01另查持久化MinerU原文，返回来源/页码/chunk；不含历史报告。"),
            make("project_read", project_read, "按document_id和物理页码读取原始文件文字，每次1至5页；扫描图像不可见时必须承认视觉核对缺口。"),
            make("read_file", read_file, "读取当前输出、/project/manifest.json或清单内原件。原件可用清单相对路径、/project/相对路径或document_id；PDF/DOCX返回带来源页码的提取文本并支持offset分页。禁止宿主机、非清单历史报告和其他任务。"),
            make("write_file", write_file, "在当前任务outputs中创建新HTML/JSON/TXT产物，不覆盖已有文件。" if self.report_format == "html" else "在当前任务outputs中创建新Markdown/JSON/TXT产物，不覆盖已有文件。"),
        ]
