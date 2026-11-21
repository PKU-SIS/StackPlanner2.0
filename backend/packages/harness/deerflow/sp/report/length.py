"""Opt-in report length contracts and reproducible, explicitly approximate Word counts.

No Word process is invoked here. Measured Word statistics are never inferred
from character counts or model claims. Existing reports without a contract
retain their original acceptance policy.
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Mapping
from copy import deepcopy
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from markdown_it import MarkdownIt

from deerflow.sp.report import store
from deerflow.sp.report.ledger import ReportLedger, ReportValidationError

COUNTER_VERSION = "mixed-word-estimate-v1"
_DEFAULTS = {"min_words": 10000, "target_words": 12000, "max_words": 15000, "reference_reserve_words": 1000, "heading_reserve_words": 300, "safety_margin_words": 500}
_FIXED = {"scope": "whole_document", "measurement_mode": "estimate", "counter_version": COUNTER_VERSION}
# Coarse prospective recommendations from the archived 18 reports, not a length
# predictor. These are sums of writable-node targets, excluding all reserves.
_OUTLINE_BODY_RANGES = {
    10000: (3000, 3500, 4000, (6, 8), (23, 37)),
    20000: (8000, 9500, 11500, (8, 10), (30, 48)),
    30000: (13000, 15000, 16500, (8, 9), (40, 51)),
}


def _integer(value: Any, field: str, *, minimum: int = 1) -> int:
    if type(value) is not int or value < minimum:
        raise ReportValidationError(f"{field} must be an integer >= {minimum}")
    return value


def normalize_contract(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ReportValidationError("report_length must be an object")
    unknown = (
        set(value)
        - set(_DEFAULTS)
        - set(_FIXED)
        - {"enabled", "writer_target_percent", "paragraph_plan_required", "writer_submission_limit", "writer_submission_tolerance_percent", "compact_outline", "writer_task_cards", "length_auto_revision", "outline_budget_selection"}
    )
    if unknown:
        raise ReportValidationError(f"unknown report_length fields: {sorted(unknown)}")
    if type(value.get("enabled", True)) is not bool:
        raise ReportValidationError("report_length.enabled must be boolean")
    if "length_auto_revision" in value and type(value["length_auto_revision"]) is not bool:
        raise ReportValidationError("length_auto_revision must be boolean")
    if "compact_outline" in value and type(value["compact_outline"]) is not bool:
        raise ReportValidationError("compact_outline must be boolean")
    if "writer_task_cards" in value and type(value["writer_task_cards"]) is not bool:
        raise ReportValidationError("writer_task_cards must be boolean")
    if "outline_budget_selection" in value and type(value["outline_budget_selection"]) is not bool:
        raise ReportValidationError("outline_budget_selection must be boolean")
    if value.get("outline_budget_selection"):
        if value.get("compact_outline") is not True:
            raise ReportValidationError("outline_budget_selection requires compact_outline=true")
        final_target = value.get("target_words", _DEFAULTS["target_words"])
        if type(final_target) is not int or final_target not in _OUTLINE_BODY_RANGES:
            raise ReportValidationError("outline_budget_selection supports final targets 10000, 20000 or 30000")
        if value.get("writer_target_percent", 100) != 100:
            raise ReportValidationError("outline_budget_selection cannot combine with reduced writer_target_percent")
    if value.get("writer_task_cards") and value.get("compact_outline") is not True:
        raise ReportValidationError("writer_task_cards requires compact_outline=true")
    if value.get("compact_outline") and (value.get("paragraph_plan_required") or value.get("writer_submission_limit")):
        raise ReportValidationError("compact_outline cannot combine with paragraph_plan_required or writer_submission_limit; use first-pass scope selection and native whole-report review")
    if "paragraph_plan_required" in value and type(value["paragraph_plan_required"]) is not bool:
        raise ReportValidationError("paragraph_plan_required must be boolean")
    if "writer_submission_limit" in value and type(value["writer_submission_limit"]) is not bool:
        raise ReportValidationError("writer_submission_limit must be boolean")
    if "writer_submission_tolerance_percent" in value:
        tolerance = _integer(value["writer_submission_tolerance_percent"], "writer_submission_tolerance_percent", minimum=0)
        if tolerance > 5:
            raise ReportValidationError("writer_submission_tolerance_percent must be <= 5")
        if value.get("writer_submission_limit") is not True:
            raise ReportValidationError("writer_submission_tolerance_percent requires writer_submission_limit=true")
    for key, expected in _FIXED.items():
        if value.get(key, expected) != expected:
            raise ReportValidationError(f"report_length.{key} must be {expected!r}; actual Word measurement is separate")
    if value.get("enabled") is False:
        return None
    result = {**_DEFAULTS, **value, **_FIXED, "enabled": True}
    for key in _DEFAULTS:
        _integer(result[key], key, minimum=0 if key.endswith("reserve_words") or key == "safety_margin_words" else 1)
    if "writer_target_percent" in result:
        percent = _integer(result["writer_target_percent"], "writer_target_percent")
        if percent > 100:
            raise ReportValidationError("writer_target_percent must be <= 100")
    if not result["min_words"] <= result["target_words"] <= result["max_words"]:
        raise ReportValidationError("report_length requires min_words <= target_words <= max_words")
    if sum(result[k] for k in ("reference_reserve_words", "heading_reserve_words", "safety_margin_words")) >= result["target_words"]:
        raise ReportValidationError("report length reserves leave no positive body budget")
    return result


def contract_for_band(target_words: int) -> dict[str, Any]:
    """Select an approved DS band without changing the native report lifecycle."""
    _integer(target_words, "length_band")
    if target_words not in _OUTLINE_BODY_RANGES:
        raise ReportValidationError("length_band must be 10000, 20000 or 30000")
    contract = normalize_contract(
        {
            "min_words": target_words * 9 // 10,
            "target_words": target_words,
            "max_words": target_words * 11 // 10,
            "compact_outline": True,
            "writer_task_cards": True,
            "length_auto_revision": False,
            "outline_budget_selection": True,
        }
    )
    assert contract is not None
    return contract


def writer_generation_budget(node: Mapping[str, Any], contract: Mapping[str, Any]) -> dict[str, int] | None:
    """Derive optional lower generation guidance without changing acceptance.

    The percentage is explicit experimental configuration, not a prediction of
    actual length. No output is cut and no node allocation is rewritten.
    """
    normalized = normalize_contract(contract)
    percent = (normalized or {}).get("writer_target_percent", 100)
    if percent == 100:
        return None
    allocation = node_budget(node)
    return {"percent": percent, **{key: max(1, (value * percent + 99) // 100) for key, value in allocation.items()}}


def _is_cjk(ch: str) -> bool:
    return any(a <= ord(ch) <= b for a, b in ((0x3400, 0x4DBF), (0x4E00, 0x9FFF), (0xF900, 0xFAFF), (0x20000, 0x323AF), (0x3040, 0x30FF), (0xAC00, 0xD7AF)))


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag in {"br", "p", "div", "td", "th", "tr", "li"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        if tag in {"p", "div", "td", "th", "tr", "li"}:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _html_text(value: str) -> str:
    parser = _HTMLText()
    parser.feed(value)
    return "".join(parser.parts)


def visible_text(markdown: str) -> str:
    blocks = []
    for token in MarkdownIt("commonmark").enable("table").parse(markdown):
        if token.type == "inline":
            blocks.append(
                "".join(
                    child.content if child.type in {"text", "code_inline", "image"} else "\n" if child.type in {"softbreak", "hardbreak"} else _html_text(child.content) if child.type == "html_inline" else ""
                    for child in token.children or []
                )
            )
        elif token.type in {"fence", "code_block"}:
            blocks.append(token.content)
        elif token.type == "html_block":
            blocks.append(_html_text(token.content))
    return "\n".join(blocks)


def estimate_words(markdown: str) -> int:
    """CJK glyphs/wide symbols individually; other meaningful runs as words.

    This is a versioned approximation, not Word's proprietary tokenizer.
    URLs and Western punctuation do not count one word per character.
    """
    return estimate_text(visible_text(markdown))


def estimate_text(text: str) -> int:
    """Use the same estimator for already-rendered DOCX/plain-text content."""
    total = 0
    meaningful = False
    for ch in text:
        separate = _is_cjk(ch) or not ch.isspace() and (unicodedata.east_asian_width(ch) in {"F", "W"} or ch in "“”‘’…—")
        if separate or ch.isspace():
            total += int(meaningful) + int(separate)
            meaningful = False
        else:
            meaningful |= unicodedata.category(ch)[0] in "LNS"
    return total + int(meaningful)


def node_budget(node: Mapping[str, Any]) -> dict[str, int]:
    constraints = node.get("constraints") or {}
    target = _integer(constraints.get("target_words"), f"{node.get('node_id')}.target_words")
    maximum = _integer(constraints.get("max_words"), f"{node.get('node_id')}.max_words")
    if target > maximum:
        raise ReportValidationError(f"{node.get('node_id')} budget requires target_words <= max_words")
    return {"target_words": target, "max_words": maximum}


def budget_summary(tree: Mapping[str, Any], ledger: ReportLedger) -> dict[str, Any]:
    contract = ledger.payload.get("length_contract")
    if not contract:
        return {}
    nodes = list(tree.get("nodes") or [])
    selected = {sid for n in nodes for e in n.get("evidence") or [] for sid in e.get("source_ids") or []}
    sources = [s for s in ledger.payload.get("sources") or [] if s.get("source_id") in selected]
    reference_text = "\n".join(f"[{i}] {s.get('title') or ''} — {s.get('url') or ''}" for i, s in enumerate(sources, 1)) if sources else "（无引用）"
    reference_estimate = estimate_words(reference_text)
    heading_estimate = estimate_words("\n".join([tree.get("report_title") or "", *[n.get("title") or "" for n in nodes], "来源"]))
    references = max(contract["reference_reserve_words"], reference_estimate)
    headings = max(contract["heading_reserve_words"], heading_estimate)
    missing = []
    targets = maximums = 0
    for node in nodes:
        if node.get("kind") != "writable":
            continue
        try:
            budget = node_budget(node)
        except ReportValidationError:
            missing.append(str(node.get("node_id")))
        else:
            targets += budget["target_words"]
            maximums += budget["max_words"]
    overhead = references + headings + contract["safety_margin_words"]
    summary = {
        "contract": dict(contract),
        "reference_words_reserved": references,
        "heading_words_reserved": headings,
        "reference_words_estimated": reference_estimate,
        "heading_words_estimated": heading_estimate,
        "safety_margin_words": contract["safety_margin_words"],
        "body_min_words": max(1, contract["min_words"] - reference_estimate - heading_estimate),
        "body_target_words": contract["target_words"] - overhead,
        "body_max_words": contract["max_words"] - overhead,
        "node_target_sum": targets,
        "node_max_sum": maximums,
        "missing_node_budgets": missing,
        "counter_version": COUNTER_VERSION,
        "word_verified": False,
    }
    if contract.get("outline_budget_selection"):
        minimum, default, maximum, nodes_seen, arguments_seen = _OUTLINE_BODY_RANGES[contract["target_words"]]
        chosen = targets if not missing and minimum <= targets <= maximum else None
        summary.update(
            body_min_words=minimum,
            body_target_words=chosen if chosen is not None else default,
            body_max_words=maximum,
            selected_body_target_words=chosen,
            body_budget_recommendation={
                "min_words": minimum,
                "default_words": default,
                "max_words": maximum,
                "scope": "sum_of_writable_node_target_words; excludes references/headings/safety reserves",
                "observed_writable_nodes": list(nodes_seen),
                "observed_total_arguments": list(arguments_seen),
                "structure_counts_are_reference_only": True,
            },
        )
    return summary


def validate_budgets(tree: Mapping[str, Any], ledger: ReportLedger) -> None:
    if not ledger.payload.get("length_contract"):
        return
    summary = budget_summary(tree, ledger)
    for node in tree.get("nodes") or []:
        if node.get("kind") == "writable":
            budget = node_budget(node)
            if ledger.payload["length_contract"].get("paragraph_plan_required"):
                constraints = node.get("constraints") or {}
                plan = constraints.get("paragraph_plan")
                if not isinstance(plan, list) or not 3 <= len(plan) <= 6:
                    raise ReportValidationError(f"{node['node_id']}.paragraph_plan requires 3-6 paragraphs")
                total = 0
                for index, item in enumerate(plan, 1):
                    if not isinstance(item, Mapping) or type(item.get("paragraph")) is not int or item["paragraph"] != index or not isinstance(item.get("purpose"), str) or not item["purpose"].strip():
                        raise ReportValidationError(f"{node['node_id']}.paragraph_plan requires ordered paragraph numbers and nonempty purpose")
                    total += _integer(item.get("target_words"), f"{node['node_id']}.paragraph_plan.target_words")
                if not budget["target_words"] * 0.8 <= total <= budget["target_words"]:
                    raise ReportValidationError(f"{node['node_id']}.paragraph_plan target sum {total} must be 80-100% of node target {budget['target_words']}")
                exclusions = constraints.get("scope_exclusions")
                if not isinstance(exclusions, list) or not exclusions or any(not isinstance(v, str) or not v.strip() for v in exclusions):
                    raise ReportValidationError(f"{node['node_id']}.scope_exclusions requires a nonempty list of optional details not to develop; preserve explicit requirements")
    if summary["body_target_words"] <= 0 or summary["body_max_words"] <= 0:
        raise ReportValidationError("report length budget leaves no room for body after references/headings")
    recommendation = summary.get("body_budget_recommendation")
    target_ceiling = recommendation["max_words"] if recommendation else summary["body_target_words"]
    if summary["node_target_sum"] < summary["body_min_words"]:
        label = "internal body budget minimum" if recommendation else "whole-report minimum"
        raise ReportValidationError(f"node word budget targets cannot reach the {label}: " + json.dumps(summary, ensure_ascii=False))
    if summary["node_target_sum"] > target_ceiling or summary["node_max_sum"] > summary["body_max_words"]:
        label = "internal body budget" if recommendation else "whole-report body budget"
        raise ReportValidationError(f"node word budget sums exceed {label}: " + json.dumps(summary, ensure_ascii=False))


def validate_budget_patch(tree: Mapping[str, Any], ledger: ReportLedger) -> None:
    marker = tree.get("budget_patch_base")
    if marker is None:
        return
    current = ledger.payload.get("tree") or {}
    if not ledger.payload.get("length_contract") or not isinstance(marker, Mapping) or type(marker.get("version")) is not int or marker != {"version": current.get("version"), "sha256": current.get("sha256")}:
        raise ReportValidationError("stale or invalid budget patch base")
    original_text = store.read_text(Path(current["path"]))
    if store.sha256_text(original_text) != current["sha256"]:
        raise ReportValidationError("budget patch baseline hash mismatch")
    original = json.loads(original_text)
    editable_roots = {"version", "accepted_at", "budget_patch_base", "write_requests", "draft_dispositions", "feedback_dispositions", "user_decisions", "gap_outcomes", "removed_node_ids"}
    if tree.get("gap_outcomes") or tree.get("removed_node_ids"):
        raise ReportValidationError("budget patch cannot change research gaps or remove nodes")
    for name in set(original) | set(tree):
        if name == "nodes" or name in editable_roots:
            continue
        if original.get(name) != tree.get(name):
            raise ReportValidationError(f"budget patch cannot change {name}")
    before, after = deepcopy(original.get("nodes") or []), deepcopy(tree.get("nodes") or [])
    for nodes in (before, after):
        for node in nodes:
            constraints = node.get("constraints") or {}
            for key in ("target_words", "max_words"):
                constraints.pop(key, None)
    if before != after:
        raise ReportValidationError("budget patch cannot change node structure, content or evidence")


def candidate_length(ledger: ReportLedger, candidate: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    if not ledger.payload.get("length_contract"):
        return None
    from deerflow.sp.report.assembly import candidate_file_content
    from deerflow.sp.report.citation_display import render_final_citations

    candidate = candidate or ledger.payload.get("candidate")
    if not isinstance(candidate, Mapping):
        return None
    canonical = candidate_file_content(candidate)
    rendered, _ = render_final_citations(canonical, candidate.get("source_table") or [])
    body, separator, references = rendered.rpartition("\n## 来源\n")
    if not separator:
        body, references = rendered, ""
    actual = estimate_words(rendered)
    contract = ledger.payload["length_contract"]
    nodes = []
    for section in candidate.get("sections") or []:
        if section.get("kind") != "writable":
            continue
        # Sections in a candidate contain the exact reviewed visible body.
        node_id = section.get("node_id")
        nodes.append({"node_id": node_id, "estimated_words": estimate_words(str(section.get("body") or ""))})
    return {
        "estimated_words": actual,
        "body_words": estimate_words(body),
        "reference_words": estimate_words(references),
        "in_estimated_range": contract["min_words"] <= actual <= contract["max_words"],
        "direction": "short" if actual < contract["min_words"] else "long" if actual > contract["max_words"] else "within",
        "min_words": contract["min_words"],
        "target_words": contract["target_words"],
        "max_words": contract["max_words"],
        "counter_version": COUNTER_VERSION,
        "scope": "whole_document",
        "word_verified": False,
        "measured_word_count": None,
        "candidate_sha256": candidate["sha256"],
        "rendered_sha256": store.sha256_text(rendered),
        "nodes": nodes,
    }


def length_finding(measurement: Mapping[str, Any]) -> dict[str, Any] | None:
    if measurement["in_estimated_range"]:
        return None
    return {
        "kind": "other",
        "severity": "blocking",
        "node_ids": [n["node_id"] for n in sorted(measurement["nodes"], key=lambda n: n["estimated_words"], reverse=True)],
        "draft_ids": [],
        "message": (
            f"Explicit report length constraint: whole-document estimated Word count {measurement['estimated_words']} "
            f"is {measurement['direction']} (required {measurement['min_words']}–{measurement['max_words']}, target {measurement['target_words']}). "
            "This is a versioned estimate, NOT a measured Microsoft Word count. Preserve requirements/evidence; "
            "revise affected nodes using the existing bounded content-revision path. For a long report remove repetition, "
            "not required evidence; for a short report add substantive missing analysis, not filler. Do not truncate the export. "
            f"Node estimates (largest first): {sorted(measurement['nodes'], key=lambda n: n['estimated_words'], reverse=True)}"
        ),
    }


def bound_writer_draft(ledger: ReportLedger, *, node_id: str, write_key: str, draft_id: str, action_id: str):
    """Read one host-bound draft. Caller holds report_lock for ledger consistency."""
    if not ledger.payload.get("length_contract"):
        raise ReportValidationError("length_control_disabled")
    request = next((r for r in ledger.payload.get("write_requests") or [] if r.get("key") == write_key), None)
    if not request or request.get("status") != "in_flight" or request.get("last_action_id") != action_id or request.get("last_draft_id") != draft_id or request.get("node_ids") != [node_id]:
        raise ReportValidationError("writer_not_in_flight")
    attempt = next((a for a in request.get("attempts") or [] if a.get("action_id") == action_id), None)
    if attempt is None:
        raise ReportValidationError("writer_not_in_flight")
    path = ledger.paths.draft_file(node_id, draft_id)
    if path.is_symlink() or not path.resolve().is_relative_to(ledger.paths.directory.resolve() / "drafts"):
        raise ReportValidationError("invalid_draft_path")
    try:
        content = store.read_text(path)
    except (OSError, UnicodeDecodeError) as exc:
        raise ReportValidationError("draft_not_written") from exc
    tree_text = store.read_text(Path(ledger.payload["tree"]["path"]))
    if store.sha256_text(tree_text) != ledger.payload["tree"]["sha256"]:
        raise ReportValidationError("invalid_tree_binding")
    node = next(n for n in json.loads(tree_text)["nodes"] if n["node_id"] == node_id)
    budget = node_budget(node)
    actual = estimate_words(content)
    return (
        attempt,
        content,
        {
            **budget,
            "estimated_words": actual,
            "over_node_max": actual > budget["max_words"],
            "remove_words_to_target": max(0, actual - budget["target_words"]),
            "retain_percent_to_target": round(100 * budget["target_words"] / max(1, actual), 1),
            "counter_version": COUNTER_VERSION,
            "word_verified": False,
            "measured_word_count": None,
            "draft_sha256": store.sha256_text(content),
        },
    )


def build_measure_draft_tool(*, paths: store.ReportPaths, node_id: str, write_key: str, draft_id: str, action_id: str):
    from langchain.tools import tool

    @tool("report_measure_draft", parse_docstring=True)
    def measure_draft() -> str:
        """Measure only this Writer's assigned draft using the report's word estimate.

        The host measures the saved body at completion automatically. This tool
        is available for diagnostic inspection when needed, with at most two
        checks per attempt. Whole-report acceptance owns length revision.
        Results never claim to be measured Microsoft Word statistics.
        """
        with store.report_lock(paths):
            ledger = ReportLedger.load(paths)
            try:
                attempt, _, measurement = bound_writer_draft(ledger, node_id=node_id, write_key=write_key, draft_id=draft_id, action_id=action_id)
            except ReportValidationError as exc:
                return json.dumps({"error": str(exc)})
            if int(attempt.get("length_checks") or 0) >= 2:
                return json.dumps({"error": "length_check_limit"})
            attempt["length_checks"] = int(attempt.get("length_checks") or 0) + 1
            attempt.setdefault("length_measurements", []).append(measurement)
            ledger.save()
            return json.dumps(
                {
                    **measurement,
                    "remaining_checks": 2 - attempt["length_checks"],
                }
            )

    return measure_draft
