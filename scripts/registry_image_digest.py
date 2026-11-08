"""Resolve the approved registry image from manifest bytes, without metadata APIs."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'backend')]

from staging_db_target import read_http, _access_token, REGISTRY
from stg_ocr_producer_deploy import Blocked

PREFIX = REGISTRY + '/sawahospitalsystem/backend/'
ACCEPT = 'application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json'
IMAGE = re.compile(re.escape(PREFIX) +
                   r'(frontend|backend|ocr-pipeline|agent-runtime)(?::([A-Za-z0-9_][A-Za-z0-9_.-]{0,127})|@(sha256:[0-9a-f]{64}))')


def manifest_document(raw):
    try:
        document = json.loads(raw)
    except (ValueError, UnicodeError, TypeError):
        raise Blocked('registry_manifest_invalid') from None
    if not isinstance(document, dict):
        raise Blocked('registry_manifest_invalid')
    return document


def resolve_image_digest(image):
    match = IMAGE.fullmatch(image)
    if not match:
        raise Blocked('registry_image_reference_rejected')
    component, tag, expected = match.groups()
    status, raw = read_http(
        f'https://{REGISTRY}/v2/sawahospitalsystem/backend/{component}/manifests/{tag or expected}',
        _access_token(), accept=ACCEPT, blob_redirect=False)
    if status != 200:
        raise Blocked('registry_manifest_http_failure')
    if not isinstance(raw, bytes) or not raw:
        raise Blocked('registry_manifest_bytes_required')
    digest = 'sha256:' + hashlib.sha256(raw).hexdigest()
    if expected is not None and digest != expected:
        raise Blocked('registry_manifest_digest_mismatch')
    document = manifest_document(raw)
    if 'manifests' in document:
        descriptors = document['manifests']
        if not isinstance(descriptors, list) or any(
                not isinstance(item, dict) or not isinstance(item.get('platform', {}), dict)
                for item in descriptors):
            raise Blocked('registry_manifest_invalid')
        matches = [item for item in descriptors
                   if item.get('platform', {}).get('os') == 'linux'
                   and item.get('platform', {}).get('architecture') == 'amd64']
        if (len(matches) != 1 or not isinstance(matches[0].get('digest'), str)
                or not re.fullmatch(r'sha256:[0-9a-f]{64}', matches[0]['digest'])):
            raise Blocked('unique_linux_amd64_manifest_required')
        child = matches[0]['digest']
        status, content = read_http(
            f'https://{REGISTRY}/v2/sawahospitalsystem/backend/{component}/manifests/{child}',
            _access_token(), accept=ACCEPT, blob_redirect=False)
        if status != 200 or not isinstance(content, bytes) or 'sha256:' + hashlib.sha256(content).hexdigest() != child:
            raise Blocked('platform_manifest_digest_mismatch')
        if 'manifests' in manifest_document(content):
            raise Blocked('nested_platform_index_rejected')
        digest = child
    return PREFIX + component + '@' + digest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    try:
        image = resolve_image_digest(args.image)
    except Exception as error:
        print(str(error) if isinstance(error, Blocked) else 'registry_digest_resolution_failed', file=sys.stderr)
        return 1
    print(image)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
