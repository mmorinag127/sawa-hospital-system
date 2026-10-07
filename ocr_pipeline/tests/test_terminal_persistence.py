import json
import types
import unittest
from copy import deepcopy
from contextlib import ExitStack
from unittest import mock

from test_main import pipeline_main as producer
from terminal_store_fakes import Blob, Stores, PreconditionFailed, install_transport_exceptions


class TerminalPersistenceTests(unittest.TestCase):
    def test_replacement_between_output_read_and_write_cannot_be_overwritten(self):
        for status in ("done", "failed"):
            stores = Stores()
            with self.subTest(status=status), self.environment(stores):
                source, result = self.owned_result(stores, status)
                original = Blob.upload_from_string
                def replace(blob, value, **kwargs):
                    if blob.name == stores.output:
                        original(blob, json.dumps({"job_id": "foreign"}))
                    return original(blob, value, **kwargs)
                with mock.patch.object(Blob, "upload_from_string", replace):
                    with self.assertRaises(producer.TerminalPersistenceIncomplete):
                        producer._complete_terminal(stores, "own-bucket", stores.name, "19", stores.output, source, result)
                self.assertEqual(json.loads(stores.objects[stores.output]), {"job_id": "foreign"})
                self.assertEqual(producer.handler()[1], 409)

    def test_final_claim_change_denies_done_failed_and_complete_replay_ack(self):
        for status in ("done", "failed"):
            for replay in (False, True):
                stores = Stores()
                with self.subTest(status=status, replay=replay), self.environment(stores):
                    source, result = self.owned_result(stores, status)
                    if replay:
                        producer._complete_terminal(stores, "own-bucket", stores.name, "19", stores.output, source, result)
                    original = Blob.download_as_bytes
                    def change_claim(blob, **kwargs):
                        value = original(blob, **kwargs)
                        if blob.name == stores.output and stores.job.get("status") == status:
                            stores.job["execution_claim"] = {"job_id": "replacement", "output_reference": source["output_reference"]}
                            stores.version += 1
                        return value
                    with mock.patch.object(Blob, "download_as_bytes", change_claim):
                        with self.assertRaises(producer.TerminalReceiptConflict):
                            producer._complete_terminal(stores, "own-bucket", stores.name, "19", stores.output, source, result)

    def environment(self, stores):
        stack = ExitStack()
        stack.enter_context(install_transport_exceptions())
        for name, value in {
            "db": stores, "gcs": stores, "INPUT_PREFIX": "input/", "OUTPUT_PREFIX": "output/",
            "request": types.SimpleNamespace(get_json=lambda **_: {}),
            "parse_gcs_event": lambda _: ("own-bucket", stores.name, "19"),
            "run_yomitoku": lambda **_: self.fail("redelivery must not recognize again"),
            "_run_template_classification": lambda **_: self.fail("redelivery must not calculate again"),
        }.items():
            stack.enter_context(mock.patch.object(producer, name, value))
        return stack

    def owned_result(self, stores, status):
        source = producer._original_pipeline_source("own-bucket", stores.name, "19")
        stores.output = producer._owned_output_name("output/", source)
        source["output_reference"] = producer.format_gcs_uri("own-bucket", stores.output)
        stores.create({"status": "running", "execution_claim": {"job_id": source["job_id"], "output_reference": source["output_reference"]}})
        return source, {**source, "status": status, "quantity": 0, "unknown": None,
                        "canonical_adoption_allowed": status == "done", "stage": "done" if status == "done" else "error"}

    def assert_dual(self, stores, result):
        self.assertEqual(json.loads(stores.objects[stores.output]), result)
        self.assertEqual(stores.job["status"], result["status"])
        self.assertEqual(stores.job["terminal_receipt"]["result_digest"], producer._terminal_digest(result))

    def test_both_terminal_statuses_partial_failures_repair_without_second_ocr(self):
        for status in ("done", "failed"):
            for failed_store in ("firestore", "output", "firestore_after_output", "verification"):
                with self.subTest(status=status, failed_store=failed_store):
                    stores = Stores()
                    with self.environment(stores):
                        source, result = self.owned_result(stores, status)
                        if failed_store == "firestore":
                            stores.fail_fs = True
                        elif failed_store == "firestore_after_output":
                            stores.fail_fs_terminal = True
                        elif failed_store == "output":
                            stores.fail_object = stores.output
                        else:
                            stores.fail_fs_read_after_output = True
                        with self.assertRaises(producer.TerminalPersistenceIncomplete):
                            producer._complete_terminal(stores, "own-bucket", stores.name, "19", stores.output, source, result)
                        if failed_store == "firestore_after_output":
                            # Durable partial-store fixture: only the exact immutable own intent may populate JSON.
                            own_intent = json.loads(stores.objects[stores.output + ".terminal-intent.json"])
                            stores.bucket("own-bucket").blob(stores.output).upload_from_string(json.dumps(own_intent["result"]))
                            self.assertEqual(stores.job["status"], "terminal_persisting")
                        _, code = producer.handler()
                        self.assertEqual(code, 200)
                        self.assert_dual(stores, result)
                        write_count = len(stores.writes)
                        _, code = producer.handler()
                        self.assertEqual(code, 200)
                        self.assert_dual(stores, result)
                        self.assertEqual(len(stores.writes), write_count)

    def test_first_intent_store_failure_is_inspection_not_completed_or_ocr_retry(self):
        stores = Stores()
        with self.environment(stores):
            source, result = self.owned_result(stores, "failed")
            stores.fail_object = stores.output + ".terminal-intent.json"
            with self.assertRaises(producer.TerminalPersistenceIncomplete):
                producer._complete_terminal(stores, "own-bucket", stores.name, "19", stores.output, source, result)
            _, code = producer.handler()
            self.assertEqual(code, 409)
            self.assertNotIn(stores.output, stores.objects)

    def test_exact_receipt_required_on_replay(self):
        for change in ("generation", "pdf", "config", "owner", "result", "foreign_firestore", "foreign_output"):
            with self.subTest(change=change):
                stores = Stores()
                with self.environment(stores):
                    source, result = self.owned_result(stores, "done")
                    producer._complete_terminal(stores, "own-bucket", stores.name, "19", stores.output, source, result)
                    terminal_before = deepcopy(stores.objects[stores.output])
                    if change == "pdf":
                        stores.objects[stores.name] = b"same-name-different-PDF"
                    elif change == "config":
                        stores.objects[stores.name + ".configured.json"] = b"different-config"
                    elif change == "owner":
                        owner = json.loads(stores.metadata["execution_owner"])
                        owner["workflow_attempt_id"] = "other-attempt"
                        stores.metadata["execution_owner"] = json.dumps(owner)
                    elif change == "foreign_firestore":
                        stores.job["terminal_receipt"]["result_digest"] = "foreign"
                    elif change == "foreign_output":
                        foreign = json.loads(stores.objects[stores.output])
                        foreign["job_id"] = "other-generation"
                        stores.objects[stores.output] = json.dumps(foreign).encode()
                        terminal_before = stores.objects[stores.output]
                    elif change == "result":
                        intent = json.loads(stores.objects[stores.output + ".terminal-intent.json"])
                        intent["result"]["quantity"] = 99
                        stores.objects[stores.output + ".terminal-intent.json"] = json.dumps(intent).encode()
                    with self.assertRaises((producer.TerminalPersistenceIncomplete, ValueError, PreconditionFailed)):
                        producer._replay_terminal(stores, "own-bucket", stores.name, "20" if change == "generation" else "19", stores.output)
                    self.assertEqual(stores.objects[stores.output], terminal_before)
                    if change == "generation":
                        with mock.patch.object(producer, "parse_gcs_event", return_value=("own-bucket", stores.name, "20")):
                            _, code = producer.handler()
                    else:
                        _, code = producer.handler()
                    self.assertEqual(code, 409)
                    self.assertEqual(stores.objects[stores.output], terminal_before)

    def test_active_duplicate_waits_unknown_existing_requires_inspection(self):
        stores = Stores()
        with self.environment(stores):
            source, _ = self.owned_result(stores, "done")
            with producer._ACTIVE_PRODUCER_LOCK:
                producer._ACTIVE_PRODUCER_JOBS.add(source["job_id"])
            try:
                _, code = producer.handler()
                self.assertEqual(code, 202)
            finally:
                with producer._ACTIVE_PRODUCER_LOCK:
                    producer._ACTIVE_PRODUCER_JOBS.discard(source["job_id"])
            _, code = producer.handler()
            self.assertEqual(code, 409)
            self.assertNotIn(stores.output, stores.objects)

    def test_atomic_creation_race_waits_for_actual_active_owner_without_ocr(self):
        stores = Stores()
        with self.environment(stores):
            source, _ = self.owned_result(stores, "done")
            original_get = stores.get
            reads = []
            def raced_get():
                reads.append(True)
                if len(reads) == 1:
                    return types.SimpleNamespace(exists=False)
                return original_get()
            with producer._ACTIVE_PRODUCER_LOCK:
                producer._ACTIVE_PRODUCER_JOBS.add(source["job_id"])
            try:
                with mock.patch.object(stores, "get", side_effect=raced_get):
                    _, code = producer.handler()
                self.assertEqual(code, 202)
                self.assertNotIn(stores.output, stores.objects)
            finally:
                with producer._ACTIVE_PRODUCER_LOCK:
                    producer._ACTIVE_PRODUCER_JOBS.discard(source["job_id"])

    def test_firestore_owner_switch_between_read_and_write_fails_real_precondition(self):
        stores = Stores()
        with self.environment(stores):
            source, result = self.owned_result(stores, "failed")
            update = stores.update
            injected = []
            def switch_owner(value, option=None):
                if not injected:
                    injected.append(True)
                    stores.job["execution_claim"] = {"job_id": "foreign-attempt", "output_reference": source["output_reference"]}
                    stores.version += 1
                return update(value, option=option)
            with mock.patch.object(stores, "update", side_effect=switch_owner):
                with self.assertRaises(producer.TerminalPersistenceIncomplete):
                    producer._complete_terminal(stores, "own-bucket", stores.name, "19", stores.output, source, result)
            _, status = producer.handler()
            self.assertEqual(status, 409)
            self.assertEqual(stores.job["execution_claim"]["job_id"], "foreign-attempt")
            self.assertNotIn(stores.output, stores.objects)
