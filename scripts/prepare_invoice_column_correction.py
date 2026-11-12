"""Prepare an explicit invoice-only correction without modifying saved lineage."""

from copy import deepcopy
import argparse
from datetime import date
import hashlib
import json
from pathlib import Path


def prepare_correction(facility, *, source_columns, invoice_columns, excluded_source_columns=()):
    """Require reviewed source identities; never infer aliases or aggregation."""
    actual = facility.get("fax_template", {}).get("columns", [])
    authored = [column for column in actual if column.get("role") == "quantity"
                and (column.get("semantic") or {}).get("aggregation_role", "include") != "exclude"]
    identities = lambda columns: [
        (column.get("source_index"), column.get("header"),
         column.get("diet_type"), column.get("area_id")) for column in columns
    ]
    reviewed = sorted([*source_columns, *excluded_source_columns], key=lambda column: column["source_index"])
    if not source_columns or identities(authored) != identities(reviewed):
        raise ValueError("reviewed_source_columns_changed")
    expected = [(column["diet_type"], column.get("area_id")) for column in source_columns]
    targets = [column for column in invoice_columns if column.get("source") == "quantity"]
    if [(column.get("diet_type"), column.get("area_id")) for column in targets] != expected:
        raise ValueError("invoice_requires_explicit_one_to_one_source_mapping")
    if [column.get("name") for column in targets] != [column["header"] for column in source_columns]:
        raise ValueError("invoice_headers_must_match_reviewed_original")
    current = facility.get("invoice_template")
    if not isinstance(current, dict):
        raise ValueError("existing_invoice_template_required")
    if [column for column in current["columns"] if column.get("source") != "quantity"] != [
            column for column in invoice_columns if column.get("source") != "quantity"]:
        raise ValueError("nonquantity_invoice_columns_must_be_preserved")
    result = deepcopy(facility)
    result["invoice_template"]["columns"] = deepcopy(invoice_columns)
    return result


def prepare_source_labels(facility, review):
    """Author future column identities from reviewed positions, not legacy aliases."""
    result = deepcopy(facility)
    names = {patch["expected"]["name"]: patch["replacement"]["name"]
             for patch in review["source_label_patches"]}
    for template_key in ("fax_template", "fax_template_override"):
        template = result.get(template_key)
        if template is None:
            continue
        for patch in review["source_label_patches"]:
            matches = [column for column in template["columns"]
                       if column.get("source_index") == patch["source_index"]]
            if len(matches) != 1:
                raise ValueError("reviewed_source_position_ambiguous")
            column = matches[0]
            if any(column.get(key) != value for key, value in patch["expected"].items()):
                raise ValueError("reviewed_source_column_changed")
            if set(patch["replacement"]) - {"header", "area_id", "name", "delivery_header"}:
                raise ValueError("source_label_patch_outside_scope")
            column.update(deepcopy(patch["replacement"]))
            if isinstance(column.get("semantic"), dict):
                column["semantic"]["area_id"] = column["area_id"]
        if "main_ocr_row_fields" in template:
            template["main_ocr_row_fields"] = [names.get(field, field)
                                               for field in template["main_ocr_row_fields"]]
    for patch in review["invoice_label_patches"]:
        matches = [column for column in result["invoice_template"]["columns"]
                   if column.get("column_index") == patch["column_index"]]
        if len(matches) != 1 or any(matches[0].get(key) != value for key, value in patch["expected"].items()):
            raise ValueError("reviewed_invoice_column_changed")
        if set(patch["replacement"]) - {"name", "area_id", "header"}:
            raise ValueError("invoice_label_patch_outside_scope")
        matches[0].update(deepcopy(patch["replacement"]))
    # New IDs have original labels, with no alias to an old floor or historic cell.
    if result.get("areas") != review["expected_areas"]:
        raise ValueError("reviewed_areas_changed")
    result["areas"] = deepcopy(review["new_areas"])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--effective-date", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    snapshot = json.loads(args.snapshot.read_text())
    review = json.loads(args.review.read_text())
    master = snapshot["facility_master"]
    matches = [entry for entry in master["facilities"]
               if entry["facility_id"] == review["facility_id"]]
    if len(matches) != 1 or not snapshot.get("revision"):
        raise ValueError("revision_bound_facility_snapshot_required")
    versions = matches[0].get("versions", [])
    day = args.effective_date.isoformat()
    selected = [version for version in versions
                if (version["valid_from"] is None or version["valid_from"] <= day)
                and (version["valid_to"] is None or version["valid_to"] >= day)]
    if len(selected) != 1:
        raise ValueError("single_effective_facility_version_required")
    base = deepcopy(selected[0]["facility"])
    if "source_label_patches" in review:
        corrected = prepare_source_labels(base, review)
    else:
        corrected = prepare_correction(base,
                                       source_columns=review["source_columns"],
                                       invoice_columns=review["invoice_columns"],
                                       excluded_source_columns=review.get("excluded_source_columns", []))
    for field in ("valid_from", "valid_to"):
        if field in corrected:
            corrected[field] = day if field == "valid_from" else None
    identity = json.dumps({"facility": corrected, "valid_from": day},
                          ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    version = {"version_id": "invoice-correction-" + hashlib.sha256(identity.encode()).hexdigest(),
               "valid_from": day, "valid_to": None, "facility": corrected}
    body = {"expected_revision": snapshot["revision"], "version": version}
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(body, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


if __name__ == "__main__":
    main()
