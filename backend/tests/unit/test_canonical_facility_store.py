"""Source-only addition for H1; execution is deferred by the task contract."""

from copy import deepcopy
import json
from pathlib import Path
import subprocess

from google.api_core.exceptions import PreconditionFailed
import pytest

from src.services import canonical_facility_store as store, config_service


@pytest.fixture
def local_master(tmp_path, monkeypatch):
    master = {"unknown_root": {"zero": 0, "unset": None}, "facilities": [
        {"facility_id": "FAC_TEST", "facility_name": "Original", "unknown_facility": {"value": False}},
    ]}
    path = tmp_path / "master.json"
    path.write_text(json.dumps(master), encoding="utf-8")
    monkeypatch.delenv("K_SERVICE", raising=False)
    monkeypatch.delenv("FACILITY_MASTER_URI", raising=False)
    monkeypatch.setenv("FACILITY_MASTER_PATH", str(path))
    return path, master


def test_local_cas_preserves_values_and_own_revision_for_second_save(local_master):
    path, original = local_master
    first = store.read_snapshot()
    next_master = deepcopy(first.master)
    next_master["facilities"][0]["facility_name"] = "Edited"
    saved = store.update_snapshot(first.revision, lambda _current: next_master)
    assert saved.master["unknown_root"] == original["unknown_root"]
    assert saved.master["facilities"][0]["unknown_facility"] == original["facilities"][0]["unknown_facility"]
    assert saved.revision != first.revision
    assert store.read_snapshot().revision == saved.revision
    second = store.update_snapshot(saved.revision, lambda current: current)
    assert second.master == saved.master
    assert second.revision != saved.revision
    with pytest.raises(store.CanonicalStoreError) as caught:
        store.update_snapshot(first.revision, lambda _current: original)
    assert caught.value.status_code == 409
    assert json.loads(path.read_text()) == second.master


def test_missing_revision_never_dispatches_transform(local_master):
    path, _ = local_master
    before = path.read_bytes()
    def forbidden(_current):
        pytest.fail("missing revision dispatched a mutation")
    with pytest.raises(store.CanonicalStoreError) as caught:
        store.update_snapshot(None, forbidden)
    assert caught.value.status_code == 428
    assert path.read_bytes() == before


def test_cloud_run_cannot_use_local_source(local_master, monkeypatch):
    path, _ = local_master
    monkeypatch.setenv("K_SERVICE", "worker-test")
    with pytest.raises(store.CanonicalStoreError, match="facility_master_gcs_uri_required"):
        store.read_snapshot()
    assert not path.with_name(path.name + ".lock").exists()


def test_missing_configuration_has_no_packaged_default(monkeypatch):
    for key in ("K_SERVICE", "FACILITY_MASTER_URI", "FACILITY_MASTER_PATH"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(store.CanonicalStoreError, match="facility_master_source_required"):
        store.read_snapshot()


def test_atomic_replace_failure_keeps_old_json(local_master, monkeypatch):
    path, _ = local_master
    first = store.read_snapshot()
    before = path.read_bytes()
    def fail_replace(*_args):
        raise OSError("replace failed")
    monkeypatch.setattr(store.os, "replace", fail_replace)
    with pytest.raises(store.CanonicalStoreError):
        store.update_snapshot(first.revision, lambda current: {**current, "new_key": True})
    assert path.read_bytes() == before


@pytest.mark.parametrize("data", [
    b'{"facilities":[],"facilities":[]}',
    b'{"facilities":[{"facility_id":"A"},{"facility_id":"A"}]}',
    b'{"facilities":[],"value":NaN}',
])
def test_invalid_json_is_blocked_without_repair(local_master, data):
    path, _ = local_master
    path.write_bytes(data)
    with pytest.raises(store.CanonicalStoreError, match="facility_master_json_invalid"):
        store.read_snapshot()
    assert path.read_bytes() == data


def test_config_reads_one_snapshot_without_db_or_data_cache(local_master, monkeypatch):
    from test_modernization_explicit_ocr_quantity_contract import authored_template

    path, original = local_master
    authored = deepcopy(original)
    authored["facilities"][0]["fax_template"] = authored_template()
    path.write_text(json.dumps(authored), encoding="utf-8")
    calls = []
    real_read = store.read_snapshot
    def read():
        calls.append(True)
        return real_read()
    monkeypatch.setattr(store, "read_snapshot", read)
    resolved = config_service.get_facility_config("FAC_TEST")
    assert resolved["facility_name"] == "Original"
    assert len(calls) == 1
    first = real_read()
    changed = deepcopy(first.master)
    changed["facilities"][0]["facility_name"] = "New canonical value"
    store.update_snapshot(first.revision, lambda _current: changed)
    assert config_service.get_facility_by_id("FAC_TEST")["facility_name"] == "New canonical value"


@pytest.fixture
def gcs_master(monkeypatch):
    state = {"generation": 10, "master": {"facilities": []}, "uploads": [], "downloads": [],
             "race_read": False, "race_write": False, "write_after_save": False, "reloads": 0}
    class Blob:
        generation = None
        def reload(self, *, retry):
            assert retry is None
            state["reloads"] += 1
            self.generation = state["generation"]
        def download_as_bytes(self, *, if_generation_match, retry):
            assert retry is None
            state["downloads"].append(if_generation_match)
            if state["race_read"]:
                state["generation"] += 1
            if if_generation_match != state["generation"]:
                raise PreconditionFailed("changed")
            return json.dumps(state["master"]).encode()
        def upload_from_string(self, data, *, content_type, if_generation_match, retry):
            assert retry is None
            assert content_type == "application/json"
            state["uploads"].append(if_generation_match)
            if state["race_write"]:
                state["generation"] += 1
            if if_generation_match != state["generation"]:
                raise PreconditionFailed("changed")
            state["generation"] += 1
            self.generation = state["generation"]
            state["master"] = json.loads(data)
            if state["write_after_save"]:
                state["generation"] += 1
                state["master"] = {"facilities": [], "other_writer": True}
    class Bucket:
        def blob(self, name):
            assert name == "canonical.json"
            return Blob()
    class Client:
        def bucket(self, name):
            assert name == "test-bucket"
            return Bucket()
    class Response:
        status_code = 200
        def __init__(self):
            import base64
            import google_crc32c

            generation = state["generation"]
            data = json.dumps(state["master"]).encode()
            self.raw = type("Body", (), {"read": lambda _self, **kwargs: data})()
            self.headers = {"X-Goog-Generation": str(generation), "X-Goog-Hash":
                "crc32c=" + base64.b64encode(google_crc32c.Checksum(data).digest()).decode()}
            state["downloads"].append(generation)
            if state["race_read"]:
                state["generation"] += 1
                state["master"] = {"facilities": [], "other_writer": True}
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def raise_for_status(self):
            pass
    class ReadSession:
        def get(self, endpoint, **kwargs):
            assert endpoint == "https://storage.googleapis.com/download/storage/v1/b/test-bucket/o/canonical.json"
            assert kwargs == {"params": {"alt": "media"}, "headers": {"Accept-Encoding": "gzip"},
                              "timeout": 30, "stream": True, "allow_redirects": False}
            return Response()
    monkeypatch.setenv("FACILITY_MASTER_URI", "gs://test-bucket/canonical.json")
    monkeypatch.setenv("K_SERVICE", "worker-test")
    monkeypatch.setattr(store, "_get_thread_gcs_client", lambda: Client())
    monkeypatch.setattr(store, "_get_thread_gcs_read_session", lambda: ReadSession())
    return state


@pytest.mark.parametrize("problem,expected", [
    ("generation", "facility_master_generation_missing"),
    ("checksum", "facility_master_checksum_mismatch"),
    ("truncated", "facility_master_checksum_mismatch"),
    ("redirect", "facility_master_storage_unavailable"),
])
def test_gcs_atomic_read_rejects_unbound_or_corrupt_response(monkeypatch, problem, expected):
    import base64
    from contextlib import nullcontext
    from types import SimpleNamespace
    import google_crc32c

    body = b'{"facilities": []}'
    headers = {"X-Goog-Generation": "10", "X-Goog-Hash":
        "crc32c=" + base64.b64encode(google_crc32c.Checksum(body).digest()).decode()}
    if problem == "generation":
        headers.pop("X-Goog-Generation")
    elif problem == "checksum":
        headers.pop("X-Goog-Hash")
    elif problem == "truncated":
        body = body[:-1]
    response = SimpleNamespace(headers=headers, status_code=302 if problem == "redirect" else 200,
        raw=SimpleNamespace(read=lambda **kwargs: body), raise_for_status=lambda: None)
    monkeypatch.setattr(store, "_get_thread_gcs_read_session",
                        lambda: SimpleNamespace(get=lambda *args, **kwargs: nullcontext(response)))
    with pytest.raises(store.CanonicalStoreError, match=expected):
        store._read_gcs("gs://test-bucket/canonical.json")


def test_gcs_read_binds_same_response_bytes_and_generation_across_later_write(gcs_master):
    gcs_master["race_read"] = True
    snapshot = store.read_snapshot()
    assert snapshot.revision.endswith(":10")
    assert snapshot.master == {"facilities": []}
    assert gcs_master["generation"] == 11
    assert gcs_master["master"] == {"facilities": [], "other_writer": True}
    assert gcs_master["downloads"] == [10]
    assert gcs_master["reloads"] == 0
    assert gcs_master["uploads"] == []
    with pytest.raises(store.CanonicalStoreError) as caught:
        store.update_snapshot(snapshot.revision, lambda current: {**current, "own": True})
    assert caught.value.status_code == 409
    assert gcs_master["uploads"] == []


def test_gcs_write_cas_race_does_not_retry_or_choose_latest(gcs_master):
    first = store.read_snapshot()
    gcs_master["race_write"] = True
    with pytest.raises(store.CanonicalStoreError) as caught:
        store.update_snapshot(first.revision, lambda current: {**current, "own": True})
    assert caught.value.status_code == 409
    assert gcs_master["uploads"] == [10]
    assert gcs_master["master"] == {"facilities": []}


def test_gcs_success_returns_own_bytes_and_generation_not_later_get(gcs_master):
    first = store.read_snapshot()
    gcs_master["write_after_save"] = True
    own = {"facilities": [], "own": {"zero": 0, "null": None}}
    saved = store.update_snapshot(first.revision, lambda _current: own)
    assert saved.master == own
    assert saved.revision.endswith(":11")
    assert gcs_master["generation"] == 12
    assert gcs_master["reloads"] == 0
    assert gcs_master["uploads"] == [10]


def test_geometry_config_edit_keeps_exact_authored_column_identity():
    from src.services.facility_service import _contains_template_definition_change

    current = {"fax_template_id": "T", "fax_template_override": {
        "columns": [{"name": "quantity", "source_index": 0}],
        "main_ocr_row_fields": ["quantity"], "columns_authoritative": True,
        "table_box": [0, 0, 1, 1], "unknown": {"zero": 0, "unset": None},
    }}
    edited = deepcopy(current)
    edited["fax_template_override"]["table_box"] = [0.1, 0.2, 0.9, 0.8]
    assert not _contains_template_definition_change(current, edited)
    assert edited["fax_template_override"]["unknown"] == current["fax_template_override"]["unknown"]
    for key in ("columns", "main_ocr_row_fields", "columns_authoritative"):
        missing = deepcopy(edited)
        del missing["fax_template_override"][key]
        assert _contains_template_definition_change(current, missing)
    stale_column = deepcopy(edited)
    stale_column["fax_template_override"]["columns"][0]["source_index"] = 1
    assert _contains_template_definition_change(current, stale_column)
    new_template = deepcopy(edited)
    new_template["fax_template_id"] = "OTHER"
    assert _contains_template_definition_change(current, new_template)


def test_geometry_without_authored_columns_does_not_invent_them():
    from src.services.facility_service import _contains_template_definition_change

    edited = {"fax_template_override": {"table_box": [0, 0, 1, 1]}}
    assert not _contains_template_definition_change({}, edited)
    edited["fax_template_override"]["columns"] = []
    assert _contains_template_definition_change({}, edited)


def test_frontend_field_and_area_serializers_preserve_untouched_values():
    root = Path(__file__).resolve().parents[3]
    # Exercise the actual pure TypeScript source, without mounting pages or mocking its serialization.
    script = r"""
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const ts = require('typescript');
function load(path, start, end, expose) {
  const source = fs.readFileSync(path, 'utf8');
  const first = source.indexOf(start), last = source.indexOf(end, first);
  assert(first >= 0 && last > first);
  const code = source.slice(first, last).replace(/export const/g, 'const') + '\n' + expose;
  const context = { exports: {} };
  vm.runInNewContext(ts.transpileModule(code, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, context);
  return context.exports;
}
const config = load('src/pages/facilities/[id].tsx', 'const parseStringList', 'const validateConfigSave',
  'exports.merge = mergeConfigDraft; exports.draft = configDraft;');
const original = { openai_ocr_prompt: null, gemini_ocr_enabled: null, hakodate_header_rows: 0,
  menu_override_tags: [null, 0, ' A '], unknown: { n: null, zero: 0 }, main_ocr_provider: 'old' };
const draft = config.draft(original);
const result = config.merge(original, { ...draft, main_ocr_provider: 'new' }, draft);
assert.equal(JSON.stringify(result), JSON.stringify({ ...original, main_ocr_provider: 'new' }));
assert.equal(JSON.stringify(original.main_ocr_provider), '"old"');
const rawEdit = { ...original, openai_ocr_prompt: 'JSON edit', unknown: null };
assert.equal(JSON.stringify(config.merge(rawEdit, draft, draft)), JSON.stringify(rawEdit));
const area = load('src/pages/facility-master.tsx', 'export const patchArea', 'const readFaxOverride', 'exports.patch = patchArea;');
const facility = load('src/pages/facility-master.tsx', 'const readString =', 'const readStringList =',
  'exports.merge = mergeFacilityDraft; exports.draft = facilityDraft;');
const entry = { facility_id: 'F', facility_name: 'Original', address: null, phone: 0, unknown: null,
  areas: [{ name: 'No ID' }, null, 0] };
const values = facility.draft(entry);
assert.equal(JSON.stringify(facility.merge(entry, { ...values, facility_name: 'Edited' }, entry)),
  JSON.stringify({ ...entry, facility_name: 'Edited' }));
const specialized = { ...entry, address: 'Explicit JSON edit' };
assert.equal(JSON.stringify(facility.merge(specialized, values, entry)), JSON.stringify(specialized));
const rows = [{ name: 'No ID', unknown: null }, { area_id: null, name: null, zero: 0 }, 'Legacy', null, 0];
const changed = rows.map((row, index) => index === 0 ? area.patch(row, { name: 'Edited' }) : row);
assert.equal(JSON.stringify(changed), JSON.stringify([{ name: 'Edited', unknown: null }, ...rows.slice(1)]));
assert(!Object.hasOwn(changed[0], 'id') && !Object.hasOwn(changed[0], 'area_id'));
assert.equal(area.patch('Legacy', { name: 'Renamed' }), 'Renamed');
assert.equal(JSON.stringify(area.patch({ label: 'old', id: null, zero: 0 }, { name: 'new' })),
  JSON.stringify({ label: 'new', id: null, zero: 0 }));
assert.equal(JSON.stringify(area.patch({ name: 'No ID' }, { id: 'EXPLICIT' })),
  JSON.stringify({ name: 'No ID', area_id: 'EXPLICIT' }));
"""
    subprocess.run(["node", "-e", script], cwd=root / "frontend", check=True, capture_output=True, text=True)


def test_existing_area_projection_never_generates_identity():
    from src.services import facility_service

    areas = [{"area_id": "A", "name": " Name ", "unknown": None, "zero": 0}]
    before = deepcopy(areas)
    assert facility_service._projection_areas(areas) == [{"id": "A", "name": " Name "}]
    assert areas == before
    for existing in [[{"name": "No ID"}], [{"id": None, "name": "No ID"}], ["Legacy"], [None], [0]]:
        original = deepcopy(existing)
        with pytest.raises(store.CanonicalStoreError, match="facility_area_projection_identity_missing"):
            facility_service._projection_areas(existing)
        assert existing == original
    with pytest.raises(store.CanonicalStoreError, match="facility_area_projection_identity_ambiguous"):
        facility_service._projection_areas([{"id": "A", "area_id": "B", "name": "Ambiguous"}])


def test_new_identity_creation_is_not_existing_identity_reconstruction(monkeypatch):
    from types import SimpleNamespace
    from src.services import facility_service

    monkeypatch.setattr(facility_service, "uuid4", lambda: SimpleNamespace(hex="123456abcdef"))
    request = [{"name": "New", "unknown": None, "zero": 0}, {"id": None, "name": "Explicit null"}]
    before = deepcopy(request)
    created = facility_service._new_area_payload(request)
    assert request == before
    assert created[0] == {"name": "New", "unknown": None, "zero": 0, "id": "ARE123456"}
    assert created[1] == request[1]


def test_facility_update_preserves_raw_areas_and_siblings(monkeypatch):
    from src.services import facility_master_service, facility_service

    original = {"root_unknown": None, "facilities": [
        {"facility_id": "F", "facility_name": "Old", "areas": [
            {"name": "No ID", "unknown": None}, {"id": None, "zero": 0}, "Legacy", None, 0],
         "config_null": None, "config_zero": 0},
        {"facility_id": "S", "facility_name": "Sibling", "areas": None},
    ]}
    receipts = []
    def mutate(revision, transform):
        assert revision == "fixed"
        result = transform(deepcopy(original))
        receipts.append(result)
        return {"facility_master": result, "revision": "own", "projection": {"status": "reconcile_required", "revision": "own"}}
    monkeypatch.setattr(facility_master_service, "mutate_master", mutate)
    monkeypatch.setattr(facility_service, "_command_response", lambda saved, _id: {
        "facility": {"name": saved["facility_master"]["facilities"][0]["facility_name"],
                     "areas": saved["facility_master"]["facilities"][0]["areas"]}})
    monkeypatch.setattr(facility_service, "_record_command_event", lambda response, *_args, **_kwargs: response)
    monkeypatch.setattr(facility_service, "facility_response", lambda *_args: {})
    facility_service.update_facility("F", "New", expected_revision="fixed")
    expected = deepcopy(original)
    expected["facilities"][0]["facility_name"] = "New"
    assert receipts[-1] == expected
    selected = deepcopy(original["facilities"][0]["areas"])
    selected[0]["name"] = "Edited"
    facility_service.update_facility("F", None, selected, expected_revision="fixed")
    expected = deepcopy(original)
    expected["facilities"][0]["areas"] = selected
    assert receipts[-1] == expected
    assert "id" not in receipts[-1]["facilities"][0]["areas"][0]
    facility_service.update_facility("F", None, None, expected_revision="fixed")
    expected = deepcopy(original)
    expected["facilities"][0]["areas"] = None
    assert receipts[-1] == expected
