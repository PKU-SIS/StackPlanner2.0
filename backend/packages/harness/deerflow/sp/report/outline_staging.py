"""Report-Only Outline tree staging for accepted-tree revisions.

The tool does not commit a tree.  It merges a small structured patch into a
report/action-scoped staging file, then (only on finalize) writes a complete
candidate to the host-assigned delivery path.  The normal Outline acceptance
path remains the only writer of the accepted tree/ledger.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from deerflow.sp.report import store as report_store
from deerflow.sp.report.ledger import ReportLedger, ReportValidationError
from deerflow.sp.report.tree import validate_tree


class OutlineStageError(ReportValidationError):
    """A staging/finalization failure that must not touch the accepted tree."""


_PATCH_KEYS = frozenset({
    "report_title",
    "requirements",
    "upsert_nodes",
    "remove_node_ids",
    "gap_outcomes",
    "write_requests",
    "user_decisions",
    "feedback_dispositions",
    "draft_dispositions",
})
_ACTION_FIELDS = frozenset({
    "gap_outcomes",
    "write_requests",
    "user_decisions",
    "feedback_dispositions",
    "draft_dispositions",
})


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class OutlineTreeStage:
    """One report/action staging session.

    The accepted tree is the baseline.  Planning content (title, requirements,
    nodes) is inherited; action-scoped fields start empty so historical
    outcomes/decisions/removals are never replayed.  ``write_requests`` is
    inherited only when explicitly supplied by this action.
    """

    def __init__(
        self,
        paths: report_store.ReportPaths,
        action_id: str,
        delivery_path: Path,
        *,
        resume_action_id: str | None = None,
    ):
        self.paths = paths
        self.action_id = str(action_id)
        self.delivery_path = Path(delivery_path)
        self.resume_action_id = str(resume_action_id) if resume_action_id else None
        self.stage_dir = paths.directory / "outline" / "_staging"
        self.stage_path = self.stage_dir / f"{self.action_id}.json"
        self._resumed_from: str | None = None
        self._baseline_version = 0
        self._baseline_sha = ""
        self._baseline: dict[str, Any] = {}
        self._candidate: dict[str, Any] = {}
        self._write_requests_set = False
        self._load()

    def _load(self) -> None:
        ledger = ReportLedger.load(self.paths)
        ref = ledger.payload.get("tree")
        if not isinstance(ref, Mapping) or not ref.get("path"):
            raise OutlineStageError("accepted tree baseline is missing")
        try:
            version = int(ref.get("version") or 0)
            tree_path = Path(str(ref["path"]))
            tree_text = tree_path.read_text(encoding="utf-8")
        except (OSError, TypeError, ValueError) as exc:
            raise OutlineStageError(f"accepted tree baseline is unreadable: {exc}") from exc
        baseline_sha = _sha256_text(tree_text)
        if str(ref.get("sha256") or "") and str(ref["sha256"]) != baseline_sha:
            raise OutlineStageError("accepted tree baseline hash does not match ledger")
        baseline = json.loads(tree_text)
        if not isinstance(baseline, dict):
            raise OutlineStageError("accepted tree baseline is not an object")
        self._baseline_version = version
        self._baseline_sha = baseline_sha
        self._baseline = copy.deepcopy(baseline)
        stage_source = self.stage_path
        if not stage_source.exists() and self.resume_action_id and self.resume_action_id != self.action_id:
            stage_source = self.stage_dir / f"{self.resume_action_id}.json"
        if stage_source.exists():
            try:
                stage = json.loads(stage_source.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise OutlineStageError(f"staging file is unreadable: {exc}") from exc
            if (
                stage.get("report_id") != self.paths.report_id
                or stage.get("base_version") is None
                or int(stage.get("base_version") or 0) != version
                or str(stage.get("base_sha") or "") != baseline_sha
            ):
                raise OutlineStageError("staging file belongs to a different report/base")
            if stage_source == self.stage_path and stage.get("action_id") != self.action_id:
                raise OutlineStageError("staging file belongs to a different action")
            if stage_source != self.stage_path:
                if stage.get("action_id") != self.resume_action_id:
                    raise OutlineStageError("resume staging source identity mismatch")
                self._resumed_from = self.resume_action_id
            candidate = stage.get("candidate")
            if not isinstance(candidate, dict):
                raise OutlineStageError("staging file candidate is invalid")
            self._candidate = candidate
            self._write_requests_set = bool(stage.get("write_requests_set"))
        else:
            if self.resume_action_id and self.resume_action_id != self.action_id:
                raise OutlineStageError("requested resume stage does not exist")
            self._candidate = self._baseline_candidate(baseline)
            self._write_requests_set = False

    def _baseline_candidate(self, baseline: Mapping[str, Any]) -> dict[str, Any]:
        """Copy planning fields, reset every action-scoped field."""
        candidate = {
            "report_title": baseline.get("report_title"),
            "requirements": copy.deepcopy(baseline.get("requirements") or []),
            "nodes": copy.deepcopy(baseline.get("nodes") or []),
            "removed_node_ids": [],
        }
        for field in _ACTION_FIELDS:
            candidate[field] = []
        return candidate

    def _check_current_base(self) -> None:
        ledger = ReportLedger.load(self.paths)
        ref = ledger.payload.get("tree")
        if not isinstance(ref, Mapping):
            raise OutlineStageError("accepted tree baseline is missing")
        current_version = int(ref.get("version") or 0)
        current_sha = str(ref.get("sha256") or "")
        if current_version != self._baseline_version:
            raise OutlineStageError(
                f"stale base_version: staged={self._baseline_version}, current={current_version}"
            )
        if current_sha and current_sha != self._baseline_sha:
            raise OutlineStageError("stale base tree sha256")

    def _save(self) -> None:
        self.stage_dir.mkdir(parents=True, exist_ok=True)
        report_store.write_json_atomic(self.stage_path, {
            "report_id": self.paths.report_id,
            "action_id": self.action_id,
            "base_version": self._baseline_version,
            "base_sha": self._baseline_sha,
            "resumed_from": self._resumed_from,
            "write_requests_set": self._write_requests_set,
            "candidate": self._candidate,
        })

    @staticmethod
    def _merge_node(nodes: dict[str, dict[str, Any]], patch: Any) -> None:
        if not isinstance(patch, Mapping):
            raise OutlineStageError("upsert_nodes items must be objects")
        node_id = str(patch.get("node_id") or "").strip()
        if not node_id:
            raise OutlineStageError("upsert_nodes item requires node_id")
        if node_id in nodes:
            nodes[node_id].update(copy.deepcopy(dict(patch)))
        else:
            nodes[node_id] = copy.deepcopy(dict(patch))

    def apply(self, patch: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(patch, Mapping):
            raise OutlineStageError("patch must be an object")
        unknown = sorted(set(patch) - _PATCH_KEYS)
        if unknown:
            raise OutlineStageError(f"unsupported patch fields: {', '.join(unknown)}")
        with report_store.report_lock(self.paths):
            self._check_current_base()
            self._load()
            candidate = copy.deepcopy(self._candidate)
            nodes = {str(n["node_id"]): n for n in candidate.get("nodes") or []}
            if "upsert_nodes" in patch:
                for item in patch["upsert_nodes"] or []:
                    self._merge_node(nodes, item)
                candidate["nodes"] = list(nodes.values())
            removed = list(candidate.get("removed_node_ids") or [])
            for raw in patch.get("remove_node_ids") or []:
                node_id = str(raw)
                if node_id not in nodes:
                    if node_id in removed:
                        continue
                    raise OutlineStageError(f"remove_node_ids references unknown node: {node_id}")
                nodes.pop(node_id)
                candidate["nodes"] = list(nodes.values())
                if node_id not in removed:
                    removed.append(node_id)
            candidate["removed_node_ids"] = removed
            if "report_title" in patch:
                candidate["report_title"] = patch["report_title"]
            if "requirements" in patch:
                candidate["requirements"] = copy.deepcopy(patch["requirements"])
            for field in ("gap_outcomes", "user_decisions", "feedback_dispositions", "draft_dispositions"):
                if field in patch:
                    if not isinstance(patch[field], list):
                        raise OutlineStageError(f"{field} must be a list")
                    candidate[field] = copy.deepcopy(patch[field])
            if "write_requests" in patch:
                if not isinstance(patch["write_requests"], list):
                    raise OutlineStageError("write_requests must be a list")
                candidate["write_requests"] = copy.deepcopy(patch["write_requests"])
                self._write_requests_set = True
            self._candidate = candidate
            self._save()
            return self._receipt()

    def _receipt(self) -> dict[str, Any]:
        return {
            "staged": True,
            "base_version": self._baseline_version,
            "nodes": len(self._candidate.get("nodes") or []),
            "gap_outcomes": len(self._candidate.get("gap_outcomes") or []),
            "write_requests": len(self._candidate.get("write_requests") or []),
            "write_requests_set": self._write_requests_set,
        }

    def finalize(self) -> dict[str, Any]:
        with report_store.report_lock(self.paths):
            self._check_current_base()
            self._load()
            candidate = copy.deepcopy(self._candidate)
            if not self._write_requests_set:
                raise OutlineStageError(
                    "write_requests must be explicitly supplied by this action; "
                    "omission does not preserve or clear historical write scheduling"
                )
            old_ids = [str(n["node_id"]) for n in self._baseline.get("nodes") or []]
            current_ids = {str(n["node_id"]) for n in candidate.get("nodes") or []}
            removed = {str(item) for item in candidate.get("removed_node_ids") or []}
            missing = [node_id for node_id in old_ids if node_id not in current_ids and node_id not in removed]
            if missing:
                raise OutlineStageError(
                    "accepted nodes are missing without removed_node_ids: " + ", ".join(missing)
                )
            ledger = ReportLedger.load(self.paths)
            try:
                validate_tree(candidate, ledger)
            except Exception as exc:
                raise OutlineStageError(f"candidate rejected by tree validation: {exc}") from exc
            self._save()
            report_store.write_json_atomic(self.delivery_path, candidate)
            digest = hashlib.sha256(self.delivery_path.read_bytes()).hexdigest()
            return {
                "staged": False,
                "delivery_path": str(self.delivery_path),
                "sha256": digest,
                "nodes": len(candidate.get("nodes") or []),
                "gap_outcomes": len(candidate.get("gap_outcomes") or []),
                "write_requests": len(candidate.get("write_requests") or []),
                "base_version": self._baseline_version,
            }


def build_outline_staging_tool(
    *,
    paths: report_store.ReportPaths,
    action_id: str,
    delivery_path: Path,
    resume_action_id: str | None = None,
):
    from langchain.tools import tool

    @tool("report_update_outline_tree", parse_docstring=True)
    def report_update_outline_tree(
        base_version: int,
        patch: dict[str, Any],
        finalize: bool = False,
    ) -> str:
        """Stage a small patch to the accepted Outline tree, or finalize a candidate.

        Use only for a revise action on an accepted tree.  The tool preserves
        accepted planning fields, resets historical action fields, and never
        commits the accepted tree.  ``write_requests`` must be explicitly
        supplied (an empty list means intentional clear).  ``finalize=true``
        validates the merged tree and writes the complete candidate to the
        host-assigned delivery path; acceptance remains the host's job.

        Args:
            base_version: Accepted tree version this patch is based on.
            patch: Structured patch: report_title/requirements/upsert_nodes/
                remove_node_ids/gap_outcomes/write_requests/user_decisions/
                feedback_dispositions/draft_dispositions.
            finalize: If true, validate and write a complete candidate.
        """
        try:
            stage = OutlineTreeStage(
                paths=paths,
                action_id=action_id,
                delivery_path=delivery_path,
                resume_action_id=resume_action_id,
            )
            if int(base_version) != int(stage._baseline_version):
                raise OutlineStageError(
                    f"stale base_version: supplied={base_version}, current={stage._baseline_version}"
                )
            if finalize:
                stage.apply(patch)
                return json.dumps(stage.finalize(), ensure_ascii=False)
            return json.dumps(stage.apply(patch), ensure_ascii=False)
        except OutlineStageError as exc:
            return json.dumps({"staged": False, "error": str(exc)}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"staged": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False)

    return report_update_outline_tree
