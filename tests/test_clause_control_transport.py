import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from dcc_harness.datasets.clause_provider import recover_control
from dcc_harness.datasets.clause_tools import document_read
from dcc_harness.datasets.clause_feedback import _central_control_channel

RAW = "<tool_call><function=sp_think><parameter=task>inspect</parameter></function></tool_call>"
OPTIONS = {
    "tools": [
        {
            "type": "function",
            "function": {
                "name": n,
                "parameters": {
                    "type": "object",
                    "required": ["task"],
                    "properties": {"task": {"type": "string"}},
                },
            },
        }
        for n in ("sp_think", "sp_delegate")
    ]
}


def response(content=RAW, finish="stop", **kwargs):
    return ChatResult(
        generations=[
            ChatGeneration(
                message=AIMessage(content=content, **kwargs),
                generation_info={"finish_reason": finish},
            )
        ]
    )


def test_restores_single_bound_visible_call_without_changing_decision():
    r = recover_control(response(), OPTIONS).generations[0].message
    assert not r.content and [(c["name"], c["args"]) for c in r.tool_calls] == [
        ("sp_think", {"task": "inspect"})
    ]


@pytest.mark.parametrize(
    "raw, finish, options",
    [
        ("Example " + RAW, "stop", OPTIONS),
        (RAW + RAW, "stop", OPTIONS),
        (RAW, "length", OPTIONS),
        (RAW, "stop", {}),
        (RAW, "stop", {**OPTIONS, "tool_choice": "none"}),
        (RAW.replace("sp_think", "sp_finish"), "stop", OPTIONS),
        (
            RAW.replace("</function>", "<parameter=task>B</parameter></function>"),
            "stop",
            OPTIONS,
        ),
        (RAW.replace("parameter=task", "parameter=unknown"), "stop", OPTIONS),
        (
            RAW,
            "stop",
            {
                **OPTIONS,
                "tool_choice": {
                    "type": "function",
                    "function": {"name": "sp_delegate"},
                },
            },
        ),
    ],
)
def test_unsafe_or_incomplete_markup_stays_text(raw, finish, options):
    r = recover_control(response(raw, finish), options).generations[0].message
    assert r.content == raw and not r.tool_calls


def test_does_not_recover_promoted_reasoning_or_overwrite_native_calls():
    assert (
        not recover_control(
            response(additional_kwargs={"reasoning_promoted_to_content": True}), OPTIONS
        )
        .generations[0]
        .message.tool_calls
    )
    call = dict(name="sp_think", args={"task": "other"}, id="already")
    assert (
        recover_control(response(tool_calls=[call]), OPTIONS)
        .generations[0]
        .message.tool_calls[0]["id"]
        == "already"
    )


def test_source_index_retains_source_and_distinguishes_partial_reads():
    source = "Section 2 and Section 2.1\nSECTION 2 again.\n"
    read = document_read(source)
    assert read["numbered_mentions"] == {
        "complete": True,
        "lines_by_label": {"section 2": [1, 2], "section 2.1": [1]},
    }
    assert "".join(row["text"] for row in read["lines"]) == source
    assert "numbered_mentions" not in document_read(source, end_line=1)


def test_only_native_warning_on_read_only_delegation_closes_tool_turn():
    task = {
        "role": "user",
        "content": '<sp-subagent-task>{"action_id":"a"}</sp-subagent-task>',
    }
    warn = {
        "role": "user",
        "name": "loop_warning",
        "content": "[LOOP DETECTED] You have made 15 total tool calls without producing a final answer. Stop exploring, use the evidence already collected, and produce your final answer now.",
    }
    q = {"messages": [task, warn], "tools": [{"function": {"name": "grep"}}]}
    assert _central_control_channel(q) == {"messages": [task, warn]}
    for alt in [
        {**q, "messages": [task, {**warn, "name": "other"}]},
        {**q, "tools": [{"function": {"name": "write_file"}}]},
    ]:
        assert _central_control_channel(alt) == alt


def test_truncated_write_retries_same_destination_without_using_partial_body():
    from langchain_core.utils.function_calling import convert_to_openai_tool
    from deerflow.sandbox.tools import write_file_tool
    from dcc_harness.datasets.clause_provider import (
        _answer_write_retry,
        _serialized_write,
    )

    options = {"tools": [convert_to_openai_tool(write_file_tool)]}
    header = '{"description":"deliver","path":"/mnt/user-data/outputs/findings.json","content":"[\\n {\\"text\\":\\"partial'
    first = response(
        "",
        invalid_tool_calls=[
            {"name": "write_file", "args": header, "id": "cut", "error": "truncated"}
        ],
        usage_metadata={
            "input_tokens": 100,
            "output_tokens": 8192,
            "total_tokens": 8292,
        },
    )
    retry = _answer_write_retry(first, options, 8192)
    assert retry["max_completion_tokens"] == 24576
    assert _answer_write_retry(first, options, 10000) is None

    def reply(path, body):
        return response(
            "",
            finish="tool_calls",
            tool_calls=[
                {
                    "name": "write_file",
                    "args": {"path": path, "content": body, "description": "deliver"},
                    "id": "fresh",
                }
            ],
            usage_metadata={
                "input_tokens": 100,
                "output_tokens": 20,
                "total_tokens": 120,
            },
        )

    path = "/mnt/user-data/outputs/findings.json"
    actual = _serialized_write(
        first, reply(path, [{"text": "fresh", "explanation": "brief"}]), retry
    )
    msg = actual.generations[0].message
    assert (
        msg.tool_calls[0]["args"]["content"]
        == '[{"text":"fresh","explanation":"brief"}]'
    )
    assert msg.usage_metadata["output_tokens"] == 8212
    assert _serialized_write(first, reply("/tmp/x", []), retry) is first


def test_shell_label_keeps_command_and_requires_bound_bash():
    from dcc_harness.datasets.clause_provider import _normalize_shell_labels

    r = response(
        "", tool_calls=[{"name": "bash", "args": {"command": "pwd"}, "id": "native"}]
    )
    assert (
        "description"
        not in _normalize_shell_labels(r, {})
        .generations[0]
        .message.tool_calls[0]["args"]
    )
    actual = _normalize_shell_labels(r, {"tools": [{"function": {"name": "bash"}}]})
    assert actual.generations[0].message.tool_calls[0]["args"] == {
        "command": "pwd",
        "description": "Run the delegated shell command",
    }


def test_alternate_json_needs_successful_action_write_and_matching_bytes(tmp_path):
    from types import SimpleNamespace as S
    from dcc_harness.datasets.clause_delivery import FileDeliveryPolicy

    policy = FileDeliveryPolicy()
    ctx = S(state={"thread_data": {"outputs_path": str(tmp_path)}})
    task = S(
        action_id="a",
        task="Write the final JSON answer.",
        expected_output="Final JSON file under /mnt/user-data/outputs/findings.json.",
    )
    policy.configure_task(task, ctx)
    alias = tmp_path / "findings.json"
    alias.write_text("[]")
    args = {"path": "/mnt/user-data/outputs/findings.json", "content": "[]"}
    policy.checked_execution(task, "write_file", args, "Error")
    assert not policy._verified_writes
    policy.checked_execution(task, "write_file", args, "OK")
    result = S(
        artifact_metadata={
            "completion_status": "complete",
            "created_paths": [args["path"]],
        },
        artifact_content=None,
        stop_reason=None,
    )
    policy.prepare_result(task, result, ctx)
    assert (
        tmp_path / "review-a.json"
    ).read_text() == "[]" and result.artifact_metadata["created_paths"] == [
        "/mnt/user-data/outputs/review-a.json"
    ]


@pytest.mark.parametrize(
    "scenario", ["valid", "preexisting", "overlap", "undeclared", "symlink", "partial"]
)
def test_central_assigned_filename_requires_exclusive_fresh_declared_file(
    tmp_path, scenario
):
    from types import SimpleNamespace as S
    from dcc_harness.datasets.clause_delivery import FileDeliveryPolicy

    policy = FileDeliveryPolicy()
    ctx = S(state={"thread_data": {"outputs_path": str(tmp_path)}})
    alias = tmp_path / "findings.json"
    virtual = "/mnt/user-data/outputs/findings.json"
    if scenario == "preexisting":
        alias.write_text("[]")
    task = S(
        action_id="a",
        task="Write the final JSON answer.",
        expected_output=f"A registered UTF-8 .json file at {virtual} containing the final JSON array.",
    )
    policy.configure_task(task, ctx)
    if scenario == "overlap":
        policy.configure_task(
            S(action_id="b", task="Check the source.", expected_output="Notes."), ctx
        )
    if scenario == "symlink":
        other = tmp_path / "unrelated"
        other.write_text("[]")
        alias.symlink_to(other)
    else:
        alias.write_text("[]")
    md = {
        "completion_status": "partial" if scenario == "partial" else "complete",
        "created_paths": [] if scenario == "undeclared" else [virtual],
    }
    result = S(
        artifact_metadata=md,
        artifact_content=None,
        stop_reason=None,
        result="status only",
    )
    policy.prepare_result(task, result, ctx)
    assert (tmp_path / "review-a.json").exists() == (scenario == "valid")
    if scenario == "valid":
        assert (
            md["created_paths"] == ["/mnt/user-data/outputs/review-a.json"]
            and md["clause_file_sha256"]
        )


def test_truncated_shell_is_a_native_schema_error_and_never_an_executable_command():
    from dcc_harness.datasets.clause_provider import _report_cut_shell
    from deerflow.sandbox.tools import bash_tool
    from langchain_core.utils.function_calling import convert_to_openai_tool
    from jsonschema import Draft202012Validator

    wire = convert_to_openai_tool(bash_tool)
    opts = {"tools": [wire]}
    bad = {
        "name": "bash",
        "id": "native-id",
        "args": '{"command": "partial',
        "error": "truncated",
    }

    def cut():
        return response(
            "",
            invalid_tool_calls=[bad],
            usage_metadata={
                "input_tokens": 10,
                "output_tokens": 8192,
                "total_tokens": 8202,
            },
        )

    assert not _report_cut_shell(cut(), opts, 10000).generations[0].message.tool_calls
    msg = _report_cut_shell(cut(), opts, 8192).generations[0].message
    call = msg.tool_calls[0]
    assert (
        call["id"] == "native-id"
        and call["name"] == "bash"
        and not msg.invalid_tool_calls
    )
    assert "command" not in call["args"] and not Draft202012Validator(
        wire["function"]["parameters"]
    ).is_valid(call["args"])


def test_genuine_final_return_uses_receipt_only_after_verified_complete_source(tmp_path):
    import json
    from dcc_harness.datasets.clause_feedback import (
        _final_answer_closeout,
        _IDENTICAL_WARNING,
    )
    from dcc_harness.datasets.clause_tools import document_read

    source = "/mnt/user-data/uploads/document.txt"
    task = {
        "action_id": "a",
        "task": "Deliver the final JSON answer.",
        "protocol": {"sender": "central"},
    }
    messages = [
        {
            "role": "user",
            "content": "<sp-subagent-task>" + json.dumps(task) + "</sp-subagent-task>",
        },
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "read",
                    "function": {
                        "name": "read_file",
                        "arguments": json.dumps({"path": source}),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "read",
            "content": json.dumps(document_read("first\nsecond\n")),
        },
    ]
    q = {
        "tools": [{"function": {"name": "bash"}}],
        "messages": messages,
        "max_completion_tokens": 8192,
    }
    from types import SimpleNamespace
    from jsonschema import Draft202012Validator
    from deerflow.sp.actions.run_policy import active_run_policy
    from dcc_harness.datasets.clause_delivery import FileDeliveryPolicy

    policy = FileDeliveryPolicy()
    dest = "/mnt/user-data/outputs/result.json"
    ctx = SimpleNamespace(state={"thread_data":{"outputs_path":str(tmp_path)}})
    writer = SimpleNamespace(action_id="a", task="Deliver the final JSON answer.", expected_output="Final JSON array at " + dest)
    policy.configure_task(writer, ctx)
    token = active_run_policy.set(policy)
    try:
        assert not _final_answer_closeout(q, messages, ["bash"])
        (tmp_path / "result.json").write_text("[]")
        assert not _final_answer_closeout(q, messages[:-1], ["bash"])
        short = _final_answer_closeout(q, messages, ["bash"])
        schema = short["response_format"]["json_schema"]["schema"]
        assert "artifact_content" not in schema["properties"]
        for status in ("complete", "partial", "blocked"):
            record = {"summary":"Decision", "artifact_metadata":{"completion_status":status,"evidence_gaps":[],"disclosed_limitations":[],"created_paths":[dest]}}
            Draft202012Validator(schema).validate(record)
    finally:
        active_run_policy.reset(token)
    warn = {"role": "user", "name": "loop_warning", "content": _IDENTICAL_WARNING}
    assert _final_answer_closeout(q, messages, ["bash"]) is None
    blocked_write = [
        {"role": "assistant", "tool_calls": [{"id": "output", "function": {
            "name": "bash", "arguments": json.dumps({"command": "cat > /mnt/user-data/outputs/report.json"})
        }}]},
        {"role": "tool", "tool_call_id": "output", "content":
         "Command blocked: command too long. Please use a safer alternative approach."},
    ]
    assert _final_answer_closeout(q, messages + blocked_write, ["bash"])
    assert not _final_answer_closeout(q, messages[:-1] + blocked_write, ["bash"])
    assert not _final_answer_closeout(q, messages + [*blocked_write[:1], {**blocked_write[1], "tool_call_id":"other"}], ["bash"])
    assert not _final_answer_closeout(q, messages + [*blocked_write[:1], {**blocked_write[1], "role":"user"}], ["bash"])
    alt = _final_answer_closeout(q, messages + [warn], ["bash"])
    assert (
        alt["messages"] == q["messages"]
        and "tools" not in alt
        and alt["max_completion_tokens"] == 12288
    )
    fmt = alt["response_format"]["json_schema"]["schema"]["properties"]
    assert (
        fmt["artifact_content"]["type"] == "array"
        and "partial"
        in fmt["artifact_metadata"]["properties"]["completion_status"]["enum"]
    )
    from jsonschema import Draft202012Validator
    receipt_schema = alt["response_format"]["json_schema"]["schema"]
    bad = {"summary":"Wrote results", "artifact_content":None, "artifact_metadata":{"completion_status":"complete","evidence_gaps":[],"disclosed_limitations":[]}}
    assert not Draft202012Validator(receipt_schema).is_valid(bad)
    assert Draft202012Validator(receipt_schema).is_valid({**bad,"artifact_content":[]})
    assert _final_answer_closeout(q, messages, ["bash"], returning=True)
    assert not _final_answer_closeout(q, messages[:-1], ["bash"], returning=True)
    assert not _final_answer_closeout(q, messages + [warn], ["sp_delegate"])
    assert not _final_answer_closeout(
        q,
        [
            {
                **messages[0],
                "content": messages[0]["content"].replace(
                    "Deliver the final JSON answer.", "Inspect the source."
                ),
            },
            *messages[1:],
            warn,
        ],
        ["bash"],
    )


@pytest.mark.parametrize(
    "body,missing",
    [
        ("[]", False),
        ('{"summary":"done","artifact_content":[]}', False),
        ('{"artifact_metadata":{"completion_status":"partial"}}', False),
        (
            '{"artifact_metadata":{"created_paths":["/mnt/user-data/outputs/a.json"]}}',
            False,
        ),
        ('{"summary":"review complete","artifact_content":"draft only"}', True),
    ],
)
def test_receipt_retry_respects_existing_body_and_explicit_status(body, missing):
    from dcc_harness.datasets.clause_provider import _needs_delivery_body

    assert _needs_delivery_body(AIMessage(content=body)) == missing


def test_unexecuted_specialist_call_alone_is_not_an_intermediate_completion(tmp_path):
    from types import SimpleNamespace as S
    from dcc_harness.datasets.clause_delivery import FileDeliveryPolicy
    policy=FileDeliveryPolicy()
    ctx=S(state={"thread_data":{"outputs_path":str(tmp_path)}})
    task=S(action_id="inspect", task="Inspect the document.",expected_output="Inspection notes.")
    policy.configure_task(task,ctx)
    result=S(artifact_metadata={"completion_status":"complete"},stop_reason=None,
             artifact_content="<tool_call><function=write_file><parameter=content>[]</parameter></function></tool_call>",result="inspection")
    policy.prepare_result(task,result,ctx)
    assert result.artifact_metadata["completion_status"]=="partial"
    assert policy.intermediate_artifact_type(task) is None and not list(tmp_path.iterdir())
