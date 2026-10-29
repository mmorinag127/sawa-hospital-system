"""Reviewed staging behavior groups, independent of a warm coverage database."""
import json
from pathlib import Path


def select_modules(root, changed, retained=()):
    definition = root / 'infra/ci/staging_backend_scope.json'
    config = json.loads(definition.read_text())
    if config.get('version') != 2:
        raise ValueError('Unsupported staging backend scope')
    groups, sources = config['groups'], config['sources']
    names = set(config['always'])
    unknown = []
    changed_tests = set()
    for path in changed:
        if path.startswith('backend/tests/') and path.endswith('.py'):
            if not (root / path).is_file():
                raise ValueError('Review removed test coverage before running: ' + path)
            if Path(path).name.startswith('test_') and (root / path).is_file():
                changed_tests.add(path)
            elif (root / path).is_file():
                # Shared test fixtures can affect every maintained behavior group.
                names.update(groups)
        elif path.startswith('backend/'):
            if path in sources:
                names.update(groups if sources[path] == ['*'] else sources[path])
            elif not path.startswith('backend/docs/'):
                unknown.append(path)
    if unknown:
        raise ValueError('Map backend changes to behavior tests before running: ' + ', '.join(sorted(unknown)))
    if not names <= groups.keys():
        raise ValueError('Unknown staging behavior group')
    modules = sorted({module for name in names for module in groups[name]} | changed_tests | set(retained))
    if not modules or any(not module.startswith('backend/tests/') or '..' in Path(module).parts
                          or not module.endswith('.py') or not (root / module).is_file() for module in modules):
        raise ValueError('Missing or invalid staging behavior test module')
    return {'kind': 'reviewed-staging-behaviors', 'groups': sorted(names), 'modules': modules,
            'changed_tests': sorted(changed_tests), 'definition': str(definition.relative_to(root)),
            'outside_scope': 'Extended tests are not executed, reused, or claimed as passed; use full explicitly.'}
