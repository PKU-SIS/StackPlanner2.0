"""Stage a structured Outline proposal at its host-owned delivery path."""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from langchain.tools import tool
from pydantic import StrictInt

from deerflow.sp.report import store
from deerflow.sp.report.draft_choices import serialize_draft_choices
from deerflow.sp.report.ledger import ReportLedger, ReportValidationError
from deerflow.sp.report.tree import validate_tree_with_diagnostics


def normalize_evidence_sources(tree: dict[str, Any], ledger: ReportLedger):
    """One deterministic pass: material identity owns its canonical source ID."""
    corrected = deepcopy(tree)
    changes = []
    known = {row.get("source_id") for row in ledger.payload.get("sources", [])}
    for node in corrected.get("nodes", []) if isinstance(corrected.get("nodes"), list) else []:
        if not isinstance(node, dict) or not isinstance(node.get("evidence"), list):
            continue
        for index, evidence in enumerate(node["evidence"]):
            if not isinstance(evidence, dict) or not isinstance(evidence.get("material_id"), str):
                continue
            material = ledger.material(evidence["material_id"])
            canonical = material.get("source_id") if material else None
            supplied = evidence.get("source_ids")
            if canonical in known and canonical and isinstance(supplied, list) and supplied and all(isinstance(x, str) for x in supplied) and canonical not in supplied:
                changes.append({"node_id": node.get("node_id"), "evidence_index": index, "material_id": evidence["material_id"], "before": supplied, "after": [canonical]})
                evidence["source_ids"] = [canonical]
    return corrected, changes


def _apply_node_choices(tree, ledger, keep_nodes, rewrite_nodes, drop_nodes, decision_reason):
    if any(v is not None for v in (keep_nodes, rewrite_nodes, drop_nodes)):
        return serialize_draft_choices(tree, ledger, keep_nodes=keep_nodes, rewrite_nodes=rewrite_nodes, drop_nodes=drop_nodes, reason=decision_reason)
    if (ledger.payload.get("length_contract") or {}).get("compact_outline") and any(d.get("is_current") for d in ledger.payload.get("drafts") or []):
        raise ReportValidationError("Compact revision requires explicit keep_nodes/rewrite_nodes/drop_nodes and decision_reason; the host generates draft_dispositions")
    return tree


def validate_required_research_registration(tree, ledger, *, required: bool):
    """Fail closed for an explicitly required native initial research round.

    Scope/exception classification stays with the host, not a keyword heuristic.
    Revisions and ordinary unmarked tasks retain their native acceptance behavior.
    """
    if not (required or ledger.payload.get("supplementary_registration_required") is True) or ledger.payload.get("tree"):
        return
    selected = {gap["gap_id"] for gap in ledger.gaps() if gap.get("status") == "open"}
    attached = {gap_id for node in tree.get("nodes", []) if isinstance(node, dict)
                for gap_id in node.get("gap_ids", []) if isinstance(gap_id, str)}
    if not selected.intersection(attached):
        raise ReportValidationError(
            "This initial plan requires the native fixed supplementary-research round: "
            "register actual writing-related evidence needs and attach the returned open gap ids "
            "to their nodes before submitting. Do not invent gaps or bypass research."
        )
    if tree.get("gap_outcomes"):
        raise ReportValidationError("Initial required research has not returned; gap outcomes belong to final consolidation after the fixed research attempt")


def _candidate_budget_feedback(paths, delivery_path, candidate_path, tree, ledger):
    """Offer a patch only for an actual budget rejection, bound to current state."""
    from deerflow.sp.report.length import validate_budgets

    if not ledger.payload.get("length_contract"):
        return {}
    try:
        validate_budgets(tree, ledger)
    except ReportValidationError:
        pass
    except (AttributeError, KeyError, TypeError, ValueError):
        return {}
    else:
        return {}
    candidate_sha = store.sha256_text(store.read_text(candidate_path))
    ledger_sha = store.sha256_text(store.read_text(paths.ledger))
    receipt = {
        "candidate_sha256": candidate_sha,
        "candidate_ledger_sha256": ledger_sha,
        "delivery_sha256": store.sha256_text(store.read_text(delivery_path)),
        "candidate_slot": "delivery" if candidate_path == delivery_path else "invalid",
    }
    store.write_json_atomic(delivery_path.with_suffix(".budget-candidate.json"), receipt)
    return {
        "candidate_budget_patch": {"candidate_sha256": candidate_sha, "candidate_ledger_sha256": ledger_sha},
        "next": (
            "For a budget-only correction, call report_patch_outline_budget with these two exact "
            "candidate hashes and node_budgets. Omit accepted-tree base fields and all disposition "
            "fields. Do not resend the full tree. All validation and normal host acceptance still apply. "
            "Other errors require the usual complete corrected submission."
        ),
    }


def build_submit_outline_tool(
    *, paths: store.ReportPaths, delivery_path: Path, normalize_sources: bool = False,
    require_research_registration: bool = False, allow_staged_budget_patch: bool = False,
    prepared_candidate: bool = False,
):
    if not delivery_path.resolve().is_relative_to((paths.directory / "outline").resolve()):
        raise ReportValidationError("Outline delivery must stay in the bound report directory")

    @tool("report_submit_outline", parse_docstring=True)
    def submit_outline(tree: dict[str, Any], keep_nodes: list[str] | None = None, rewrite_nodes: list[str] | None = None, drop_nodes: list[str] | None = None, decision_reason: str | None = None) -> str:
        """Stage one complete tree object; the host serializes JSON and checks it.

        Prefer this over embedding JSON text in write_file.content. Include all
        root fields, original requirements and nodes. This stages a proposal;
        final acceptance still occurs through the normal report handler.
        After success, return the file-delivery envelope instead of submitting
        a second confirmation or partial tree. Invalid proposals return
        diagnostics and cannot overwrite a still-valid staged proposal.

        Args:
            tree: Complete tree object, not a JSON string or response envelope.
            keep_nodes: Explicit current-draft node IDs to preserve.
            rewrite_nodes: Explicit current-draft node IDs to rewrite.
            drop_nodes: Explicit removed current-draft node IDs to drop.
            decision_reason: Business reason for the node choices. With choices,
                leave tree.draft_dispositions empty; host generates its fields.
        """
        with store.report_lock(paths):
            return stage_outline(tree, keep_nodes, rewrite_nodes, drop_nodes, decision_reason)

    def stage_outline(tree, keep_nodes, rewrite_nodes, drop_nodes, decision_reason):
        ledger = ReportLedger.load(paths)
        # Every new submission supersedes an earlier patch offer, including a
        # valid submission that leaves an old .invalid.json file on disk.
        delivery_path.with_suffix(".budget-candidate.json").unlink(missing_ok=True)
        try:
            if not prepared_candidate:
                tree = _apply_node_choices(tree, ledger, keep_nodes, rewrite_nodes, drop_nodes, decision_reason)
        except ReportValidationError as exc:
            return json.dumps({"saved": False, "validation_ok": False, "error": "invalid_node_choices", "detail": str(exc)})
        corrections = []
        if normalize_sources:
            original = tree
            tree, corrections = normalize_evidence_sources(tree, ledger)
            if corrections:
                original_path = delivery_path.with_suffix(".source-ids-original.json")
                if not original_path.exists():
                    store.write_text_atomic(original_path, json.dumps(original, ensure_ascii=False, indent=2) + "\n")
        content = json.dumps(tree, ensure_ascii=False, indent=2) + "\n"
        try:
            validate_tree_with_diagnostics(tree, ledger)
            validate_required_research_registration(tree, ledger, required=require_research_registration)
        except ReportValidationError as exc:
            # Revalidate against the current ledger: a proposal that was valid
            # before requirements/tree versions changed may now be stale.
            try:
                previous = store.read_json(delivery_path)
                validate_tree_with_diagnostics(previous, ledger)
                validate_required_research_registration(previous, ledger, required=require_research_registration)
            except (OSError, ValueError, TypeError, KeyError, store.ReportStoreError, ReportValidationError):
                previous = None
            if previous is not None:
                invalid_path = delivery_path.with_suffix(".invalid.json")
                store.write_text_atomic(invalid_path, content)
                return json.dumps({
                    "saved": True, "validation_ok": False, "error": "invalid_outline_tree",
                    "detail": str(exc), "invalid_tree_path": paths.virtual(invalid_path),
                    "last_valid_tree_path": paths.virtual(delivery_path),
                    "previous_proposal_preserved": True, "source_id_corrections": corrections,
                    "next": (
                        "This submission failed. The last valid proposal is unchanged. "
                        "If it is your intended tree, return its compact file-delivery envelope; "
                        "otherwise submit a complete corrected tree. Normal host acceptance still applies."
                    ),
                    **(_candidate_budget_feedback(paths, delivery_path, invalid_path, tree, ledger) if allow_staged_budget_patch else {}),
                }, ensure_ascii=False)
            # Retain the original correction contract when no valid proposal
            # exists; an invalid tree still never reaches ledger acceptance.
            store.write_text_atomic(delivery_path, content)
            return json.dumps({
                "saved": True, "validation_ok": False, "error": "invalid_outline_tree", "detail": str(exc),
                "tree_path": paths.virtual(delivery_path), "source_id_corrections": corrections,
                **(_candidate_budget_feedback(paths, delivery_path, delivery_path, tree, ledger) if allow_staged_budget_patch else {}),
            }, ensure_ascii=False)
        store.write_text_atomic(delivery_path, content)
        if store.read_text(delivery_path) != content:
            raise store.ReportStoreError("Outline delivery read-back differs from the submitted tree")
        return json.dumps(
            {"saved": True, "validation_ok": True, "source_id_corrections": corrections, "tree_path": paths.virtual(delivery_path), "next": "Return the compact file-delivery envelope; the normal host gate still decides acceptance."},
            ensure_ascii=False,
        )

    return submit_outline


def _patch_staged_budget(
    *, paths, delivery_path, ledger, candidate_sha256, candidate_ledger_sha256, node_budgets,
    normalize_sources, require_research_registration,
):
    """Patch the latest host-staged candidate without changing business state."""
    from deerflow.sp.report.length import node_budget

    try:
        receipt = store.read_json(delivery_path.with_suffix(".budget-candidate.json"))
        if (
            not isinstance(receipt, dict)
            or not candidate_sha256 or not candidate_ledger_sha256
            or receipt.get("candidate_sha256") != candidate_sha256
            or receipt.get("candidate_ledger_sha256") != candidate_ledger_sha256
            or store.sha256_text(store.read_text(paths.ledger)) != candidate_ledger_sha256
            or store.sha256_text(store.read_text(delivery_path)) != receipt.get("delivery_sha256")
            or receipt.get("candidate_slot") not in {"delivery", "invalid"}
        ):
            raise ReportValidationError("candidate or ledger changed; use the latest native submission feedback")
        candidate_path = delivery_path if receipt["candidate_slot"] == "delivery" else delivery_path.with_suffix(".invalid.json")
        content = store.read_text(candidate_path)
        if store.sha256_text(content) != candidate_sha256:
            raise ReportValidationError("staged candidate hash mismatch")
    except (OSError, ValueError, TypeError, KeyError, store.ReportStoreError) as exc:
        return json.dumps({"saved": False, "validation_ok": False, "error": "stale_candidate_budget_patch", "detail": str(exc)})
    try:
        tree = json.loads(content)
        nodes = {node["node_id"]: node for node in tree["nodes"]}
        if not node_budgets:
            raise ReportValidationError("node_budgets must be nonempty")
        seen = set()
        for item in node_budgets:
            if not isinstance(item, dict) or set(item) != {"node_id", "target_words", "max_words"}:
                raise ReportValidationError("candidate budget patch permits node_id, target_words and max_words only")
            node = nodes.get(item["node_id"])
            if node is None or node.get("kind") != "writable" or item["node_id"] in seen:
                raise ReportValidationError("candidate budget patch must name unique existing writable nodes")
            node_budget({"node_id": item["node_id"], "constraints": item})
            node["constraints"].update(target_words=item["target_words"], max_words=item["max_words"])
            seen.add(item["node_id"])
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        return json.dumps({"saved": False, "validation_ok": False, "error": "invalid_candidate_budget_patch", "detail": str(exc)})
    # Preserve the exact failed candidate before staging its corrected copy.
    original = delivery_path.with_suffix(f".budget-base-{candidate_sha256}.json")
    if not original.exists():
        store.write_text_atomic(original, content)
    submit = build_submit_outline_tool(
        paths=paths, delivery_path=delivery_path, normalize_sources=normalize_sources,
        require_research_registration=require_research_registration, allow_staged_budget_patch=True,
        prepared_candidate=True,
    )
    # Call the host staging implementation, not a model or a second agent turn.
    # The report lock is reentrant; final acceptance stays with the usual handler.
    return submit.func(tree=tree)


def build_patch_outline_budget_tool(
    *, paths: store.ReportPaths, delivery_path: Path, allow_staged_budget_patch: bool = False,
    normalize_sources: bool = False, require_research_registration: bool = False,
):
    """Stage a length-only proposal; the ordinary acceptance handler commits it."""
    if not delivery_path.resolve().is_relative_to((paths.directory / "outline").resolve()):
        raise ReportValidationError("Outline budget delivery must stay in the bound outline directory")

    @tool("report_patch_outline_budget", parse_docstring=True)
    def patch_outline_budget(
        node_budgets: list[dict[str, Any]],
        base_tree_version: StrictInt | None = None,
        base_tree_sha256: str | None = None,
        draft_dispositions: list[dict[str, Any]] | None = None,
        write_requests: list[dict[str, Any]] | None = None,
        user_decisions: list[dict[str, Any]] | None = None,
        feedback_dispositions: list[dict[str, Any]] | None = None,
        keep_nodes: list[str] | None = None,
        rewrite_nodes: list[str] | None = None,
        drop_nodes: list[str] | None = None,
        decision_reason: str | None = None,
        candidate_sha256: str | None = None,
        candidate_ledger_sha256: str | None = None,
    ) -> str:
        """Stage a complete tree from a small, hash-bound word-budget proposal.

        Only target_words and max_words on existing writable nodes may change.
        Requirements, evidence, headings and source identities are copied from
        the hash-verified accepted tree. This tool never commits the proposal.
        After success return the normal file-delivery envelope. Existing
        draft dispositions and write requests are still required for rewrites;
        final acceptance performs all ordinary business validation.
        For a rejected candidate with candidate_budget_patch feedback, supply
        its candidate_sha256 and candidate_ledger_sha256 instead of accepted-tree
        base fields. This changes only existing node budgets in that candidate;
        omit all disposition fields. It still needs full validation and acceptance.

        Args:
            base_tree_version: Exact accepted tree version from the packet.
            base_tree_sha256: Exact accepted tree SHA256 from the packet.
            node_budgets: Nonempty objects with node_id, target_words, max_words only.
            draft_dispositions: Existing keep/rewrite dispositions with node identity.
            write_requests: Existing single-node requests for affected nodes.
            user_decisions: Explicitly authorized existing input dispositions only.
            feedback_dispositions: Existing feedback dispositions for affected nodes.
            keep_nodes: Explicit current-draft nodes to preserve.
            rewrite_nodes: Explicit current-draft nodes to rewrite.
            drop_nodes: Explicit removed nodes; budget-only patches cannot remove nodes.
            decision_reason: Reason for choices; leave draft_dispositions empty.
            candidate_sha256: Exact staged-candidate hash offered by the host, or omit.
            candidate_ledger_sha256: Exact ledger hash from that same offer, or omit.
        """
        from deerflow.sp.report.length import node_budget

        with store.report_lock(paths):
            ledger = ReportLedger.load(paths)
            if candidate_sha256 is not None or candidate_ledger_sha256 is not None:
                dispositions = (draft_dispositions, write_requests, user_decisions, feedback_dispositions,
                                keep_nodes, rewrite_nodes, drop_nodes, decision_reason)
                if (
                    not allow_staged_budget_patch or not ledger.payload.get("length_contract")
                    or base_tree_version is not None or base_tree_sha256 is not None
                    or any(value is not None for value in dispositions)
                ):
                    return json.dumps({"saved": False, "validation_ok": False, "error": "invalid_candidate_budget_patch", "detail": "candidate mode permits only its two hashes and node_budgets"})
                return _patch_staged_budget(
                    paths=paths, delivery_path=delivery_path, ledger=ledger,
                    candidate_sha256=candidate_sha256, candidate_ledger_sha256=candidate_ledger_sha256,
                    node_budgets=node_budgets, normalize_sources=normalize_sources,
                    require_research_registration=require_research_registration,
                )
            base = ledger.payload.get("tree") or {}
            if not ledger.payload.get("length_contract"):
                return json.dumps({"saved": False, "error": "length_control_disabled"})
            if type(base_tree_version) is not int or base_tree_version != base.get("version") or base_tree_sha256 != base.get("sha256"):
                return json.dumps({"saved": False, "error": "stale_budget_patch"})
            try:
                content = store.read_text(Path(base["path"]))
                if store.sha256_text(content) != base_tree_sha256:
                    raise ReportValidationError("accepted tree hash mismatch")
                tree = json.loads(content)
                nodes = {n["node_id"]: n for n in tree["nodes"]}
                if not node_budgets:
                    raise ReportValidationError("node_budgets must be nonempty")
                seen = set()
                for item in node_budgets:
                    if not isinstance(item, dict) or set(item) != {"node_id", "target_words", "max_words"}:
                        raise ReportValidationError("budget patch permits node_id, target_words and max_words only")
                    node = nodes.get(item["node_id"])
                    if node is None or node.get("kind") != "writable" or item["node_id"] in seen:
                        raise ReportValidationError("budget patch must name unique existing writable nodes")
                    node_budget({"node_id": item["node_id"], "constraints": item})
                    node["constraints"].update(target_words=item["target_words"], max_words=item["max_words"])
                    seen.add(item["node_id"])
                tree.update(
                    version=base_tree_version + 1,
                    gap_outcomes=[],
                    removed_node_ids=[],
                    draft_dispositions=draft_dispositions or [],
                    write_requests=write_requests or [],
                    user_decisions=user_decisions or [],
                    feedback_dispositions=feedback_dispositions or [],
                    budget_patch_base={"version": base_tree_version, "sha256": base_tree_sha256},
                )
                tree.pop("accepted_at", None)
                tree = _apply_node_choices(tree, ledger, keep_nodes, rewrite_nodes, drop_nodes, decision_reason)
                result = json.loads(build_submit_outline_tool(
                    paths=paths, delivery_path=delivery_path, prepared_candidate=True,
                    require_research_registration=require_research_registration,
                ).func(tree=tree))
                if not result.get("validation_ok"):
                    return json.dumps({**result, "error": "invalid_budget_patch"}, ensure_ascii=False)
            except (ReportValidationError, OSError, ValueError, KeyError, TypeError) as exc:
                return json.dumps({"saved": delivery_path.exists(), "validation_ok": False, "error": "invalid_budget_patch", "detail": str(exc)}, ensure_ascii=False)
            return json.dumps({"saved": True, "validation_ok": True, "tree_path": paths.virtual(delivery_path), "next": "Return the normal file-delivery envelope. Normal host acceptance and draft applicability checks still apply."})

    return patch_outline_budget
