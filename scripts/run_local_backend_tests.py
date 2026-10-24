"""Run general cases once; owned PostgreSQL modules have dedicated runners."""
import json
import os
from pathlib import Path
import subprocess
import sys

from local_hospital_test_runner import ROOT, clean_tree, git, reject_hosted_runner
from run_readonly_pg_tests import MODULES


DEDICATED_MODULES = {
    **{name: MODULES[suite] for suite, name in {
        "readonly": "hospital-readonly-pg", "c1": "hospital-c1-pg",
        "c1-guards": "hospital-c1-guard-pg", "menu-identity": "hospital-menu-identity-pg",
    }.items()},
    "modernization-pg-output": ["backend/tests/integration/pg_output_boundary_cases.py",
                                "backend/tests/integration/test_formal_alembic_fresh_chain.py"],
    "live-schema-adoption": ["backend/tests/integration/test_live_schema_adoption.py",
                             "backend/tests/unit/test_prepare_stg_saved_import.py",
                             "backend/tests/integration/test_stg_saved_import_transport.py"],
}


def main():
    reject_hosted_runner()
    if not clean_tree():
        raise ValueError("Backend tests require a fixed clean source")
    source = git("rev-parse", "HEAD")
    evidence = Path(os.environ["SAWA_LOCAL_EVIDENCE_DIR"]) / "backend-tests"
    evidence.mkdir(exist_ok=False)
    excluded = sorted({name for modules in DEDICATED_MODULES.values() for name in modules})
    if any(not (ROOT / name).is_file() for name in excluded):
        raise ValueError("Dedicated PostgreSQL test module is missing")
    argv = [sys.executable, "-m", "pytest", "backend/tests",
            *["--ignore=" + name for name in excluded], *sys.argv[1:]]
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("DB_") and key not in ("DATABASE_URL", "ALEMBIC_DB_URI")}
    env["DB_URI"] = "sqlite:///" + str(evidence / "runtime.sqlite")
    env["PYTHONPATH"] = str(ROOT / "backend") + os.pathsep + str(ROOT / "backend/tests/unit")
    (evidence / "partition.json").write_text(json.dumps({
        "source_sha": source, "dedicated_modules": DEDICATED_MODULES,
        "general_argv": argv, "database_uri": env["DB_URI"],
    }, indent=2))
    result = subprocess.run(argv, cwd=ROOT, env=env)
    if not clean_tree() or git("rev-parse", "HEAD") != source:
        raise ValueError("Backend tests changed their fixed source")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
