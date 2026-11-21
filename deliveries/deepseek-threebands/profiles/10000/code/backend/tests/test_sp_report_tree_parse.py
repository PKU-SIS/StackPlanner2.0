"""Strict Outline JSON boundaries: syntax errors stay syntax errors."""

from __future__ import annotations

import json

import pytest

from deerflow.sp.report.ledger import ReportValidationError
from deerflow.sp.report.tree import parse_tree_payload


def test_syntax_error_reports_position_and_never_extracts_inner_object() -> None:
    text = (
        "{\n"
        '  "report_title": "示例报告",\n'
        '  "requirements": [\n'
        '    {"id": "req1", "argument": "第一行";}\n'
        "  ],\n"
        '  "nodes": []\n'
        "}\n"
    )

    with pytest.raises(ReportValidationError) as exc_info:
        parse_tree_payload(text)

    message = str(exc_info.value)
    assert "JSON syntax error at line 4, column" in message
    assert "nested object was extracted" in message
    assert "tree.report_title is required" not in message


def test_complete_json_document_is_parsed_normally() -> None:
    tree = {
        "report_title": "示例报告",
        "requirements": [{"id": "req1", "input_id": "u1", "text": "要求"}],
        "nodes": [],
    }
    assert parse_tree_payload(json.dumps(tree, ensure_ascii=False)) == tree


def test_fenced_complete_document_remains_compatible() -> None:
    tree = {"report_title": "示例报告", "requirements": [], "nodes": []}
    fenced = "```json\n" + json.dumps(tree, ensure_ascii=False) + "\n```"
    assert parse_tree_payload(fenced) == tree


def test_return_envelope_is_identified_as_envelope_not_tree() -> None:
    envelope = {
        "summary": "compact",
        "artifact_content": {"report_title": "示例报告", "requirements": [], "nodes": []},
        "artifact_type": "outline",
        "artifact_metadata": {"completion_status": "complete"},
    }

    with pytest.raises(ReportValidationError, match="response envelope"):
        parse_tree_payload(envelope)


def test_auxiliary_fields_on_a_legal_tree_are_not_rejected() -> None:
    tree = {
        "report_title": "示例报告",
        "requirements": [],
        "nodes": [],
        "summary": "auxiliary notes are allowed",
        "artifact_content": {"note": "not the tree"},
    }

    assert parse_tree_payload(tree) == tree
