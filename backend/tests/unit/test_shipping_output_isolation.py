import ast
from copy import deepcopy
from pathlib import Path
import subprocess
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[3]
SOURCE = "backend/src/services/shipping_service.py"
BASE = "a91b4c745e6d99ffd33ac9d1a3b97ac81435507c"


def output_assignment():
    tree = ast.parse((ROOT / SOURCE).read_text())
    return next(node for node in tree.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "OUTPUT_DIR" for target in node.targets))


def scoped_output(function):
    result = deepcopy(function)
    for node in ast.walk(result):
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
                and target.id == "output_path" for target in node.targets):
            assert ast.dump(node.value.left) == ast.dump(ast.Name(id="OUTPUT_DIR", ctx=ast.Load()))
            node.value.left = ast.Call(func=ast.Attribute(value=ast.Name(id="datasets", ctx=ast.Load()),
                attr="local_directory", ctx=ast.Load()), args=[node.value.left], keywords=[])
    return result


def test_default_output_directory_without_import_or_shared_write():
    calls = []

    def getenv(key, default):
        calls.append((key, default))
        return default

    value = eval(compile(ast.Expression(output_assignment().value), SOURCE, "eval"),
                 {"Path": Path, "os": SimpleNamespace(getenv=getenv)})
    assert value == Path("/tmp/shipping-outputs")
    assert calls == [("SHIPPING_OUTPUT_DIR", "/tmp/shipping-outputs")]


def test_explicit_owned_output_directory_without_service_import(tmp_path):
    owned = tmp_path / "shipping"

    def getenv(key, default):
        assert key == "SHIPPING_OUTPUT_DIR"
        assert default == "/tmp/shipping-outputs"
        return str(owned)

    value = eval(compile(ast.Expression(output_assignment().value), SOURCE, "eval"),
                 {"Path": Path, "os": SimpleNamespace(getenv=getenv)})
    assert value == owned
    assert not owned.exists()


def test_shipping_business_rows_remain_unchanged_when_template_source_is_snapshot_aware():
    before = ast.parse(subprocess.check_output(["git", "show", f"{BASE}:{SOURCE}"], cwd=ROOT, text=True))
    after = ast.parse((ROOT / SOURCE).read_text())
    original = {node.name: node for node in before.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    current = {node.name: node for node in after.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
    assert original.keys() == current.keys()
    for name in original:
        if name != "build_shipping_excel":
            expected = scoped_output(original[name])
            if name == "enrich_tracking_excel":
                expected.body.insert(0, ast.ImportFrom(module="openpyxl",
                    names=[ast.alias(name="load_workbook")], level=0))
            assert ast.dump(expected) == ast.dump(current[name]), name
    def business_tail(function):
        index = next(i for i, node in enumerate(function.body) if isinstance(node, ast.Assign)
                     and any(isinstance(target, ast.Name) and target.id == "workbook" for target in node.targets))
        return [ast.dump(node) for node in function.body[index + 1:]]
    assert business_tail(scoped_output(original["build_shipping_excel"])) == business_tail(current["build_shipping_excel"])


def test_generated_artifact_filename_expressions_keep_original_names(tmp_path):
    from datetime import datetime

    class RecordedDatetime:
        @staticmethod
        def now():
            return datetime(2026, 10, 8, 12, 34, 56)

    tree = ast.parse((ROOT / SOURCE).read_text())
    expressions = [node.value for node in ast.walk(tree) if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "output_path" for target in node.targets)]
    assert len(expressions) == 2
    directories = []
    def local_directory(path):
        directories.append(path)
        return path
    paths = [eval(compile(ast.Expression(expression), SOURCE, "eval"),
                  {"OUTPUT_DIR": tmp_path, "datetime": RecordedDatetime,
                   "datasets": SimpleNamespace(local_directory=local_directory)}) for expression in expressions]
    assert paths == [tmp_path / "shipping_20261008_123456.xlsx",
                     tmp_path / "shipping_enriched_20261008_123456.xlsx"]
    assert not any(path.exists() for path in paths)
    assert directories == [tmp_path, tmp_path]
