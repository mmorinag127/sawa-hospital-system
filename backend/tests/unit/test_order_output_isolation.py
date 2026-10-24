import ast
from pathlib import Path
import subprocess
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[3]
SOURCE = "backend/src/services/output_builder.py"
BASE = "9495fee9cc2f6a55ae860f9512ef44e7355612a1"
ISOLATION = "42db2edd7f60377aae657ff0a80e910e9ea2b009"


def sources():
    before = subprocess.check_output(["git", "show", f"{BASE}:{SOURCE}"], cwd=ROOT, text=True)
    return before, (ROOT / SOURCE).read_text()


def assignment(tree):
    matches = [node for node in tree.body if isinstance(node, ast.Assign)
               and any(isinstance(target, ast.Name) and target.id == "OUTPUT_DIR" for target in node.targets)]
    assert len(matches) == 1
    return matches[0]


def evaluate(getenv):
    node = assignment(ast.parse((ROOT / SOURCE).read_text()))
    return eval(compile(ast.Expression(node.value), SOURCE, "eval"),
                {"Path": Path, "os": SimpleNamespace(getenv=getenv)})


def test_unset_default_binding_without_service_import():
    calls = []

    def getenv(key, default):
        calls.append((key, default))
        return default

    assert evaluate(getenv) == Path("/tmp/orders-outputs")
    assert calls == [("ORDERS_OUTPUT_DIR", "/tmp/orders-outputs")]


def test_explicit_owned_binding_without_import_or_write(tmp_path):
    owned = tmp_path / "orders"

    def getenv(key, default):
        assert (key, default) == ("ORDERS_OUTPUT_DIR", "/tmp/orders-outputs")
        return str(owned)

    assert evaluate(getenv) == owned
    assert not owned.exists()


def test_empty_value_is_not_replaced_with_default():
    assert evaluate(lambda key, default: "") == Path("")


def test_exact_source_and_entire_other_ast_parity():
    before, _ = sources()
    after = subprocess.check_output(["git", "show", f"{ISOLATION}:{SOURCE}"], cwd=ROOT, text=True)
    original = 'OUTPUT_DIR = Path("/tmp/orders-outputs")'
    replacement = 'OUTPUT_DIR = Path(os.getenv("ORDERS_OUTPUT_DIR", "/tmp/orders-outputs"))'
    assert before.count(original) == 1
    assert before.startswith("import csv\n")
    assert after == before.replace("import csv\n", "import csv\nimport os\n", 1).replace(original, replacement)
    old_tree, new_tree = ast.parse(before), ast.parse(after)
    imports = [node for node in new_tree.body if isinstance(node, ast.Import)
               and ast.dump(node) == ast.dump(ast.parse("import os").body[0])]
    assert len(imports) == 1
    new_tree.body.remove(imports[0])
    assignment(new_tree).value = assignment(old_tree).value
    assert ast.dump(new_tree, include_attributes=False) == ast.dump(old_tree, include_attributes=False)


def test_output_consumers_and_explicit_output_dir_priority_unchanged(tmp_path):
    before, after = sources()
    def consumers(source):
        return [ast.dump(node, include_attributes=False) for node in ast.parse(source).body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and any(isinstance(child, ast.Name) and child.id == "OUTPUT_DIR" for child in ast.walk(node))]
    historical = subprocess.check_output(["git", "show", f"{ISOLATION}:{SOURCE}"], cwd=ROOT, text=True)
    assert consumers(historical) == consumers(before)
    assert consumers(after)
    expressions = [node for node in ast.walk(ast.parse(after)) if isinstance(node, ast.IfExp)
                   and ast.dump(node, include_attributes=False) == ast.dump(
                       ast.parse("output_dir if output_dir is not None else local_directory(OUTPUT_DIR)", mode="eval").body,
                       include_attributes=False)]
    assert len(expressions) == 1
    for explicit, expected in [(tmp_path / "explicit", tmp_path / "explicit"), (None, tmp_path / "default")]:
        routed = []
        def local_directory(directory):
            routed.append(directory)
            return directory
        assert eval(compile(ast.Expression(expressions[0]), SOURCE, "eval"),
                    {"output_dir": explicit, "OUTPUT_DIR": tmp_path / "default", "local_directory": local_directory}) == expected
        assert routed == ([] if explicit is not None else [tmp_path / "default"])
    assert not (tmp_path / "explicit").exists()
    assert not (tmp_path / "default").exists()
