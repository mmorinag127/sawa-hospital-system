import base64
import hashlib
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import prepare_hospital_production_settings as preparation


def test_embedded_settings_require_matching_bytes():
    raw = b'{"facilities":[]}'
    value = {"content_base64": base64.b64encode(raw).decode(),
             "sha256": hashlib.sha256(raw).hexdigest()}
    assert preparation.document(value) == {"facilities": []}
    with pytest.raises(ValueError, match="hash mismatch"):
        preparation.document({**value, "sha256": "0" * 64})


@pytest.mark.parametrize("value", [None, [], "gs://sawahospitalsystem-prod-templates/master.json"])
def test_settings_uri_is_not_silently_fetched(value):
    with pytest.raises(ValueError, match="embedded snapshot"):
        preparation.document(value)


@pytest.mark.parametrize("key", ["CI", "GITHUB_ACTIONS"])
def test_preparation_rejects_ci_before_any_io(monkeypatch, tmp_path, key):
    monkeypatch.setenv(key, "true")
    output = tmp_path / "output"
    with pytest.raises(ValueError, match="local preparation only"):
        preparation.prepare(tmp_path / "missing", output, "HEAD")
    assert not output.exists()
