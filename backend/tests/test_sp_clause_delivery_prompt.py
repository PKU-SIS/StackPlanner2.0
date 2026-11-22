"""One final transport for a bound CLAUSE audit; original host inputs stay intact."""

import copy
import json
import os
import subprocess
import sys
import textwrap
from dataclasses import asdict
from pathlib import Path

import pytest
from dcc_harness.data import PublicTask
from dcc_harness.datasets.clause import prepare_task

from deerflow.sp.subagents import SPSubagentTask
from deerflow.sp.subagents.dr2_adapter import render_sp_subagent_prompt

LEGACY_MARKERS = (
    "The reporter must write /mnt/user-data/outputs/clause_answer-<action_id>.json",
    "immediately return the internal SP specialist envelope",
    "set artifact_content to null and artifact_metadata.created_paths",
    "The required deliverable is a UTF-8 .json file",
)


def bound_task():
    # Pin the recorded legacy harness control text. Upstream dataset prompt
    # changes must not silently change the regression input being exercised.
    original = (Path(__file__).parent / "fixtures/clause_legacy_public_delivery.txt").read_text()
    public = {
        "delivery_mode": "artifact",
        "output_parse_mode": "json",
        "output_schema": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"text": {"type": "string"}, "explanation": {"type": "string"}},
                "required": ["text", "explanation"],
                "additionalProperties": False,
            },
        },
        "source_requirements": {"audit_agent": "reporter", "files": [{"path": "/mnt/user-data/uploads/document.txt", "total_lines": 1}], "document_ids": []},
        "original_issue": original,
        "original_request": original,
    }
    return SPSubagentTask(action_id="audit", subagent_type="reporter", task=original, description="Whole-document audit", context_refs={"task_contract": public}, metadata={"skill_names": ["clause"]})


def test_bound_clause_renderer_unifies_only_known_transport_in_model_visible_copy():
    task = bound_task()
    before = copy.deepcopy(task)
    rendered = render_sp_subagent_prompt(task)
    for marker in LEGACY_MARKERS:
        assert marker not in rendered
    assert "complete raw JSON array" in rendered
    assert "optional draft" in rendered
    assert "<clause-audit-completion>" in rendered
    assert "their presence alone does not make the audit incomplete" in rendered
    assert "public document actually supplied" in rendered
    assert "physical line breaks and page boundaries" in rendered
    assert "unread required ranges" in rendered
    assert "A completed audit may return []" in rendered
    assert "required source access, task coverage or required execution/checks" in rendered
    assert "Read it using file tools; review the entire document" in rendered
    assert "Preserve every supported issue." in rendered
    assert task == before
    assert all(marker in task.task for marker in LEGACY_MARKERS)


@pytest.mark.parametrize("damage", ["unbound", "different_shape", "different_role", "different_skill", "invalid_skill_selection", "different_source", "missing_source", "inline"])
def test_unbound_and_other_tasks_keep_their_original_transport(damage):
    task = bound_task()
    if damage == "unbound":
        task.context_refs = {}
    elif damage == "different_shape":
        task.context_refs["task_contract"]["output_schema"] = {"type": "object"}
    elif damage == "different_role":
        task.subagent_type = "researcher"
    elif damage == "different_skill":
        task.metadata["skill_names"] = ["other"]
    elif damage == "invalid_skill_selection":
        task.metadata["skill_names"] = 1
    elif damage == "different_source":
        task.context_refs["task_contract"]["source_requirements"]["files"][0]["path"] = "/mnt/user-data/uploads/other.txt"
    elif damage == "missing_source":
        task.context_refs["task_contract"]["source_requirements"]["files"] = []
    else:
        task.context_refs["task_contract"]["delivery_mode"] = "inline"
    rendered = render_sp_subagent_prompt(task)
    assert all(marker in rendered for marker in LEGACY_MARKERS)
    assert "<clause-audit-completion>" not in rendered


def test_source_artifact_bodies_and_unknown_user_wording_are_never_rewritten():
    task = bound_task()
    original = task.task
    task.context_refs["artifact_bodies"] = {"items": [{"body": original}]}
    task.expected_output = "A user-authored custom file instruction: preserve this exact text."
    value = render_sp_subagent_prompt(task)
    payload = json.loads(value.split("<sp-subagent-task>\n", 1)[1].split("\n</sp-subagent-task>", 1)[0])
    assert payload["context_refs"]["artifact_bodies"]["items"][0]["body"] == original
    assert payload["expected_output"] == task.expected_output


def test_unknown_similar_instruction_block_is_not_partially_removed():
    task = bound_task()
    task.task = task.task.replace("is required by native artifact ownership checks", "is requested by the caller")
    value = render_sp_subagent_prompt(task)
    payload = json.loads(value.split("<sp-subagent-task>\n", 1)[1].split("\n</sp-subagent-task>", 1)[0])
    assert payload["task"] == task.task
    assert "<clause-audit-completion>" in value


@pytest.mark.parametrize("bound", [False, True])
def test_current_dataset_prepare_task_keeps_its_independent_prompt_behavior(bound):
    original = prepare_task(PublicTask(benchmark="clause", prompt="Review every supported issue."))
    task = bound_task()
    task.task = original
    task.context_refs["task_contract"]["original_issue"] = original
    task.context_refs["task_contract"]["original_request"] = original
    if not bound:
        task.metadata["skill_names"] = []
    before = copy.deepcopy(task)
    rendered = render_sp_subagent_prompt(task)
    payload = json.loads(rendered.split("<sp-subagent-task>\n", 1)[1].split("\n</sp-subagent-task>", 1)[0])
    if bound and all(marker in original for marker in LEGACY_MARKERS):
        assert all(marker not in payload["task"] for marker in LEGACY_MARKERS)
    else:
        assert payload["task"] == original
        assert payload["protocol"]["objective"] == original
        assert payload["context_refs"]["task_contract"]["original_issue"] == original
        assert payload["context_refs"]["task_contract"]["original_request"] == original
    assert task == before


@pytest.mark.parametrize("outcome", ["empty_complete", "empty_without_source", "explicit_partial"])
def test_initial_completion_guidance_preserves_native_empty_and_missing_source_gates(tmp_path, outcome):
    from types import SimpleNamespace

    from langchain_core.messages import AIMessage, ToolMessage

    from deerflow.sp.actions import ActionType, SPAction, build_default_action_router
    from deerflow.sp.artifacts import SPArtifactAdapter
    from deerflow.sp.memory import TaskMemoryStack
    from deerflow.sp.subagents.dr2_adapter import normalize_dr2_subagent_result

    current_task = bound_task()
    raw = "[]" if outcome != "explicit_partial" else json.dumps({"artifact_content": None, "artifact_metadata": {"completion_status": "partial", "evidence_gaps": ["The required source file could not be read."]}})
    pairs = []
    if outcome == "empty_complete":
        pairs = [
            AIMessage(content="", tool_calls=[{"id": "read", "name": "read_file", "args": {"path": "/mnt/user-data/uploads/document.txt"}}]),
            ToolMessage(content="A complete public source sentence.", name="read_file", tool_call_id="read"),
        ]
    messages = [*pairs, AIMessage(content=raw)]

    class Executor:
        def execute(self, delegated_task):
            assert "<clause-audit-completion>" in render_sp_subagent_prompt(delegated_task)
            return normalize_dr2_subagent_result(SimpleNamespace(status="completed", result=raw, ai_messages=[message.model_dump() for message in messages]), task=delegated_task)

    action = SPAction.create(ActionType.DELEGATE, reason="Audit public source", action_id="audit", target_agent="reporter", task=current_task.task, stage="reporting", metadata={"skill_names": ["clause"]})
    state = {"sp_task_contract": current_task.context_refs["task_contract"], "thread_data": {"outputs_path": str(tmp_path)}, "sp_loop_run_id": "run", "sp_non_interactive": True}
    result = build_default_action_router(delegate_executor=Executor()).execute(action, state=state, thread_id="thread", run_id="run")
    observation = TaskMemoryStack.from_dict(result.state_update["sp_task_memory"]).entries[-1]
    if outcome == "empty_complete":
        assert observation.metadata["completion_status"] == "complete"
        reference = result.state_update["sp_current_artifact_refs"]["report_revision"]
        content, truncated = SPArtifactAdapter().read_text_artifact(reference, state=state, thread_id="thread", max_chars=1000)
        assert content == "[]" and not truncated
    else:
        assert observation.metadata["completion_status"] in {"partial", "blocked"}
        assert observation.metadata["evidence_gaps"]
        if outcome == "explicit_partial":
            assert "The required source file could not be read." in observation.metadata["evidence_gaps"]


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("syntax_grammar", [False, True])
def test_real_native_builder_wire_has_one_clause_success_transport(tmp_path, asynchronous, syntax_grammar):
    # A fresh process imports the real executor, bypassing conftest's lightweight
    # executor mock. The actual middleware chain and Vllm HTTP serialization run.
    task = bound_task()
    fixture = tmp_path / "task.json"
    fixture.write_text(json.dumps(asdict(task)))
    script = textwrap.dedent(
        """
        import asyncio, json, os, pathlib
        from types import SimpleNamespace
        from unittest.mock import patch
        import httpx
        from langchain_core.messages import AIMessage, ToolMessage
        from deerflow.config.app_config import AppConfig, set_app_config
        from deerflow.models.vllm_provider import VllmChatModel
        from deerflow.runtime.user_context import set_current_user
        from deerflow.sp.runtime import DR2SPExecutorProvider
        from deerflow.sp.subagents import SPSubagentTask
        from deerflow.sp.subagents.dr2_adapter import render_sp_subagent_prompt

        native = pathlib.Path(os.environ['FIXTURE_NATIVE_ROOT'])
        task = SPSubagentTask(**json.loads(pathlib.Path(os.environ['FIXTURE_TASK']).read_text()))
        syntax_grammar = os.environ['FIXTURE_SYNTAX_GRAMMAR'] == '1'
        for flag in ['SP_CLAUSE_SYNTAX_GRAMMAR', 'SP_CLAUSE_SEMANTIC_REVIEW', 'SP_CLAUSE_QUOTE_REVIEW']:
            os.environ[flag] = '1' if syntax_grammar else '0'
        source = 'Payment is due on 1 January.'
        if syntax_grammar:
            source = 'The definition says "Information");tion"); Payment is due on 1 January.'
        final = json.dumps([{'text':source, 'explanation':'A source-only fixture.'}])
        wrapped = json.dumps([{'text':'"'+source+'"', 'explanation':'A source-only fixture.'}])
        app = AppConfig.model_validate({
            'models':[{'name':'fixture','use':'deerflow.models.vllm_provider:VllmChatModel','model':'fixture',
                       'api_key':'fixture','base_url':'http://fixture.invalid/v1','context_window':73728}],
            'sandbox':{'use':'deerflow.sandbox.local:LocalSandboxProvider'},
            'skills':{'path':str(native/'configs/stackplanner_skills/clause'),
                      'use':'deerflow.skills.storage.local_skill_storage:LocalSkillStorage'},
            'summarization':{'enabled':True},'memory':{'enabled':False}})
        set_app_config(app)
        set_current_user(SimpleNamespace(id='fixture'))
        context={'thread_id':'fixture','run_id':'fixture','user_id':'fixture'}
        prefetch = {'sp_source_prefetch_enabled':True,'sp_source_prefetch_tokenizer':'fixture','sp_source_prefetch_context_tokens':73728,'sp_source_prefetch_response_tokens':1024} if syntax_grammar else {}
        provider=DR2SPExecutorProvider(app_config=app,parent_model='fixture',runnable_config={'configurable':prefetch},available_skill_names=frozenset({'clause'}),user_id='fixture')
        executor=provider({'sp_non_interactive':True},SimpleNamespace(context=context,stream_writer=None)).adapter._executor_factory(task)
        requests=[]
        def endpoint(request):
            requests.append(json.loads(request.content))
            replies = ['[{"text":"bad"quote","explanation":"draft"}]', final, wrapped, final] if syntax_grammar else [final]
            choice = {'index':0,'message':{'role':'assistant','content':replies[len(requests)-1]},'finish_reason':'stop'}
            return httpx.Response(200,json={'id':'fixture-'+str(len(requests)),'object':'chat.completion','created':0,'model':'fixture','choices':[choice]})
        transport=httpx.MockTransport(endpoint)
        sync=httpx.Client(transport=transport)
        ac=httpx.AsyncClient(transport=transport)
        model=VllmChatModel(model='fixture',api_key='fixture',base_url='http://fixture.invalid/v1',http_client=sync,http_async_client=ac,max_retries=0,syntax_grammar_backend='sglang')
        async def initial():
            state,tools,deferred=await executor._build_initial_state(render_sp_subagent_prompt(task))
            state['messages'].extend([AIMessage(content='',tool_calls=[{'name':'read_file','id':'read','args':{'path':'/mnt/user-data/uploads/document.txt'}}]),ToolMessage(content=source,name='read_file',tool_call_id='read')])
            executor._prefetch_captured.extend(message.model_dump() for message in state['messages'][-2:])
            return state,tools,deferred
        with (
            patch('deerflow.subagents.executor.create_chat_model',return_value=model),
            patch('deerflow.models.create_chat_model',return_value=model),
            patch('deerflow.agents.middlewares.summarization_middleware.create_chat_model',return_value=model),
            patch('deerflow.sp.source_prefetch.count_source_prompt_tokens',return_value=20),
        ):
            state,tools,deferred=asyncio.run(initial())
            graph=executor._create_agent(tools,deferred_setup=deferred)
            result=asyncio.run(graph.ainvoke(state,context=context)) if os.environ['FIXTURE_ASYNC']=='1' else graph.invoke(state,context=context)
        assert result['messages'][-1].content==final
        if syntax_grammar:
            assert len(requests)==4
            assert [i for i, request in enumerate(requests) if 'response_format' in request]==[1,2,3]
            assert all(not request.get('tools') for request in requests[1:])
        assert len(executor._runtime_middlewares)>=20
        for request in requests:
            rendered=json.dumps(request['messages'])
            assert '<clause-audit-completion>' in rendered
            assert 'their presence alone does not make the audit incomplete' in rendered
            assert 'A completed audit may return []' in rendered
            assert 'required source access, task coverage or required execution/checks' in rendered
            for marker in json.loads(os.environ['FIXTURE_LEGACY_MARKERS']):
                assert marker not in rendered, marker
            assert 'complete raw JSON array' in rendered
        pathlib.Path(os.environ['FIXTURE_WIRE']).write_text(json.dumps(requests))
        asyncio.run(ac.aclose())
        sync.close()
        """
    )
    native = Path(__file__).resolve().parents[2]
    env = {
        **os.environ,
        "DEER_FLOW_HOME": str(tmp_path / "runtime"),
        "DEER_FLOW_PROJECT_ROOT": str(native),
        "FIXTURE_NATIVE_ROOT": str(native),
        "FIXTURE_TASK": str(fixture),
        "FIXTURE_ASYNC": "1" if asynchronous else "0",
        "FIXTURE_SYNTAX_GRAMMAR": "1" if syntax_grammar else "0",
        "FIXTURE_WIRE": str(tmp_path / "wire.json"),
        "FIXTURE_LEGACY_MARKERS": json.dumps(LEGACY_MARKERS),
    }
    completed = subprocess.run([sys.executable, "-c", script], env=env, text=True, capture_output=True, timeout=90)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads((tmp_path / "wire.json").read_text())
