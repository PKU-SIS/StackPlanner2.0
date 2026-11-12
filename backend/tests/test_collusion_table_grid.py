"""MinerU rowspans must not shift requirement/response columns."""

from bs4 import BeautifulSoup

from app.experiments.collusion_audit.table_grid import table_grid


def test_expands_rowspans_before_selecting_response_columns():
    table = BeautifulSoup(
        "<table><tr><th>序号</th><th>标的</th><th>招标要求</th><th>实际响应</th></tr><tr><td>1</td><td rowspan='2'>产品</td><td>req1</td><td>resp1</td></tr><tr><td>2</td><td>req2</td><td>resp2</td></tr></table>", "html.parser"
    ).table
    rows = table_grid(table)
    assert rows[2] == ["2", "产品", "req2", "resp2"]


def test_colspan_preserves_following_column_positions():
    table = BeautifulSoup("<table><tr><td colspan='2'>section</td><td>response</td></tr></table>", "html.parser").table
    assert table_grid(table) == [["section", "section", "response"]]
