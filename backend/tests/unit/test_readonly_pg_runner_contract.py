"""Runner definition contracts; these tests do not start PostgreSQL."""
import importlib.util
from pathlib import Path
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location('readonly_pg_runner', ROOT / 'scripts/run_readonly_pg_tests.py')
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


@pytest.mark.parametrize('run_id,attempt', [('0', '1'), ('1', '../bad'), (' 1', '1'), ('1', ''), ('a', '2')])
def test_owned_root_rejects_non_ci_identity(run_id, attempt):
    with pytest.raises(ValueError, match='explicit numeric CI identity required'):
        runner.owned_root(run_id, attempt)


def test_owned_root_is_short_physical_tmp_without_alias():
    root = runner.owned_root('123456789', '2')
    assert root == Path('/tmp').resolve() / 'sawa-ro-123456789-2'
    assert len(str(root / 'pg/socket/.s.PGSQL.55471').encode()) < 104


def test_existing_module_runs_unfiltered_with_owned_artifacts():
    root = Path('/tmp/sawa-ro-123-1')
    assert runner.pytest_command(root) == [
        sys.executable, '-m', 'pytest', '-p', 'no:cacheprovider',
        'backend/tests/integration/test_menu_readonly_boundary.py',
        '--basetemp', str(root / 'tests'), '--junitxml', str(root / 'evidence/results.xml'), '-v',
    ]
    assert runner.PG_BIN == Path('/usr/lib/postgresql/16/bin')


def test_workflow_only_owner_feature_push_or_manual_without_cloud_permissions():
    workflow = yaml.safe_load((ROOT / 'local-checks/legacy/modernization-readonly-pg.yml').read_text())
    triggers = workflow.get('on', workflow.get(True))
    assert triggers == {'push': {'branches': ['codex/modernization-readonly-pg-ci-20261012']}, 'workflow_dispatch': None}
    assert workflow['permissions'] == {'contents': 'read'}
    assert set(workflow['jobs']) == {'readonly-boundary'}
    job = workflow['jobs']['readonly-boundary']
    assert job['if'] == (
        "github.actor == github.repository_owner && "
        "(github.event_name == 'workflow_dispatch' ||\n "
        "(github.event_name == 'push' && github.ref == 'refs/heads/codex/modernization-readonly-pg-ci-20261012' && github.event.repository.full_name == github.repository))"
    )
    assert 'environment' not in job and 'services' not in job
    steps = [s for s in job['steps'] if s.get('name') not in ('Update TLS trust stores', 'Update Python trust bundles')]
    assert steps[0] == {'uses': 'actions/checkout@v6', 'with': {'persist-credentials': False}}
    assert steps[1] == {'uses': 'actions/setup-python@v6', 'with': {'python-version': '3.12'}}
    assert steps[3]['run'] == 'python -m pip install --extra-index-url https://download.pytorch.org/whl/cpu -r backend/requirements.txt pytest httpx2'
    assert 'set -euo pipefail' in steps[4]['run']
    assert 'python scripts/run_readonly_pg_tests.py' in steps[4]['run']
    assert steps[-1]['if'] == 'always()'
    assert steps[-1]['uses'] == 'actions/upload-artifact@v4'
    assert steps[-1]['with']['include-hidden-files'] is True
    paths = steps[-1]['with']['path'].splitlines()
    assert all('/evidence/' in path or '/readonly-evidence/' in path for path in paths)
    assert not any('pgdata' in path or 'bootstrap' in path for path in paths)
