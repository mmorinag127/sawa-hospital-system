"""Distinguish fresh pytest outputs from external inputs, without trusting old DBs."""
from dataclasses import dataclass
import hashlib
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit


@dataclass
class GeneratedRoot:
    path: Path
    owner: str
    producer: dict | None
    shared: bool
    sealed: bool = False
    snapshot: dict | None = None


def snapshot(directory):
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('generated-resource-directory-missing')
    result, total = {}, 0
    for path in sorted(directory.rglob('*')):
        if path.is_symlink():
            raise ValueError('generated-resource-symlink')
        relative = path.relative_to(directory).as_posix()
        if path.is_dir():
            result[relative] = ('directory',)
        elif path.is_file():
            total += path.stat().st_size
            if total > 64 * 1024 * 1024:
                raise ValueError('generated-resource-too-large')
            with path.open('rb') as stream:
                result[relative] = ('file', hashlib.file_digest(stream, 'sha256').hexdigest())
        else:
            raise ValueError('generated-resource-type')
    return result


class OwnedInputs:
    def __init__(self, unsafe, inherit):
        self.unsafe, self.inherit = unsafe, inherit
        self.roots = {}
        self.produced = {}
        self.used = {}
        self.connections = {}
        self.pending_connection = None

    def register(self, directory, *, owner, producer, base, shared):
        path = Path(directory)
        if (not owner or path.is_symlink() or path.resolve() != path
                or path == base or not path.is_relative_to(base)
                or not path.is_dir() or any(path.iterdir())):
            self.unsafe('generated-resource-ownership-unresolved')
            return
        if path in self.roots:
            self.unsafe('generated-resource-reused-directory')
            return
        self.roots[path] = GeneratedRoot(path, owner, producer, shared)
        if not shared:
            self.used.setdefault(owner, {})[id(self.roots[path])] = self.roots[path]
        if producer is not None and shared:
            self.produced.setdefault(id(producer), []).append(self.roots[path])

    def seal(self, producer):
        for root in self.produced.get(id(producer), ()):
            if root.sealed:
                continue
            try:
                root.snapshot = snapshot(root.path)
            except (OSError, ValueError) as error:
                producer['unsafe'].add(str(error) if isinstance(error, ValueError) else 'generated-resource-unreadable')
            root.sealed = True

    def accepts(self, path, *, owner, stack, write=False, database=False, readonly=False):
        try:
            original = Path(path)
            if not original.is_absolute():
                return False
            path = original.resolve()
        except (OSError, RuntimeError, ValueError):
            return False
        root = next((self.roots[parent] for parent in (original, *original.parents)
                     if parent in self.roots), None)
        if root is None or not path.is_relative_to(root.path):
            return False
        if not root.shared:
            if root.owner != owner:
                self.unsafe('cross-case-generated-input')
                if root.producer is not None:
                    root.producer['unsafe'].add('cross-case-generated-input')
            return True
        producer = root.producer
        if producer is None:
            self.unsafe('generated-resource-producer-unresolved')
            return True
        if any(record is producer for record in stack):
            return True
        self.used.setdefault(owner, {})[id(root)] = root
        if write or database and not readonly:
            producer['unsafe'].add('shared-generated-input-write')
        if not root.sealed or root.snapshot is None:
            producer['unsafe'].add('generated-resource-unsealed')
        else:
            try:
                if snapshot(root.path) != root.snapshot:
                    producer['unsafe'].add('shared-generated-input-changed')
            except (OSError, ValueError):
                producer['unsafe'].add('shared-generated-input-changed')
        self.inherit(producer)
        return True

    def database(self, database, *, owner, stack, connection=None):
        if owner is None:
            return False
        if connection is not None:
            record = self.connections.get(id(connection))
            if record is None:
                return False
            database, created_by = record
            if database == ':memory:':
                if created_by != owner:
                    self.unsafe('cross-case-generated-database')
                return True
        if database == ':memory:':
            return owner is not None and not any(record.get('scope') != 'function' for record in stack)
        if not isinstance(database, (str, bytes, Path)):
            return False
        try:
            value = str(database) if not isinstance(database, bytes) else database.decode()
        except UnicodeError:
            return False
        readonly = False
        if value.startswith('file:'):
            uri = urlsplit(value)
            if uri.netloc:
                return False
            readonly = parse_qs(uri.query).get('mode') == ['ro']
            value = unquote(uri.path)
        if not Path(value).is_absolute():
            return False
        return self.accepts(value, owner=owner, stack=stack, database=True, readonly=readonly)

    def unsafe_for(self, owner):
        reasons = set()
        for root in self.used.get(owner, {}).values():
            if root.producer is not None:
                reasons.update(root.producer['unsafe'])
        return reasons

    def finish(self):
        roots = {identity: root for used in self.used.values() for identity, root in used.items()}
        for root in roots.values():
            if not root.shared:
                continue
            try:
                unchanged = root.snapshot is not None and snapshot(root.path) == root.snapshot
            except (OSError, ValueError):
                unchanged = False
            if not unchanged and root.producer is not None:
                root.producer['unsafe'].add('shared-generated-input-changed')
