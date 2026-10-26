"""Run the explicit staging contracts or the preserved full frontend suite."""
import json
import os
from pathlib import Path
import subprocess

from local_hospital_test_runner import ROOT, clean_tree, frontend_scope, git, reject_hosted_runner


def main():
    reject_hosted_runner()
    if not clean_tree():
        raise ValueError('Frontend contracts require a fixed clean source')
    source = git('rev-parse', 'HEAD')
    modules = frontend_scope('config')
    evidence = Path(os.environ['SAWA_LOCAL_EVIDENCE_DIR']) / 'frontend-contracts'
    evidence.mkdir(exist_ok=False)
    command = (['node', '--test', '--test-concurrency=1', *modules] if modules
               else ['npm', 'run', 'test:config'])
    (evidence / 'scope.json').write_text(json.dumps({
        'source_sha': source, 'modules': modules, 'command': command,
        'outside_scope': 'Other frontend contracts not run or claimed as passed/reused' if modules else None,
    }, indent=2))
    result = subprocess.run(command, cwd=ROOT / 'frontend')
    if not clean_tree() or git('rev-parse', 'HEAD') != source:
        raise ValueError('Frontend contracts changed the fixed source')
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
