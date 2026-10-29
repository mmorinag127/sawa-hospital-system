"""Run the explicit staging contracts or the preserved full frontend suite."""
import json
import hashlib
import os
from pathlib import Path
import subprocess
import fcntl
import shlex

from local_hospital_test_runner import ROOT, clean_tree, focused_staging, frontend_scope, git, reject_hosted_runner


def requires_full_environment(environment):
    try:
        options = shlex.split(environment.get('NODE_OPTIONS', ''))
    except ValueError:
        return True
    # These options remain part of the dependency tracker's environment hash.
    return bool(environment.get('NODE_PATH')) or bool(set(options) - {
        '--use-openssl-ca', '--max-old-space-size=2048',
    })


def main():
    reject_hosted_runner()
    if not clean_tree():
        raise ValueError('Frontend contracts require a fixed clean source')
    source = git('rev-parse', 'HEAD')
    modules = frontend_scope('config')
    evidence = Path(os.environ['SAWA_LOCAL_EVIDENCE_DIR']) / 'frontend-contracts'
    evidence.mkdir(exist_ok=False)
    focused = focused_staging()
    environment = dict(os.environ)
    state = Path(git('rev-parse', '--git-common-dir'))
    if not state.is_absolute():
        state = ROOT / state
    state = state.resolve() / 'sawa-local-impact' / 'frontend-focused'
    state.parent.mkdir(parents=True, exist_ok=True)
    if focused:
        state.mkdir(parents=True, exist_ok=True)
        if state.is_symlink():
            raise ValueError('Frontend dependency state must not be a symlink')
        environment.update(SAWA_IMPACT_ROOT=str(ROOT), SAWA_NODE_IMPACT_STATE=str(state),
                           SAWA_IMPACT_FULL='1' if requires_full_environment(environment) else '0')
    else:
        environment['SAWA_IMPACT_FULL'] = '1'
        environment.pop('SAWA_IMPACT_ROOT', None)
        environment.pop('SAWA_NODE_IMPACT_STATE', None)
    command = (['node', *(['--import', str(ROOT / 'infra/ci/node-impact/register.mjs')] if focused else []),
                '--test', '--test-concurrency=1', *modules] if modules
               else ['npm', 'run', 'test:config'])
    (evidence / 'scope.json').write_text(json.dumps({
        'source_sha': source, 'modules': modules, 'command': command,
        'outside_scope': 'Other frontend contracts not run or claimed as passed/reused' if modules else None,
    }, indent=2))
    with (state.parent / 'frontend-focused.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if focused:
            for path in state.glob('*.report.json'):
                if path.is_symlink():
                    raise ValueError('Frontend report must not be a symlink')
                path.unlink()
        result = subprocess.run(command, cwd=ROOT / 'frontend', env=environment)
        if focused:
            reports = []
            for module in modules:
                key = hashlib.sha256(('frontend/' + module).encode()).hexdigest()
                path = state / (key + '.json.report.json')
                if not path.is_file() or path.is_symlink():
                    raise ValueError('Frontend module did not publish its selection report: ' + module)
                record = json.loads(path.read_text())
                if (not isinstance(record.get('selected'), list) or not isinstance(record.get('reused'), list)
                        or set(record['selected']) & set(record['reused'])):
                    raise ValueError('Invalid frontend selection report')
                reports.append({'module': module, 'report': record})
            selected = [node for item in reports for node in item['report']['selected']]
            reused = [node for item in reports for node in item['report']['reused']]
            selection = evidence / 'selection.json'
            selection.write_text(json.dumps({
                'source_sha': source, 'mode': 'focused', 'exit_code': result.returncode,
                'selected': selected, 'reused': reused, 'modules': reports,
                'outside_scope': 'Unselected frontend modules are not claimed as passed',
            }, indent=2) + '\n')
            print(json.dumps({'source_sha': source, 'frontend_selected': len(selected),
                              'frontend_reused': len(reused), 'exit_code': result.returncode,
                              'selection_sha256': hashlib.sha256(selection.read_bytes()).hexdigest()}), flush=True)
    if not clean_tree() or git('rev-parse', 'HEAD') != source:
        raise ValueError('Frontend contracts changed the fixed source')
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
