"""In-memory external GCS transport only; not provider/native OCR proof."""

from copy import deepcopy
from types import SimpleNamespace


def install_owned_pipeline_transport(monkeypatch, *, on_missing=None):
    from google.cloud import storage

    objects = {}
    transport_events = []

    class Blob:
        def __init__(self, bucket, name, generation=None):
            self.key = (bucket, name)
            self.generation = generation
            self.metadata = None

        def upload_from_string(self, data, *, content_type, if_generation_match):
            assert if_generation_match == 0
            assert self.key not in objects
            self.generation = len(objects) + 1
            objects[self.key] = (bytes(data), deepcopy(self.metadata), self.generation, content_type)
            transport_events.append(("upload", self.key))

        def exists(self, *, timeout, retry):
            assert timeout > 0 and retry is None
            transport_events.append(("exists", self.key))
            if self.key not in objects and on_missing is not None:
                on_missing(self.key)
            return self.key in objects

        def reload(self, *, timeout=None, retry=None):
            _, metadata, generation, _ = objects[self.key]
            if self.generation is not None:
                assert self.generation == generation
            self.generation = generation
            self.metadata = deepcopy(metadata)
            transport_events.append(("reload", self.key))

        def download_as_bytes(self, *, if_generation_match, timeout=None, retry=None):
            data, _, generation, _ = objects[self.key]
            assert self.generation == if_generation_match == generation
            transport_events.append(("download", self.key))
            return data

    class Client:
        def bucket(self, name):
            assert name == "explicit-component-input"
            return SimpleNamespace(blob=lambda object_name, generation=None: Blob(name, object_name, generation))

    monkeypatch.setattr(storage, "Client", Client)
    return objects, transport_events
