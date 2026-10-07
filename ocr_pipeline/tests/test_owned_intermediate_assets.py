import json
from copy import deepcopy
from contextlib import contextmanager
from unittest import mock
import types

from terminal_store_fakes import Blob, Stores, PreconditionFailed
from test_terminal_persistence import TerminalPersistenceTests, producer


class OwnedIntermediateAssetTests(TerminalPersistenceTests):
    def test_real_handler_keeps_slash_underscore_neighbors_and_repairs_each_original_owner(self):
        storage = Stores()
        names = ("input/a/b.pdf", "input/a_b.pdf")
        configured_bytes = storage.objects[storage.name + ".configured.json"]
        for name in names:
            storage.objects[name] = storage.body
            storage.objects[name + ".configured.json"] = configured_bytes
            storage.initial_generations[name] = 19
            storage.initial_generations[name + ".configured.json"] = 17
            metadata = deepcopy(storage.metadata)
            receipt = json.loads(metadata["configured_input_receipt"])
            receipt["object_name"] = name + ".configured.json"
            metadata["configured_input_receipt"] = json.dumps(receipt)
            storage.object_metadata[name] = metadata

        class Firestore:
            def __init__(self):
                self.documents = {}
                self.lookups = []
            def collection(self, _):
                return self
            def document(self, key):
                self.lookups.append(key)
                if key not in self.documents:
                    self.documents[key] = Stores()
                return self.documents[key]
            def write_option(self, *, last_update_time):
                return last_update_time

        firestore = Firestore()
        original_parser = producer.parse_gcs_event
        sources = []
        with self.environment(storage), mock.patch.object(producer, "db", firestore), mock.patch.object(producer, "parse_gcs_event", original_parser):
            for index, name in enumerate(names):
                source = producer._terminal_source("own-bucket", name, "19", "")
                output = producer._owned_output_name("output/", source)
                source["output_reference"] = f"gs://own-bucket/{output}"
                reference = firestore.document(producer._safe_job_id(source))
                reference.create({"status": "running", "execution_claim": {"job_id": source["job_id"], "output_reference": source["output_reference"]}})
                result = {**source, "status": "done", "stage": "done", "quantity": 0, "unknown": None, "canonical_adoption_allowed": True}
                if index == 0:
                    reference.fail_fs_terminal = True
                    with self.assertRaises(producer.TerminalPersistenceIncomplete):
                        producer._complete_terminal(reference, "own-bucket", name, "19", output, source, result)
                else:
                    producer._complete_terminal(reference, "own-bucket", name, "19", output, source, result)
                sources.append((source, output, result))
            self.assertEqual(len(firestore.documents), 2)
            first_key = producer._safe_job_id(sources[0][0])
            historical_reference = firestore.documents.pop(first_key)
            firestore.documents["historical-punctuation-key"] = historical_reference
            historical_snapshot = deepcopy(historical_reference.job)
            for index in (1, 0, 1, 0):
                source, output, result = sources[index]
                event = {"bucket": "own-bucket", "name": names[index], "generation": "19"}
                with mock.patch.object(producer, "request", types.SimpleNamespace(get_json=lambda **_: event)):
                    response, status = producer.handler()
                self.assertEqual(status, 200)
                ack = response.get_json() if hasattr(response, "get_json") else response["args"][0]
                self.assertEqual(ack["terminal_receipt"]["source"], source)
                self.assertEqual(json.loads(storage.objects[output]), result)
                reference = firestore.documents[producer._safe_job_id(source)]
                self.assertEqual(reference.job["terminal_receipt"]["source"], source)
                self.assertEqual(reference.job["status"], "done")
            self.assertNotEqual(producer._safe_job_id(sources[0][0]), producer._safe_job_id(sources[1][0]))
            self.assertEqual(firestore.lookups[-4:], [producer._safe_job_id(sources[index][0]) for index in (1, 0, 1, 0)])
            self.assertNotIn("historical-punctuation-key", firestore.lookups)
            self.assertEqual(historical_reference.job, historical_snapshot)
            source, output, result = sources[1]
            firestore.documents.pop(producer._safe_job_id(source))
            storage.objects.pop(output + ".terminal-intent.json")
            event = {"bucket": "own-bucket", "name": names[1], "generation": "19"}
            with mock.patch.object(producer, "request", types.SimpleNamespace(get_json=lambda **_: event)):
                response, status = producer.handler()
            self.assertEqual(status, 409)
            blocked = response.get_json() if hasattr(response, "get_json") else response["args"][0]
            self.assertEqual(blocked["error"], "output_original_claim_missing")
            self.assertEqual(json.loads(storage.objects[output]), result)
            self.assertIsNone(firestore.documents[producer._safe_job_id(source)].job)

    @contextmanager
    def owner(self, stores):
        with self.environment(stores):
            source, result = self.owned_result(stores, "done")
            token = producer._OUTPUT_OWNER.set((source, stores))
            try:
                yield source, result
            finally:
                producer._OUTPUT_OWNER.reset(token)

    def partial(self, stores, source):
        producer._write_output_partial(
            bucket="own-bucket", object_name=stores.output,
            job_id=source["job_id"], status="running", stage="recognition",
            input_reference=source["input_reference"],
            output_reference=source["output_reference"],
            payload={"quantity": 0, "unknown": None},
        )

    def test_partial_then_terminal_duplicate_repairs_original_intent(self):
        stores = Stores()
        with self.owner(stores) as (source, result):
            self.partial(stores, source)
            partial = json.loads(stores.objects[stores.output])
            self.assertEqual({key: partial[key] for key in source}, source)
            self.assertEqual(partial["quantity"], 0)
            self.assertIsNone(partial["unknown"])
            producer._complete_terminal(stores, "own-bucket", stores.name, "19", stores.output, source, result)
            self.assertEqual(producer.handler()[1], 200)
            self.assert_dual(stores, result)
            with self.assertRaisesRegex(producer.TerminalReceiptConflict, "terminal_replay_required"):
                self.partial(stores, source)

    def test_intermediate_exact_generation_race_keeps_replacement(self):
        stores = Stores()
        with self.owner(stores) as (source, _):
            self.partial(stores, source)
            original = Blob.upload_from_string
            def replace(blob, value, **kwargs):
                if blob.name == stores.output:
                    original(blob, json.dumps({"foreign": True}))
                return original(blob, value, **kwargs)
            with mock.patch.object(Blob, "upload_from_string", replace):
                with self.assertRaises(PreconditionFailed):
                    self.partial(stores, source)
            self.assertEqual(json.loads(stores.objects[stores.output]), {"foreign": True})

    def test_changed_claim_and_historical_unowned_output_stop(self):
        for foreign_claim in (False, True):
            stores = Stores()
            with self.subTest(foreign_claim=foreign_claim), self.owner(stores) as (source, _):
                if foreign_claim:
                    stores.job["execution_claim"]["job_id"] = "replacement"
                    stores.version += 1
                else:
                    stores.bucket("own-bucket").blob(stores.output).upload_from_string("{}")
                before = deepcopy(stores.objects)
                with self.assertRaises(producer.TerminalReceiptConflict):
                    self.partial(stores, source)
                self.assertEqual(stores.objects, before)

    def test_complete_uri_generation_and_execution_define_namespace(self):
        stores = Stores()
        with self.owner(stores) as (source, _):
            names = {stores.output}
            for key, replacement in (
                ("input_reference", "gs://own-bucket/input/other/own.pdf"),
                ("input_generation", "20"),
                ("execution_owner", {**source["execution_owner"], "workflow_attempt_id": "other-attempt"}),
                ("configured_input_sha256", "0" * 64),
            ):
                changed = {**source, key: replacement}
                names.add(producer._owned_output_name("output/", changed))
            self.assertEqual(len(names), 5)

    def test_same_basename_and_generation_in_distinct_input_directories_publish_distinct_assets(self):
        outputs, assets = [], []
        for directory in ("first", "second"):
            stores = Stores()
            old_name = stores.name
            stores.name = f"input/{directory}/own.pdf"
            stores.objects[stores.name] = stores.objects.pop(old_name)
            stores.objects[stores.name + ".configured.json"] = stores.objects.pop(old_name + ".configured.json")
            configured_receipt = json.loads(stores.metadata["configured_input_receipt"])
            configured_receipt["object_name"] = stores.name + ".configured.json"
            stores.metadata["configured_input_receipt"] = json.dumps(configured_receipt)
            with self.owner(stores) as (source, result):
                self.assertEqual(source["input_generation"], "19")
                self.partial(stores, source)
                name = stores.output.rsplit("/", 1)[0] + "/assets/own.md"
                result["markdown_uri"] = producer._upload_text("own-bucket", name, directory, "text/markdown")
                producer._complete_terminal(stores, "own-bucket", stores.name, "19", stores.output, source, result)
                outputs.append(source["output_reference"])
                assets.append(result["markdown_uri"])
                self.assert_dual(stores, result)
        self.assertNotEqual(outputs[0], outputs[1])
        self.assertNotEqual(assets[0], assets[1])

    def test_assets_are_immutable_pinned_and_identical_replay_is_allowed(self):
        stores = Stores()
        with self.owner(stores) as (source, result):
            name = stores.output.rsplit("/", 1)[0] + "/assets/own_overlay.png"
            uri = producer._upload_bytes("own-bucket", name, b"actual-overlay", "image/png")
            self.assertEqual(producer._upload_bytes("own-bucket", name, b"actual-overlay", "image/png"), uri)
            with self.assertRaisesRegex(producer.TerminalReceiptConflict, "immutable_artifact_conflict"):
                producer._upload_bytes("own-bucket", name, b"changed-overlay", "image/png")
            result["overlay_uri"] = uri
            producer._complete_terminal(stores, "own-bucket", stores.name, "19", stores.output, source, result)
            # External transport mutation cannot change bytes behind the saved generation reference.
            stores.bucket("own-bucket").blob(name).upload_from_string(b"other-generation")
            generation = int(uri.split("?generation=", 1)[1])
            self.assertEqual(stores.bucket("own-bucket").blob(name, generation=generation).download_as_bytes(if_generation_match=generation), b"actual-overlay")
            self.assertEqual(json.loads(stores.objects[stores.output])["overlay_uri"], uri)

    def test_foreign_asset_namespace_is_blocked_before_upload(self):
        stores = Stores()
        with self.owner(stores):
            with self.assertRaisesRegex(producer.TerminalReceiptConflict, "artifact_owner_namespace_mismatch"):
                producer._upload_text("own-bucket", "output/legacy/own.md", "actual text", "text/markdown")
            self.assertNotIn("output/legacy/own.md", stores.objects)

    def test_full_original_source_comparison_is_typed_not_python_numeric_equality(self):
        stores = Stores()
        with self.owner(stores) as (source, _):
            changed = deepcopy(source)
            changed["execution_owner"]["workflow_revision"] = True
            original = deepcopy(source)
            original["execution_owner"]["workflow_revision"] = 1
            self.assertFalse(producer._same_original_source(changed, original))
            self.assertFalse(producer._same_original_source({}, source))
            with self.assertRaisesRegex(producer.TerminalReceiptConflict, "partial_output_identity_mismatch"):
                producer._write_output_partial(
                    bucket="own-bucket", object_name=stores.output, job_id=source["job_id"],
                    status="running", stage="recognition", input_reference=source["input_reference"],
                    output_reference=source["output_reference"], payload={"execution_owner": changed["execution_owner"]},
                )
            self.assertNotIn(stores.output, stores.objects)
