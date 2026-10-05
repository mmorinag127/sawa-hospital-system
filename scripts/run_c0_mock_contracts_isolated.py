#!/usr/bin/env python3
"""Run the C0 mock-contract nodes in a worktree-local offline environment."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
EXACT_NODES = [
    "backend/tests/contract/test_orders_ocr_status_api.py::test_download_document_falls_back_to_archived_ocr_input_when_canonical_uri_is_missing",
    "backend/tests/contract/test_orders_ocr_status_api.py::test_download_document_returns_404_when_source_and_ocr_artifacts_missing",
    "backend/tests/integration/test_ocr_pipeline.py::test_reparse_order_llm_prompt_includes_previous_saved_candidate_rows",
    "backend/tests/integration/test_ocr_pipeline.py::test_reparse_order_large_structural_projection_requires_manual_review",
    "backend/tests/integration/test_ocr_sheet_history.py::test_get_ocr_sheet_preserves_authoritative_current_sheet_gate_when_blocked",
    "backend/tests/integration/test_uploaded_pdf_recovery_flow.py::test_process_ingest_inline_prefers_payload_ocr_job_id",
]
SIBLING_NODES = [
    "backend/tests/integration/test_ocr_pipeline.py::test_reparse_order_projects_quantity_only_rows_onto_structural_baseline",
    "backend/tests/integration/test_ocr_pipeline.py::test_reparse_order_blocks_llm_reparse_without_first_pass_context",
]


def _reject_external_events(event: str, _args: tuple[object, ...]) -> None:
    if event in {"socket.connect", "socket.bind", "socket.sendto", "socket.getaddrinfo"}:
        raise RuntimeError(f"offline_runtime_blocked:{event}")
    if event.startswith("subprocess."):
        raise RuntimeError(f"offline_runtime_blocked:{event}")


def _configure_environment(run_dir: Path) -> None:
    home = run_dir / "home"
    tmp = run_dir / "tmp"
    cache = run_dir / "cache"
    artifacts = run_dir / "artifacts"
    for path in (home, tmp, cache, artifacts):
        path.mkdir(parents=True, exist_ok=False)
    db_path = run_dir / "test.sqlite"
    os.environ.clear()
    os.environ.update(
        {
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": str(BACKEND),
            "PYTHONDONTWRITEBYTECODE": "1",
            "DB_URI": f"sqlite:///{db_path}",
            "AUTH_DISABLED": "true",
            "HOME": str(home),
            "TMPDIR": str(tmp),
            "XDG_CACHE_HOME": str(cache),
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "GOOGLE_APPLICATION_CREDENTIALS": str(run_dir / "missing-google-credentials.json"),
            "AWS_EC2_METADATA_DISABLED": "true",
            "GCE_METADATA_HOST": "127.0.0.1:9",
            "HTTP_PROXY": "http://127.0.0.1:9",
            "HTTPS_PROXY": "http://127.0.0.1:9",
            "NO_PROXY": "",
            "SAWA_ARTIFACT_DIR": str(artifacts),
        }
    )


def _write_import_proof(run_dir: Path) -> None:
    import src
    import src.db
    import src.services.ocr_job_service
    import src.services.order_service
    import src.services.position_column_mapping_service
    import src.workers.ingest_worker

    source_files = [
        Path(src.__file__ or "").resolve(),
        Path(src.db.__file__ or "").resolve(),
        Path(src.services.ocr_job_service.__file__ or "").resolve(),
        Path(src.services.order_service.__file__ or "").resolve(),
        Path(src.services.position_column_mapping_service.__file__ or "").resolve(),
        Path(src.workers.ingest_worker.__file__ or "").resolve(),
    ]
    if any(ROOT not in path.parents for path in source_files):
        raise RuntimeError(f"unexpected_import_source:{source_files}")
    proof = [
        f"python: {Path(sys.executable).resolve()}",
        f"root: {ROOT}",
        f"dont_write_bytecode: {sys.dont_write_bytecode}",
        f"database: {src.db.DB_URI}",
        *[f"source: {path}" for path in source_files],
    ]
    (run_dir / "import-proof.log").write_text("\n".join(proof) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_name")
    parser.add_argument("scope", choices=("exact", "siblings", "affected-files"))
    args = parser.parse_args()
    run_dir = ROOT / "tmp" / "c0-direct-mocks" / args.run_name
    if run_dir.exists():
        raise SystemExit(f"run_directory_exists:{run_dir}")
    run_dir.mkdir(parents=True)
    _configure_environment(run_dir)
    sys.path.insert(0, str(BACKEND))
    sys.addaudithook(_reject_external_events)
    _write_import_proof(run_dir)
    import pytest

    targets = {
        "exact": EXACT_NODES,
        "siblings": SIBLING_NODES,
        "affected-files": [
            "backend/tests/contract/test_orders_ocr_status_api.py",
            "backend/tests/integration/test_ocr_pipeline.py",
            "backend/tests/integration/test_ocr_sheet_history.py",
            "backend/tests/integration/test_uploaded_pdf_recovery_flow.py",
        ],
    }[args.scope]
    return pytest.main([*targets, f"--junitxml={run_dir / 'results.xml'}", "-o", "faulthandler_timeout=120"])


if __name__ == "__main__":
    raise SystemExit(main())
