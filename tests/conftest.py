"""Expose the source checkout's native SP package to benchmark tests."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend/packages/harness"))


def pytest_addoption(parser):
    parser.addoption(
        "--no-sp-runtime",
        action="store_true",
        help="Skip graph integration tests and forbid real SP graph construction",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "sp_runtime: constructs and runs the native SP graph using scripted models",
    )


@pytest.fixture(autouse=True)
def forbid_sp_runtime_when_requested(request, monkeypatch):
    if request.config.getoption("--no-sp-runtime"):
        if request.node.get_closest_marker("sp_runtime"):
            pytest.skip("Native SP graph execution excluded by --no-sp-runtime")
        import deerflow.sp.runtime as native

        def forbidden(*args, **kwargs):
            pytest.fail("Native SP runtime must be stubbed in --no-sp-runtime tests")

        monkeypatch.setattr(native, "make_sp_agent", forbidden)
