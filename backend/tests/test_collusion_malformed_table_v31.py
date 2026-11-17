"""Malformed OCR layout must not abort unrelated evidence extraction."""

import pytest
from bid_audit.structured_text import html_tables


@pytest.mark.parametrize("attribute,value", [("colspan", "5000"), ("rowspan", "9000"), ("colspan", "0"), ("rowspan", "-1"), ("colspan", "broken")])
def test_invalid_table_is_quarantined_without_losing_text_or_valid_tables(attribute, value):
    warnings = []
    raw = f'<table><tr><td>A</td><td {attribute}="{value}">原始文字</td></tr></table><table><tr><td>要求</td><td>响应</td></tr></table>'
    result = html_tables(raw, warnings=warnings)
    assert result[0]["rows"] == []
    assert result[0]["unexpanded_rows"] == [["A", "原始文字"]]
    assert result[0]["structured_rows_unavailable"] is True
    assert result[0]["layout_warnings"] and warnings
    assert result[1]["rows"] == [["要求", "响应"]]


def test_valid_spans_keep_original_alignment():
    result = html_tables('<table><tr><td rowspan="2">A</td><td>B</td></tr><tr><td>C</td></tr></table>')
    assert result[0]["rows"] == [["A", "B"], ["A", "C"]]
