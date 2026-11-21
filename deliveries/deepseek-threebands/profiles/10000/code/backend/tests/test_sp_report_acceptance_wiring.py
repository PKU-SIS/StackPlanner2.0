"""Wiring probe: make_sp_agent must enter fixed acceptance without Central."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_sp_report_p3 import _seed
from test_sp_report_wiring import _app_config

from deerflow.config.paths import get_paths
from deerflow.runtime.user_context import get_effective_user_id
from deerflow.sp.subagents import SPSubagentResult, SPSubagentStatus


class _NoCentralModel:
    def bind_tools(self, tools, **kwargs):  # type: ignore[no-untyped-def]
        return self

    def _generate(self, messages, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("fixed acceptance gate should bypass the Central model")


class _ReviewExecutor:
    def __init__(self):
        self.tasks = []

    def execute(self, task):
        self.tasks.append(task)
        report_task = str(task.metadata.get("report_task") or "")
        assert report_task == "integrate_review"
        packet = task.context_refs["report_packet"]
        content = json.dumps(
            {
                "inputs_digest": packet["inputs_digest"],
                "reviewed_candidate_sha256": packet["candidate_sha256"],
                "review": {
                    "status": "ready",
                    "outcome": "deliver",
                    "findings": [],
                    "covered_requirement_ids": ["R1"],
                    "edits": [],
                },
            },
            ensure_ascii=False,
        )
        return SPSubagentResult(
            status=SPSubagentStatus.COMPLETED,
            result="fixed review",
            artifact_content=content,
            artifact_type="integration_review",
            artifact_metadata={
                "artifact_type": "integration_review",
                "report_task": "integrate_review",
                "status": "ready",
            },
        )


def test_make_sp_agent_routes_ready_report_to_fixed_acceptance(monkeypatch, tmp_path):
    outputs = get_paths().sandbox_outputs_dir("t1", user_id=get_effective_user_id())
    if outputs.exists():
        import shutil

        shutil.rmtree(outputs)
    outputs.mkdir(parents=True, exist_ok=True)
    ctx, ledger = _seed(outputs)
    state = dict(ctx.state)
    state["messages"] = []
    executor = _ReviewExecutor()

    import deerflow.agents.lead_agent.agent as lead_agent_module
    import deerflow.sp.runtime as sp_runtime

    monkeypatch.setattr(lead_agent_module, "create_chat_model", lambda **kwargs: _NoCentralModel())
    monkeypatch.setattr(
        sp_runtime.DR2SPExecutorProvider,
        "__call__",
        lambda self, state, runtime: executor,
    )

    graph = sp_runtime.make_sp_agent({"configurable": {"model_name": "fake-model"}}, app_config=_app_config())
    result = graph.invoke(state, context={"thread_id": "t1", "run_id": "run1"})

    assert len(executor.tasks) == 1
    assert executor.tasks[0].metadata.get("report_acceptance_origin") == "harness"
    assert executor.tasks[0].subagent_type == "outline"
    assert "report_packet" in executor.tasks[0].context_refs
    assert set(executor.tasks[0].context_refs) == {"report_packet"}
    assert "instructions" not in executor.tasks[0].context_refs["report_packet"]
    assert ledger.paths.ledger.exists()
    current = json.loads(ledger.paths.ledger.read_text(encoding="utf-8"))
    assert current["review"]["status"] == "ready"
    assert current["final_report"] is not None
    assert current["delivered"] is not None
    assert result["messages"][-1].content
