"""Control transport is bounded; scoped source evidence stays complete."""

import copy
import json
from types import SimpleNamespace

from langchain_core.messages import HumanMessage
from test_collusion_audit import scoped_profile as scoped_profile

from app.experiments.collusion_audit.fact_context import CurrentCodeFactsMiddleware


def test_control_preview_bounds_preserve_counts_paths_rules_and_scoped_bodies():
    previews = [{"candidate_id": str(i), "kind": "source", "entity_type": "a" if i < 7 else "b", "candidate": {"text": f"complete body {i}"}, "evidence": [{"quote": f"unabridged endpoint {i}"}]} for i in range(11)]
    nav = {
        "candidate_count": 11,
        "preview_count": 11,
        "candidate_previews": previews,
        "candidate_index_path": "/actual/index.json",
        "complete_code_result_path": "/actual/full.json",
        "supplementary_collections": {
            "inventory": {
                "total": 9,
                "shown": 9,
                "examples": [{"body": f"inventory {i}"} for i in range(9)],
                "source_observation_index": [{"body": f"source {i}"} for i in range(9)],
                "source_observation_index_complete": True,
                "full_collection_path": "/actual/inventory.jsonl",
            }
        },
    }
    original = copy.deepcopy(nav)
    adapter = SimpleNamespace(fact_indexes={"run": {"rule_id": "M09", "audit_run_id": "run", "findings": [], "result_navigation": nav}}, ocr_receipts=None)
    rule = HumanMessage("Full rule including every judgement criterion")
    req = SimpleNamespace(messages=[rule], override=lambda **kw: SimpleNamespace(**kw))
    central = CurrentCodeFactsMiddleware(adapter, compact_control_navigation=True).project(req)
    bounded = json.loads(central.messages[-1].content.split("\n", 1)[1])[0]["code_facts"]["result_navigation"]
    assert central.messages[0] is rule
    assert bounded["candidate_count"] == 11 and bounded["preview_count"] == 3
    assert bounded["complete_code_result_path"] == "/actual/full.json"
    assert {x["entity_type"] for x in bounded["candidate_previews"]} == {"a", "b"}
    assert all(item in previews for item in bounded["candidate_previews"])
    collection = bounded["supplementary_collections"]["inventory"]
    assert collection["total"] == 9 and collection["shown"] == 2
    assert collection["source_observation_index"] == [] and not collection["source_observation_index_complete"]
    assert collection["full_collection_path"] == "/actual/inventory.jsonl"
    scoped = CurrentCodeFactsMiddleware(adapter).project(req)
    full = json.loads(scoped.messages[-1].content.split("\n", 1)[1])[0]["code_facts"]["result_navigation"]
    assert full == original and nav == original
    assert "complete body 10" in scoped.messages[-1].content


def test_unbacked_source_body_is_never_dropped():
    nav = {"candidate_previews": [{"body": str(i)} for i in range(8)], "supplementary_collections": {"unbacked": {"examples": list(range(8)), "source_observation_index": list(range(8))}}}
    adapter = SimpleNamespace(fact_indexes={"run": {"rule_id": "M09", "audit_run_id": "run", "findings": [], "result_navigation": nav}}, ocr_receipts=None)
    req = SimpleNamespace(messages=[], override=lambda **kw: SimpleNamespace(**kw))
    result = CurrentCodeFactsMiddleware(adapter, compact_control_navigation=True).project(req)
    actual = json.loads(result.messages[-1].content.split("\n", 1)[1])[0]["code_facts"]["result_navigation"]
    assert actual == nav


def test_only_quality_central_enables_bounded_control_navigation(scoped_profile):
    from deerflow.sp.subagents import SPSubagentTask

    assert scoped_profile.extensions().central_middlewares == ()
    scoped_profile.quality_controls = True
    extension = scoped_profile.extensions()
    assert extension.central_middlewares[0].compact_control_navigation is True
    scoped = extension.subagent_middlewares(SPSubagentTask("a", "researcher", "review", "test"))
    assert all(not m.compact_control_navigation for m in scoped if isinstance(m, CurrentCodeFactsMiddleware))
