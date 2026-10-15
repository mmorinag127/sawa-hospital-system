"""Fresh startup without site initialization, editable installs, or network."""
import os
from pathlib import Path
import subprocess
import sys
import sysconfig

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/run_stg_runtime_schema_preflight.py"
CHILD = r"""
import importlib.util
from pathlib import Path
import runpy
import sys

# -I -S disables PYTHONPATH, site, .pth files and editable import finders.
# Add only the dependency directories themselves, without site.addsitedir().
script, mode, *dependencies = sys.argv[1:]
sys.path.extend(dependencies)
assert importlib.util.find_spec('src') is None, 'src unexpectedly installed'
assert not any('editable' in str(finder).lower() for finder in sys.meta_path)
def prohibit_external(event, args):
    if event.startswith('socket.') or event in ('subprocess.Popen', 'os.system', 'os.posix_spawn'):
        raise AssertionError('external operation forbidden: ' + event)
sys.addaudithook(prohibit_external)
if mode == 'import':
    module = runpy.run_path(script, run_name='isolated_preflight_import')
    assert 'src.db' not in sys.modules
    assert Path(sys.modules['src'].__file__).resolve().parent == Path(script).resolve().parents[1] / 'backend/src'
    raise SystemExit(module['main']())
runpy.run_path(script, run_name='__main__')
"""


@pytest.mark.parametrize("location", ["repo", "unrelated"])
@pytest.mark.parametrize("mode", ["import", "start"])
def test_fresh_entrypoint_reaches_staging_gate_without_environment(tmp_path, location, mode):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("PYTHON", "DB_", "GITHUB_"))
           and key not in {"DATABASE_URL", "ALEMBIC_DB_URI"}}
    dependencies = list(dict.fromkeys(sysconfig.get_path(key) for key in ("purelib", "platlib")))
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", CHILD, str(SCRIPT), mode, *dependencies],
        cwd=ROOT if location == "repo" else tmp_path,
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stdout == ""
    assert result.stderr.strip() == "0027 blocked: staging Actions requires GITHUB_ACTIONS=true"
