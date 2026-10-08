import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src/services/position_column_mapping_service.py"
BASE = "07c31f7ecbd8d6ef68f8b50555ce36d25a46c7e7"
PATH = "backend/src/services/position_column_mapping_service.py"


# Baseline byte segments and structural AST hashes from BASE; no history is needed at test runtime.
PUBLIC_HASHES = {
    "build_position_fallback_artifacts": (
        "48d2dc6939cc6c53e4cf836c38d02153605c1b7372747f1bbabe9ff3824451c3",
        "61525640629e7be07cb3ac93e32f1d4f081caa8df3a1467357d13538343ed944",
    ),
    "augment_payload_with_position_fallback": (
        "bbd6ac654e4711c481a7b201d169276d4508739808a96e6f045994db624e752d",
        "f2ef58c27d89854f18f94bd58eababb5245016cb0f17ed3663d66ce4f0aeb594",
    ),
    "candidate_resolution_uses_position_fallback": (
        "1b77d6985f4498158019c73df93b7aef9b002ade6edbdf539a7bcd51f72f9135",
        "c09bcdfdbddd7f2a8999f90a7bd84b698441ad3b8a05ba776ec2c344af2af7ef",
    ),
    "payload_uses_ready_position_fallback": (
        "842c6de2a84573164bc3f9f5ae2ce7177bea0afaf4f7323dbc288be848820e19",
        "30566e3ffda5db366f90734b938e1933dc93f809225be12c3a4c8be466a7cc82",
    ),
    "payload_uses_partial_position_fallback": (
        "cb3fde0a609fe273198a08f5cb2660350edb6a013af11dac1e52eb621f66a66f",
        "413063b9b2660983c3a5fa80d391a1dad7c129488cd9805bb22b6661df8b595d",
    ),
    "payload_position_fallback_requires_choice": (
        "3e529c492815078055e5046d1eb85612fac8d67b0bcfdbbf3acedb348d2e024e",
        "1cfc1f6e82522b586176f7cfe806d320ba120bc042713448f388485196e5c3e9",
    ),
    "stable_mapping_signature": (
        "31dd527dcd13c08c2858a829429cb799b20b567aa65cdde06b3ef67f0533e32c",
        "ee867c682c8d55051896d8dc4c93d6a06c5865f93760a1edb1f21b1d518fc275",
    ),
}
REMOVED_PRIVATE_NAMES = frozenset({
    "_clean_cell_text",
    "_looks_like_menu_text",
    "_looks_like_numeric_text",
    "_infer_body_start_row",
    "_column_bounds",
    "_row_bounds",
    "_infer_quantity_subgrid",
    "_strict_template_semantics_ready",
    "_resolved_position_fallback_source_indexes",
    "_payload_observed_width",
    "_payload_uses_position_normalized_evidence",
    "_position_fallback_matches_template_contract",
    "_invalidated_position_fallback_resolution",
    "_payload_has_position_fallback_resolution",
    "_position_fallback_already_ready",
    "_row_fields",
    "_is_quantity_field",
    "_stable_candidate_id",
    "_candidate_score",
    "_normalize_position_candidates",
    "_position_fallback_choice_required",
    "_existing_position_fallback_requires_choice",
    "_normalize_matrix",
    "_column_header_text",
    "_field_label",
    "_mapping_value",
    "_find_first_quantity_field",
    "_select_header_quantity_field",
    "_merged_header_for_matrix",
    "_header_quantity_pairs_from_range",
    "_augment_quantity_pairs_from_header",
    "_filter_quantity_pairs_to_allowed_sources",
    "_source_column_has_numeric_signal",
    "_table_grid_metadata",
})


def structural_ast(value):
    if isinstance(value, ast.AST):
        return {"node": type(value).__name__, "fields": {
            name: structural_ast(field) for name, field in ast.iter_fields(value)
            if not (name == "type_params" and field == [])
        }}
    if isinstance(value, list):
        return [structural_ast(item) for item in value]
    return value


def test_public_definitions_are_byte_and_ast_identical():
    current = SOURCE.read_text()
    current_tree = ast.parse(current)
    assert len(PUBLIC_HASHES) == 7
    actual = {node.name: node for node in current_tree.body
              if isinstance(node, ast.FunctionDef)}
    assert set(actual) == set(PUBLIC_HASHES)
    for name, (byte_hash, ast_hash) in PUBLIC_HASHES.items():
        replacement = actual[name]
        encoded_ast = json.dumps(structural_ast(replacement), sort_keys=True,
                                 separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        assert hashlib.sha256(encoded_ast).hexdigest() == ast_hash
        assert hashlib.sha256(ast.get_source_segment(current, replacement).encode("utf-8")).hexdigest() == byte_hash
    removed = REMOVED_PRIVATE_NAMES
    assert len(removed) == 34
    assert not removed.intersection(
        node.id for node in ast.walk(current_tree) if isinstance(node, ast.Name))
    imports = [node for node in current_tree.body
               if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert [ast.unparse(node) for node in imports] == [
        "from __future__ import annotations", "import hashlib", "import json", "from typing import Any",
    ]
    assert not any(isinstance(node, ast.Assign) for node in current_tree.body)


def test_no_tracked_python_caller_uses_retired_private_entries():
    removed = REMOVED_PRIVATE_NAMES
    files = subprocess.check_output(["git", "ls-files", "--", "*.py"],
                                    cwd=ROOT.parent, text=True).splitlines()
    for filename in files:
        if filename == PATH:
            continue
        text = (ROOT.parent / filename).read_text()
        if "position_column_mapping_service" not in text:
            continue
        tree = ast.parse(text)
        aliases = {"position_column_mapping_service"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                aliases.update(item.asname or item.name for item in node.names
                               if item.name.endswith("position_column_mapping_service"))
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").endswith("position_column_mapping_service"):
                    assert not removed.intersection(item.name for item in node.names), filename
                    assert not any(item.name == "*" for item in node.names), filename
                aliases.update(item.asname or item.name for item in node.names
                               if item.name == "position_column_mapping_service")
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                owner = ast.unparse(node.value)
                if owner in aliases or owner.endswith(".position_column_mapping_service"):
                    assert node.attr not in removed, filename
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr":
                if node.args and ast.unparse(node.args[0]) in aliases:
                    assert len(node.args) >= 2 and isinstance(node.args[1], ast.Constant), filename
                    assert node.args[1].value not in removed, filename


@pytest.fixture
def isolated_module(monkeypatch):
    import builtins
    original_import = builtins.__import__

    def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
        if "fax_extractor" in name or "fax_extractor" in (fromlist or ()):
            raise AssertionError("Retired mapping module imported fax_extractor")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    spec = importlib.util.spec_from_file_location("retired_position_mapping_case", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("payload", [None, 0, {}, {"quantity": 0, "optional": None}])
def test_disabled_entries_preserve_exact_results_and_identity(isolated_module, payload):
    module = isolated_module
    template = {"quantity": 0, "optional": None}
    assert module.build_position_fallback_artifacts(payload, template, template_id="original") is None
    assert module.augment_payload_with_position_fallback(payload, template, template_id="original") is payload
    assert module.candidate_resolution_uses_position_fallback(payload) is False
    assert module.payload_uses_ready_position_fallback(payload, template=template) is False
    assert module.payload_uses_partial_position_fallback(payload) is False
    assert module.payload_position_fallback_requires_choice(payload) is False


@pytest.mark.parametrize("value,encoded", [
    (None, b"null"),
    (0, b"0"),
    ({"quantity": 0, "optional": None}, b'{"optional": null, "quantity": 0}'),
    ({"b": 0, "a": None}, b'{"a": null, "b": 0}'),
])
def test_signature_preserves_original_json_hash_bytes(isolated_module, value, encoded):
    assert json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8") == encoded
    assert isolated_module.stable_mapping_signature(value) == hashlib.sha1(encoded).hexdigest()
