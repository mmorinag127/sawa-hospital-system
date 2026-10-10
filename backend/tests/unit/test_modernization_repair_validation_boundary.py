"""Owned local component definitions, not HTTP/auth/provider/staging proof."""

from copy import deepcopy

import pytest

from src.services import order_service
from test_h2_operator_repair_revision import (
    repair_order,  # noqa: F401
    _owned_artifact_fixture,
    _attach_provider_artifact_packet,
)


FAULTS = [
    ("target_missing", "hakodate_canonical_representation_missing"),
    ("target_type", "hakodate_canonical_representation_invalid"),
    ("target_conflict", "hakodate_own_artifact_representations_conflict"),
    ("record_missing", "hakodate_canonical_representation_missing"),
    ("record_type", "hakodate_canonical_representation_invalid"),
    ("record_conflict", "hakodate_own_artifact_representations_conflict"),
]


def _malform(payload, fault):
    result = deepcopy(payload)
    if fault == "target_missing":
        result["hakodate_target_cell_map"] = result["hakodate_preprocessing"].pop("target_cell_map")
    elif fault == "target_type":
        result["hakodate_preprocessing"]["target_cell_map"] = None
    elif fault == "target_conflict":
        result["hakodate_target_cell_map"] = []
    elif fault == "record_missing":
        del result["hakodate_ocr_evidence"]
    elif fault == "record_type":
        result["hakodate_ocr_evidence"]["records"] = None
    elif fault == "record_conflict":
        result["hakodate_ocr_evidence_records"] = []
    else:
        raise AssertionError(fault)
    return result


@pytest.fixture
def owned_boundary(repair_order, monkeypatch):
    order_id, revision, _ = repair_order
    monkeypatch.setenv("OCR_QUANTITY_ASSIGNMENT_STRATEGY", "hakodate")
    start = order_service._capture_synchronous_cache_write_input(order_id)
    assert start is not None
    source = order_service._hakodate_owned_source(
        start["configured_input"], start["selected_pdf_receipt"], start,
    )
    targets, records = _owned_artifact_fixture(source, 0)
    payload = {
        "hakodate_repair_source_receipt": deepcopy(source),
        "hakodate_canonical_pipeline": {
            "status": "ready", "version": order_service.HAKODATE_CANONICAL_PIPELINE_VERSION,
            "expected_sheet_cells": deepcopy(source["expected_sheet_cells"]),
        },
        "hakodate_preprocessing": {"target_cell_map": targets},
        "hakodate_ocr_evidence": {"records": records},
    }
    _attach_provider_artifact_packet(payload)
    order_service._validate_hakodate_owned_artifacts(payload, source)
    base, error = order_service._build_hakodate_weekly_menu_base_sheet(
        order_id, synchronous_write_input=start,
    )
    assert error is None
    start["hakodate_base_sheet"] = base
    return order_id, revision, start, source, payload


def _augment(boundary, payload, *, force=False):
    order_id, _, start, _, _ = boundary
    return order_service._augment_hakodate_ocr_payload_artifacts(
        order_id=order_id, payload=payload,
        template=start["configured_input"]["facility"]["fax_template"],
        force_hakodate=force, selected_pdf_receipt=start["selected_pdf_receipt"],
        configured_input=start["configured_input"], synchronous_write_input=start,
    )


def _forbid_write(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("invalid representation reached cache write")
    monkeypatch.setattr(order_service, "_save_order_ocr_cache", forbidden)


@pytest.mark.parametrize("fault,reason", FAULTS)
@pytest.mark.parametrize("phase", ["reuse", "postprovider", "public_default"])
def test_malformed_representation_stops_at_boundary(owned_boundary, monkeypatch, fault, reason, phase):
    order_id, revision, start, _, valid = owned_boundary
    malformed = _malform(valid, fault)
    before = deepcopy(malformed)
    calls = []
    def provider(**kwargs):
        calls.append(kwargs)
        if phase != "postprovider":
            pytest.fail("known corrupt reusable input started a provider")
        return malformed
    monkeypatch.setattr(order_service, "_hakodate_canonical_payload_from_manifest_item", provider)
    _forbid_write(monkeypatch)
    if phase == "public_default":
        # Deliberate corruption of the already captured selected payload;
        # no validator or selected-evidence service is replaced.
        start["evidence"]["payload_json"] = malformed
        result = order_service.build_order_hakodate_assignment(
            order_id, synchronous_write_input=start, expected_revision=revision,
        )
        assert result == (None, reason)
    else:
        result = _augment(owned_boundary, {} if phase == "postprovider" else malformed)
        assert result["hakodate_canonical_pipeline"] == {"status": "blocked", "blockers": [reason]}
    assert len(calls) == (1 if phase == "postprovider" else 0)
    assert malformed == before


def test_explicit_force_skips_old_malformed_and_uses_fresh_owned_packet(owned_boundary, monkeypatch):
    order_id, _, start, source, valid = owned_boundary
    old = _malform(valid, "target_type")
    before = deepcopy(old)
    calls = []
    def provider(**kwargs):
        calls.append(kwargs)
        assert kwargs["order_id"] == order_id
        assert kwargs["configured_input"] == start["configured_input"]
        assert kwargs["pdf_bytes_override"].selected_pdf_receipt == start["selected_pdf_receipt"]
        return deepcopy(valid)
    monkeypatch.setattr(order_service, "_hakodate_canonical_payload_from_manifest_item", provider)
    _forbid_write(monkeypatch)
    result = _augment(owned_boundary, old, force=True)
    assert result == valid
    assert len(calls) == 1
    assert old == before
    order_service._validate_hakodate_owned_artifacts(result, source)
    assert [cell["value"] for cell in result["hakodate_preprocessing"]["target_cell_map"]] == [0]
    assert [record["raw_text"] for record in result["hakodate_ocr_evidence"]["records"]] == [0]


class _ProgrammerFailureTarget(dict):
    def get(self, key, default=None):
        if key == "source":
            raise self.failure
        return super().get(key, default)


@pytest.mark.parametrize("error_type", [RuntimeError, TypeError, OSError])
@pytest.mark.parametrize("phase", ["reuse", "postprovider"])
def test_non_value_error_from_real_reader_propagates(owned_boundary, monkeypatch, error_type, phase):
    payload = deepcopy(owned_boundary[-1])
    failure = error_type("reader programming failure")
    target = _ProgrammerFailureTarget(payload["hakodate_preprocessing"]["target_cell_map"][0])
    # A malformed provider object, not a replacement reader/validator.
    target.failure = failure
    payload["hakodate_preprocessing"]["target_cell_map"] = [target]
    def provider(**kwargs):
        if phase != "postprovider":
            pytest.fail("reader failure started a provider")
        return payload
    monkeypatch.setattr(order_service, "_hakodate_canonical_payload_from_manifest_item", provider)
    _forbid_write(monkeypatch)
    with pytest.raises(error_type, match="reader programming failure"):
        _augment(owned_boundary, {} if phase == "postprovider" else payload)
