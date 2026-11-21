"""Offline counts and trusted-operator Word calibration records stay distinct."""

import json
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/report_word_count.py"


def _run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True, timeout=30)


def test_markdown_count_is_explicitly_estimated_and_does_not_modify_source(tmp_path):
    path = tmp_path / "report.md"
    path.write_text("# 中文\n\nEnglish words")
    before = path.read_bytes()
    result = _run(path)
    assert result.returncode == 0, result.stderr
    row = json.loads(result.stdout)["reports"][0]
    assert row["estimated_words"] == 4 and row["measured_word_count"] is None
    assert path.read_bytes() == before


def test_calibration_requires_matching_document_hash_and_numeric_count(tmp_path):
    path = tmp_path / "report.txt"
    path.write_text("中文 English")
    record = tmp_path / "word.json"
    record.write_text(json.dumps([{"file_sha256": "0" * 64, "measured_word_count": 3, "measurement_kind": "microsoft_word_compute_statistics", "word_version": "test"}]))
    result = _run(path, "--word-measurements", record)
    assert result.returncode != 0 and "hash" in result.stderr.lower()


def test_docx_text_uses_cell_and_paragraph_boundaries_and_can_be_calibrated(tmp_path):
    import hashlib

    path = tmp_path / "report.docx"
    with ZipFile(path, "w") as z:
        z.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>中文</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>English</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>words</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>',
        )
    record = tmp_path / "word.json"
    record.write_text(json.dumps([{"file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "measured_word_count": 5, "measurement_kind": "microsoft_word_compute_statistics", "word_version": "test"}]))
    result = _run(path, "--word-measurements", record)
    assert result.returncode == 0, result.stderr
    row = json.loads(result.stdout)["reports"][0]
    assert row["estimated_words"] == 4 and row["measured_word_count"] == 5
    assert row["estimate_error"] == -1 and row["measurement_source"] == "operator_supplied_word_record"


@pytest.mark.parametrize("bad", [True, -1, "123"])
def test_calibration_does_not_coerce_false_word_counts(tmp_path, bad):
    import hashlib

    path = tmp_path / "report.txt"
    path.write_text("中文 English")
    record = tmp_path / "word.json"
    record.write_text(json.dumps([{"file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "measured_word_count": bad, "measurement_kind": "microsoft_word_compute_statistics", "word_version": "test"}]))
    result = _run(path, "--word-measurements", record)
    assert result.returncode != 0
