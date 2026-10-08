"""Source-only retirement contract; no imports of production services."""
import ast
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[3]
SERVICE = "backend/src/services/config_service.py"
BASELINE_SHA = "d6181ae30a270534a0ac1b17a5372c7a1fb0989b"
REMOVED_NAMES = frozenset({
    "_strip_facility_template_storage_keys",
    "_FACILITY_TEMPLATE_STORAGE_KEYS",
    "_master_facility_template_ids",
})


def test_retired_facility_config_nodes_are_absent():
    tree = ast.parse((ROOT / SERVICE).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            names.add(node.value)
    assert not names & REMOVED_NAMES


def test_tracked_source_has_no_retired_facility_config_callers():
    # git inventory works with shallow checkouts; no history or fetch required.
    inventory = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=ROOT,
    ).decode("utf-8").split("\0")
    this_test = Path(__file__).resolve()
    references = []
    for relative in filter(None, inventory):
        path = ROOT / relative
        if path.resolve() == this_test:
            continue
        source = path.read_bytes()
        for name in REMOVED_NAMES:
            if name.encode("ascii") in source:
                references.append((relative, name))
    assert references == []
