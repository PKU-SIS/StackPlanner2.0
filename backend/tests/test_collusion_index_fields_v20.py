"""A full-source index must support selecting punctuation/error patterns."""

import json

from app.experiments.collusion_audit.general_delivery import materialize


def test_all_actual_punctuation_types_and_patterns_are_selectable_without_guessing(tmp_path):
    rows = [{"kind": "shared_punctuation_context", "punctuation": p, "pattern": "literal-" + p, "context": "full source context " + str(i)} for i, p in enumerate([";;"] * 25 + ["。;"] * 3 + ["：："] * 2)]
    nav = materialize(tmp_path, "/current", {"summary": {"run_id": "M08_synthetic"}}, rows, [])
    index = json.loads((tmp_path / "candidates/index.json").read_text())
    assert [(r["finding_offset"], r["punctuation"], r["pattern"]) for r in index["candidates"]] == [(i, row["punctuation"], row["pattern"]) for i, row in enumerate(rows)]
    assert nav["summary_statistics"]["by_punctuation"] == {";;": 25, "。;": 3, "：：": 2}
    assert nav["summary_statistics"]["by_pattern"]["literal-：："] == 2
    assert json.loads((tmp_path / "candidates/C000030.json").read_text())["candidate"] == rows[-1]
    assert nav["summary_statistics"]["counts_are_risk_verdicts"] is False
