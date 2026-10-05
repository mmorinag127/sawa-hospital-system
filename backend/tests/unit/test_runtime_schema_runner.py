from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = ROOT.parent / "scripts" / "run_c0_runtime_schema_isolated.py"


@pytest.fixture(scope="module")
def runner_module():
    spec = importlib.util.spec_from_file_location("runtime_schema_runner", RUNNER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pytest_target_allows_existing_source_backend_test_node(tmp_path, runner_module) -> None:
    source_root = tmp_path / "source"
    test_file = source_root / "backend/tests/unit/test_allowed.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("def test_allowed(): pass\n", encoding="utf-8")

    resolved = runner_module._resolve_pytest_target(
        source_root,
        "backend/tests/unit/test_allowed.py::test_allowed",
    )

    assert resolved == f"{test_file.resolve()}::test_allowed"


@pytest.mark.parametrize("target", ["../outside.py", "backend/src/main.py", "backend/tests", "backend/tests/unit/missing.py"])
def test_pytest_target_rejects_non_test_or_missing_path(tmp_path, runner_module, target) -> None:
    source_root = tmp_path / "source"
    (source_root / "backend/tests/unit").mkdir(parents=True)
    (source_root / "backend/src").mkdir(parents=True)
    (source_root / "backend/src/main.py").write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="pytest_target_outside_source_backend_tests"):
        runner_module._resolve_pytest_target(source_root, target)
