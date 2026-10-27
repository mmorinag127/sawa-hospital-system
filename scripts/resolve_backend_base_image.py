"""Reuse an immutable base only after checking its complete input fingerprint."""
import argparse
import json
import re
import subprocess
import sys

REPOSITORY = 'asia-northeast2-docker.pkg.dev/sawahospitalsystem/backend/backend'


def resolve(fingerprint):
    if not isinstance(fingerprint, str) or not re.fullmatch('[0-9a-f]{64}', fingerprint):
        raise ValueError('invalid backend base fingerprint')
    raw = subprocess.check_output([
        'gcloud', 'artifacts', 'docker', 'images', 'list', REPOSITORY, '--include-tags',
        '--filter=tags:backend-base-yomitoku-cpu-' + fingerprint[:12],
        '--sort-by=~CREATE_TIME', '--format=json'], text=True, timeout=180)
    rows = json.loads(raw)
    if not isinstance(rows, list):
        raise ValueError('invalid registry image inventory')
    tag = re.compile(r'(?:stg|prod)-backend-base-yomitoku-cpu-' + fingerprint[:12] + r'-[0-9]{8}-[0-9]{6}')
    candidates = []
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get('tags'), list)
                or any(not isinstance(value, str) for value in row['tags'])):
            raise ValueError('invalid registry image entry')
        if not any(isinstance(value, str) and tag.fullmatch(value) for value in row['tags']):
            continue
        digest = row.get('version', '')
        if row.get('package') != REPOSITORY or not re.fullmatch('sha256:[0-9a-f]{64}', digest):
            raise ValueError('backend base registry identity mismatch')
        candidates.append(REPOSITORY + '@' + digest)
    if not candidates:
        return ''
    image = candidates[0]
    subprocess.run(['docker', 'pull', '--platform', 'linux/amd64', image],
                   stdout=sys.stderr, check=True, timeout=900)
    config = json.loads(subprocess.check_output(
        ['docker', 'image', 'inspect', '--format', '{{json .}}', image], text=True, timeout=30))
    settings = config.get('Config') if isinstance(config, dict) else None
    labels = settings.get('Labels') if isinstance(settings, dict) else None
    if (not isinstance(config, dict) or config.get('Os') != 'linux' or config.get('Architecture') != 'amd64'
            or not isinstance(labels, dict) or labels.get('sawa.backend_base_fingerprint') != fingerprint
            or not isinstance(config.get('RepoDigests'), list) or image not in config['RepoDigests']):
        raise ValueError('backend base fingerprint, platform or digest mismatch')
    return image


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fingerprint', required=True)
    args = parser.parse_args()
    try:
        image = resolve(args.fingerprint)
    except (ValueError, OSError, subprocess.SubprocessError):
        print('backend base lookup failed; refusing unverified reuse', file=sys.stderr)
        return 1
    if image:
        print(image)
    return 0


if __name__ == '__main__':
    sys.exit(main())
