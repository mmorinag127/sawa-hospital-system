"""Reviewed staging behavior groups, independent of a warm coverage database."""
import json
from pathlib import Path


def select_modules(root, changed, retained=()):
    definition = root / 'infra/ci/staging_backend_scope.json'
    config = json.loads(definition.read_text())
    if config.get('version') != 2:
        raise ValueError('Unsupported staging backend scope')
    groups, sources = config['groups'], config['sources']
    source_targets = config.get('targets_by_source', {})
    if (not isinstance(source_targets, dict) or not source_targets.keys() <= sources.keys()
            or any(sources[path] == ['*'] or not isinstance(targets, list) or not targets
                   or any(not isinstance(target, str) for target in targets)
                   for path, targets in source_targets.items())):
        raise ValueError('Invalid reviewed source-specific targets')
    names = set(config['always'])
    direct_targets = set()
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
                if path in source_targets:
                    direct_targets.update(source_targets[path])
                else:
                    names.update(groups if sources[path] == ['*'] else sources[path])
            elif not path.startswith('backend/docs/'):
                unknown.append(path)
    if unknown:
        raise ValueError('Map backend changes to behavior tests before running: ' + ', '.join(sorted(unknown)))
    if not names <= groups.keys():
        raise ValueError('Unknown staging behavior group')
    targets = {module for name in names for module in groups[name]} | changed_tests | direct_targets | set(retained)
    if any(not isinstance(target, str) for target in targets):
        raise ValueError('Invalid staging behavior test target')
    whole_modules = {target for target in targets if isinstance(target, str) and '::' not in target}
    modules = sorted(target for target in targets if not isinstance(target, str)
                     or '::' not in target or target.split('::', 1)[0] not in whole_modules)
    if not modules or any(not isinstance(module, str) or not module.startswith('backend/tests/')
                          or '..' in Path(module.split('::', 1)[0]).parts
                          or not module.split('::', 1)[0].endswith('.py')
                          or any(not part or '\x00' in part or '\n' in part for part in module.split('::')[1:])
                          or not (root / module.split('::', 1)[0]).is_file() for module in modules):
        raise ValueError('Missing or invalid staging behavior test module')
    return {'kind': 'reviewed-staging-behaviors', 'groups': sorted(names), 'modules': modules,
            'changed_tests': sorted(changed_tests), 'definition': str(definition.relative_to(root)),
            'direct_targets': sorted(direct_targets),
            'outside_scope': 'Extended tests are not executed, reused, or claimed as passed; use full explicitly.'}
