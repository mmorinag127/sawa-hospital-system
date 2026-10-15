"""Pure canonical normalization and standalone cleanup import regressions."""
import ast
import hashlib
from contextlib import nullcontext
import os
from pathlib import Path
import re
import subprocess
import sys
import unicodedata
from unittest.mock import Mock, patch

import pytest

ROOT = Path(__file__).resolve().parents[3]
BASE = "b86c44cd001fc9f053f4d710d219b54942eb5939"
# SHA256 of ast.dump (without location attributes) from BASE, not current code.
NORMALIZATION_AST_SHA256 = "4f77c50219b9e7980a069b4337e63f674b2e4e1f916554e4ab1502f2b8478097"
SAFETY_AST_SHA256 = "9ee4e30cdc1a79038b30ef56d070dc80ee0358daac9aed429e0f9bd934ab1995"
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))
from src.services import menu_name_normalization as pure
from scripts import stg_menu_master_safety as safety


def ast_hash(tree):
    return hashlib.sha256(ast.dump(tree).encode()).hexdigest()


def normalization_nodes(tree):
    return [node for node in tree.body if
            isinstance(node, ast.FunctionDef) and node.name == "_normalize_menu_name"
            or isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_MENU_TRANSLATION" for t in node.targets)]


def test_exact_prechange_ast_and_normalization_parity():
    fixture = Path(__file__).with_name("fixtures") / "menu_normalization_prechange.py"
    nodes = normalization_nodes(ast.parse(fixture.read_text()))
    current = normalization_nodes(ast.parse(Path(pure.__file__).read_text()))
    assert ast_hash(ast.Module(body=nodes, type_ignores=[])) == NORMALIZATION_AST_SHA256
    assert ast_hash(ast.Module(body=current, type_ignores=[])) == NORMALIZATION_AST_SHA256
    assert [ast.dump(n) for n in current] == [ast.dump(n) for n in nodes]
    namespace = dict(re=re, unicodedata=unicodedata)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "frozen-normalization", "exec"), namespace)
    samples = [None, "", " \t\n", "鶏・魚禁", "ＡＢＣ１２３", "ｿｰｽ／（魚）", "[米]『豆』", "‐‑‒–—―ーｰ"]
    samples.extend(chr(i) for i in range(0x10000))
    for value in samples:
        assert pure._normalize_menu_name(value) == namespace["_normalize_menu_name"](value)


def test_old_service_api_is_direct_alias():
    tree = ast.parse((ROOT / "backend/src/services/menu_service.py").read_text())
    imports = [n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "src.services.menu_name_normalization"]
    assert len(imports) == 1
    assert {(a.name, a.asname) for a in imports[0].names} == {("_MENU_TRANSLATION", None), ("_normalize_menu_name", None)}
    assert normalization_nodes(tree) == []


def test_cleanup_all_existing_guards_unchanged():
    after = ast.parse((ROOT / "scripts/stg_menu_master_safety.py").read_text())
    changed_imports = 0
    for node in after.body:
        if isinstance(node, ast.FunctionDef) and node.name == "cleanup_owned":
            for child in ast.walk(node):
                if isinstance(child, ast.ImportFrom) and child.module == "src.services.menu_name_normalization":
                    child.module = "src.services.menu_service"
                    changed_imports += 1
    assert changed_imports == 1
    assert ast_hash(after) == SAFETY_AST_SHA256


def cleanup_probe():
    assert "src.db" not in sys.modules
    current = {"id": "owned", "normalized_name": pure._normalize_menu_name("Ａ ・Ｂ")}
    connection = Mock()
    connection.execute.return_value.mappings.return_value.first.return_value = current
    connection.execution_options.return_value = connection
    connection.execute.return_value.first.return_value = None
    connection.execute.return_value.scalars.return_value = iter(["Ａ ・Ｂ"])
    engine = Mock()
    engine.begin.return_value = nullcontext(connection)
    with patch.object(safety, "last_owned_record", return_value=current), patch.object(safety, "lock_tables"), patch.object(safety, "schema_gate", return_value={}), patch.object(safety, "snapshot", side_effect=lambda x: x):
        with pytest.raises(safety.Blocked, match="^cleanup-name-reference-present$"):
            safety.cleanup_owned(engine, {}, {}, {})
        assert not any(str(c.args[0]).startswith("DELETE") for c in connection.execute.call_args_list)
        with pytest.raises(safety.Blocked, match="^cleanup-schema-or-privilege-changed$"):
            safety.cleanup_owned(engine, {}, {}, {"changed": True})
    initial = dict(version=1, name="owned", source="sha", runId="1", attempt="1", nonce="n", schemaSHA256="s", absentBefore=True)
    with pytest.raises(safety.Blocked, match="^unresolved-post-no-trustworthy-returned-id$"):
        safety.cleanup_owned(engine, initial, {**initial, "writeAttempted": True}, {})
    assert "src.db" not in sys.modules
    assert "src.services.menu_service" not in sys.modules


def test_fresh_process_cleanup_without_application_database_environment():
    env = {k: v for k, v in os.environ.items() if not any(s in k.upper() for s in ("DATABASE", "DB_", "SQL", "POSTGRES"))}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run([sys.executable, __file__, "probe"], env=env, cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


if __name__ == "__main__":
    cleanup_probe()
