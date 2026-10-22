from copy import deepcopy
import json

from src.maintenance import formal_migration_schema as schema


def shapes():
    contract = json.loads(schema.CONTRACT.read_text())
    reviewed = contract["shapes"][contract["revisions"]["postgresql"]["0036"]["tables"]["user_system_access"]]
    restored = deepcopy(reviewed)
    for check in restored["checks"]:
        check["sqltext"] = "system_key::text = ANY (ARRAY['hospital'::character varying::text, 'shift'::character varying::text, 'school-lunch'::character varying::text])"
    for item in restored["native"]:
        if item.get("constraint_name") == "ck_user_system_access_system_key":
            item["definition"] = "CHECK (((system_key)::text = ANY (ARRAY[('hospital'::character varying)::text, ('shift'::character varying)::text, ('school-lunch'::character varying)::text])))"
    return reviewed, restored


def test_dump_restored_check_has_exact_reviewed_semantics():
    reviewed, restored = shapes()
    assert schema._normalize_reflected_catalog_aliases(restored, "postgresql") == schema._normalize_reflected_catalog_aliases(reviewed, "postgresql")


def test_other_role_values_and_constraint_flags_remain_incompatible():
    reviewed, restored = shapes()
    expected = schema._normalize_reflected_catalog_aliases(reviewed, "postgresql")
    for field in ("checks", "native"):
        altered = deepcopy(restored)
        for item in altered[field]:
            key = "sqltext" if field == "checks" else "definition"
            if key in item:
                item[key] = item[key].replace("school-lunch", "admin")
        assert schema._normalize_reflected_catalog_aliases(altered, "postgresql") != expected
    altered = deepcopy(restored)
    for item in altered["native"]:
        if item.get("constraint_name") == "ck_user_system_access_system_key":
            item["convalidated"] = False
    assert schema._normalize_reflected_catalog_aliases(altered, "postgresql") != expected
