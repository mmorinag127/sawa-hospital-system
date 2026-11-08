import base64
from copy import deepcopy
import hashlib
import json
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


@pytest.mark.parametrize('values', [[], ['staff', 'no_meat']])
def test_explicit_display_migration_is_nonmutating_and_idempotent(values):
    original = {'facility_id': 'synthetic', 'areas': [], 'unknown': {'preserved': True}}
    before = deepcopy(original)
    result, changes = preparation.apply_label_display_settings(original, {'synthetic': values})
    assert result == {**original, 'daily_label_comparable_diet_types': values}
    assert changes == [{'op': 'add', 'path': '/daily_label_comparable_diet_types', 'value': values}]
    assert original == before
    assert preparation.apply_label_display_settings(result, {'synthetic': values}) == (result, [])


@pytest.mark.parametrize('values', [None, 'staff', {}, [None], ['']])
def test_explicit_display_migration_rejects_invalid_values(values):
    with pytest.raises(ValueError, match='daily_label_comparable_diet_types_invalid'):
        preparation.apply_label_display_settings({'facility_id': 'synthetic'}, {'synthetic': values})


def test_explicit_display_migration_does_not_default_or_replace_authored_values():
    with pytest.raises(ValueError, match='explicit_label_display_settings_required'):
        preparation.apply_label_display_settings({'facility_id': 'synthetic'}, {})
    with pytest.raises(ValueError, match='existing_label_display_settings_conflict'):
        preparation.apply_label_display_settings({'facility_id': 'synthetic', 'daily_label_comparable_diet_types': ['staff']}, {'synthetic': []})


@pytest.mark.parametrize('value', [None, [], 'staff', 0])
def test_explicit_settings_file_rejects_nonobjects_before_database_work(tmp_path, value):
    snapshot = tmp_path / 'snapshot'
    snapshot.mkdir()
    (snapshot / 'source.json').write_text('{}')
    (snapshot / 'settings.json').write_text('{}')
    settings = tmp_path / 'labels.json'
    settings.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='explicit_label_display_settings_required'):
        preparation.prepare(snapshot, tmp_path / 'output', 'HEAD', settings)
    assert not (tmp_path / 'output').exists()
