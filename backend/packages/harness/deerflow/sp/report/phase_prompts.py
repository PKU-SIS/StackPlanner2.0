"""Select applicable existing Outline rules from the native packet state."""

from collections.abc import Mapping

from deerflow.sp.report.prompts import REPORT_COMPACT_OUTLINE_PROMPT, REPORT_OUTLINE_PROMPT


def outline_skill_sections(packet: Mapping, report_task: str, contract: Mapping) -> dict[str, list[str]]:
    review = report_task == "integrate_review"
    outline = ["Boundary", "Tree Contract"]
    if review:
        outline.append("Integration Review")
    else:
        outline += ["Read Before Planning", "Register User Materials", "Register Research Requests",
                    "Envelope and body", "File delivery", "Required, recommended, optional"]
        if packet.get("rejected_outline"):
            outline.append("Retained candidate recovery")
        if packet.get("gaps"):
            outline.append("gap_outcomes shape")
        if any(gap.get("status") == "awaiting_outline" for gap in packet.get("gaps", [])):
            outline.append("Judge Returned Results")
    if packet.get("unapplied_input_ids") or any(not item.get("applied", False) for item in packet.get("inputs", [])):
        outline.append("User Inputs")
    selected = {"report-outline": outline}
    if contract:
        budget = ["Common Counting Contract", "Integration Review" if review else "Outline"]
        if not review:
            if contract.get("paragraph_plan_required"):
                budget.append("Paragraph plan")
            if packet.get("tree"):
                budget.append("Budget patches")
            if packet.get("drafts") or packet.get("feedback"):
                budget.append("Content revision")
            if contract.get("compact_outline"):
                budget += ["Compact first-pass mode", "Reduce the tasks inside each judgement", "Requirement ownership and chapter depth"]
                if packet.get("drafts"):
                    budget.append("Compact proposal serialization")
        selected["report-word-budget"] = budget
    return selected


def _without_between(text: str, start: str, end: str) -> str:
    # Preserve the full instructions if an upstream edit moves the boundary.
    if start not in text or end not in text or text.index(start) >= text.index(end):
        return text
    return text[:text.index(start)] + text[text.index(end):]


def outline_role_prompt(packet: Mapping, report_task: str) -> str:
    prompt = REPORT_OUTLINE_PROMPT
    initial = report_task == "plan" and not packet.get("tree")
    returned = any(gap.get("status") == "awaiting_outline" for gap in packet.get("gaps", []))
    if not initial:
        prompt = _without_between(prompt, "Initial plan:\n", "One fixed research round and final consolidation:\n")
    if not returned:
        # Keep the evidence/write-request contract at the end of this block.
        prompt = _without_between(prompt, "One fixed research round and final consolidation:\n", "- The final tree must put")
    if initial and packet.get("materials"):
        prompt += (
            "\n\n<initial-outline-next-action>\n"
            "Use the original request, catalog and synthesis to identify the necessary saved-material reads. "
            "Issue those read calls before designing the complete chapter structure, arguments and budget allocation. "
            "At this first response, decide the next evidence action; defer the complete tree until the reads return. "
            "Batch independent necessary ranges. Do not add a redundant read when the packet already contains "
            "the exact evidence needed. After the reads, finish the same Outline task with the existing schema, "
            "research registration, budget and complete-tree delivery rules.\n"
            "</initial-outline-next-action>"
        )
    return prompt


def compact_outline_prompt(packet: Mapping) -> str:
    if packet.get("drafts"):
        return REPORT_COMPACT_OUTLINE_PROMPT
    return _without_between(REPORT_COMPACT_OUTLINE_PROMPT, "With current drafts,\n", "Preserve the original content review")
