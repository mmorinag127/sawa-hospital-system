"""Explicit offline ID conversion; not canonical authoring or schema validation."""

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path


def prepare_config(config, *, facility_id, template_id):
    """Copy one effective config, adding only an absent nested template ID."""
    if not all(isinstance(v, str) and v and v == v.strip() for v in (facility_id, template_id)):
        raise ValueError("explicit_nonempty_ids_required")
    if not isinstance(config, dict) or "facilities" in config:
        raise ValueError("single_effective_facility_config_required")
    if config.get("facility_id") != facility_id:
        raise ValueError("facility_id_mismatch")
    if config.get("fax_template_id") != template_id:
        raise ValueError("outer_template_id_mismatch")
    if "template_id" in config and config["template_id"] != template_id:
        raise ValueError("top_level_template_id_conflict")
    if "fax_template_ids" in config and config["fax_template_ids"] != [template_id]:
        raise ValueError("ambiguous_template_ids")
    template = config.get("fax_template")
    if not isinstance(template, dict):
        raise ValueError("existing_fax_template_object_required")
    if "template_id" in template and template["template_id"] != template_id:
        raise ValueError("nested_template_id_conflict")
    result = deepcopy(config)
    changes = []
    if "template_id" not in template:
        result["fax_template"]["template_id"] = template_id
        changes.append({"op": "add", "path": "/fax_template/template_id", "value": template_id})
    return result, changes


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key: " + key)
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("non_json_constant: " + value)


def _encode(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def convert_file(*, input_path, output_path, report_path, expected_sha256, facility_id, template_id):
    paths = [Path(p).resolve() for p in (input_path, output_path, report_path)]
    if len(set(paths)) != 3:
        raise ValueError("input_output_report_must_be_distinct")
    source, output, report = paths
    if output.exists() or report.exists():
        raise FileExistsError("output_or_report_already_exists")
    raw = source.read_bytes()
    input_hash = hashlib.sha256(raw).hexdigest()
    if expected_sha256 != input_hash:
        raise ValueError("input_sha256_mismatch")
    config = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    converted, changes = prepare_config(config, facility_id=facility_id, template_id=template_id)
    encoded = _encode(converted) if changes else raw
    audit = {"input": str(source), "output": str(output), "facility_id": facility_id,
             "template_id": template_id, "input_sha256": input_hash,
             "output_sha256": hashlib.sha256(encoded).hexdigest(), "changes": changes,
             "scope": "Nested ID conversion only; no complete-schema, canonical-authoring, date or approval validation."}
    created = []
    try:
        # Exclusive creation also prevents overwrites if a destination appears after the check.
        with output.open("xb") as output_file:
            created.append(output)
            with report.open("xb") as report_file:
                created.append(report)
                output_file.write(encoded)
                report_file.write(_encode(audit))
    except BaseException:
        for path in reversed(created):
            path.unlink()
        raise
    return audit


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--facility-id", required=True)
    parser.add_argument("--template-id", required=True)
    args = parser.parse_args(argv)
    try:
        convert_file(input_path=args.input, output_path=args.output, report_path=args.report,
                     expected_sha256=args.expected_sha256, facility_id=args.facility_id,
                     template_id=args.template_id)
    except (ValueError, OSError) as exc:
        parser.exit(2, str(exc) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
