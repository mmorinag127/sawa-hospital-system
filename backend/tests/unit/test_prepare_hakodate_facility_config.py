import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[3] / "scripts/prepare_hakodate_facility_config.py"
SPEC = importlib.util.spec_from_file_location("prepare_hakodate_facility_config", SCRIPT)
converter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(converter)


class PrepareConfigTests(unittest.TestCase):
    def setUp(self):
        self.config = {"facility_id": "FAC_TEST", "fax_template_id": "明示テンプレート",
                       "fax_template_ids": ["明示テンプレート"],
                       "fax_template": {"columns": [{"name": "quantity", "value": 6}], "page": 0},
                       "unrelated": {"values": [None, False, 0, "朝食", {"quantity": 8}]}}
        self.ids = {"facility_id": "FAC_TEST", "template_id": "明示テンプレート"}

    def test_adds_only_missing_id_and_preserves_all_other_values(self):
        before = copy.deepcopy(self.config)
        output, changes = converter.prepare_config(self.config, **self.ids)
        self.assertEqual(changes, [{"op": "add", "path": "/fax_template/template_id", "value": self.ids["template_id"]}])
        del output["fax_template"]["template_id"]
        self.assertEqual(output, before)
        output["fax_template"]["columns"][0]["value"] = 99
        self.assertEqual(self.config, before)

    def test_same_id_is_idempotent_and_deep_copied(self):
        first, _ = converter.prepare_config(self.config, **self.ids)
        second, changes = converter.prepare_config(first, **self.ids)
        self.assertEqual(second, first)
        self.assertEqual(changes, [])
        self.assertIsNot(second["fax_template"], first["fax_template"])

    def test_conflicting_or_null_nested_id_refused(self):
        for value in ("other", None, ""):
            with self.subTest(value=value):
                self.config["fax_template"]["template_id"] = value
                with self.assertRaisesRegex(ValueError, "nested_template_id_conflict"):
                    converter.prepare_config(self.config, **self.ids)

    def test_matching_top_level_id_preserved_without_input_mutation(self):
        self.config["template_id"] = self.ids["template_id"]
        before = copy.deepcopy(self.config)
        output, changes = converter.prepare_config(self.config, **self.ids)
        self.assertEqual(output["template_id"], self.ids["template_id"])
        self.assertEqual(changes, [{"op": "add", "path": "/fax_template/template_id", "value": self.ids["template_id"]}])
        again, repeat_changes = converter.prepare_config(output, **self.ids)
        self.assertEqual(again, output)
        self.assertEqual(repeat_changes, [])
        del output["fax_template"]["template_id"]
        self.assertEqual(output, before)
        self.assertEqual(self.config, before)

    def test_conflicting_null_or_empty_top_level_id_writes_nothing(self):
        for value in ("other", None, ""):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                self.config["template_id"] = value
                before = copy.deepcopy(self.config)
                with self.assertRaisesRegex(ValueError, "top_level_template_id_conflict"):
                    converter.prepare_config(self.config, **self.ids)
                self.assertEqual(self.config, before)
                args = self.files(directory)
                original = args["input_path"].read_bytes()
                with self.assertRaisesRegex(ValueError, "top_level_template_id_conflict"):
                    converter.convert_file(**args)
                self.assertEqual(args["input_path"].read_bytes(), original)
                self.assertFalse(args["output_path"].exists())
                self.assertFalse(args["report_path"].exists())

    def test_multiple_facilities_or_templates_refused(self):
        for value in ([self.config, self.config], {"facilities": [self.config]},
                      dict(self.config, fax_template_ids=[self.ids["template_id"], "other"])):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    converter.prepare_config(value, **self.ids)

    def test_explicit_identity_mismatches_refused(self):
        for ids in (dict(self.ids, facility_id="OTHER"), dict(self.ids, template_id="OTHER"),
                    dict(self.ids, facility_id=""), dict(self.ids, template_id=" padded ")):
            with self.subTest(ids=ids):
                with self.assertRaises(ValueError):
                    converter.prepare_config(self.config, **ids)

    def test_missing_template_schema_is_not_manufactured(self):
        del self.config["fax_template"]
        with self.assertRaisesRegex(ValueError, "existing_fax_template_object_required"):
            converter.prepare_config(self.config, **self.ids)

    def files(self, directory):
        source, output, report = (Path(directory) / name for name in ("input.json", "output.json", "report.json"))
        source.write_bytes(json.dumps(self.config, ensure_ascii=False).encode())
        return {"input_path": source, "output_path": output, "report_path": report,
                "expected_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), **self.ids}

    def test_hash_mismatch_writes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.files(directory)
            original = args["input_path"].read_bytes()
            args["expected_sha256"] = "0" * 64
            with self.assertRaisesRegex(ValueError, "input_sha256_mismatch"):
                converter.convert_file(**args)
            self.assertEqual(args["input_path"].read_bytes(), original)
            self.assertFalse(args["output_path"].exists())
            self.assertFalse(args["report_path"].exists())

    def test_cli_report_hashes_original_and_second_conversion_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.files(directory)
            original = args["input_path"].read_bytes()
            command = ["--input", str(args["input_path"]), "--output", str(args["output_path"]),
                       "--report", str(args["report_path"]), "--expected-sha256", args["expected_sha256"],
                       "--facility-id", self.ids["facility_id"], "--template-id", self.ids["template_id"]]
            self.assertEqual(converter.main(command), 0)
            audit = json.loads(args["report_path"].read_bytes())
            self.assertEqual(audit["input_sha256"], args["expected_sha256"])
            self.assertEqual(audit["output_sha256"], hashlib.sha256(args["output_path"].read_bytes()).hexdigest())
            self.assertEqual(args["input_path"].read_bytes(), original)
            second = dict(args, input_path=args["output_path"], output_path=Path(directory) / "second.json",
                          report_path=Path(directory) / "second-report.json", expected_sha256=audit["output_sha256"])
            second_audit = converter.convert_file(**second)
            self.assertEqual(second_audit["changes"], [])
            self.assertEqual(second["output_path"].read_bytes(), args["output_path"].read_bytes())

    def test_existing_destinations_and_input_aliases_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.files(directory)
            for key in ("output_path", "report_path"):
                with self.subTest(key=key):
                    args[key].write_bytes(b"keep")
                    with self.assertRaises(FileExistsError):
                        converter.convert_file(**args)
                    self.assertEqual(args[key].read_bytes(), b"keep")
                    args[key].unlink()
            with self.assertRaisesRegex(ValueError, "must_be_distinct"):
                converter.convert_file(**dict(args, output_path=args["input_path"]))
            with self.assertRaisesRegex(ValueError, "must_be_distinct"):
                converter.convert_file(**dict(args, report_path=args["output_path"]))

    def test_duplicate_json_identity_keys_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            args = self.files(directory)
            raw = b'{"facility_id":"FAC_TEST","facility_id":"OTHER"}'
            args["input_path"].write_bytes(raw)
            args["expected_sha256"] = hashlib.sha256(raw).hexdigest()
            with self.assertRaisesRegex(ValueError, "duplicate_json_key"):
                converter.convert_file(**args)
            self.assertFalse(args["output_path"].exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
