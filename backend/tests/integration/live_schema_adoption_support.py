"""Owned real PostgreSQL lifecycle and reproducible observed contract generation."""
from contextlib import contextmanager, nullcontext
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
import sqlalchemy as sa

from src.maintenance import formal_migration_schema as schema

ROOT = Path(__file__).resolve().parents[3]


def _owned_postgres_settings():
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise RuntimeError("owned PostgreSQL is restricted to GitHub Actions")
    pg_bin_value = os.environ.get("OWNED_POSTGRES_PG_BIN")
    tmpdir_value = os.environ.get("OWNED_POSTGRES_TMPDIR")
    if not pg_bin_value or not tmpdir_value:
        raise RuntimeError("owned PostgreSQL requires explicit PG_BIN and tmpdir")
    pg_bin = Path(pg_bin_value)
    tmpdir = Path(tmpdir_value)
    if not (pg_bin / "initdb").is_file() or not (pg_bin / "pg_ctl").is_file():
        raise RuntimeError("owned PostgreSQL requires PG16 initdb and pg_ctl")
    tmpdir.mkdir(parents=True, exist_ok=True)
    return pg_bin, tmpdir


@contextmanager
def owned_postgres():
    pg_bin, tmpdir = _owned_postgres_settings()
    root = Path(tempfile.mkdtemp(prefix="sawa-adopt35-", dir=tmpdir))
    (root / "socket").mkdir()
    commands = []
    def run(args):
        commands.append(args)
        result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        with (root / "commands.log").open("a") as log:
            log.write(result.stdout)
        if result.returncode:
            raise RuntimeError(f"owned PostgreSQL command failed: {result.stdout}")
    started = False
    try:
        run([str(pg_bin / "initdb"), "-D", str(root / "pgdata"), "-A", "trust", "-U", "adoption_test", "--no-locale", "--encoding=UTF8",
             "-c", "shared_memory_type=mmap", "-c", "dynamic_shared_memory_type=mmap"])
        run([str(pg_bin / "pg_ctl"), "-D", str(root / "pgdata"), "-l", str(root / "postgres.log"),
             "-o", f"-k {root / 'socket'} -p 55495 -c listen_addresses=''", "-w", "start"])
        started = True
        url = sa.URL.create("postgresql+psycopg2", username="adoption_test", database="postgres",
                            query={"host": str(root / "socket"), "port": "55495"})
        engine = sa.create_engine(url)
        try:
            with engine.connect() as conn:
                assert Path(conn.exec_driver_sql("SHOW data_directory").scalar_one()) == root / "pgdata"
                assert conn.exec_driver_sql("SHOW listen_addresses").scalar_one() == ""
            yield engine, root
        finally:
            engine.dispose()
    finally:
        if started:
            run([str(pg_bin / "pg_ctl"), "-D", str(root / "pgdata"), "-m", "fast", "-w", "stop"])
        (root / "lifecycle.json").write_text(json.dumps({"commands": commands, "stopped": not (root / "pgdata/postmaster.pid").exists()}, indent=2))


@contextmanager
def target(url):
    names = ("ALEMBIC_DB_URI", "DB_URI", "DATABASE_URL", "DB_USER", "DB_PASSWORD", "DB_NAME", "DB_HOST", "DB_PORT", "DB_DRIVER")
    old = {name: os.environ.get(name) for name in names}
    for name in names:
        os.environ.pop(name, None)
    os.environ["ALEMBIC_DB_URI"] = url.render_as_string(hide_password=False)
    try:
        yield
    finally:
        for name, value in old.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def restore_captured(conn, shapes):
    """Construct the captured physical schema, not an approximation of a numbered revision."""
    quote = conn.dialect.identifier_preparer.quote
    for table, shape in shapes.items():
        natives = {item["column_name"]: item for item in shape["native"] if "column_name" in item}
        columns = []
        for col in shape["columns"]:
            declaration = quote(col["name"]) + " " + natives[col["name"]]["native_type"]
            if col["default"] is not None:
                declaration += " DEFAULT " + col["default"]
            if not col["nullable"]:
                declaration += " NOT NULL"
            columns.append(declaration)
        conn.exec_driver_sql("CREATE TABLE " + quote(table) + " (" + ",".join(columns) + ")")
    for kind in ("p", "u", "c", "f"):
        for table, shape in shapes.items():
            for item in shape["native"]:
                if item.get("contype") == kind:
                    conn.exec_driver_sql("ALTER TABLE " + quote(table) + " ADD CONSTRAINT "
                                         + quote(item["constraint_name"]) + " " + item["definition"])
    for shape in shapes.values():
        seen = set()
        for item in shape["native"]:
            if "index_name" in item and item["owner"] is None and item["index_name"] not in seen:
                conn.exec_driver_sql(item["definition"])
                seen.add(item["index_name"])


def generate(proof_root, contract_path, *, sqlite_only=False):
    proof_root.mkdir(parents=True, exist_ok=False)
    contract = json.loads(schema.CONTRACT.read_text())
    original = json.loads(schema.CONTRACT.read_text())
    contract_path = contract_path.resolve()
    if proof_root.resolve() not in contract_path.parents:
        raise RuntimeError("generated contract must be contained by the proof directory")
    # Persist the reviewed baseline contract before PostgreSQL initialization so failure artifacts are complete.
    contract_path.write_text(json.dumps(contract, sort_keys=True, separators=(",", ":")) + "\n")
    def register(shapes):
        refs = {}
        for table, shape in shapes.items():
            raw = json.dumps(shape, sort_keys=True, separators=(",", ":")).encode()
            identity = hashlib.sha256(raw).hexdigest()
            contract["shapes"][identity] = shape
            refs[table] = identity
        return refs
    with (nullcontext((None, None)) if sqlite_only else owned_postgres()) as (pg, pgroot):
        engines = ([pg] if pg is not None else []) + [sa.create_engine("sqlite:///" + str(proof_root.resolve() / "fresh.sqlite"))]
        for engine in engines:
            with target(engine.url):
                command.upgrade(Config(str(schema.ALEMBIC_CONFIG)), "0034")
            with engine.begin() as conn:
                with Operations.context(MigrationContext.configure(conn)):
                    ScriptDirectory.from_config(Config(str(schema.ALEMBIC_CONFIG))).get_revision("0035").module.upgrade()
                conn.exec_driver_sql("UPDATE alembic_version SET version_num='0035'")
                catalog = schema.read_catalog(conn)
            dialect = engine.dialect.name
            raw = json.dumps(catalog, sort_keys=True, indent=2).encode()
            (proof_root / (dialect + "-0035.json")).write_bytes(raw)
            contract["revisions"][dialect]["0035"] = {
                "tables": register(schema.table_shapes(catalog, dialect)), "views": catalog["views"],
                "column_identity": "name" if dialect == "postgresql" else "position",
            }
            contract["sources"][dialect + "-0035.json"] = hashlib.sha256(raw).hexdigest()
            if engine is not pg:
                engine.dispose()
        (proof_root / "postgres-root.txt").write_text(str(pgroot))
    for dialect, revisions in original["revisions"].items():
        assert all(contract["revisions"][dialect][rev] == value for rev, value in revisions.items() if rev != "0035")
    assert contract["adoption_baselines"] == original["adoption_baselines"]
    assert all(contract["shapes"][key] == value for key, value in original["shapes"].items())
    contract_path.write_text(json.dumps(contract, sort_keys=True, separators=(",", ":")) + "\n")
    print(json.dumps({"contract_sha256": hashlib.sha256(contract_path.read_bytes()).hexdigest(),
                      "proof": str(proof_root), "pgroot": str(pgroot)}))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--proof", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--sqlite-only", action="store_true")
    args = parser.parse_args()
    generate(args.proof, args.contract, sqlite_only=args.sqlite_only)
