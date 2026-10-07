import json
import sys
import types
from copy import deepcopy
from hashlib import sha256


class NotFound(Exception):
    pass


class PreconditionFailed(Exception):
    pass


class AlreadyExists(Exception):
    pass


def install_transport_exceptions():
    from unittest.mock import patch
    module = types.ModuleType("google.api_core.exceptions")
    module.NotFound = NotFound
    module.PreconditionFailed = PreconditionFailed
    module.AlreadyExists = AlreadyExists
    return patch.dict(sys.modules, {"google.api_core.exceptions": module})


class Stores:
    def __init__(self):
        self.objects = {}
        self.generations = {}
        self.object_metadata = {}
        self.generation_metadata = {}
        self.job = None
        self.fail_fs = False
        self.fail_fs_terminal = False
        self.fail_fs_read_after_output = False
        self.version = 0
        self.fail_object = None
        self.writes = []
        self.name = "input/own.pdf"
        self.initial_generations = {self.name: 19, self.name + ".configured.json": 17}
        self.output = "output/own.pdf.g19.json"
        self.configured = {"facility": {"facility_id": "own", "fax_template": {"template_id": "own-template", "zero": 0, "unknown": None}}, "policy": {}}
        config = json.dumps(self.configured, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        self.body = b"own-pdf"
        self.metadata = {
            "facility_id": "own", "template_id": "own-template",
            "selected_pdf_receipt": json.dumps({"storage_uri": "/explicit/own.pdf", "sha256": sha256(self.body).hexdigest(), "generation": None}),
            "configured_input_receipt": json.dumps({"bucket": "own-bucket", "object_name": self.name + ".configured.json", "generation": "17", "sha256": sha256(config).hexdigest()}),
            "execution_owner": json.dumps({"job_id": "OCR-own", "order_id": "own", "workflow_attempt_id": "attempt-own", "workflow_revision": 7, "nonworkflow_execution_id": None}),
        }
        self.objects[self.name] = self.body
        self.objects[self.name + ".configured.json"] = config

    def bucket(self, name):
        if name != "own-bucket":
            raise ValueError("foreign bucket")
        stores = self
        class Bucket:
            def __init__(self):
                self.name = name

            def blob(self, object_name, generation=None):
                return Blob(stores, self, object_name, generation)
        return Bucket()

    def collection(self, _):
        return self

    def document(self, _):
        return self

    def get(self):
        if self.fail_fs_read_after_output and self.output in self.objects:
            self.fail_fs_read_after_output = False
            raise OSError("injected Firestore verification outage after GCS success")
        snapshot = deepcopy(self.job)
        return types.SimpleNamespace(exists=snapshot is not None, update_time=self.version, to_dict=lambda: deepcopy(snapshot))

    def write_option(self, *, last_update_time):
        return last_update_time

    def create(self, value):
        if self.job is not None:
            raise AlreadyExists()
        self.job = deepcopy(value)
        self.version += 1

    def update(self, value, option=None):
        if option is not None and option != self.version:
            raise PreconditionFailed("changed Firestore owner")
        if self.fail_fs_terminal and value.get("status") in {"done", "failed"}:
            self.fail_fs_terminal = False
            raise OSError("injected terminal Firestore outage after GCS success")
        if self.fail_fs:
            self.fail_fs = False
            raise OSError("injected Firestore outage")
        if self.job is None:
            raise NotFound()
        self.job.update(deepcopy(value))
        self.version += 1


class Blob:
    def __init__(self, stores, bucket, name, generation):
        self.stores, self.bucket, self.name = stores, bucket, name
        self.generation = generation
        self.requested_generation = generation
        self.metadata = deepcopy(stores.metadata if name == stores.name else stores.object_metadata.get(name, {}))

    def reload(self):
        if self.name not in self.stores.objects:
            raise NotFound()
        if self.requested_generation is not None:
            if (self.name, self.requested_generation) not in self.stores.generations and self.requested_generation != self._current_generation():
                raise NotFound()
            self.generation = self.requested_generation
            self.metadata = deepcopy(self.stores.generation_metadata.get((self.name, self.generation), self.stores.metadata if self.name == self.stores.name else self.stores.object_metadata.get(self.name, {})))
            return
        self.generation = self._current_generation()
        self.metadata = deepcopy(self.stores.metadata if self.name == self.stores.name else self.stores.object_metadata.get(self.name, {}))

    def _current_generation(self):
        if self.name in self.stores.initial_generations:
            return self.stores.initial_generations[self.name] + sum(name == self.name for name, _ in self.stores.writes)
        if self.name == self.stores.name:
            return 19 + sum(name == self.name for name, _ in self.stores.writes)
        if self.name.endswith(".configured.json"):
            return 17 + sum(name == self.name for name, _ in self.stores.writes)
        return 1 + sum(name == self.name for name, _ in self.stores.writes)

    def download_as_bytes(self, **kwargs):
        historical = self.stores.generations.get((self.name, self.generation))
        if historical is not None:
            if kwargs.get("if_generation_match") not in (None, self.generation):
                raise PreconditionFailed()
            return historical
        expected = self._current_generation()
        if self.generation is not None and self.generation != expected:
            raise PreconditionFailed()
        if kwargs.get("if_generation_match") is not None and kwargs["if_generation_match"] != expected:
            raise PreconditionFailed()
        if self.name not in self.stores.objects:
            raise NotFound()
        return self.stores.objects[self.name]

    def upload_from_string(self, value, **kwargs):
        if self.stores.fail_object == self.name:
            self.stores.fail_object = None
            raise OSError("injected GCS outage")
        if kwargs.get("if_generation_match") == 0 and self.name in self.stores.objects:
            raise PreconditionFailed()
        condition = kwargs.get("if_generation_match")
        if condition not in (None, 0) and (self.name not in self.stores.objects or condition != self._current_generation()):
            raise PreconditionFailed()
        data = value.encode() if isinstance(value, str) else value
        if self.name in self.stores.objects:
            self.stores.generations[(self.name, self._current_generation())] = self.stores.objects[self.name]
            self.stores.generation_metadata[(self.name, self._current_generation())] = deepcopy(self.stores.metadata if self.name == self.stores.name else self.stores.object_metadata.get(self.name, {}))
        self.stores.objects[self.name] = data
        self.stores.object_metadata[self.name] = deepcopy(self.metadata)
        if self.name == self.stores.name:
            self.stores.metadata = deepcopy(self.metadata)
        self.stores.writes.append((self.name, data))
        self.generation = self._current_generation()
        self.stores.generations[(self.name, self.generation)] = data
        self.stores.generation_metadata[(self.name, self.generation)] = deepcopy(self.metadata)
