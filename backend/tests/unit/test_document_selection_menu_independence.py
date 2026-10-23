import pytest
from fastapi import HTTPException

from src.api import orders


@pytest.mark.parametrize("document_id", [None, "current", "history"])
def test_document_selection_preserves_explicit_unresolved_menu_state(monkeypatch, document_id):
    versions = [{"document_id": identity, "storage_uri": f"gs://owned/{identity}.pdf"}
                for identity in ("current", "history")]
    payload = {"id": "O", "versions": versions, "current_version": versions[0],
               "menu_amount_resolution": {"status": "unresolved", "affected_lines": [{"quantity": "?"}]}}
    def read(order_id, *, allow_unresolved_menu_amount=False):
        assert order_id == "O"
        assert allow_unresolved_menu_amount is True
        return payload
    monkeypatch.setattr(orders.order_service, "get_order_by_id", read)
    result, selected = orders._resolve_order_document_version("O", document_id)
    assert result is payload
    assert result["menu_amount_resolution"]["status"] == "unresolved"
    assert selected is versions[1 if document_id == "history" else 0]
    with pytest.raises(HTTPException) as missing:
        orders._resolve_order_document_version("O", "foreign")
    assert missing.value.status_code == 404
