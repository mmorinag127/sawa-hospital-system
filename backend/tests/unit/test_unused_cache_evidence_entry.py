"""Read-only source contracts; never import or invoke production services."""

import ast
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[3]
BASE = "23b33fb419dd7d9efab3b3cda53fabf6bf166440"
SERVICE = "backend/src/services/ocr_evidence_service.py"
REMOVED = "backfill_evidence_run_" + "from_cached_payload"


def _git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def test_only_unused_entry_and_its_following_separator_are_removed():
    original = _git("show", f"{BASE}:{SERVICE}").decode("utf-8")
    current = (ROOT / SERVICE).read_bytes().decode("utf-8")
    before = ast.parse(original)
    targets = [node for node in before.body
               if isinstance(node, ast.FunctionDef) and node.name == REMOVED]
    assert len(targets) == 1
    target = targets[0]
    lines = original.splitlines(keepends=True)
    assert lines[target.end_lineno:target.end_lineno + 2] == ["\n", "\n"]
    expected = "".join(lines[:target.lineno - 1] + lines[target.end_lineno + 2:])
    assert current == expected
    before.body.remove(target)
    assert ast.dump(ast.parse(current), include_attributes=False) == ast.dump(
        before, include_attributes=False)
    original_functions = {node.name: ast.dump(node, include_attributes=False)
                          for node in before.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    current_functions = {node.name: ast.dump(node, include_attributes=False)
                         for node in ast.parse(current).body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert current_functions == original_functions
    assert REMOVED not in current


def test_tracked_source_has_no_literal_callers_or_entry_references():
    paths = _git("ls-files", "-z").decode("utf-8").split("\0")
    own = Path(__file__).resolve().relative_to(ROOT).as_posix()
    checked = []
    for relative in paths:
        if not relative or relative == own or relative.startswith("docs/"):
            continue
        path = ROOT / relative
        if not path.is_file():
            continue
        data = path.read_bytes()
        assert REMOVED.encode() not in data, relative
        if path.suffix == ".py":
            tree = ast.parse(data, filename=relative)
            for node in ast.walk(tree):
                if isinstance(node, ast.Name):
                    assert node.id != REMOVED, relative
                if isinstance(node, ast.Attribute):
                    assert node.attr != REMOVED, relative
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    assert REMOVED not in node.value, relative
            checked.append(relative)
    assert SERVICE in checked
    assert any(path.startswith("backend/") for path in checked)
    assert any(path.startswith("ocr_pipeline/") for path in checked)
