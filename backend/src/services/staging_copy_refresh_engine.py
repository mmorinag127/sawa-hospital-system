"""Read-only production snapshot -> fresh staging databases/files -> verified manifest.

No DROP, UPDATE or DELETE against source/Test/current-copy databases. A partial
attempt stays unpublished; only the registry CAS in the control service activates it.
"""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import time
from urllib.parse import parse_qs, unquote, urlsplit, urlunsplit

import psycopg2
from psycopg2 import sql
from sqlalchemy.engine import make_url

from src import staging_data as datasets
from src.services.staging_copy_refresh_service import RefreshError


def _cloud_session():
    import google.auth
    from google.auth.transport.requests import AuthorizedSession
    credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    return AuthorizedSession(credentials)


def runtime_environment(service, settings_keys=(), *, credential_keys=frozenset()):
    """Pin the actual serving revision; credentials exist only in this process."""
    if credential_keys and (credential_keys != {"SAWA_API_SERVICE_TOKEN"} or not service.endswith("/services/shift-api-stg")):
        raise RefreshError("refresh_runtime_credential_scope_invalid")
    session = _cloud_session()
    response = session.get("https://run.googleapis.com/v2/" + service, timeout=30)
    if response.status_code != 200:
        raise RefreshError("refresh_runtime_source_unavailable")
    value = response.json()
    traffic = [item for item in value.get("trafficStatuses", []) if item.get("percent", 0) > 0]
    if len(traffic) != 1 or traffic[0].get("percent") != 100 or not traffic[0].get("revision"):
        raise RefreshError("refresh_runtime_revision_ambiguous")
    revision = traffic[0]["revision"]
    if "/" not in revision:
        revision = service + "/revisions/" + revision
    if not revision.startswith(service + "/revisions/") or not re.fullmatch(r"[a-z0-9-]+", revision.rsplit("/", 1)[-1]):
        raise RefreshError("refresh_runtime_revision_ambiguous")
    response = session.get("https://run.googleapis.com/v2/" + revision, timeout=30)
    if response.status_code != 200:
        raise RefreshError("refresh_runtime_revision_unavailable")
    containers = response.json().get("containers", [])
    if len(containers) != 1:
        raise RefreshError("refresh_runtime_container_ambiguous")
    environment = {}
    for item in containers[0].get("env", []):
        key = item["name"]
        if key in environment:
            raise RefreshError("refresh_runtime_setting_duplicate")
        if "valueSource" not in item:
            environment[key] = item.get("value", "")
        elif key in settings_keys and key not in {"DB_URI", "DATABASE_URL", "DB_PASSWORD", "DB_HOST", "DB_NAME", "DB_USER"}:
            raise RefreshError("refresh_business_setting_secret_reference_forbidden")
        elif key in {"DB_URI", "DATABASE_URL", "DB_PASSWORD", "DB_HOST", "DB_NAME", "DB_USER"} | credential_keys:
            reference = item.get("valueSource", {}).get("secretKeyRef", {})
            secret, version = reference.get("secret"), reference.get("version")
            if not secret or not version:
                raise RefreshError("refresh_runtime_secret_unresolved")
            if not secret.startswith("projects/"):
                secret = service.split("/locations/", 1)[0] + "/secrets/" + secret
            import base64
            reply = session.get("https://secretmanager.googleapis.com/v1/" + secret + "/versions/" + version + ":access", timeout=30)
            if reply.status_code != 200:
                raise RefreshError("refresh_runtime_secret_unavailable")
            environment[key] = base64.b64decode(reply.json()["payload"]["data"]).decode()
    return environment, revision


def runtime_source(service, settings_keys=()):
    environment, revision = runtime_environment(service, settings_keys)
    uris = [make_url(environment[key]) for key in ("DB_URI", "DATABASE_URL") if environment.get(key)]
    if any(key in environment for key in ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD")):
        from sqlalchemy.engine import URL
        if not all(environment.get(key) for key in ("DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD")):
            raise RefreshError("refresh_runtime_database_incomplete")
        uris.append(URL.create("postgresql+psycopg2", username=environment["DB_USER"], password=environment["DB_PASSWORD"],
                              database=environment["DB_NAME"], query={"host": environment["DB_HOST"]}))
    if not uris or any(uri != uris[0] for uri in uris):
        raise RefreshError("refresh_runtime_database_ambiguous")
    return uris[0].render_as_string(hide_password=False), environment, revision


def _database(uri, environment):
    value = make_url(uri)
    host = value.query.get("host", value.host or "")
    if (value.get_backend_name() != "postgresql" or not value.username or not value.password
            or not value.database or not host.startswith("/cloudsql/")
            or not re.fullmatch(r"[a-z0-9-]+:[a-z0-9-]+:[a-z0-9-]+-" + environment, host.removeprefix("/cloudsql/"))
            or value.database.startswith("sawa_replay_")):
        raise RefreshError("refresh_database_" + environment + "_configuration_invalid")
    return {"instance": host.removeprefix("/cloudsql/"), "user": value.username,
            "password": value.password, "database": value.database}


@contextmanager
def _proxy(config):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen(["/usr/local/bin/cloud-sql-proxy", "--address=127.0.0.1", "--port=" + str(port), config["instance"]],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            if process.poll() is not None:
                raise RefreshError("refresh_database_proxy_failed")
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    break
            except OSError:
                time.sleep(.2)
        else:
            raise RefreshError("refresh_database_proxy_timeout")
        yield port
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def _connect(config, port, *, database=None, readonly=True):
    connection = psycopg2.connect(host="127.0.0.1", port=port, user=config["user"], password=config["password"],
                                  dbname=database or config["database"], connect_timeout=30)
    if readonly:
        connection.set_session(isolation_level="REPEATABLE READ", readonly=True)
    return connection


def _pg_tool(name, major):
    binary = Path("/usr/lib/postgresql") / str(major) / "bin" / name
    if not binary.is_file():
        # Portable native test installation; verify the same explicit major.
        import shutil
        found = shutil.which(name)
        if not found:
            raise RefreshError("refresh_postgres_client_unavailable")
        binary = Path(found)
    reply = subprocess.run([str(binary), "--version"], capture_output=True, text=True, timeout=10)
    match = re.search(r"PostgreSQL\) (\d+)\.", reply.stdout)
    if reply.returncode or not match or int(match[1]) != major:
        raise RefreshError("refresh_postgres_client_version_mismatch")
    return str(binary)


def _run(command, *, config, port, readonly=False):
    environment = {**os.environ, "PGPASSWORD": config["password"], "PGHOST": "127.0.0.1", "PGPORT": str(port), "PGUSER": config["user"]}
    environment.pop("PGOPTIONS", None)
    if readonly:
        environment["PGOPTIONS"] = "-c default_transaction_read_only=on"
    result = subprocess.run(command, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=1200)
    if result.returncode:
        raise RefreshError("refresh_database_snapshot_command_failed")


def _references(value):
    if isinstance(value, dict):
        for item in value.values():
            yield from _references(item)
    elif isinstance(value, list):
        for item in value:
            yield from _references(item)
    elif isinstance(value, str):
        if value.startswith(("{", "[")):
            try:
                nested = json.loads(value)
            except ValueError:
                pass
            else:
                yield from _references(nested)
        if value.startswith(("gs://", "file://")) or (value.startswith("/") and re.search(r"\.(?:pdf|xlsx?|json|ya?ml|png|jpe?g)$", value, re.I)):
            yield value


def _collect_local_receipts(value, receipts):
    if isinstance(value, str) and value.startswith(("{", "[")):
        try:
            value = json.loads(value)
        except ValueError:
            return
    if isinstance(value, dict):
        reference = value.get("pdf_reference")
        version = value.get("storage_version")
        digest = value.get("pdf_sha256")
        if isinstance(reference, str) and isinstance(version, dict) and version.get("mode") == "local":
            required = {"mode", "device", "inode", "size", "mtime_ns"}
            if (set(version) != required or not re.fullmatch(r"[0-9a-f]{64}", str(digest))
                    or any(type(version[key]) is not int or version[key] < 0 for key in required - {"mode"})):
                raise RefreshError("refresh_saved_local_source_proof_invalid")
            receipt = {"storage_version": version, "pdf_sha256": digest}
            if reference in receipts and receipts[reference] != receipt:
                raise RefreshError("refresh_saved_local_source_ambiguous")
            receipts[reference] = receipt
        for item in value.values():
            _collect_local_receipts(item, receipts)
    elif isinstance(value, list):
        for item in value:
            _collect_local_receipts(item, receipts)


def fingerprints(connection, directory, local_receipts=None):
    """Full raw rows sorted on disk: bounded memory even for large OCR JSON."""
    result, references = {}, set()
    with connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM information_schema.foreign_tables")
        if cursor.fetchone()[0]:
            raise RefreshError("refresh_foreign_database_source_unsupported")
        cursor.execute("SELECT schemaname,tablename FROM pg_tables WHERE schemaname<>'information_schema' AND schemaname !~ '^pg_' UNION ALL SELECT schemaname,matviewname FROM pg_matviews WHERE schemaname<>'information_schema' AND schemaname !~ '^pg_' ORDER BY 1,2")
        tables = cursor.fetchall()
    for index, (schema, table) in enumerate(tables):
        raw_path = directory / (str(index) + ".rows")
        count = 0
        with raw_path.open("w", encoding="utf-8", newline="\n") as output, connection.cursor(name="copy_raw_rows") as cursor:
            cursor.execute(sql.SQL("SELECT row_to_json(t)::text FROM {} t").format(sql.Identifier(schema, table)))
            while True:
                batch = cursor.fetchmany(1)
                if not batch:
                    break
                row = batch[0][0]
                output.write(row + "\n")
                decoded = json.loads(row)
                references.update(_references(decoded))
                if local_receipts is not None:
                    _collect_local_receipts(decoded, local_receipts)
                count += 1
        subprocess.run(["sort", "-S", "16M", "-T", str(directory), "-o", str(raw_path), str(raw_path)],
                       env={**os.environ, "LC_ALL": "C"}, check=True, timeout=300, capture_output=True)
        digest = hashlib.sha256()
        with raw_path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        result[schema + "." + table] = {"rows": count, "sha256": digest.hexdigest()}
        raw_path.unlink()
    return result, references


def _heads(connection):
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass('public.alembic_version')")
        if cursor.fetchone() == (None,):
            return []
        cursor.execute("SELECT version_num FROM alembic_version ORDER BY version_num")
        return [row[0] for row in cursor.fetchall()]


def clone_database(source, destination, target, directory, expected_heads, *, source_heads=None, upgrade=None, receipt=None, snapshot_callback=None):
    if not re.fullmatch(r"sawa_replay_[a-f0-9_]{1,40}", target) or target == destination["database"]:
        raise RefreshError("refresh_destination_not_isolated")
    if source["instance"].split(":")[0] != destination["instance"].split(":")[0]:
        raise RefreshError("refresh_database_project_mismatch")
    original_heads = sorted(expected_heads if source_heads is None else source_heads)
    if original_heads != sorted(expected_heads) and upgrade is None:
        raise RefreshError("refresh_copy_upgrade_required")
    dump = directory / "snapshot.dump"
    with _proxy(source) as source_port, closing(_connect(source, source_port)) as original:
        with original.cursor() as cursor:
            cursor.execute("SHOW transaction_read_only")
            if cursor.fetchone() != ("on",):
                raise RefreshError("refresh_source_must_be_readonly")
            cursor.execute("SELECT pg_export_snapshot()")
            exported_snapshot = cursor.fetchone()[0]
        local_receipts = {}
        before, references = fingerprints(original, directory, local_receipts)
        if snapshot_callback is not None:
            snapshot_callback(exported_snapshot)
        if _heads(original) != original_heads:
            raise RefreshError("refresh_source_schema_incompatible")
        major = original.server_version // 10000
        dump_tool, restore_tool = _pg_tool("pg_dump", major), _pg_tool("pg_restore", major)
        _run([dump_tool, "--dbname=" + source["database"], "--format=custom", "--no-owner", "--no-privileges",
              "--snapshot=" + exported_snapshot, "--file=" + str(dump)], config=source, port=source_port, readonly=True)
        original.rollback()
    with _proxy(destination) as destination_port:
        admin = _connect(destination, destination_port, database="postgres", readonly=False)
        try:
            if admin.server_version // 10000 < major:
                raise RefreshError("refresh_database_server_version_incompatible")
            admin.autocommit = True
            with admin.cursor() as cursor:
                cursor.execute("SELECT datname FROM pg_database WHERE datname=%s", (target,))
                if cursor.fetchone():
                    raise RefreshError("refresh_destination_already_exists")
                cursor.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(target)))
        finally:
            admin.close()
        _run([restore_tool, "--dbname=" + target, "--no-owner", "--no-privileges", "--single-transaction", "--exit-on-error", str(dump)],
             config=destination, port=destination_port)
        with closing(_connect(destination, destination_port, database=target)) as copied:
            after, _ = fingerprints(copied, directory)
            if before != after or _heads(copied) != original_heads:
                raise RefreshError("refresh_database_verification_failed")
            copied.rollback()
        if original_heads != sorted(expected_heads):
            from sqlalchemy import create_engine
            from sqlalchemy.engine import URL
            from src import staging_copy_schema as preservation
            verification = create_engine(URL.create('postgresql+psycopg2', username=destination['user'],
                password=destination['password'], host='127.0.0.1', port=destination_port, database=target), hide_parameters=True)
            try:
                with verification.connect().execution_options(postgresql_readonly=True) as connection:
                    original_columns, preserved = preservation.preserve_before(connection, before)
                proof = upgrade(destination, destination_port, target, before, original_heads, expected_heads)
                if (not isinstance(proof, dict) or proof.get("existing_values_preserved") is not True
                        or proof.get('preserved_tables') != preserved):
                    raise RefreshError("refresh_copy_upgrade_unverified")
                # Independently verify the remote migration's receipt from the copied DB itself.
                with verification.connect().execution_options(postgresql_readonly=True) as connection:
                    preservation.require_preserved(connection, original_columns, preserved)
            finally:
                verification.dispose()
            with closing(_connect(destination, destination_port, database=target)) as copied:
                after, _ = fingerprints(copied, directory)
                if _heads(copied) != sorted(expected_heads):
                    raise RefreshError("refresh_copy_upgrade_head_mismatch")
                copied.rollback()
            if receipt is not None:
                receipt.update(proof, source_tables=before, source_heads=original_heads, target_heads=sorted(expected_heads))
            before = after
    dump.unlink()
    return before, references, local_receipts


from src.services.staging_prod_settings_transport import export_runtime_settings


class ObjectCopier:
    def __init__(self, client, bucket, prefix, source_buckets, aliases, local_receipts=None, *, source_environment=None):
        self.client, self.bucket, self.prefix = client, bucket, prefix
        self.allowed = set(source_buckets)
        self.aliases = aliases
        self.objects = {}
        self.missing_at_source = set()
        self.local_receipts = local_receipts or {}
        self.source_environment = source_environment or {}

    def copy_exported_setting(self, key, exported):
        import base64
        receipt = exported["settings"][key]
        content = base64.b64decode(receipt["content_base64"], validate=True)
        logical = "production-settings://" + exported["revision"] + "/" + key
        digest = hashlib.sha256(content).hexdigest()
        if digest != receipt["sha256"]:
            raise RefreshError("refresh_production_settings_export_mismatch")
        physical = self.bucket.blob(self.prefix + "/objects/" + hashlib.sha256(logical.encode()).hexdigest())
        physical.upload_from_string(content, if_generation_match=0, timeout=120)
        generation = int(physical.generation)
        actual = physical.download_as_bytes(if_generation_match=generation, timeout=120)
        if hashlib.sha256(actual).hexdigest() != digest:
            raise RefreshError("refresh_object_verification_failed")
        entry = {"uri": "gs://" + self.bucket.name + "/" + physical.name, "generation": str(generation), "sha256": digest,
            "source_export": {"revision": exported["revision"], "setting": key, "path": receipt["path"],
                              "raw_file_sha256": receipt["raw_file_sha256"], "database_snapshot": exported["database_snapshot"]}}
        self.objects[logical] = self.objects[entry["uri"]] = entry
        for reference in _references(json.loads(content)):
            self.copy(reference)
        return entry["uri"]

    def copy(self, original, *, required=False):
        if original in self.objects:
            return self.objects[original]["uri"]
        if original in self.missing_at_source:
            if required:
                raise RefreshError("refresh_required_setting_missing_at_source")
            return None
        if original in self.aliases and not datasets.local_source_reference(original):
            raise RefreshError("refresh_alias_original_not_local")
        alias = self.aliases.get(original, original)
        configured_receipt = None
        if isinstance(alias, dict):
            if set(alias) != {"uri", "source_identity", "sha256"}:
                raise RefreshError("refresh_local_source_receipt_invalid")
            identity = alias["source_identity"]
            required_identity = {"mode", "device", "inode", "size", "mtime_ns"}
            if (not isinstance(identity, dict) or set(identity) != required_identity or identity.get("mode") != "local"
                    or not re.fullmatch(r"[0-9a-f]{64}", str(alias["sha256"]))
                    or any(type(identity[key]) is not int or identity[key] < 0 for key in required_identity - {"mode"})):
                raise RefreshError("refresh_local_source_receipt_invalid")
            configured_receipt = {"storage_version": identity, "pdf_sha256": alias["sha256"]}
            source_uri = alias["uri"]
        else:
            source_uri = alias
        if not isinstance(source_uri, str):
            raise RefreshError("refresh_original_object_source_unresolved")
        def source_bucket(match):
            key = match.group(1)
            value = self.source_environment.get(key)
            if key != 'TEMPLATE_BUCKET' or value not in self.allowed:
                raise RefreshError('refresh_source_uri_environment_unresolved')
            return value
        source_uri = re.sub(r'\$\{([A-Z0-9_]+)\}', source_bucket, source_uri)
        parsed = urlsplit(source_uri)
        if parsed.scheme != "gs" or parsed.netloc not in self.allowed or parsed.fragment:
            raise RefreshError("refresh_original_object_source_unresolved")
        query = parse_qs(parsed.query, strict_parsing=True)
        if set(query) - {"generation"} or len(query.get("generation", [])) > 1:
            raise RefreshError("refresh_object_generation_invalid")
        generation = (query.get("generation") or [None])[0]
        if original in self.aliases and generation is None:
            raise RefreshError("refresh_local_source_generation_required")
        if generation is not None and not re.fullmatch(r"[1-9][0-9]*", generation):
            raise RefreshError("refresh_object_generation_invalid")
        blob = self.client.bucket(parsed.netloc).blob(unquote(parsed.path.lstrip("/")), generation=int(generation) if generation else None)
        from google.api_core.exceptions import NotFound
        try:
            blob.reload(timeout=30)
            content = blob.download_as_bytes(if_generation_match=int(blob.generation), timeout=120)
        except NotFound:
            # Absence is source data, not a reason to inject an old/different file.
            if original in self.aliases or required:
                raise RefreshError("refresh_required_original_missing_at_source") from None
            self.missing_at_source.add(original)
            return None
        digest = hashlib.sha256(content).hexdigest()
        local_proof = None
        if original.startswith(("/", "file://")):
            receipt = self.local_receipts.get(original)
            if receipt and configured_receipt and receipt != configured_receipt:
                raise RefreshError("refresh_local_original_proof_mismatch")
            receipt = receipt or configured_receipt
            if not receipt:
                raise RefreshError("refresh_local_original_proof_mismatch")
            if receipt["pdf_sha256"] != digest or receipt["storage_version"]["size"] != len(content):
                raise RefreshError("refresh_local_original_proof_mismatch")
            local_proof = dict(receipt["storage_version"])
        physical = self.bucket.blob(self.prefix + "/objects/" + hashlib.sha256(original.encode()).hexdigest())
        physical.upload_from_string(content, if_generation_match=0, timeout=120)
        physical_generation = int(physical.generation)
        actual = physical.download_as_bytes(if_generation_match=physical_generation, timeout=120)
        if hashlib.sha256(actual).hexdigest() != digest:
            raise RefreshError("refresh_object_verification_failed")
        entry = {"uri": "gs://" + self.bucket.name + "/" + physical.name,
                 "generation": str(physical_generation), "sha256": digest}
        if local_proof is not None:
            entry["source_identity"] = local_proof
        # Save identity before recursion; repeated/self-referencing JSON does not loop.
        self.objects[original] = entry
        self.objects[entry["uri"]] = entry
        base = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
        self.objects[base + "?generation=" + str(blob.generation)] = entry
        if original not in self.aliases and generation is None:
            self.objects[base] = entry
        try:
            document = json.loads(content)
        except (UnicodeDecodeError, ValueError):
            document = None
        for reference in _references(document):
            self.copy(reference)
        return entry["uri"]


def build_snapshot(job, config, progress):
    from google.cloud import storage
    client = storage.Client()
    control = urlsplit(datasets.refresh_control_uri())
    value = {"schema": 1, "id": job["dataset"], "state": "ready", "snapshot_at": datetime.now(timezone.utc).isoformat(), "runtime_settings_provenance": "production_environment", "components": {}}
    # A fresh attempt has distinct DBs/files; a late worker cannot touch a successor.
    target = "sawa_replay_" + job["id"][:24] + "_" + job["attempt"][:8]
    with tempfile.TemporaryDirectory(prefix="sawa-copy-refresh-") as temporary:
        for name in ("order", "shift", "school-lunch"):
            entry = config["components"][name]
            if entry.get("mode") == "retain":
                retained = datasets.retained_snapshot(entry["snapshot"])
                parent = datasets._runtime_manifest(retained)
                if parent["id"] != retained["id"] or parent["snapshot_at"] != retained["snapshot_at"]:
                    raise RefreshError("refresh_retained_snapshot_mismatch")
                value["components"][name] = parent["components"][name]
                value.setdefault("retained_components", {})[name] = parent.get("retained_components", {}).get(name, retained)
                continue
            progress("copying_database", name)
            source_uri, source_environment, source_revision = runtime_source(entry["source_service"], set(entry["settings_keys"]) | (set(datasets.RUNTIME_BACKEND_KEYS) if name == "order" else datasets.RUNTIME_SETTING_KEYS[name]))
            runtime_keys = set(datasets.RUNTIME_BACKEND_KEYS) if name == "order" else set(datasets.RUNTIME_SETTING_KEYS[name])
            runtime_settings = {key: source_environment.get(key) for key in runtime_keys}
            producer_revision = None
            producer_runtime_settings = {}
            if name == "order":
                producer_environment, producer_revision = runtime_environment(entry["producer_service"], datasets.RUNTIME_PRODUCER_KEYS)
                producer_runtime_settings = {key: producer_environment.get(key) for key in datasets.RUNTIME_PRODUCER_KEYS}
            for key in ("GEMINI_API_BASE", "OPENAI_API_BASE"):
                if runtime_settings.get(key):
                    endpoint = urlsplit(runtime_settings[key])
                    if endpoint.scheme not in {"https", "http"} or not endpoint.netloc or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
                        raise RefreshError("refresh_business_endpoint_contains_credentials_or_invalid")
            destination_uri, destination_environment, _ = runtime_source(entry["destination_service"])
            source = _database(source_uri, "prod")
            destination = _database(destination_uri, "stg")
            runtime_manifest_uri = destination_environment.get("SAWA_STG_REPLAY_MANIFEST_URI", "")
            if urlsplit(runtime_manifest_uri).netloc != entry["destination_bucket"]:
                raise RefreshError("refresh_destination_runtime_bucket_mismatch")
            directory = Path(temporary) / name
            directory.mkdir(mode=0o700)
            migration = {}
            exported_settings = {}
            clone_options = {}
            if "source_migration_heads" in entry:
                from src.services.staging_copy_migration import upgrade_hospital, upgrade_shift
                clone_options = {"source_heads": entry["source_migration_heads"], "receipt": migration,
                    "upgrade": upgrade_hospital if name == "order" else
                        lambda *args: upgrade_shift(entry["destination_service"], job, *args)}
            if entry.get("settings_mode") == "runtime_export":
                clone_options["snapshot_callback"] = lambda snapshot: exported_settings.update(export_runtime_settings(source_revision, snapshot))
            tables, references, local_receipts = clone_database(source, destination, target, directory, entry["expected_migration_heads"], **clone_options)
            progress("copying_files_and_settings", name)
            if entry.get("settings_mode") == "runtime_export":
                settings_source = exported_settings["settings"]
            else:
                if any(key not in source_environment for key in entry["settings_keys"]):
                    raise RefreshError("refresh_runtime_setting_missing")
                settings_source = {key: source_environment[key] for key in entry["settings_keys"]}
            if not isinstance(settings_source, dict) or (not exported_settings and any(not isinstance(v, str) or not v for v in settings_source.values())):
                raise RefreshError("refresh_settings_source_invalid")
            if name == "order" and not {"FACILITY_MASTER_URI", "INGEST_POLICY_URI", "OCR_PIPELINE_STATE_URI"} <= set(settings_source):
                raise RefreshError("refresh_order_settings_incomplete")
            destination_bucket = client.bucket(entry["destination_bucket"])
            copier = ObjectCopier(client, destination_bucket, "stg-replay/" + job["dataset"] + "/" + job["attempt"] + "/" + name,
                                  entry["source_buckets"], entry["local_objects"], local_receipts,
                                  source_environment={'TEMPLATE_BUCKET': source_environment.get('TEMPLATE_BUCKET')})
            settings = {}
            for key, setting in settings_source.items():
                if exported_settings and key in {"FACILITY_MASTER_URI", "INGEST_POLICY_URI"} and isinstance(setting, dict):
                    settings[key] = copier.copy_exported_setting(key, exported_settings)
                elif key == "GCS_BUCKET_NAME":
                    if setting not in entry["source_buckets"]:
                        raise RefreshError("refresh_storage_setting_source_mismatch")
                    settings[key] = destination_bucket.name
                else:
                    settings[key] = copier.copy(setting, required=True)
            for reference in sorted(references):
                copier.copy(reference)
            if name == "order" and runtime_settings.get("SHIPPING_TEMPLATE_PATH"):
                copier.copy(runtime_settings["SHIPPING_TEMPLATE_PATH"], required=True)
            for reference in sorted(entry["local_objects"]):
                copier.copy(reference, required=True)
            value["components"][name] = {"database": target, "verified": True,
                "runtime_settings": runtime_settings, "producer_runtime_settings": producer_runtime_settings, "producer_revision": producer_revision,
                "table_digests": {table: proof["sha256"] for table, proof in tables.items()},
                "settings": settings, "objects": copier.objects,
                "table_counts": {table: proof["rows"] for table, proof in tables.items()},
                "migration": migration,
                "source_instance": source["instance"], "source_database": source["database"],
                "source_configuration_sha256": job["source_configuration_sha256"], "source_revision": source_revision,
                "missing_at_source": sorted(copier.missing_at_source)}
    progress("verifying_manifest")
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    digest = hashlib.sha256(raw).hexdigest()
    destinations = {entry["destination_bucket"] for entry in config["components"].values()}
    if len(destinations) != 3 or control.netloc not in destinations:
        raise RefreshError("refresh_destination_buckets_invalid")
    published = {}
    for destination in sorted(destinations):
        mirror = client.bucket(destination)
        manifest = mirror.blob("stg-replay/" + job["dataset"] + "/manifests/" + digest + ".json")
        manifest.upload_from_string(raw, if_generation_match=0, content_type="application/json")
        uri = "gs://" + mirror.name + "/" + manifest.name
        if datasets._read_manifest(uri, digest) != value:
            raise RefreshError("refresh_manifest_verification_failed")
        published[destination] = {"uri": uri, "sha256": digest, "snapshot_at": value["snapshot_at"]}
    # Control service commits active + terminal state together, after every replica exists.
    return {"id": job["dataset"], **published[control.netloc],
            "missing_at_source": {name: len(component["missing_at_source"]) for name, component in value["components"].items() if name not in value.get("retained_components", {})}}
