"""Source/schema tests only: no app, database, lifespan or HTTP requests."""
import ast
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
PARENT = "0f93dafa0e280786bc461a60185bad2c5c64c1fc"
API = "backend/src/api/menus.py"
PREFIX = "/monthly-menus"
OPERATIONS = {
    (PREFIX, "post"), (PREFIX + "/{month_id}/imports", "get"),
    (PREFIX + "/{month_id}/imports/{import_id}", "get"),
    (PREFIX + "/{month_id}/imports/{import_id}/review", "post"),
    (PREFIX + "/{month_id}/imports/{import_id}/download", "get"),
    (PREFIX + "/{month_id}/imports/{import_id}/apply", "post"),
    (PREFIX + "/{month_id}/entries/{entry_id}/exceptions", "post"),
}


def parent_source(path):
    return subprocess.check_output(["git", "show", f"{PARENT}:{path}"], cwd=ROOT, text=True)


def definitions(source):
    return {node.name: node for node in ast.parse(source).body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}


class MenuImportContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = json.loads((ROOT / "frontend/src/generated/menu-import-openapi.json").read_text())

    def test_exact_seven_actual_registered_operations(self):
        generated = {(path, method) for path, methods in self.schema["paths"].items()
                     for method in methods if method in {"get", "post", "put", "delete"}}
        self.assertEqual(generated, OPERATIONS)
        actual = []
        for node in definitions((ROOT / API).read_text()).values():
            if isinstance(node, ast.ClassDef):
                continue
            for decorator in node.decorator_list:
                if (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)
                        and ast.unparse(decorator.func.value) == "router" and decorator.args):
                    operation = (PREFIX + ast.literal_eval(decorator.args[0]), decorator.func.attr)
                    if operation in OPERATIONS:
                        actual.append(operation)
                        values = {kw.arg: kw.value for kw in decorator.keywords}
                        self.assertIsNone(ast.literal_eval(values["response_model"]))
                        self.assertIn("responses", values)
        self.assertEqual(len(actual), 7)
        self.assertEqual(set(actual), OPERATIONS)

    def test_actual_source_export_matches_saved_schema_without_runtime_import(self):
        spec = importlib.util.spec_from_file_location("final_import_contract_exporter", ROOT / "scripts/export_menu_import_openapi.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.response_contract(), self.schema)

    def test_multipart_raw_review_if_match_and_binary_are_actual_boundaries(self):
        upload = self.schema["paths"][PREFIX]["post"]
        form = upload["requestBody"]["content"]["multipart/form-data"]["schema"]
        self.assertEqual(form["properties"]["file"], {"type": "string", "format": "binary"})
        self.assertIn("file", form["required"])
        self.assertIn("review_resolutions", form["properties"])
        review = self.schema["paths"][PREFIX + "/{month_id}/imports/{import_id}/review"]["post"]
        self.assertIn("application/json", review["requestBody"]["content"])
        for suffix in ["/{month_id}/imports/{import_id}/review", "/{month_id}/imports/{import_id}/apply",
                       "/{month_id}/entries/{entry_id}/exceptions"]:
            parameters = self.schema["paths"][PREFIX + suffix]["post"]["parameters"]
            self.assertEqual(len([p for p in parameters if p["name"] == "if-match" and p["in"] == "header"]), 1)
        binary = self.schema["paths"][PREFIX + "/{month_id}/imports/{import_id}/download"]["get"]
        self.assertEqual(binary["responses"]["200"]["content"],
                         {"*/*": {"schema": {"type": "string", "format": "binary"}}})

    def test_packet_did_not_change_handlers_request_signatures_or_producers(self):
        before, after = definitions(parent_source(API)), definitions((ROOT / API).read_text())
        for name, old in before.items():
            with self.subTest(name=name):
                new = after[name]
                self.assertEqual(ast.dump(ast.Module(body=old.body, type_ignores=[])),
                                 ast.dump(ast.Module(body=new.body, type_ignores=[])))
                if not isinstance(old, ast.ClassDef):
                    self.assertEqual(ast.dump(old.args), ast.dump(new.args))
                    self.assertEqual(ast.dump(old.returns) if old.returns else None,
                                     ast.dump(new.returns) if new.returns else None)
        path = "backend/src/services/menu_service.py"
        before, after = definitions(parent_source(path)), definitions((ROOT / path).read_text())
        for name in ["_serialize_import", "_monthly_snapshot_body", "review_menu_import", "monthly_editor_command"]:
            self.assertEqual(ast.dump(before[name]), ast.dump(after[name]), name)

    def test_docs_retain_unknown_nested_fields_and_nullable_menu(self):
        sys.path.insert(0, str(ROOT / "backend"))
        from src.api.menu_import_contract import MonthlySnapshotResponse
        value = {"menu": None, "items": [{"unknown": {"quantity": "0.000100"}}],
                 "entries": [{"unknown": [0, None]}], "master_checks": {"count": 0, "issues": []},
                 "source_month_id": "neighbor-month", "revision": "synthetic-revision", "extra": "keep"}
        self.assertEqual(MonthlySnapshotResponse.model_validate(value).model_dump(), value)


if __name__ == "__main__":
    unittest.main()
