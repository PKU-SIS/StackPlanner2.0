"""Report content identity must preserve original UTF-8 line endings."""

import pytest

from deerflow.sp.report import assembly, store
from deerflow.sp.report.ledger import ReportValidationError


@pytest.mark.parametrize("text", ["中文\r\nsecond\nlast\r", "a\r\nb\r\n", "a\nb\n"])
def test_bound_text_preserves_line_endings_and_detects_tampering(tmp_path, text):
    path = tmp_path / "material.txt"
    path.write_bytes(text.encode())
    record = {"path": str(path), "sha256": store.sha256_text(text)}
    assert assembly._bound_text(record, "material") == text
    assert store.read_text(path) == text
    path.write_bytes((text + "changed").encode())
    with pytest.raises(ReportValidationError, match="hash does not match"):
        assembly._bound_text(record, "material")


def test_crlf_to_lf_change_is_still_detected_as_a_change(tmp_path):
    path = tmp_path / "material.txt"
    path.write_bytes(b"a\r\nb\r\n")
    record = {"path": str(path), "sha256": store.sha256_text("a\r\nb\r\n")}
    path.write_bytes(b"a\nb\n")
    with pytest.raises(ReportValidationError, match="hash does not match"):
        assembly._bound_text(record, "material")
