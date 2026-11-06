"""Source/schema tests only: no app, database, lifespan or HTTP requests."""
import ast
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[2]
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

    def test_handlers_forward_identity_cursor_revision_and_explicit_resolution(self):
        handlers = definitions((ROOT / API).read_text())
        expected = {
            "list_menu_imports": "_monthly_call(menu_service.list_menu_imports, month_id, before_id=before_id)",
            "read_menu_import": "_monthly_call(menu_service.read_menu_import, month_id, import_id)",
            "review_menu_import": "_monthly_call(menu_service.review_menu_import, month_id, import_id, if_match, body['resolutions'], input_expected=body.get('input_revision'))",
            "apply_menu_import": "_monthly_call(menu_service.review_menu_import, month_id, import_id, if_match, None, apply=True)",
        }
        for name, expression in expected.items():
            with self.subTest(name=name):
                result = next(node.value for node in handlers[name].body if isinstance(node, ast.Return))
                self.assertEqual(ast.dump(result), ast.dump(ast.parse(expression, mode="eval").body))
        review = handlers["review_menu_import"]
        self.assertEqual(ast.unparse(review.body[0].test), "not isinstance(body.get('resolutions'), list)")
        self.assertIsInstance(review.body[0].body[0], ast.Raise)
        for name in ("review_menu_import", "apply_menu_import"):
            self.assertEqual(handlers[name].args.args[-1].arg, "if_match")
            self.assertEqual(ast.unparse(handlers[name].args.defaults[-1]), "Header(None)")
        download = handlers["download_menu_import"]
        self.assertEqual(ast.unparse(download.body[0].value),
                         "_monthly_call(menu_service.get_menu_import_download, month_id, import_id)")
        response = download.body[-1].value
        self.assertEqual(ast.unparse(response.func), "Response")
        self.assertEqual({kw.arg: ast.unparse(kw.value) for kw in response.keywords}, {
            "content": "source['bytes']", "media_type": "source['media_type']",
            "headers": "{'Content-Disposition': _attachment_content_disposition(source['filename'])}"})

    def test_docs_retain_unknown_nested_fields_and_nullable_menu(self):
        sys.path.insert(0, str(ROOT / "backend"))
        from src.api.menu_import_contract import MonthlySnapshotResponse
        value = {"menu": None, "items": [{"unknown": {"quantity": "0.000100"}}],
                 "entries": [{"unknown": [0, None]}], "master_checks": {"count": 0, "issues": []},
                 "source_month_id": "neighbor-month", "revision": "synthetic-revision", "extra": "keep"}
        self.assertEqual(MonthlySnapshotResponse.model_validate(value).model_dump(), value)


if __name__ == "__main__":
    unittest.main()
