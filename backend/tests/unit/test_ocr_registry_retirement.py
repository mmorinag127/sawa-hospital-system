"""Source-only retirement contracts; never import the Firestore service."""
import ast
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[3]
BASE = "4677ef58dd94c5c99eef228b03691bf6cace8a75"
SERVICE = "backend/src/services/ocr_registry_service.py"
API = "backend/src/api/ocr_registry.py"
REMOVED = {
    "list_templates", "get_template", "save_template", "delete_template",
    "list_facilities", "get_facility", "save_facility", "delete_facility",
    "resolve_unclassified", "_utc_now",
}
KEPT = {"_project_id", "_client", "list_unclassified", "get_unclassified"}


def original(path):
    return subprocess.check_output(
        ["git", "show", f"{BASE}:{path}"], cwd=ROOT, text=True
    )


def source(path):
    return (ROOT / path).read_text()


def functions(text):
    return {
        node.name: node for node in ast.parse(text).body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def test_removed_helpers_and_firestore_writers_are_absent():
    tree = ast.parse(source(SERVICE))
    assert set(functions(source(SERVICE))) == KEPT
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert not names & REMOVED
    assert not names & {"TEMPLATE_COLLECTION", "FACILITY_COLLECTION", "JOBS_COLLECTION"}
    assert not any(
        isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr in {"set", "delete", "update", "create", "batch"}
        for node in ast.walk(tree)
    )
    assert not any(
        isinstance(node, ast.ImportFrom) and node.module == "datetime"
        for node in tree.body
    )


def test_reads_client_environment_and_collection_are_byte_identical():
    before, after = original(SERVICE), source(SERVICE)
    old, new = functions(before), functions(after)
    for name in KEPT:
        assert ast.get_source_segment(after, new[name]) == ast.get_source_segment(before, old[name])
    for name in ("UNCLASSIFIED_COLLECTION", "_CLIENT"):
        def assignment(text):
            return next(
                ast.get_source_segment(text, node) for node in ast.parse(text).body
                if isinstance(node, (ast.Assign, ast.AnnAssign))
                and any(isinstance(child, ast.Name) and child.id == name for child in ast.walk(node))
            )
        assert assignment(after) == assignment(before)


def test_api_retired_reads_use_same_guard_and_training_contract_is_unchanged():
    before, after = original(API), source(API)
    old, new = functions(before), functions(after)
    changed = {"list_unclassified", "get_unclassified"}
    assert set(old) == set(new)
    for name in set(old) - changed:
        assert ast.get_source_segment(before, old[name]) == ast.get_source_segment(after, new[name])
    for name in changed:
        assert ast.get_source_segment(before, old[name].decorator_list[0]) == ast.get_source_segment(after, new[name].decorator_list[0])
    funcs = functions(source(API))
    disabled = [
        name for name, node in funcs.items()
        if name != "_legacy_ocr_registry_disabled"
        and len(node.body) == 1 and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Call)
        and isinstance(node.body[0].value.func, ast.Name)
        and node.body[0].value.func.id == "_legacy_ocr_registry_disabled"
    ]
    assert len(disabled) == 15
    assert changed <= set(disabled)
    helper = funcs["_legacy_ocr_registry_disabled"]
    assert isinstance(helper.body[0], ast.Raise)
    values = {arg.arg: ast.literal_eval(arg.value) for arg in helper.body[0].exc.keywords}
    assert values == {"status_code": 410, "detail": "legacy_ocr_registry_disabled"}


def test_tracked_python_import_alias_and_call_census():
    paths = subprocess.check_output(
        ["git", "ls-files", "*.py"], cwd=ROOT, text=True
    ).splitlines()
    callers = []
    for path in paths:
        if path == SERVICE:
            continue
        tree = ast.parse(source(path), filename=path)
        aliases = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for item in node.names:
                    if item.name.endswith(".ocr_registry_service"):
                        assert item.asname, (path, "unbound dotted import requires census review")
                        aliases.add(item.asname)
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").endswith(".ocr_registry_service"):
                    raise AssertionError((path, "direct registry member import requires review"))
                if node.module == "src.services":
                    assert all(item.name != "*" for item in node.names)
                    aliases.update(item.asname or item.name for item in node.names if item.name == "ocr_registry_service")
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in aliases:
                parent = parents[node]
                assert isinstance(parent, ast.Attribute) and parent.value is node, (path, "registry alias escapes")
                assert parent.attr in {"list_unclassified", "get_unclassified"}, (path, parent.attr)
                assert isinstance(parents[parent], ast.Call), (path, "indirect registry method reference")
                callers.append((path, parent.attr))
            if isinstance(node, ast.Call) and (
                isinstance(node.func, ast.Name) and node.func.id == "__import__"
                or isinstance(node.func, ast.Attribute) and node.func.attr == "import_module"
            ):
                assert not any(
                    isinstance(child, ast.Constant) and isinstance(child.value, str)
                    and "ocr_registry_service" in child.value
                    for child in ast.walk(node)
                ), (path, "dynamic registry import")
    assert callers == []


def test_retired_reader_http_auth_and_410_without_registry_access(monkeypatch):
    import runpy
    import sys
    from types import ModuleType
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.api import auth

    class ForbiddenRegistry(ModuleType):
        def __getattr__(self, name):
            raise AssertionError(f"retired registry accessed: {name}")

    monkeypatch.setitem(sys.modules, "src.services.ocr_registry_service", ForbiddenRegistry("registry"))
    module = runpy.run_path(str(ROOT / API))
    app = FastAPI()
    app.include_router(module["router"])
    monkeypatch.setenv("AUTH_DISABLED", "false")
    monkeypatch.setattr(auth, "AUTH_PROVIDER", "local")
    with TestClient(app) as client:
        for path in ("/ocr/unclassified", "/ocr/unclassified/old-record"):
            assert client.get(path).status_code == 401
        app.dependency_overrides[auth.get_current_admin] = lambda: auth.UserContext("operator")
        assert client.get("/ocr/unclassified").status_code == 403
        app.dependency_overrides[auth.get_current_admin] = lambda: auth.UserContext("admin")
        for path in ("/ocr/unclassified?status=pending&limit=100", "/ocr/unclassified/old-record"):
            response = client.get(path)
            assert response.status_code == 410
            assert response.json() == {"detail": "legacy_ocr_registry_disabled"}
        assert client.post("/ocr/unclassified/old-record/resolve", json={"template_id": "old"}).status_code == 410
