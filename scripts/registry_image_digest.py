"""Resolve the approved registry image from manifest bytes, without metadata APIs."""
import argparse
import hashlib
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'backend')]

from staging_db_target import read_http, _access_token, REGISTRY
from stg_ocr_producer_deploy import Blocked

PREFIX = REGISTRY + '/sawahospitalsystem/backend/'
ACCEPT = 'application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json'
IMAGE = re.compile(re.escape(PREFIX) +
                   r'(frontend|backend|ocr-pipeline)(?::([A-Za-z0-9_][A-Za-z0-9_.-]{0,127})|@(sha256:[0-9a-f]{64}))')


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
