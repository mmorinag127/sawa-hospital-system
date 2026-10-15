"""Compact cell receipts; the complete captured source stays at payload level."""
import hashlib
import json

CONTRACT = "hakodate_source_receipt_ref/v1"


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def source_reference(source):
    if not isinstance(source, dict) or not source or "contract" in source:
        raise ValueError("hakodate_artifact_source_mismatch")
    return {"contract": CONTRACT,
            "sha256": hashlib.sha256(canonical_json(source).encode("utf-8")).hexdigest()}


def validate_receipts(receipts, source):
    expected = source_reference(source)
    representation = None
    source_json = None
    for receipt in receipts:
        if not isinstance(receipt, dict):
            raise ValueError("hakodate_artifact_source_mismatch")
        is_reference = "contract" in receipt
        if representation is not None and representation != is_reference:
            raise ValueError("hakodate_artifact_source_mixed")
        representation = is_reference
        if is_reference:
            if receipt != expected:
                raise ValueError("hakodate_artifact_source_mismatch")
        else:
            # Legacy is the exact complete source, never an inferred replacement.
            if source_json is None:
                source_json = canonical_json(source)
            if canonical_json(receipt) != source_json:
                raise ValueError("hakodate_artifact_source_mismatch")
