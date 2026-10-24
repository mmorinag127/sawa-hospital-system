"""Deletion-only and closed-reference proof; no production module execution here."""
import ast
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]
SOURCE = "backend/src/services/order_service.py"
BASE = "0d165588f98a12829217938058662a3745a82d8f"
DELETION = "23916d904943fda3f5b88c2dd9561d28ad9835b1"
INVENTORY_TEST = "backend/tests/unit/test_workflow_recorded_job_status.py"
RETIRED = frozenset({
    "_apply_payload_quantities_by_row_index",
    "_attach_review_metadata_to_order_payload",
    "_build_ocr_review_metadata",
    "_build_position_menu_entries_from_order_lines",
    "_build_position_menu_entries_from_orders",
    "_build_prompt_rows_from_table_rows",
    "_build_rows_from_order_lines",
    "_build_sheet_rows_from_order_lines",
    "_build_synthetic_ocr_pages",
    "_draft_requires_facility_template_rebase",
    "_expand_explicit_sheet_week_range",
    "_format_block_order_hint",
    "_format_sheet_week_value",
    "_grid_detection_payload",
    "_hakodate_projection_row_from_sheet_cell",
    "_hakodate_target_cell_map_from_grid_and_rows",
    "_hakodate_target_cell_map_from_order_document",
    "_hakodate_week_rows_from_menu_entries",
    "_is_saved_draft_revision",
    "_is_supported_daypart",
    "_load_visible_ocr_rerun_output",
    "_max_source_row_index_for_position_scope",
    "_maybe_upgrade_generic_sheet_draft",
    "_normalize_materialization_signature_quantity",
    "_normalize_sheet_warning_list",
    "_overlay_historical_dayparts_onto_ocr_entries",
    "_overlay_payload_aux_cells_onto_sheet_row_lists",
    "_png_data_uri",
    "_read_ocr_pipeline_wait_timeout_seconds",
    "_read_ocr_rerun_pipeline_stage_timeout_seconds",
    "_regular_override_priority",
    "_rows_for_reparse_quality_gate",
    "_sheet_candidate_sort_key",
    "_summarize_prompt_block_coverage_gaps",
    "_user_facility_template_columns_for_save",
})


def baseline():
    return subprocess.check_output(["git", "show", f"{BASE}:{SOURCE}"], cwd=ROOT).decode()


def definitions(tree):
    return {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_only_the_fixed_retired_definitions_and_their_separators_are_deleted():
    original = baseline()
    tree = ast.parse(original)
    old = definitions(tree)
    current_source = subprocess.check_output(["git", "show", f"{DELETION}:{SOURCE}"], cwd=ROOT).decode()
    current_tree = ast.parse(current_source)
    current = definitions(current_tree)
    assert len(RETIRED) == 35
    assert old.keys() - current.keys() == RETIRED
    assert current.keys() - old.keys() == set()
    lines = original.splitlines(keepends=True)
    for node in sorted((old[name] for name in RETIRED), key=lambda node: node.lineno, reverse=True):
        assert not node.decorator_list
        assert lines[node.end_lineno:node.end_lineno + 2] == ["\n", "\n"]
        del lines[node.lineno - 1:node.end_lineno + 2]
    assert current_source == "".join(lines)
    tree.body = [node for node in tree.body if not isinstance(node, ast.FunctionDef) or node.name not in RETIRED]
    assert ast.dump(current_tree) == ast.dump(tree)
    # Whole-source byte equality and whole-AST equality above cover every
    # retained function without repeatedly scanning the full source per node.
    # Later approved source fixes are not constrained to this deletion-only diff.
    assert RETIRED.isdisjoint(definitions(ast.parse((ROOT / SOURCE).read_text())))


def test_retired_definition_metadata_has_no_registration_or_executable_default():
    old = definitions(ast.parse(baseline()))
    for name in RETIRED:
        node = old[name]
        assert not node.decorator_list
        for value in [*node.args.defaults, *node.args.kw_defaults]:
            assert value is None or isinstance(value, ast.Constant)
        annotations = [arg.annotation for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
                       if arg.annotation]
        if node.returns:
            annotations.append(node.returns)
        for annotation in annotations:
            assert not any(isinstance(part, (ast.Call, ast.Lambda, ast.NamedExpr, ast.ListComp,
                                            ast.SetComp, ast.DictComp, ast.GeneratorExp))
                           for part in ast.walk(annotation))


def test_original_incoming_references_are_closed_within_the_retired_set():
    tree = ast.parse(baseline())
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    top = definitions(tree)
    seen_edges = set()
    for node in ast.walk(tree):
        target = node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute) else None
        if target not in RETIRED:
            continue
        owner = node
        while owner not in tree.body:
            owner = parents[owner]
        assert isinstance(owner, ast.FunctionDef) and owner.name in RETIRED
        seen_edges.add((owner.name, target))
    assert ("_attach_review_metadata_to_order_payload", "_build_ocr_review_metadata") in seen_edges
    assert ("_build_ocr_review_metadata", "_is_saved_draft_revision") in seen_edges
    assert ("_build_synthetic_ocr_pages", "_png_data_uri") in seen_edges
    assert ("_hakodate_target_cell_map_from_order_document", "_grid_detection_payload") in seen_edges
    assert RETIRED <= top.keys()


def test_no_current_tracked_python_consumer_refers_to_retired_names():
    files = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    for filename in files:
        if not filename.endswith(".py") or (ROOT / filename).resolve() == Path(__file__).resolve():
            continue
        tree = ast.parse((ROOT / filename).read_text())
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            target = (node.id if isinstance(node, ast.Name) else node.attr if isinstance(node, ast.Attribute)
                      else node.name if isinstance(node, ast.alias)
                      else node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None)
            if target not in RETIRED:
                continue
            raise AssertionError((filename, node.lineno, target))


def test_other_recorded_job_tests_and_adapter_oracles_are_byte_identical():
    original = subprocess.check_output(["git", "show", f"{BASE}:{INVENTORY_TEST}"], cwd=ROOT).decode()
    current = (ROOT / INVENTORY_TEST).read_text()
    old_tree, new_tree = ast.parse(original), ast.parse(current)
    inventory = "test_existing_backend_functions_are_unchanged_except_additive_workflow_projection"
    old = definitions(old_tree)
    new = definitions(new_tree)
    assert old.keys() == new.keys()
    for name, node in old.items():
        if name != inventory:
            assert ast.dump(node) == ast.dump(new[name])
            assert ast.get_source_segment(original, node) == ast.get_source_segment(current, new[name])
    new_inventory = new[inventory]
    source = ast.get_source_segment(current, new_inventory)
    assert 'current_keys.count("common_job_status") == 1' in source
    assert "old_keys <= set(current_keys)" in source
    assert "ast.unparse(result.values[index])" in source
    assert "del result.keys[index]" in source
    assert "del result.values[index]" in source
    assert "assert ast.dump(current) == ast.dump(original)" in source
    assert not any(isinstance(node, ast.Name) and node.id == "retired"
                   for node in ast.walk(new_inventory))
