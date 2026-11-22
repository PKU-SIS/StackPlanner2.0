"""Snapshot metadata only; the native report ledger owns execution and acceptance.

Serializes one outputs root across local Gateway processes. All forks use real
copies; hashes are verified before and after copying. No model controls paths.
"""

from __future__ import annotations

import fcntl
import hashlib
import re
import shutil
from contextlib import contextmanager
from pathlib import Path

from deerflow.sp.report import store as native
from deerflow.sp.report.assembly import final_binding_error
from deerflow.sp.report.ledger import ReportLedger, ReportValidationError, utc_now_iso


def file_manifest(root: Path) -> dict[str, str]:
    if not root.is_dir():
        raise ReportValidationError("snapshot directory is missing")
    if root.is_symlink():
        raise ReportValidationError("snapshot root is a symlink")
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ReportValidationError("snapshot contains a symlink")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        elif not path.is_dir():
            raise ReportValidationError("snapshot contains a special file")
    return result


def _safe(root: Path, path: Path) -> Path:
    if path.is_symlink() or any(p.is_symlink() for p in path.parents if p != root.parent):
        raise ReportValidationError("symlink path is forbidden")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ReportValidationError("path escapes report workspace")
    return path


class ReportTurnStore:
    def __init__(self, outputs_path: Path, *, user_id: str, thread_id: str):
        self.outputs = Path(outputs_path).absolute()
        self.root = self.outputs / "report_dialogue"
        if not user_id or not thread_id:
            raise ReportValidationError("dialogue requires owner identity")
        self.owner = {"user_id": user_id, "thread_id": thread_id}
        _safe(self.outputs, self.root)

    @contextmanager
    def locked(self):
        with native.report_root_lock(self.outputs):
            self.root.mkdir(parents=True, exist_ok=True)
            with _safe(self.outputs, self.root / ".lock").open("a") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle, fcntl.LOCK_UN)

    def index(self):
        path = _safe(self.outputs, self.root / "index.json")
        value = native.read_json(path) if path.exists() else {"schema_version": 1, "owner": self.owner, "head": None, "active_version": None, "versions": {}, "requests": {}}
        if value.get("owner") != self.owner:
            raise ReportValidationError("report dialogue owner mismatch")
        return value

    def _save(self, index):
        native.write_json_atomic(self.root / "index.json", index)

    def directory(self, turn_id):
        if not isinstance(turn_id, str) or not re.fullmatch(r"t[0-9]{4,}", turn_id):
            raise ReportValidationError("invalid turn identity")
        return _safe(self.outputs, self.root / "turns" / turn_id)

    def inspect(self, turn_id=None):
        index = self.index()
        if turn_id is None:
            return {"head": index["head"], "active_version": index.get("active_version") or index["head"], "versions": index["versions"]}
        path = self.directory(turn_id) / "turn.json"
        if not path.exists():
            raise ReportValidationError("unknown turn in this report session")
        return native.read_json(path)

    def _record(self, turn):
        native.write_json_atomic(self.directory(turn["turn_id"]) / "turn.json", turn)

    def _allocate(self, index, *, message_id, raw_query, resolved_query, base=None, mode="revision"):
        if not message_id or not raw_query.strip() or not resolved_query.strip():
            raise ReportValidationError("message identity and complete requirements are required")
        # Retry identity is the source message, independent of model choices.
        if message_id in index["requests"]:
            old = self.inspect(index["requests"][message_id])
            if old["raw_query"] != raw_query:
                raise ReportValidationError("source message identity was reused with different content")
            return old, False
        existing = [p.name for p in (self.root / "turns").glob("t*")] if (self.root / "turns").exists() else []
        number = max([int(p[1:]) for p in existing if re.fullmatch(r"t[0-9]+", p)] or [0]) + 1
        turn = {
            "turn_id": f"t{number:04d}",
            "owner": self.owner,
            "message_id": message_id,
            "raw_query": raw_query,
            "resolved_query": resolved_query,
            "resolved_query_provenance": {"kind": "derived", "source_message_id": message_id, "base_version_id": base},
            "base_version_id": base,
            "expected_head": index["head"],
            "mode": mode,
            "requirements_status": "resolved",
            "status": "preparing",
            "created_at": utc_now_iso(),
        }
        self._record(turn)
        index["requests"][message_id] = turn["turn_id"]
        self._save(index)
        return turn, True

    def _paths(self, report_id):
        if not isinstance(report_id, str) or not re.fullmatch(r"r[1-9][0-9]*", report_id):
            raise ReportValidationError("invalid native report identity")
        paths = native.ReportPaths(self.outputs, report_id)
        _safe(self.outputs, paths.directory)
        return paths

    def _accepted(self, paths):
        ledger = ReportLedger.load(paths)
        delivery = ledger.payload.get("delivered") or {}
        if ledger.payload.get("status") != "delivered" or not delivery:
            raise ReportValidationError("native delivered report required")
        reason = final_binding_error(ledger)
        if reason:
            raise ReportValidationError(f"native delivery invalid: {reason}")
        if ledger.payload.get("report_id") != paths.report_id:
            raise ReportValidationError("native report identity mismatch")
        final = ledger.payload["final_report"]
        if delivery.get("sha256") != final.get("sha256") or delivery.get("final_artifact_id") != final.get("artifact_id"):
            raise ReportValidationError("delivery final binding mismatch")
        if native.read_json(paths.delivery_file(delivery["delivery_id"])) != delivery:
            raise ReportValidationError("native delivery record mismatch")
        # A recoverable snapshot cannot depend on mutable files outside it.
        for group in ("inputs", "materials", "drafts", "summaries", "candidates", "reviews", "final_reports"):
            for ref in ledger.payload.get(group) or []:
                if ref.get("path"):
                    _safe(paths.directory, Path(ref["path"]))
        for name in ("request", "tree", "candidate", "review", "final_report"):
            ref = ledger.payload.get(name)
            if isinstance(ref, dict) and ref.get("path"):
                _safe(paths.directory, Path(ref["path"]))
        return ledger

    def _snapshot(self, turn, paths):
        directory = self.directory(turn["turn_id"])
        dest = directory / "artifacts"
        before = file_manifest(paths.directory)
        if dest.exists():
            if (directory / "manifest.json").exists():
                manifest = self.verify(turn["turn_id"])
                if manifest["files"] != before:
                    raise ReportValidationError("snapshot differs from native delivered files")
                return manifest
            # A crash after rename but before manifest replace is recoverable
            # only when the complete independent copy still matches its source.
            if file_manifest(dest) != before:
                raise ReportValidationError("uncommitted snapshot checksum mismatch")
        else:
            temp = directory / "artifacts.pending"
            if temp.exists():
                file_manifest(temp)  # Reject symlinks before resuming a copy.
            shutil.copytree(paths.directory, temp, dirs_exist_ok=True)
            if file_manifest(temp) != before or file_manifest(paths.directory) != before:
                raise ReportValidationError("snapshot changed while copying")
            temp.rename(dest)
        manifest = {"schema_version": 1, "owner": self.owner, "origin_directory": str(paths.directory), "origin_report_id": paths.report_id, "files": before, "snapshotted_at": utc_now_iso()}
        native.write_json_atomic(directory / "manifest.json", manifest)
        return manifest

    def verify(self, turn_id):
        turn = self.inspect(turn_id)
        manifest = native.read_json(self.directory(turn_id) / "manifest.json")
        if turn["owner"] != self.owner or manifest["owner"] != self.owner:
            raise ReportValidationError("snapshot owner mismatch")
        if file_manifest(self.directory(turn_id) / "artifacts") != manifest["files"]:
            raise ReportValidationError("snapshot checksum mismatch")
        return manifest

    def freeze(self, report_id, *, resolved_query):
        with self.locked():
            index = self.index()
            for version, ref in index["versions"].items():
                if ref["report_id"] == report_id:
                    self.verify(version)
                    return self.inspect(version)
            paths = self._paths(report_id)
            ledger = self._accepted(paths)
            turn, _ = self._allocate(index, message_id=f"origin:{report_id}", raw_query=native.read_text(paths.request), resolved_query=resolved_query, mode="origin")
            self._snapshot(turn, paths)
            tree = native.read_json(Path(ledger.payload["tree"]["path"]))
            turn.update(
                status="accepted",
                report_id=report_id,
                delivery=ledger.payload["delivered"],
                requirements_status="native_accepted",
                native_requirement_context={
                    "requirements": tree.get("requirements", []),
                    "inputs": [{**{key: item.get(key) for key in ("input_id", "source", "source_message_id", "applied")}, "text": native.read_text(Path(item["path"])).removesuffix("\n")} for item in ledger.inputs()],
                    "user_decisions": ledger.payload.get("user_decisions", []),
                    "length_contract": ledger.payload.get("length_contract"),
                    "authority": "accepted_requirements_and_decisions; raw inputs are provenance, not cumulative active demands",
                },
            )
            self._record(turn)
            index["head"] = turn["turn_id"]
            index["active_version"] = turn["turn_id"]
            index["versions"][turn["turn_id"]] = {"report_id": report_id, "base_version_id": None}
            self._save(index)
            return turn

    def _restore(self, version_id):
        index = self.index()
        if version_id not in index["versions"]:
            raise ReportValidationError("base must be an accepted version in this session")
        manifest = self.verify(version_id)
        native_root = self.outputs / native.REPORT_ROOT
        reserved = [item.name for item in native_root.iterdir() if item.is_dir()] if native_root.exists() else []
        reserved.extend(ref["report_id"] for ref in index["versions"].values())
        reserved.extend(turn["report_id"] for turn_id in index["requests"].values() if (turn := self.inspect(turn_id)).get("report_id"))
        paths = self._paths(native.next_identifier("r", reserved))
        source = self.directory(version_id) / "artifacts"
        shutil.copytree(source, paths.directory)
        old = manifest["origin_directory"]
        old_virtual = f"{native.OUTPUTS_VIRTUAL_PREFIX}/report/{manifest['origin_report_id']}"
        new_virtual = paths.virtual(paths.directory)

        def remap(value):
            if isinstance(value, str):
                for start, replacement in ((old, str(paths.directory)), (old_virtual, new_virtual)):
                    if value == start or value.startswith(start + "/"):
                        return replacement + value[len(start) :]
                return value
            if isinstance(value, list):
                return [remap(x) for x in value]
            if isinstance(value, dict):
                return {k: paths.report_id if k == "report_id" and v == manifest["origin_report_id"] else remap(v) for k, v in value.items()}
            return value

        changed = {}
        # JSON control documents are rebased; materials and canonical drafts stay byte-identical.
        for relative in manifest["files"]:
            if relative.endswith(".json"):
                path = paths.directory / relative
                payload = native.read_json(path)
                mapped = remap(payload)
                if mapped != payload:
                    native.write_json_atomic(path, mapped)
                    changed[relative] = {"derived_from": manifest["files"][relative], "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        known_hashes = {relative: {manifest["files"][relative], ref["sha256"]} for relative, ref in changed.items()}

        def refresh_hashes(value):
            if isinstance(value, list):
                return [refresh_hashes(item) for item in value]
            if not isinstance(value, dict):
                return value
            result = {key: refresh_hashes(item) for key, item in value.items()}
            raw_path = result.get("path")
            if isinstance(raw_path, str) and isinstance(result.get("sha256"), str):
                target = Path(raw_path)
                if target.is_relative_to(paths.directory):
                    relative = target.relative_to(paths.directory).as_posix()
                    # Only update a file-hash binding, never a semantic digest.
                    if relative in changed and relative != "ledger.json" and value["sha256"] in known_hashes[relative]:
                        result["sha256"] = hashlib.sha256(target.read_bytes()).hexdigest()
            return result

        for _ in range(len(changed) + 1):
            updated = False
            for relative in list(changed):
                if relative == "ledger.json":
                    continue
                path = paths.directory / relative
                payload = native.read_json(path)
                rebound = refresh_hashes(payload)
                if rebound != payload:
                    native.write_json_atomic(path, rebound)
                    changed[relative]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
                    known_hashes[relative].add(changed[relative]["sha256"])
                    updated = True
            if not updated:
                break
        else:
            raise ReportValidationError("control hash bindings did not converge")
        ledger = ReportLedger.load(paths)
        ledger.payload = refresh_hashes(ledger.payload)
        changed.pop("ledger.json", None)
        ledger.payload["dialogue_restore"] = {"base_version_id": version_id, "control_rebase": changed, "ledger_derived_from": manifest["files"]["ledger.json"]}
        ledger.save()
        # Prove canonical content survived a real copy and path rebasing.
        after = file_manifest(paths.directory)
        for relative, digest in manifest["files"].items():
            if relative.startswith(("drafts/", "materials/", "user_inputs/")) and relative != "materials/catalog.md":
                if after.get(relative) != digest:
                    raise ReportValidationError("fork changed preserved canonical content")
        return paths

    def restore(self, version_id):
        with self.locked():
            return self._restore(version_id)

    def _require_idle(self, index):
        for turn_id in index["requests"].values():
            if self.inspect(turn_id)["status"] in {"preparing", "prepared", "executing"}:
                raise ReportValidationError("another report turn is still in progress")

    def prepare(self, base_version_id, *, message_id, raw_query, resolved_query, change_hint=""):
        with self.locked():
            index = self.index()
            if message_id in index["requests"]:
                old, _ = self._allocate(index, message_id=message_id, raw_query=raw_query, resolved_query=resolved_query)
                return old
            self._require_idle(index)
            if base_version_id not in index["versions"]:
                raise ReportValidationError("unknown accepted base version")
            self.verify(base_version_id)
            turn, _ = self._allocate(index, message_id=message_id, raw_query=raw_query, resolved_query=resolved_query, base=base_version_id)
            try:
                paths = self._restore(base_version_id)
                turn.update(status="prepared", report_id=paths.report_id, change_hint=change_hint)
                self._record(turn)
                native.write_json_atomic(self.directory(turn["turn_id"]) / "resolved_query.json", {"text": resolved_query, "provenance": turn["resolved_query_provenance"]})
                return turn
            except Exception as exc:
                turn.update(status="failed", diagnostic={"error": str(exc)})
                self._record(turn)
                raise

    def publish(self, turn_id):
        with self.locked():
            index = self.index()
            turn = self.inspect(turn_id)
            if turn["status"] == "accepted":
                self.verify(turn_id)
                if turn_id not in index["versions"]:
                    if index["head"] != turn["expected_head"]:
                        raise ReportValidationError("accepted head changed during publication recovery")
                    index["versions"][turn_id] = {"report_id": turn["report_id"], "base_version_id": turn["base_version_id"]}
                    index["head"] = turn_id
                    index["active_version"] = turn_id
                    self._save(index)
                return turn
            if turn["status"] not in {"prepared", "executing"}:
                raise ReportValidationError("turn is not publishable")
            if index["head"] != turn["expected_head"]:
                raise ReportValidationError("accepted head changed during execution")
            paths = self._paths(turn["report_id"])
            ledger = self._accepted(paths)
            if not ledger.payload.get("resume") or not ledger.payload.get("dialogue_turn"):
                raise ReportValidationError("new native resume and delivery required")
            binding = ledger.payload["dialogue_turn"]
            if binding.get("turn_id") != turn_id or binding.get("resolved_query") != turn["resolved_query"]:
                raise ReportValidationError("delivery dialogue binding mismatch")
            if ledger.payload["delivered"].get("run_id") != binding.get("run_id"):
                raise ReportValidationError("delivery belongs to another run")
            changes = self.local_diff(turn, ledger)
            native.write_json_atomic(self.directory(turn_id) / "changes.json", changes)
            self._snapshot(turn, paths)
            turn.update(status="accepted", delivery=ledger.payload["delivered"], accepted_at=utc_now_iso())
            self._record(turn)
            index["versions"][turn_id] = {"report_id": paths.report_id, "base_version_id": turn["base_version_id"]}
            index["head"] = turn_id
            index["active_version"] = turn_id
            self._save(index)
            return turn

    def end(self, turn_id, *, status, diagnostic):
        if status not in {"failed", "cancelled"}:
            raise ReportValidationError("invalid terminal status")
        with self.locked():
            turn = self.inspect(turn_id)
            if turn["status"] in {"accepted", "exact_reuse", "read_only"}:
                raise ReportValidationError("cannot change an archived turn")
            if turn["status"] in {"failed", "cancelled"}:
                return turn
            turn.update(status=status, diagnostic=diagnostic, ended_at=utc_now_iso())
            self._record(turn)
            pointer = native.pointer_path(self.outputs)
            current = native.read_json(pointer) if pointer.exists() else {}
            index = self.index()
            if current.get("report_id") == turn.get("report_id") and index["head"]:
                native.write_json_atomic(pointer, {"report_id": index["versions"][index.get("active_version") or index["head"]]["report_id"]})
            return turn

    def local_diff(self, turn, ledger):
        base_dir = self.directory(turn["base_version_id"]) / "artifacts"
        base = native.read_json(base_dir / "ledger.json")
        old = {d["node_id"]: d for d in base.get("drafts", []) if d.get("is_current")}
        new = {d["node_id"]: d for d in ledger.payload.get("drafts", []) if d.get("is_current")}
        kept, changed, removed = [], [], []
        for node_id, draft in old.items():
            if node_id not in new:
                removed.append(node_id)
            elif new[node_id].get("sha256") == draft.get("sha256"):
                kept.append(node_id)
            else:
                changed.append(node_id)
        for item in ledger.payload.get("draft_dispositions") or []:
            if item.get("action") == "keep" and item.get("node_id") in changed:
                raise ReportValidationError("kept canonical draft was modified")
        return {
            "kept_nodes": kept,
            "changed_nodes": changed,
            "removed_nodes": removed,
            "added_nodes": sorted(set(new) - set(old)),
            "scope": "canonical node bytes; final citation display and assembly may change",
            "factual_correctness_verified": False,
        }

    def reuse(self, version_id, *, message_id, raw_query, response="", mode="exact_reuse"):
        if mode not in {"exact_reuse", "read_only"}:
            raise ReportValidationError("invalid reuse mode")
        with self.locked():
            index = self.index()
            if message_id in index["requests"]:
                previous = self.inspect(index["requests"][message_id])
                if previous["raw_query"] != raw_query:
                    raise ReportValidationError("source message identity was reused with different content")
                return previous
            self._require_idle(index)
            if version_id not in index["versions"]:
                raise ReportValidationError("unknown accepted version")
            manifest = self.verify(version_id)
            base = self.inspect(version_id)
            turn, _ = self._allocate(index, message_id=message_id, raw_query=raw_query, resolved_query=base["resolved_query"], base=version_id, mode=mode)
            source = self.directory(version_id) / "artifacts"
            shutil.copytree(source, self.directory(turn["turn_id"]) / "artifacts")
            if file_manifest(self.directory(turn["turn_id"]) / "artifacts") != manifest["files"]:
                raise ReportValidationError("reuse snapshot checksum mismatch")
            native.write_json_atomic(self.directory(turn["turn_id"]) / "manifest.json", manifest)
            native.write_text_atomic(self.directory(turn["turn_id"]) / "response.md", response)
            turn.update(status=mode, reused_version_id=version_id, report_id=base["report_id"], requirements_status=base.get("requirements_status", "resolved"))
            if base.get("native_requirement_context") is not None:
                turn["native_requirement_context"] = base["native_requirement_context"]
            self._record(turn)
            if mode == "exact_reuse":
                index["active_version"] = version_id
                self._save(index)
                native.write_json_atomic(native.pointer_path(self.outputs), {"report_id": base["report_id"]})
            return turn

    def archive_resolution_failure(self, *, message_id, raw_query, base_version_id, diagnostic):
        """A failed resolver is still a self-contained dialogue turn, not a version."""
        with self.locked():
            index = self.index()
            if message_id in index["requests"]:
                return self.inspect(index["requests"][message_id])
            manifest = self.verify(base_version_id)
            turn, _ = self._allocate(index, message_id=message_id, raw_query=raw_query, resolved_query="Unresolved request", base=base_version_id, mode="resolution")
            source = self.directory(base_version_id) / "artifacts"
            shutil.copytree(source, self.directory(turn["turn_id"]) / "artifacts")
            if file_manifest(self.directory(turn["turn_id"]) / "artifacts") != manifest["files"]:
                raise ReportValidationError("failed-resolution snapshot checksum mismatch")
            native.write_json_atomic(self.directory(turn["turn_id"]) / "manifest.json", manifest)
            turn.update(status="failed", resolved_query=None, requirements_status="unresolved", diagnostic=diagnostic)
            self._record(turn)
            return turn
