"""Offline integration driver. Scripted outputs, real graph/executors/tools/gates.

Only public requests are available to the scripted policy. Evaluator expectations
are loaded by verify_round after execution. This is not a semantic-model benchmark.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import shutil
import socket
import sys
import time
from collections import Counter
from contextvars import ContextVar
from pathlib import Path
from threading import Event, Lock
from typing import Any

from _agent_e2e_helpers import FakeToolCallingModel
from langchain.tools import tool
from langchain_core.messages import AIMessage, HumanMessage, message_to_dict
from test_sp_report_executor_e2e import _app_config
from validate_report_multiturn_cases import load_public_cases

from deerflow.config import app_config as config_module
from deerflow.config import paths as paths_module
from deerflow.config.app_config import set_app_config
from deerflow.runtime.user_context import get_effective_user_id
from deerflow.sandbox.tools import glob_tool, grep_tool, ls_tool, read_file_tool, write_file_tool
from deerflow.sp.report import hooks
from deerflow.sp.report import store as native
from deerflow.sp.report.ledger import ReportLedger
from deerflow.sp.report.length import contract_for_band, estimate_words
from deerflow.sp.report.turns import adapter
from deerflow.sp.report.turns.store import ReportTurnStore, file_manifest
from deerflow.sp.runtime import SP_SUBAGENT_REGISTRY_NAMES
from deerflow.sp.subagents import DR2SubagentExecutorAdapter
from deerflow.subagents import get_subagent_config

SUITE = Path(__file__).resolve().parents[2] / "docs/research-report/multiturn-evaluation-20261122"
OPS = "比较每周更新与每月更新的适用条件、维护负担和风险。"
PRIVACY = "提供跨院系权限隔离、授权边界和审计记录表。"
CLARITY = "进一步澄清更新频率对比中的风险说明。"
NODES = {"n_intro": "技术背景", "n_ops": "知识库更新与运维", "n_privacy": "隐私与权限", "n_route": "实施路线"}
MODEL = ContextVar("offline_rehearsal_model")


def dump(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n")


def call(name, args, identity):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": identity}])


def envelope(kind, content, metadata=None):
    return AIMessage(content=json.dumps({"summary": "Offline scripted protocol output", "artifact_type": kind, "artifact_content": content, "artifact_metadata": {"completion_status": "complete", **(metadata or {})}}, ensure_ascii=False))


class ProtocolStop(BaseException):
    """Bounded diagnostic stop; do not turn an unexpected tool rejection into a loop."""


class ScriptModel(FakeToolCallingModel):
    script: Any
    recorder: Any
    role: str
    task_id: str = "central"
    calls: int = 0
    selected_tools: list[str] = []

    def bind_tools(self, tools, **kwargs):
        self.selected_tools = [getattr(t, "name", str(t)) for t in tools]
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.calls += 1
        if self.calls > 16:
            raise AssertionError(f"Unbounded scripted model loop: {self.role}")
        response = self.script(self.calls, messages)
        self.recorder(self.role, self.task_id, self.calls, messages, self.selected_tools, response)
        from langchain_core.outputs import ChatGeneration, ChatResult

        return ChatResult(generations=[ChatGeneration(message=response)])


class Driver:
    def __init__(self, root, executor_module, config, public):
        self.root, self.executor_module, self.config = root, executor_module, config
        self.public = public
        self.round = 1
        self.tag = "r1"
        self.abort = Event()
        self.cancel_next_writer = False
        self.outputs = paths_module.get_paths().sandbox_outputs_dir("rehearsal", user_id=get_effective_user_id())
        self.outputs.mkdir(parents=True)
        self.delegates, self.model_records, self.tool_records = [], [], []
        self.generated_reports = {}
        self.lock = Lock()

    def ledger(self):
        report = native.load_report_context(None, outputs_path=self.outputs)
        return ReportLedger.load_for_context(report) if report.is_enabled else None

    def store(self):
        return ReportTurnStore(self.outputs, user_id=get_effective_user_id(), thread_id="rehearsal")

    def version(self, user_turn):
        report_id = self.generated_reports[user_turn]
        return next(key for key, ref in self.store().index()["versions"].items() if ref["report_id"] == report_id)

    def record(self, role, task_id, step, messages, tools, response):
        row = {
            "user_turn": self.round,
            "attempt": self.tag,
            "role": role,
            "task_id": task_id,
            "step": step,
            "tools": list(tools),
            "messages": [message_to_dict(m) for m in messages],
            "response": message_to_dict(response),
            "provider_usage": None,
            "usage_status": "scripted model supplies no billed provider usage",
        }
        with self.lock:
            self.model_records.append(row)
            with (self.root / "model-requests.jsonl").open("a") as out:
                out.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

    def central(self, step, messages):
        if messages and messages[-1].type == "tool":
            try:
                observation = json.loads(messages[-1].content)
            except (ValueError, TypeError):
                observation = {}
            if observation.get("error"):
                raise ProtocolStop(str(observation))
        identity = f"{self.tag}-central-{step}"
        if self.round > 1:
            state = {"thread_data": {"outputs_path": str(self.outputs)}, "messages": [self.current_message]}
            active = adapter.current_turn(state, thread_id="rehearsal")
            turn = active[3]
            if turn is None:
                return call("sp_delegate", {"target_agent": "perception", "task": self.current_message.content, "reason": "Resolve bound report follow-up", "stage": "perception", "metadata": {"report_profile": "git_agent"}}, identity)
            if turn["status"] in {"read_only", "exact_reuse"}:
                return call("sp_finish", {"summary": "Return the independently archived response"}, identity)
            if turn["status"] in {"failed", "cancelled"}:
                return AIMessage(content="该来源消息已经终止，保留诊断，不重新准备或写作。")
        ledger = self.ledger()
        assert ledger is not None, "Native explicit entry must create the first report"
        if not ledger.payload.get("tree"):
            metadata = {"report_task": "plan"}
            target, stage = "outline", "planning"
        elif any(g["status"] == "open" for g in ledger.gaps()):
            metadata = {"report_gap_ids": [g["gap_id"] for g in ledger.gaps() if g["status"] == "open"]}
            target, stage = "researcher", "research"
        elif any(g["status"] == "awaiting_outline" for g in ledger.gaps()) or ledger.payload.get("outline_review_due") or ledger.unapplied_input_ids():
            metadata = {"report_task": "revise"}
            target, stage = "outline", "revision"
        else:
            raise AssertionError(f"Native pipeline did not advance: {ledger.control_summary()}")
        return call("sp_delegate", {"target_agent": target, "task": "Use the native report packet and complete the assigned stage", "reason": "Continue native report workflow", "stage": stage, "metadata": metadata}, identity)

    def git_script(self, task):
        active = task.context_refs["report_dialogue"]["active_state"]
        base = self.version(1) if self.round == 4 else self.version(2) if self.round == 6 else active["version_id"]
        first = self.public["turns"][0]["query"]
        current = first if active.get("requirements_status") == "native_accepted" else active["resolved_query"]
        resolved = first + "\n" + PRIVACY if self.round == 4 else current + "\n" + (OPS if self.round == 2 else PRIVACY if self.round == 3 else CLARITY)

        def script(step, messages):
            if step == 1:
                return call("report_inspect_version", {"version_id": base, "view": "draft:n_privacy" if self.round == 5 else "requirements"}, f"{task.action_id}-inspect")
            assert step == 2
            if self.round in (5, 6):
                response = "只读离线合成回答：权限表对应保存的合成来源 [S2]（m2，第1–6行）；这是协议夹具，不证明真实研究事实。" if self.round == 5 else "已直接复用第二版合成报告：保留更新频率对比，后续权限表不再保留；未新增检索或写作。"
                return call("report_reuse_version", {"version_id": base, "mode": "read_only" if self.round == 5 else "exact_reuse", "response": response}, f"{task.action_id}-reuse")
            return call("report_prepare_turn", {"base_version_id": base, "resolved_query": resolved, "change_hint": "Local node revision; runtime owns paths and acceptance"}, f"{task.action_id}-prepare")

        return script

    def outline_tree(self, planning):
        ledger = self.ledger()
        text = ledger.payload.get("dialogue_turn", {}).get("resolved_query") or self.public["turns"][0]["query"]
        uid = ledger.inputs()[-1]["input_id"]
        evidence = [{"source_ids": [m["source_id"]], "material_id": m["material_id"], "locator": {"lines": [1, 6]}, "use": "离线合成资料中的协议演练说明"} for m in ledger.materials()]
        nodes = []
        for node_id, title in NODES.items():
            node = {
                "node_id": node_id,
                "parent_id": None,
                "kind": "writable",
                "title": title,
                "question": f"{title}的范围与依据是什么？",
                "argument": ["说明合成资料的适用范围与限制"],
                "conclusion": "仅为离线协议夹具，不作真实事实结论",
                "evidence": evidence,
                "constraints": {"target_words": 875, "max_words": 1000},
                "requirement_ids": ["R1"],
                "relations": [],
                "gap_ids": ["g1"] if planning and node_id == "n_ops" else [],
                "status": "needs_evidence" if planning and node_id == "n_ops" else "ready",
            }
            nodes.append(node)
        changed = list(NODES) if self.round == 1 else ["n_ops"] if self.round in (2, 7) else ["n_privacy"]
        return {
            "report_title": "高校图书馆知识服务：离线合成协议演练",
            "requirements": [{"id": "R1", "input_id": uid, "text": text, "hard": True}],
            "nodes": nodes,
            "removed_node_ids": [],
            "write_requests": [] if planning else [{"key": f"{self.tag}-{node}", "node_ids": [node], "feedback_wanted": False} for node in changed],
            "gap_outcomes": [
                {"gap_id": g["gap_id"], "outcome": "resolved", "reason": "Synthetic supplement returned and saved with valid locators", "evidence_material_ids": [m["material_id"] for m in ledger.materials()]}
                for g in ledger.gaps()
                if g["status"] == "awaiting_outline"
            ],
            "user_decisions": [{"input_id": i["input_id"], "action": "apply_to_plan", "gap_ids": [], "reason": "Apply complete current requirements"} for i in ledger.inputs() if not i.get("applied")],
            "draft_dispositions": [],
            "feedback_dispositions": [],
        }

    def outline_script(self, task):
        review = task.metadata.get("report_task") == "integrate_review"
        planning = task.metadata.get("report_task") == "plan"

        def script(step, messages):
            if review:
                assert step == 1
                packet = task.context_refs["report_packet"]
                body = {"inputs_digest": packet["inputs_digest"], "reviewed_candidate_sha256": packet["candidate_sha256"], "review": {"status": "ready", "outcome": "deliver", "findings": [], "edits": [], "covered_requirement_ids": ["R1"]}}
                return envelope("integration_review", json.dumps(body), {"report_task": "integrate_review", "status": "ready"})
            if planning and step == 1:
                ledger = self.ledger()
                return call(
                    "report_register_research_request",
                    {
                        "questions": [
                            {
                                "mode": "new",
                                "question": "补充合成运维与权限演练材料",
                                "constraints": {},
                                "why_insufficient": "Exercise native fixed supplementary research",
                                "stop_condition": "One separately saved fixture body",
                                "requirement_refs": [{"input_id": ledger.inputs()[0]["input_id"], "quote": "知识库更新与运维"}],
                                "node_ids": [],
                                "materials_checked": [{"material_id": "m1", "lines": [1, 2]}],
                            }
                        ]
                    },
                    f"{task.action_id}-gap",
                )
            submit_step = 2 if planning else 1
            if step == submit_step:
                tree = self.outline_tree(planning)
                args = {"tree": tree}
                current = [d["node_id"] for d in self.ledger().payload["drafts"] if d.get("is_current")]
                if current:
                    changed = "n_ops" if self.round in (2, 7) else "n_privacy"
                    args.update(keep_nodes=[n for n in current if n != changed], rewrite_nodes=[changed], drop_nodes=[], decision_reason="Only the public user-requested node changed")
                return call("report_submit_outline", args, f"{task.action_id}-submit")
            assert step == submit_step + 1
            last_tool = next(m for m in reversed(messages) if m.type == "tool")
            saved = json.loads(last_tool.content)
            assert saved.get("saved") and saved.get("validation_ok"), saved
            return envelope("outline", None, {"delivery_mode": "file", "tree_path": saved["tree_path"]})

        return script

    def research_script(self, task):
        pre = task.metadata.get("report_pre_research") is True

        def script(step, messages):
            if step == 1:
                return call("report_fetch_material", {"url": "https://fixtures.invalid/" + ("initial" if pre else "supplement")}, f"{task.action_id}-fetch")
            assert step == 2
            materials = self.ledger().materials()
            latest = materials[-1]
            body = f"# 离线合成资料综述\n\n仅为协议夹具，非真实检索结果。 [{latest['source_id']}] ({latest['material_id']}, lines 1-6)\n"
            if not pre:
                body += "\n| gap_id | execution | material_ids |\n|---|---|---|\n" + "".join(f"| {gap} | searched | {latest['material_id']} |\n" for gap in task.metadata["report_gap_ids"])
            return AIMessage(content=body)

        return script

    def writer_script(self, task):
        def script(step, messages):
            node = task.metadata["report_node_id"]
            if step == 1:
                query = self.ledger().payload.get("dialogue_turn", {}).get("resolved_query") or self.public["turns"][0]["query"]
                body = f"离线合成章节：{NODES[node]}。以下文字只演练保存、篇幅和哈希协议，不构成真实研究。 [S1] [S2]\n\n"
                if node == "n_ops" and OPS in query:
                    body += "每周与每月更新对比：适用条件、维护负担与风险说明。\n\n"
                if node == "n_ops" and CLARITY in query:
                    body += "风险澄清：更新窗口、回滚记录与责任边界需明确。\n\n"
                if node == "n_privacy" and PRIVACY in query:
                    body += "| 跨院系权限隔离 | 授权边界 | 审计记录 |\n|---|---|---|\n| 合成演练字段 | 合成授权字段 | 合成审计字段 |\n\n"
                paragraph = "离线合成材料仅用于检验原生流程，保存证据定位并保留适用限制，不表示真实案例或事实已经得到验证。\n\n"
                while estimate_words(body) < 2400:
                    body += paragraph
                return call("write_file", {"description": "Save the assigned synthetic chapter through the native tool", "path": task.metadata["report_draft_path"], "content": body}, f"{task.action_id}-write")
            assert step == 2
            last = next(m for m in reversed(messages) if m.type == "tool")
            assert not str(last.content).startswith("Error"), last.content
            return envelope("node_draft", None, {"node_id": node, "draft_path": task.metadata["report_draft_path"]})

        return script

    def executor(self, state, runtime):
        driver = self

        class Executor:
            def execute(self, task):
                role = task.metadata.get("report_role") or task.subagent_type
                with driver.lock:
                    row = {"user_turn": driver.round, "attempt": driver.tag, "role": role, "action_id": task.action_id, "report_task": task.metadata.get("report_task"), "node_id": task.metadata.get("report_node_id")}
                    driver.delegates.append(row)
                if role == "writer" and driver.cancel_next_writer:
                    driver.cancel_next_writer = False
                    driver.abort.set()

                def factory(bound):
                    base = get_subagent_config(SP_SUBAGENT_REGISTRY_NAMES[bound.subagent_type], app_config=driver.config)
                    config, tools, _ = hooks.configure_report_subagent(bound, state=state, available_tools=[read_file_tool, write_file_tool, ls_tool, glob_tool, grep_tool, fixture_fetch], subagent_config=base)
                    return driver.executor_module.SubagentExecutor(
                        config=config,
                        tools=tools,
                        app_config=driver.config,
                        parent_model="fake-model",
                        thread_data=state["thread_data"],
                        thread_id="rehearsal",
                        run_id=bound.run_id,
                        parent_abort_event=driver.abort,
                        step_observer=lambda message, index: driver.observe_tool(bound, message),
                    )

                callback = (
                    driver.git_script(task)
                    if role == "git_agent"
                    else driver.outline_script(task)
                    if role == "outline"
                    else driver.research_script(task)
                    if role == "researcher"
                    else driver.writer_script(task)
                    if role == "writer"
                    else lambda step, messages: AIMessage(content=json.dumps({"summary": "No prior synthetic memory", "items": []}))
                )
                model = ScriptModel(responses=[], script=callback, recorder=driver.record, role=role, task_id=task.action_id)
                token = MODEL.set(model)
                try:
                    result = DR2SubagentExecutorAdapter(factory).execute(task)
                    row.update(executor_status=result.status.value, stop_reason=result.stop_reason, error=result.error)
                    return result
                finally:
                    MODEL.reset(token)

        return Executor()

    def observe_tool(self, task, message):
        if message.get("type") == "tool":
            with self.lock:
                self.tool_records.append({"user_turn": self.round, "attempt": self.tag, "role": task.metadata.get("report_role"), "action_id": task.action_id, "name": message.get("name"), "content": message.get("content")})


@tool("web_fetch")
def fixture_fetch(url: str) -> str:
    """Return an in-memory synthetic document; never access a URL."""
    assert url.startswith("https://fixtures.invalid/")
    return "\n".join(["离线合成资料，非真实研究。", "知识库更新与运维用于协议演练。", "每周和每月更新是脚本示例。", "权限隔离、授权边界和审计记录为合成字段。", "不包含真实案例、数据或事实质量判断。", "仅供原生证据定位与引用绑定测试。"])


def restore_archive(source_store, turn_id, outputs):
    manifest = source_store.verify(turn_id)
    source = source_store.directory(turn_id)
    audit = ReportTurnStore(outputs, **source_store.owner)
    restored_turn = {**source_store.inspect(turn_id), "turn_id": "t0001", "status": "accepted"}
    with audit.locked():
        shutil.copytree(source / "artifacts", audit.directory("t0001") / "artifacts")
        native.write_json_atomic(audit.directory("t0001") / "manifest.json", manifest)
        audit._record(restored_turn)
        index = audit.index()
        index.update(head="t0001", active_version="t0001", versions={"t0001": {"report_id": manifest["origin_report_id"], "base_version_id": None}})
        audit._save(index)
    paths = audit.restore("t0001")
    audit._accepted(paths)
    return {"restorable": True, "restored_report_id": paths.report_id, "directory": str(paths.directory)}


def verify_round(driver, row, live_versions):
    # Evaluator data is read only after the real graph turn has terminated.
    expected = json.loads((SUITE / "evaluator_checks.json").read_text())["cases"][0]["turns"][row["user_turn"] - 1]
    assert row["native_delivered"] == expected["native_delivery_required"]
    assert row["base_user_turn"] == expected["expected_base_turn"]
    assert row["accepted_head_user_turn"] == expected["expected_accepted_head_turn"]
    assert row["active_user_turn"] == expected["expected_active_turn"]
    assert not set(row["roles"]) & set(expected["forbidden_roles"])
    for report_id, hashes in live_versions.items():
        assert file_manifest(driver.outputs / "report" / report_id) == hashes, f"Accepted version changed: {report_id}"
    row["old_version_sha_unchanged"] = True
    if row["native_delivered"]:
        ledger = driver.ledger()
        measurement = ledger.payload["final_report"]["length"]
        assert measurement["in_estimated_range"] and 9000 <= measurement["estimated_words"] <= 11000, measurement
        row["review_action_id"] = ledger.payload["review"]["action_id"]
        row["delivery_action_id"] = ledger.payload["delivered"]["action_id"]
        row["native_length_ok"] = True
        row["estimated_words"] = measurement["estimated_words"]
        query = ledger.payload.get("dialogue_turn", {}).get("resolved_query") or driver.public["turns"][0]["query"]
        if row["user_turn"] in (3, 4):
            assert PRIVACY in query
        if row["user_turn"] == 4:
            assert OPS not in query
        if row["user_turn"] == 7:
            assert OPS in query and CLARITY in query and PRIVACY not in query
        if row["user_turn"] > 1:
            turn = driver.store().inspect(row["dialogue_turn_id"])
            changes = json.loads((driver.store().directory(turn["turn_id"]) / "changes.json").read_text())
            row["canonical_changes"] = changes
            wanted = "n_ops" if row["user_turn"] in (2, 7) else "n_privacy"
            assert changes["changed_nodes"] == [wanted], changes
            assert set(changes["kept_nodes"]) == set(NODES) - {wanted}


def rehearse(root, monkeypatch, *, inject_cancel=False):
    root.mkdir(parents=True, exist_ok=False)
    public = load_public_cases(SUITE / "requests.json")["cases"][0]
    assert hashlib.sha256((SUITE / "requests.json").read_bytes()).hexdigest() == json.loads((SUITE / "manifest.json").read_text())["sha256"]["requests.json"]
    monkeypatch.setattr(paths_module, "_paths", paths_module.Paths(root / "runtime"))
    config = _app_config()
    # Restore every singleton field after this pytest case, including failures.
    for name in ("_app_config", "_app_config_path", "_app_config_mtime", "_app_config_signature", "_app_config_is_custom"):
        monkeypatch.setattr(config_module, name, getattr(config_module, name))
    set_app_config(config)
    key = "deerflow.subagents.executor"
    old = sys.modules.pop(key, None)
    executor_module = importlib.import_module(key)
    attempts = []

    def deny_connect(*args, **kwargs):
        attempts.append("blocked")
        raise AssertionError("Network access forbidden in offline rehearsal")

    monkeypatch.setattr(socket.socket, "connect", deny_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", deny_connect)
    driver = Driver(root, executor_module, config, public)
    import deerflow.agents.lead_agent.agent as lead
    import deerflow.sp.runtime as runtime

    central = ScriptModel(responses=[], script=driver.central, recorder=driver.record, role="central")
    monkeypatch.setattr(lead, "create_chat_model", lambda **kwargs: central)
    monkeypatch.setattr(executor_module, "create_chat_model", lambda **kwargs: MODEL.get())
    monkeypatch.setattr(runtime.DR2SPExecutorProvider, "__call__", lambda self, state, runtime: driver.executor(state, runtime))
    graph = runtime.make_sp_agent({"configurable": {"model_name": "fake-model"}}, app_config=config)
    state, rows, live_versions = {}, [], {}
    cancelled = duplicate_ok = False
    try:
        for number, request in enumerate(public["turns"], 1):
            tries = ["cancelled", "duplicate", "retry"] if inject_cancel and number == 3 else ["normal"]
            for attempt in tries:
                driver.round, driver.tag, driver.abort = number, f"r{number}-{attempt}", Event()
                central.calls = 0
                driver.cancel_next_writer = attempt == "cancelled"
                mid = request["message_id"] + (".retry" if attempt == "retry" else "")
                extra = {"report_length": contract_for_band(10000)} if number == 1 else {"report_dialogue": {"enabled": True, "report_id": driver.generated_reports[1]}}
                message = HumanMessage(content=request["query"], id=mid, additional_kwargs=extra)
                driver.current_message = message
                state["messages"] = [*state.get("messages", []), message] if attempt != "duplicate" else state["messages"]
                before_calls, before_reports = len(driver.delegates), sorted(p.name for p in (driver.outputs / "report").glob("r*"))
                start = time.monotonic()
                latest = None
                try:
                    for latest in graph.stream(state, config={"recursion_limit": 1000}, context={"thread_id": "rehearsal", "run_id": driver.tag, "__run_abort_event": driver.abort}, stream_mode="values"):
                        pass
                    assert latest is not None
                    state = dict(latest)
                except BaseException as exc:
                    dump(root / driver.tag / "failure.json", {"error": str(exc), "delegates": driver.delegates[before_calls:], "last_tools": driver.tool_records[-5:]})
                    raise
                dump(root / driver.tag / "state.json", {**state, "messages": [message_to_dict(m) for m in state["messages"]]})
                dump(root / driver.tag / "execution.json", {"delegations": driver.delegates[before_calls:], "tools": [t for t in driver.tool_records if t["attempt"] == driver.tag]})
                ledger = driver.ledger()
                if attempt in ("cancelled", "duplicate"):
                    index = driver.store().index()
                    turn_id = index["requests"][mid]
                    assert driver.store().inspect(turn_id)["status"] == "cancelled"
                    assert index["head"] == driver.version(2)
                    for rid, hashes in live_versions.items():
                        assert file_manifest(driver.outputs / "report" / rid) == hashes
                    if attempt == "duplicate":
                        assert len(driver.delegates) == before_calls
                        assert sorted(p.name for p in (driver.outputs / "report").glob("r*")) == before_reports
                        duplicate_ok = True
                    else:
                        cancelled = True
                    dump(root / driver.tag / "outcome.json", {"status": attempt, "head_unchanged": True, "turn_id": turn_id})
                    continue
                native_delivered = ledger.payload.get("status") == "delivered" and (ledger.payload.get("delivered") or {}).get("run_id") == driver.tag
                assert native_delivered == (number not in (5, 6)), "This turn must be distinguished from an older native delivery"
                if native_delivered:
                    assert ledger.payload["status"] == "delivered" and ledger.payload["delivered"]["run_id"] == driver.tag, {"ledger": ledger.control_summary(), "tools": driver.tool_records[-4:]}
                    driver.generated_reports[number] = ledger.paths.report_id
                if number == 1:
                    index = {"head": None, "active_version": None}
                    turn_id, base_user_turn, head_round, active_round = None, None, 1, 1
                else:
                    index = driver.store().index()
                    turn_id = index["requests"][mid]
                    turn = driver.store().inspect(turn_id)
                    inverse = {driver.version(n): n for n in driver.generated_reports}
                    base_user_turn = inverse[turn["base_version_id"]]
                    head_round, active_round = inverse[index["head"]], inverse[index["active_version"]]
                row = {
                    "user_turn": number,
                    "source_message_id": mid,
                    "dialogue_turn_id": turn_id,
                    "base_user_turn": base_user_turn,
                    "accepted_head_user_turn": head_round,
                    "active_user_turn": active_round,
                    "native_delivered": native_delivered,
                    "roles": [d["role"] for d in driver.delegates[before_calls:]],
                    "native_length_ok": None,
                    "elapsed_seconds": time.monotonic() - start,
                }
                verify_round(driver, row, live_versions)
                if native_delivered:
                    live_versions[ledger.paths.report_id] = file_manifest(ledger.paths.directory)
                rows.append(row)
                dump(root / driver.tag / "outcome.json", row)
        for row in rows:
            turn_id = row["dialogue_turn_id"] or driver.version(1)
            row.update(restore_archive(driver.store(), turn_id, root / "standalone-restores" / f"r{row['user_turn']}"))
        assert not attempts
        private_fields = ("expected_base_turn", "expected_accepted_head_turn", "allowed_changed_intents", "withdrawn_requirements")
        for record in driver.model_records:
            prompt = "\n".join(str(m["data"]["content"]) for m in record["messages"])
            assert not any(f'"{field}"' in prompt for field in private_fields), "Evaluator metadata entered model input"
        assert rows[0]["review_action_id"] == "spact_sp-fixed-c0-review-r1"
        reviews = [row["review_action_id"] for row in rows if row["native_delivered"]]
        assert len(set(reviews)) == 5, reviews
        result = {
            "case_id": public["case_id"],
            "successful_turns": 7,
            "accepted_generation_turns": sorted(driver.generated_reports),
            "provider_calls": 0,
            "network_connect_attempts": len(attempts),
            "facts_evaluated": False,
            "evaluator_hints_in_model_inputs": False,
            "semantics": "scripted outputs, not independent model requirement resolution",
            "rounds": rows,
            "cancelled_attempt_preserved": cancelled,
            "duplicate_did_not_fork_or_write": duplicate_ok,
            "retry_used_new_source_message": inject_cancel,
            "delegations_by_role": dict(Counter(d["role"] for d in driver.delegates)),
            "model_requests_by_role": dict(Counter(d["role"] for d in driver.model_records)),
            "provider_usage": None,
        }
        dump(root / "delegations.json", driver.delegates)
        dump(root / "tool-events.json", driver.tool_records)
        dump(root / "result.json", result)
        return result
    finally:
        if old is not None:
            sys.modules[key] = old
        else:
            sys.modules.pop(key, None)
