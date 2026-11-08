import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'scripts'))
import registry_image_digest as subject


@pytest.mark.parametrize('failure', [None, 'ambiguous', 'architecture', 'child', 'parent'])
def test_linux_amd64_child_is_verified_against_original_index(monkeypatch, failure):
    child = b'{"schemaVersion":2,"config":{}}'
    digest = 'sha256:' + hashlib.sha256(child).hexdigest()
    rows = [{'platform': {'os': 'linux', 'architecture': 'amd64'}, 'digest': digest},
            {'platform': {'os': 'unknown', 'architecture': 'unknown'}, 'digest': 'sha256:' + 'c' * 64}]
    if failure == 'ambiguous': rows.append(rows[0])
    if failure == 'architecture': rows[0]['platform']['architecture'] = 'arm64'
    index = json.dumps({'schemaVersion': 2, 'manifests': rows}).encode()
    parent = 'sha256:' + hashlib.sha256(index).hexdigest()
    read = Mock(side_effect=[(200, index), (200, child + b' ' if failure == 'child' else child)])
    monkeypatch.setattr(subject, 'read_http', read)
    monkeypatch.setattr(subject, '_access_token', lambda: 'caller')
    image = subject.PREFIX + 'agent-runtime@' + ('sha256:' + 'a' * 64 if failure == 'parent' else parent)
    if failure:
        with pytest.raises(subject.Blocked): subject.resolve_image_digest(image)
    else:
        assert subject.resolve_image_digest(image) == subject.PREFIX + 'agent-runtime@' + digest
        assert read.call_args.args[0].endswith('/manifests/' + digest)
