"""Build recipe: exact old business source plus the reviewed schema-only bridge."""
import argparse
import ast
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BASE = '9a2b8a63c07aeeef9407ca93069812d5d6664323'
OVERLAYS = (
    'backend/alembic/catalog_contracts.json',
    'backend/migrations/0039_bounded_ocr_display_json.py',
    'backend/src/maintenance/formal_migration_schema.py',
    'backend/src/maintenance/online_recovery_schema.py',
    'backend/src/models/ocr_display_projection_v2.py',
)


def bridge_formal_source(source):
    tree = ast.parse(source)
    changed = 0
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == 'require_formal_head':
            for statement in node.body:
                if (isinstance(statement, ast.Assign) and len(statement.targets) == 1
                        and isinstance(statement.targets[0], ast.Name) and statement.targets[0].id == 'head'
                        and isinstance(statement.value, ast.Call)
                        and isinstance(statement.value.func, ast.Name)
                        and statement.value.func.id == 'formal_head_revision'):
                    statement.value = ast.Call(func=ast.Name(id='recovery_head_revision', ctx=ast.Load()),
                                               args=[ast.Name(id='connection', ctx=ast.Load())], keywords=[])
                    changed += 1
    if changed != 1:
        raise RuntimeError('unique reviewed schema gate required for recovery recipe')
    tree.body.append(ast.ImportFrom(module='src.maintenance.online_recovery_schema',
        names=[ast.alias(name='recovery_head_revision')], level=0))
    return ast.unparse(ast.fix_missing_locations(tree)) + '\n'


def prepare(destination):
    destination = Path(destination).resolve()
    if destination.exists():
        raise RuntimeError('recovery destination must be new')
    subprocess.run(['git','merge-base','--is-ancestor',BASE,'HEAD'],cwd=ROOT,check=True)
    subprocess.run(['git','diff','--quiet',BASE,'HEAD','--','agent_runtime','ocr_pipeline',
                    'backend/pyproject.toml','backend/uv.lock','backend/Dockerfile'],cwd=ROOT,check=True)
    raw = subprocess.check_output(['git','archive',BASE,'backend','ocr_pipeline'],cwd=ROOT)
    with tempfile.TemporaryDirectory() as directory:
        selected = Path(directory)
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            archive.extractall(selected, filter='data')
        for name in OVERLAYS:
            target = selected/name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((ROOT/name).read_bytes())
        formal = selected/'backend/src/maintenance/formal_migration_schema.py'
        formal.write_text(bridge_formal_source(formal.read_text()))
        env = dict(os.environ, SOURCE_BACKEND_DIR=str(selected/'backend'),
                   SOURCE_OCR_PIPELINE_DIR=str(selected/'ocr_pipeline'))
        output = subprocess.check_output([str(ROOT/'scripts/prepare_hakodate_backend_deploy_source.sh'),
            'online-recovery',str(destination)],cwd=ROOT,env=env,text=True).strip()
    source = Path(output)
    business = {}
    for path in sorted((source/'src').rglob('*')):
        if path.is_file() and not path.name.endswith('.pyc'):
            name = 'backend/'+path.relative_to(source).as_posix()
            if name not in OVERLAYS:
                original = subprocess.check_output(['git','show',BASE+':'+name],cwd=ROOT)
                if path.read_bytes() != original:
                    raise RuntimeError('recovery business source changed: '+name)
                business[name] = hashlib.sha256(original).hexdigest()
    (destination/'recovery-source.json').write_text(json.dumps({
        'business_source_sha':BASE,'bridge_source_sha':subprocess.check_output(
            ['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'schema_only_overlays':list(OVERLAYS),'unchanged_business_files':business},indent=2))
    return source


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('destination')
    print(prepare(parser.parse_args().destination))
