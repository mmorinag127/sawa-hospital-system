"""Conservative command reuse: signed real success, unchanged files and runtime."""
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import stat
import subprocess
import sys
import sysconfig
import tempfile
import shutil

from local_receipt_signing import canonical, sign, verify_signed


PG_COMMAND = 'python3 scripts/run_local_pg_batch.py'
STATIC_COMMANDS = {
    'cd backend && python3 -m ruff check --no-cache --select F821 src',
    'cd frontend && npm run lint',
    'cd frontend && ./node_modules/.bin/tsc --noEmit',
}


def file_digest(path):
    digest = sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


class FileHashes:
    def __init__(self, entries=None):
        self.entries = entries if isinstance(entries, dict) else {}

    def digest(self, path):
        before = path.stat()
        identity = [before.st_dev, before.st_ino, before.st_mode, before.st_size,
                    before.st_mtime_ns, before.st_ctime_ns]
        previous = self.entries.get(str(path))
        if (isinstance(previous, dict) and previous.get('identity') == identity
                and isinstance(previous.get('sha256'), str) and len(previous['sha256']) == 64
                and all(char in '0123456789abcdef' for char in previous['sha256'])):
            return previous['sha256']
        value = file_digest(path)
        after = path.stat()
        if identity != [after.st_dev, after.st_ino, after.st_mode, after.st_size,
                         after.st_mtime_ns, after.st_ctime_ns]:
            raise OSError('command input changed while hashing')
        self.entries[str(path)] = {'identity': identity, 'sha256': value}
        return value


def command_context(root, command, environment, hashes):
    if command not in {PG_COMMAND, *STATIC_COMMANDS} or not os.environ.get('SAWA_LOCAL_TEST_PUBLIC_KEY'):
        return None
    pg_bin = Path(environment.get('PG_BIN', '/usr/lib/postgresql/16/bin'))
    if command == PG_COMMAND and not all((pg_bin / name).is_file() for name in ('postgres', 'pg_ctl', 'initdb', 'createdb', 'psql', 'pg_config')):
        return None
    digest = sha256()
    digest.update(canonical({'version': 2, 'command': command, 'python': sys.version,
        'executable': str(Path(sys.executable).resolve()), 'platform': platform.platform(),
        'environment': {key: value for key, value in environment.items() if key not in {
            'SAWA_LOCAL_RUN_ID', 'SAWA_LOCAL_EVIDENCE_DIR', 'PWD', 'OLDPWD', 'SHLVL', '_'}},
        'sys_path': sys.path}))
    try:
        digest.update(canonical(['source-root-entries', sorted(path.name for path in root.iterdir())]))
        files = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode().split('\0')
        frontend = command.startswith('cd frontend && ')
        for name in sorted(name for name in files if name and (frontend or not name.startswith('frontend/'))):
            path = root / name
            if not path.is_file():
                return None
            digest.update(canonical([name, path.lstat().st_mode, str(path.resolve()), hashes.digest(path)]))
        runtime_roots = [Path(sys.executable).resolve()]
        if command == PG_COMMAND:
            runtime_roots.append(pg_bin)
        if frontend:
            node = shutil.which('node', path=environment.get('PATH'))
            if not node or not (root / 'frontend/node_modules').is_dir():
                return None
            runtime_roots.extend([Path(node), root / 'frontend'])
        # Include ignored inputs too; a private fixture cannot silently escape the key.
        runtime_roots.extend(root / name for name in ('backend', 'scripts', 'infra', 'ocr_pipeline', 'agent_runtime', 'experiments')
                             if (root / name).is_dir())
        if command == PG_COMMAND:
            for option in ('--libdir', '--pkglibdir', '--sharedir'):
                location = subprocess.check_output([str(pg_bin / 'pg_config'), option], text=True).strip()
                runtime_roots.append(Path(location))
        runtime_roots.extend(Path(sysconfig.get_path(kind)) for kind in ('stdlib', 'platstdlib', 'purelib', 'platlib'))
        for entry in sys.path:
            path = Path(entry or os.getcwd()).resolve()
            if path.is_relative_to(root):
                continue
            if path.is_dir():
                runtime_roots.append(path)
            elif path.exists():
                runtime_roots.append(path)
        for name in ('/etc/os-release', '/var/lib/dpkg/status', '/lib/apk/db/installed'):
            if Path(name).is_file():
                runtime_roots.append(Path(name))
        visited = set()
        pending = list(runtime_roots)
        while pending:
            original = pending.pop()
            if original.is_symlink():
                digest.update(canonical(['symlink', str(original), os.readlink(original)]))
            path = original.resolve(strict=True)
            if path in visited:
                continue
            visited.add(path)
            info = path.stat()
            if stat.S_ISDIR(info.st_mode):
                children = sorted(path.iterdir(), reverse=True)
                digest.update(canonical(['directory', str(path), info.st_mode, [item.name for item in children]]))
                pending.extend(children)
            elif stat.S_ISREG(info.st_mode):
                digest.update(canonical([str(path), info.st_mode, hashes.digest(path)]))
            else:
                return None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return digest.hexdigest()


def validate_proof(proof, *, command_hash, dependency_hash, log_hash):
    payload = verify_signed(proof)
    if (not isinstance(payload, dict) or set(payload) != {
            'kind', 'source_sha', 'command_sha256', 'dependency_sha256', 'log_sha256', 'input_guard_sha256', 'exit_code'}
            or payload['kind'] != 'executed-local-command-v1'
            or type(payload['exit_code']) is not int or payload['exit_code'] != 0
            or payload['command_sha256'] != command_hash or payload['dependency_sha256'] != dependency_hash
            or payload['log_sha256'] != log_hash
            or any(not isinstance(payload[key], str) or len(payload[key]) != length
                or any(char not in '0123456789abcdef' for char in payload[key]) for key, length in (
                    ('source_sha', 40), ('command_sha256', 64), ('dependency_sha256', 64), ('log_sha256', 64), ('input_guard_sha256', 64)))):
        raise ValueError('invalid signed command baseline')
    return payload


class CommandEvidence:
    def __init__(self, root, environment):
        self.root, self.environment = root, environment
        common = subprocess.check_output(['git', 'rev-parse', '--git-common-dir'], cwd=root, text=True).strip()
        self.directory = (root / common).resolve() / 'sawa-command-evidence'
        self.contexts = {}
        try:
            entries = verify_signed(json.loads((self.directory / 'file-hashes.json').read_text()))
        except (OSError, ValueError, KeyError, TypeError):
            entries = {}
        self.hashes = FileHashes(entries)

    def context(self, command):
        if command not in self.contexts:
            self.contexts[command] = command_context(self.root, command, self.environment, self.hashes)
        return self.contexts[command]

    def reuse(self, command, log):
        dependency = self.context(command)
        if dependency is None or not os.environ.get('SAWA_LOCAL_TEST_PUBLIC_KEY'):
            return None
        command_hash = sha256(command.encode()).hexdigest()
        source_log = self.directory / (command_hash + '.log')
        try:
            proof = json.loads((self.directory / (command_hash + '.json')).read_text())
            payload = validate_proof(proof, command_hash=command_hash, dependency_hash=dependency,
                                     log_hash=file_digest(source_log))
            if subprocess.run(['git', 'merge-base', '--is-ancestor', payload['source_sha'], 'HEAD'],
                              cwd=self.root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
                return None
            with source_log.open('rb') as source, log.open('wb') as output:
                import shutil
                shutil.copyfileobj(source, output, length=1024 * 1024)
            if file_digest(log) != payload['log_sha256']:
                return None
            return {'dependency_sha256': dependency, 'proof': proof}
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def save(self, command, source_sha, log, *, exit_code=None):
        if type(exit_code) is not int or exit_code != 0:
            return
        dependency = self.context(command)
        if dependency is None:
            return
        report_path = Path(self.environment['SAWA_LOCAL_EVIDENCE_DIR']) / 'pg-command-inputs.json'
        if command in STATIC_COMMANDS:
            report_path = log.with_suffix('.inputs.json')
            report_path.write_bytes(canonical({'schema': 1, 'source_sha': source_sha,
                'command_sha256': sha256(command.encode()).hexdigest(),
                'dependency_sha256': dependency, 'kind': 'executed-static-check'}))
        elif command != PG_COMMAND:
            return
        try:
            observation = json.loads(report_path.read_text())
            suites = observation.get('suites')
            reports = observation.get('reports')
            expected = ['readonly', 'c1', 'c1-guards', 'menu-identity', 'output']
            if self.environment['SAWA_LOCAL_TEST_MODE'] != 'focused':
                expected.append('formal')
            if command == PG_COMMAND and (observation.get('schema') != 1 or observation.get('source_sha') != source_sha
                    or suites != expected or set(reports) != set(expected)
                    or any(report.get('schema') != 1 or type(report.get('collected')) is not int
                        or report['collected'] <= 0 or report.get('passed') != report['collected']
                        or report.get('skipped') != 0 or report.get('failed') != 0
                        or report.get('exitstatus') != 0 or report.get('unsafe') != []
                        or report.get('observer_intact') is not True
                        for report in reports.values())):
                return
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return
        # Rehash after execution: changing inputs cannot establish a reusable baseline.
        if command_context(self.root, command, self.environment, self.hashes) != dependency:
            return
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        command_hash = sha256(command.encode()).hexdigest()
        proof = sign({'kind': 'executed-local-command-v1', 'source_sha': source_sha,
            'command_sha256': command_hash, 'dependency_sha256': dependency,
            'log_sha256': file_digest(log), 'input_guard_sha256': file_digest(report_path), 'exit_code': 0})
        for suffix, content in (('.log', None), ('.json', canonical(proof))):
            with tempfile.NamedTemporaryFile(dir=self.directory, delete=False) as output:
                temporary = Path(output.name)
                try:
                    if content is None:
                        import shutil
                        with log.open('rb') as source:
                            shutil.copyfileobj(source, output, length=1024 * 1024)
                    else:
                        output.write(content)
                    output.flush()
                    os.fsync(output.fileno())
                except BaseException:
                    temporary.unlink(missing_ok=True)
                    raise
            temporary.replace(self.directory / (command_hash + suffix))

    def persist(self):
        if not self.hashes.entries:
            return
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=self.directory, delete=False) as output:
            path = Path(output.name)
            try:
                output.write(canonical(sign(self.hashes.entries)))
            except BaseException:
                path.unlink(missing_ok=True)
                raise
        path.replace(self.directory / 'file-hashes.json')
