"""Reflection-only fixtures; no database or PG concurrency proof."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from src.maintenance import formal_migration_schema as schema


def snapshot(include, *, alias):
    index = {"name": "ix_owned", "column_names": ["id"], "unique": False,
             "dialect_options": {"postgresql_include": include}}
    if alias:
        index["include_columns"] = deepcopy(include)
    return {"tables": {"owned": {"columns": [{"name": "id"}], "indexes": [index],
                                "unique": [], "fk": [], "checks": []}},
            "native": [{"table_name": "owned", "definition": "owned physical index"}], "views": {}}


def compare(monkeypatch, tmp_path, expected, actual, dialect="postgresql"):
    # Only catalog acquisition is synthetic; the production comparator is real.
    shape = deepcopy(expected["tables"]["owned"])
    shape["native"] = deepcopy(expected["native"])
    contract = {"revisions": {dialect: {"0002": {"tables": {"owned": "fixture"}, "views": {}}}},
                "shapes": {"fixture": shape}}
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(contract))
    monkeypatch.setattr(schema, "CONTRACT", path)
    monkeypatch.setattr(schema, "read_catalog", lambda connection: deepcopy(actual))
    schema.require_revision_catalog(SimpleNamespace(dialect=SimpleNamespace(name=dialect)), "0002")


@pytest.mark.parametrize("include", [[], ["payload"]])
@pytest.mark.parametrize("expected_alias,actual_alias", [(True, False), (False, True), (True, True)])
def test_old_new_reflection_equivalent(monkeypatch, tmp_path, include, expected_alias, actual_alias):
    expected = snapshot(include, alias=expected_alias)
    actual = snapshot(include, alias=actual_alias)
    original = deepcopy(actual)
    compare(monkeypatch, tmp_path, expected, actual)
    assert actual == original
    assert schema.table_shapes(expected, "postgresql") == schema.table_shapes(actual, "postgresql")


@pytest.mark.parametrize("side", ["expected", "actual"])
def test_missing_indexes_rejected(monkeypatch, tmp_path, side):
    expected, actual = snapshot([], alias=True), snapshot([], alias=False)
    del (expected if side == "expected" else actual)["tables"]["owned"]["indexes"]
    with pytest.raises(KeyError, match="indexes"):
        compare(monkeypatch, tmp_path, expected, actual)


@pytest.mark.parametrize("side", ["expected", "actual"])
@pytest.mark.parametrize("fault", ["conflicting", "canonical-missing"])
def test_invalid_alias_stops(monkeypatch, tmp_path, side, fault):
    expected, actual = snapshot([], alias=True), snapshot([], alias=True)
    index = (expected if side == "expected" else actual)["tables"]["owned"]["indexes"][0]
    if fault == "conflicting":
        index["include_columns"] = ["other"]
    else:
        del index["dialect_options"]["postgresql_include"]
    with pytest.raises(RuntimeError, match="INCLUDE alias"):
        compare(monkeypatch, tmp_path, expected, actual)


@pytest.mark.parametrize("fault", ["name", "columns", "unique", "predicate", "expression",
                                   "include", "unknown", "native"])
def test_nonalias_changes_remain_rejected(monkeypatch, tmp_path, fault):
    expected, actual = snapshot([], alias=True), snapshot([], alias=False)
    index = actual["tables"]["owned"]["indexes"][0]
    if fault == "name":
        index["name"] = "different"
    elif fault == "columns":
        index["column_names"] = ["other"]
    elif fault == "unique":
        index["unique"] = True
    elif fault == "predicate":
        index["dialect_options"]["postgresql_where"] = "id IS NOT NULL"
    elif fault == "expression":
        index["expressions"] = ["lower(id)"]
    elif fault == "include":
        index["dialect_options"]["postgresql_include"] = ["payload"]
    elif fault == "unknown":
        index["unknown_attribute"] = True
    else:
        actual["native"][0]["definition"] = "different physical index"
    with pytest.raises(RuntimeError, match="catalog blocked"):
        compare(monkeypatch, tmp_path, expected, actual)


@pytest.mark.parametrize("dialect", ["sqlite", "other"])
def test_other_dialects_keep_alias_literal(monkeypatch, tmp_path, dialect):
    expected, actual = snapshot([], alias=True), snapshot([], alias=False)
    assert schema.table_shapes(expected, dialect)["owned"]["indexes"] == expected["tables"]["owned"]["indexes"]
    with pytest.raises(RuntimeError, match="catalog blocked"):
        compare(monkeypatch, tmp_path, expected, actual, dialect)
