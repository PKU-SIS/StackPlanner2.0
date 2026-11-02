"""Full-flow preparation must not call models before the billing start gate."""

import importlib.util
import sys
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

scripts = Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(scripts))
spec = importlib.util.spec_from_file_location("full_eval", scripts / "evaluate_report_full.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
sys.path.pop(0)


def test_worker_refuses_to_initialize_before_off_peak(tmp_path, monkeypatch):
    monkeypatch.setattr(module.time, "time", lambda: 100)
    with pytest.raises(RuntimeError, match="Off-peak gate"):
        module.worker(tmp_path, 200, 100)
    assert not (tmp_path / "runtime").exists()


def test_trace_omits_credentials_and_provider_private_reasoning():
    message = AIMessage(content="Public answer", additional_kwargs={"reasoning_content": "private"})
    result = module.safe({"message": message, "api_key": "secret", "nested": {"reasoning": "private", "value": "kept"}})
    assert result["message"]["content"] == "Public answer"
    assert "api_key" not in result and result["nested"] == {"value": "kept"}
    assert "private" not in str(result)
