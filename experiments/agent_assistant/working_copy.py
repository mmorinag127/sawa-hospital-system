"""Lossless working-copy checkpoints; before-images are never a resume source."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from uuid import uuid4

from .store import Conflict


def files(directory):
    for path in sorted(directory.rglob('*')):
        relative = path.relative_to(directory)
        if ((len(relative.parts) >= 2 and relative.parts[1] == 'home') or
                path.name in ('database.sqlite3-wal', 'database.sqlite3-shm')):
            continue
        if path.is_symlink():
            raise Conflict('作業データのリンクを保存・復元できません')
        if not path.is_file():
            continue
        if path.name == 'database.sqlite3':
            with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as source:
                with closing(sqlite3.connect(':memory:')) as target:
                    source.backup(target)
                    content = target.serialize()
        else:
            content = path.read_bytes()
        yield relative.as_posix(), content


def checkpoint(root, ident, cursor, encode_blob, *, known=None):
    data = root / 'runs' / ident / 'data'
    if not data.is_dir():
        return None
    manifest = {}
    for relative, content in files(data):
        digest = hashlib.sha256(content).hexdigest()
        key = f'runs/{ident}/checkpoints/{digest}'
        if known is None or key not in known:
            cursor.execute('INSERT INTO sawa_agent_trial.files VALUES (%s,%s,%s) ON CONFLICT DO NOTHING',
                           (key, digest, encode_blob(content)))
            if known is not None:
                known[key] = digest
        manifest[relative] = digest
    return json.dumps(manifest, sort_keys=True)


def restore(root, ident, encoded):
    manifest = json.loads(encoded)
    directory = root / 'runs' / ident
    staging = directory / ('hydrating-' + uuid4().hex)
    staging.mkdir(parents=True)
    try:
        for relative, digest in manifest.items():
            target = staging / relative
            if not target.resolve().is_relative_to(staging.resolve()) or not relative or Path(relative).is_absolute():
                raise Conflict('作業データの保存先が不正です')
            if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
                raise Conflict('作業データの照合値が不正です')
            source = directory / 'checkpoints' / digest
            content = source.read_bytes()
            if hashlib.sha256(content).hexdigest() != digest:
                raise Conflict('作業データの保存内容が一致しません')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        data = directory / 'data'
        if data.exists():
            os.replace(data, directory / ('superseded-data-' + uuid4().hex))
        os.replace(staging, data)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
