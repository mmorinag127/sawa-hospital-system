"""Compare packaged API signatures/schema declarations with an explicit Git ref.

This is a source contract check, not proof of runtime/data migration parity.
No checkout, fetch, database access, or model calls are performed.
"""
import argparse
import ast
import json
from pathlib import Path
import subprocess


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args])


def declarations(raw):
    tree = ast.parse(raw)
    result = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id == 'APIRouter':
            result['router:' + ast.unparse(node.targets[0])] = ast.dump(node.value, include_attributes=False)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == 'include_router':
            result['mount:' + ast.unparse(node)] = ast.dump(node, include_attributes=False)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            routes = [d for d in node.decorator_list if isinstance(d, ast.Call) and
                      isinstance(d.func, ast.Attribute) and d.func.attr in ('get','post','put','patch','delete')]
            if routes:
                result['route:' + node.name] = {
                    'decorators': [ast.dump(d, include_attributes=False) for d in routes],
                    'arguments': ast.dump(node.args, include_attributes=False),
                    'returns': ast.dump(node.returns, include_attributes=False) if node.returns else None}
        if isinstance(node, ast.ClassDef):
            fields = [s for s in node.body if isinstance(s, (ast.AnnAssign, ast.Assign))]
            result['schema:' + node.name] = [ast.dump(s, include_attributes=False) for s in fields]
    return result


def compare(bundle, repo, revision):
    sha = git(repo, 'rev-parse', revision).decode().strip()
    source_paths = set(git(repo, 'ls-tree', '-r', '--name-only', sha, 'backend').decode().splitlines())
    paths = sorted(source_paths | {str(p.relative_to(bundle)) for p in (bundle/'backend').rglob('*.py')})
    changed = []
    missing = []
    compared = 0
    routes = 0
    schemas = 0
    for relative in paths:
        if not relative.endswith('.py') or any(part in relative.split('/') for part in ('tests','migrations','__pycache__')):
            continue
        current = git(repo, 'show', sha + ':' + relative) if relative in source_paths else b''
        path = bundle / relative
        expected = declarations(current)
        compared += 1
        routes += sum(name.startswith('route:') for name in expected)
        schemas += sum(name.startswith('schema:') for name in expected)
        if not path.is_file():
            if expected: missing.append(relative)
            continue
        packaged = declarations(path.read_bytes())
        for name in sorted(set(expected) | set(packaged)):
            if expected.get(name) != packaged.get(name):
                changed.append({'file': relative, 'declaration': name,
                                'packaged': packaged.get(name), 'current': expected.get(name)})
    manifest = bundle.parents[1] / 'manifests' / (bundle.name + '.json')
    return {'revision': sha, 'packaged_revision': json.loads(manifest.read_text())['source_revision'],
            'compared_files': compared, 'current_route_handlers': routes, 'current_classes': schemas,
            'missing_contract_files': missing, 'changed_declarations': changed,
            'source_contract_equal': not missing and not changed,
            'limits': 'AST route signatures and schema declarations only; no runtime/migration parity claim'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = compare(args.bundle, args.repo, args.revision)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'changed_declarations'}, ensure_ascii=False))
    print('changed_declarations=' + str(len(report['changed_declarations'])))


if __name__ == '__main__':
    main()
