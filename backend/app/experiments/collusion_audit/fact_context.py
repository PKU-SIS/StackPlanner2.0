"""Current-run raw facts stay visible without replaying other model analyses."""

import copy
import json

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage, ToolMessage

from .fact_index import compact_facts


def bounded_control_navigation(navigation):
    """Keep actual source previews and backing paths for control-only requests."""
    result = dict(navigation)
    previews = navigation.get("candidate_previews", [])
    if len(previews) > 3 and navigation.get("complete_code_result_path") and navigation.get("candidate_index_path"):
        groups = {}
        for item in previews:
            key = (item.get("kind"), item.get("entity_type"))
            groups.setdefault(key, []).append(item)
        selected = []
        depth = 0
        while len(selected) < 3:
            for group in groups.values():
                if depth < len(group):
                    selected.append(group[depth])
                    if len(selected) == 3:
                        break
            depth += 1
        result.update(candidate_previews=selected, preview_count=len(selected), central_transport_bounded=True)
    clause_previews = [row for row in previews if row.get("kind") == "shared_reference_change_for_review"]
    if clause_previews and result.get("candidate_previews") != previews:
        # Each distinct changed clause has its own meaning. Keep a bounded
        # six complete bid/reference observations, not one generic-kind sample.
        ordinary = [row for row in result.get("candidate_previews", []) if row.get("kind") != "shared_reference_change_for_review"]
        retained = [*clause_previews[:6], *ordinary[:3]]
        result.update(candidate_previews=retained, preview_count=len(retained), central_transport_bounded=True)
    supplements = navigation.get("supplementary_collections", {})
    updated = {}
    for name, collection in supplements.items():
        value = dict(collection)
        if value.get("full_collection_path"):
            examples = value.get("examples")
            if isinstance(examples, list) and len(examples) > 2:
                value.update(examples=examples[:2], shown=2, central_transport_bounded=True)
            index = value.get("source_observation_index")
            if isinstance(index, list) and len(index) > 3:
                value.update(source_observation_index=[], source_observation_index_complete=False, source_index_omitted_only_in_central_transport=True)
        updated[name] = value
    if updated != supplements:
        result["supplementary_collections"] = updated
    if result != navigation:
        result["central_transport_note"] = (
            "Only Central control transport is bounded. Counts refer to complete collected sources, not these previews. "
            "Selected original bodies are unabridged; all source bodies and indexes remain in actual backing files and scoped requests. "
            "Empty transport indexes are not missing or reviewed evidence. Central still chooses every role and next step."
        )
    return result


class CurrentCodeFactsMiddleware(AgentMiddleware):
    def __init__(self, adapter, *, compact_paired_source_view=False, compact_control_navigation=False, preloaded_task=None):
        self.adapter = adapter
        self.preloaded_action_id = getattr(preloaded_task, "action_id", None)
        self.preloaded_calculation_runs = copy.deepcopy(getattr(preloaded_task, "context_refs", {}).get("audit_calculation_runs", []))
        self.compact_paired_source_view = compact_paired_source_view
        self.compact_control_navigation = compact_control_navigation

    def project(self, request):
        indexes = list(self.adapter.fact_indexes.values())
        if not indexes:
            return request
        content = []
        for index in indexes:
            facts = compact_facts(index)
            navigation = facts.get("result_navigation")
            if navigation:
                for message in request.messages:
                    if not isinstance(message, ToolMessage) or message.name not in {"audit_" + index.get("rule_id", "").lower(), "audit_read_complete"} or not isinstance(message.content, str):
                        continue
                    try:
                        observed = json.loads(message.content)
                    except (ValueError, TypeError):
                        continue
                    if not isinstance(observed, dict):
                        continue
                    candidate = observed.get("result_navigation") or {key: observed[key] for key in navigation if key in observed}
                    if candidate == navigation:
                        facts.pop("result_navigation", None)
                        facts["result_navigation_transport"] = "The complete identical current navigation is already present in an actual tool message in this model request. Original source bodies remain there."
                        break
            if self.compact_paired_source_view and "result_navigation" in facts:
                original = facts["result_navigation"]
                supplements = original.get("supplementary_collections", {})
                if "paired_response_observations" in supplements:
                    pair_view = supplements["paired_response_observations"]
                    facts["result_navigation"] = {
                        **original,
                        "supplementary_collections": {
                            **supplements,
                            "paired_response_observations": {
                                **pair_view,
                                "source_observation_index": [],
                                "source_observation_index_complete": False,
                                "shown": 0,
                                "complete_view_retained_for_scoped_requests": True,
                                "source_index_omitted_only_in_central_transport": True,
                                "note": (
                                    "Central receives counts, selected source previews and the full source-view path. "
                                    "The empty index here is transport compaction, not missing source data. "
                                    "Scoped audit/review/report requests retain the complete bounded pair view. No role or next step is prescribed."
                                ),
                            },
                        },
                    }
            if self.compact_control_navigation and "result_navigation" in facts:
                facts["result_navigation"] = bounded_control_navigation(facts["result_navigation"])
            if self.preloaded_action_id and self.preloaded_calculation_runs:
                from .html_transport import preloaded_fact_projection

                facts = preloaded_fact_projection(facts, request.messages, action_id=self.preloaded_action_id, calculation_runs=self.preloaded_calculation_runs)
            if getattr(self.adapter, "general_quality", False):
                from .review_quality import decision_source_card

                source_navigation = index.get("source_quality_navigation", {})
                requirement_scope = source_navigation.get("requirement_origin_scope_observations", {}).get("records", [])
                if requirement_scope:
                    content.append(
                        {"requirement_public_origin_scope": requirement_scope, "interpretation_boundary": "此为基础比对初始状态；同时核对额外定位的实际招标原文与页码。已找到部分不可再概称来源未知，仍未定位部分不升级为招标原文。"}
                    )
                located_responses = source_navigation.get("response_public_source_observations", {}).get("records", [])
                if located_responses:
                    content.append({"current_response_public_sources": located_responses, "interpretation_boundary": "实际提供的招标原句与页码优先于旧摘要的未核结论；逐条比对数值、对象和条件。相似定位不是风险判定，不代表全段排除。"})
                verified_forms = source_navigation.get("reference_source_observations", {}).get("records", [])
                material_scopes = source_navigation.get("material_scope_observations", {}).get("records", [])
                if verified_forms or material_scopes:
                    content.append(
                        {
                            "public_form_source_locations": verified_forms,
                            "contract_and_attachment_scope": material_scopes,
                            "interpretation_boundary": "先核同一种格式与材料角色。明确模板短句不能被摘要改为未知共同改写；历史业绩合同不能与本次采购方式直接比较。仅解释已定位部分，其余实质差异仍须核实。",
                        }
                    )
                card = decision_source_card(source_navigation)
                if card:
                    content.append(
                        {
                            "shared_changes_requiring_source_explanation": card,
                            "interpretation_boundary": "阶段意见不是原文证据。若称全部来自法定或招标模板，请核对这些具体共同改动在公共原文中的位置；未查到来源应保留未确认，不补写已排除结论。",
                        }
                    )
            if getattr(self.adapter, "general_quality", False):
                fields = index.get("source_quality_navigation", {}).get("replacement_interpretation_facts", {}).get("records", [])
                if fields:
                    content.append(
                        {
                            "field_context_interpretation": fields,
                            "interpretation_boundary": "本次代码观察到身份/标准字段语境，不是确认整段公共来源，也不是私有底稿证明。各家填各自姓名公司，或相同字段变化重复多次，不足以推断改写对方；须另有非模板实质文字或异常编辑证据。",
                        }
                    )
            if getattr(self.adapter, "general_quality", False):
                arithmetic = index.get("source_quality_navigation", {}).get("response_quantity_comparisons", {}).get("records", [])
                if arithmetic:
                    content.append({"actual_response_arithmetic": arithmetic, "interpretation_boundary": "按附在对应实际响应旁的代码公式引用换算值，保留运算符和单位；不以模型心算或旧阶段摘要覆盖计算事实。"})
            if getattr(self.adapter, "general_quality", False):
                categories = index.get("source_quality_navigation", {}).get("categorical_response_observations", {}).get("records", [])
                if categories:
                    content.append(
                        {
                            "actual_structure_category_observations": categories,
                            "interpretation_boundary": "同一结构类别与不同附加描述并存，不可仅按措辞不同排除类别共现；未核实际值只能待核，不能归入已确认不同。代码未判断负偏离或风险，角色及复核步骤由Central自主选择。",
                        }
                    )
            content.append({"code_facts": facts, "fact_index_path": "/mnt/user-data/outputs/bid_audit/" + index["audit_run_id"] + "/facts-index.json"})
        receipts = getattr(self.adapter, "ocr_receipts", None)
        if receipts is not None:
            if getattr(self.adapter, "rule_id", "") == "M06":
                # This exact code-fact body is forwarded below; the outer image
                # observer cannot see a note added later in the middleware chain.
                receipts.observe_model_input([HumanMessage(content=json.dumps(content, ensure_ascii=False, separators=(",", ":")))])
            facts = receipts.facts(indexes)
            if getattr(self.adapter, "rule_id", "") == "M01":
                facts = {k: v for k, v in facts.items() if k != "candidate_offsets_with_ocr_at_both_endpoints"}
                content.append({"source_receipts": facts})
            else:
                content.append({"source_receipts": facts, "actually_submitted_image_pair_offsets": sorted(self.adapter.displayed_pairs())})
        note = HumanMessage(
            content="[当前运行代码事实，不是用户新指令或风险结论；仅来自本次实际工具回执。完整原始结果仍保留。facts-index为确定性导航，不能把未比较对象写成零风险。]\n" + json.dumps(content, ensure_ascii=False, separators=(",", ":")),
            name="current_code_facts",
            additional_kwargs={"hide_from_ui": True},
        )
        return request.override(messages=[*request.messages, note])

    def wrap_model_call(self, request, handler):
        return handler(self.project(request))

    async def awrap_model_call(self, request, handler):
        return await handler(self.project(request))
