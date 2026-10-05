#!/usr/bin/env python3
"""Run runtime-schema tests in the same offline isolation as the C0 runner."""

from __future__ import annotations

import argparse
import hashlib
import os
import runpy
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts" / "run_c0_mock_contracts_isolated.py"
TESTS = [
    "backend/tests/unit/test_runtime_schema_mutation_guard.py",
    "backend/tests/integration/test_menu_scope_normalization.py::test_base_menu_service_normalizes_diet_type_on_save_and_serialize",
    "backend/tests/unit/test_facility_template_version_lineage.py",
    "backend/tests/unit/test_portal_access_bootstrap_service.py",
]


def _resolve_pytest_target(source_root: Path, raw_target: str) -> str:
    path_text, separator, node_id = raw_target.partition("::")
    candidate = Path(path_text)
    source_tests = (source_root / "backend" / "tests").resolve()
    resolved = (candidate if candidate.is_absolute() else source_root / candidate).resolve()
    if not resolved.is_relative_to(source_tests) or resolved.suffix != ".py" or not resolved.is_file():
        raise ValueError(f"pytest_target_outside_source_backend_tests:{raw_target}")
    return f"{resolved}::{node_id}" if separator else str(resolved)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_name")
    parser.add_argument("--postgres-uri")
    parser.add_argument("--postgres-socket-dir")
    parser.add_argument("--source-root", default=str(ROOT))
    parser.add_argument("--pytest-target", action="append", default=[])
    args = parser.parse_args()
    source_root = Path(args.source_root).resolve()
    source_backend = source_root / "backend"
    if not source_backend.is_dir():
        raise SystemExit(f"source_backend_missing:{source_backend}")
    runner = runpy.run_path(str(RUNNER))
    run_dir = ROOT / "tmp" / "c0-runtime-schema" / args.run_name
    if run_dir.exists():
        raise SystemExit(f"run_directory_exists:{run_dir}")
    run_dir.mkdir(parents=True)
    runner["_configure_environment"](run_dir)
    os.environ["PYTHONPATH"] = str(source_backend)
    if args.postgres_uri:
        from sqlalchemy.engine import make_url

        url = make_url(args.postgres_uri)
        if url.get_backend_name() != "postgresql":
            raise SystemExit("postgres_uri_must_use_postgresql")
        expected_socket = Path(args.postgres_socket_dir or "").resolve()
        actual_socket = Path(str(url.query.get("host") or "")).resolve()
        if not expected_socket.is_dir() or actual_socket != expected_socket:
            raise SystemExit("postgres_socket_directory_mismatch")
        os.environ["TEST_BOOTSTRAP_DB_URI"] = args.postgres_uri
    sys.path.insert(0, str(source_backend))
    sys.addaudithook(runner["_reject_external_events"])
    import src
    import src.main

    loaded_main = Path(src.main.__file__ or "").resolve()
    if source_root not in loaded_main.parents:
        raise RuntimeError(f"unexpected_import_source:{loaded_main}")
    (run_dir / "import-proof.log").write_text(
        "\n".join(
            (
                f"python: {Path(sys.executable).resolve()}",
                f"output_root: {ROOT}",
                f"source_root: {source_root}",
                f"main_source: {loaded_main}",
                f"main_sha256: {hashlib.sha256(loaded_main.read_bytes()).hexdigest()}",
            )
        ) + "\n",
        encoding="utf-8",
    )
    import pytest

    targets = [
        _resolve_pytest_target(source_root, target)
        for target in (args.pytest_target or TESTS)
    ]
    if args.postgres_uri:
        targets.append(str(source_root / "backend/tests/contract/test_automation_bootstrap_postgres.py"))
    return pytest.main([*targets, f"--junitxml={run_dir / 'results.xml'}", "-o", "faulthandler_timeout=120"])


if __name__ == "__main__":
    raise SystemExit(main())
