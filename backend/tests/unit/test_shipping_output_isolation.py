import ast
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


def test_original_source_unchanged_except_directory_definition():
    before = subprocess.check_output(["git", "show", f"{BASE}:{SOURCE}"], cwd=ROOT, text=True)
    after = (ROOT / SOURCE).read_text()
    original = 'OUTPUT_DIR = Path("/tmp/shipping-outputs")'
    replacement = 'OUTPUT_DIR = Path(os.getenv("SHIPPING_OUTPUT_DIR", "/tmp/shipping-outputs"))'
    assert before.count(original) == 1
    assert after == before.replace(original, replacement)


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
    paths = [eval(compile(ast.Expression(expression), SOURCE, "eval"),
                  {"OUTPUT_DIR": tmp_path, "datetime": RecordedDatetime}) for expression in expressions]
    assert paths == [tmp_path / "shipping_20261008_123456.xlsx",
                     tmp_path / "shipping_enriched_20261008_123456.xlsx"]
    assert not any(path.exists() for path in paths)
