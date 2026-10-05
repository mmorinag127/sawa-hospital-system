"""No cloud calls: real isolated PG DDL, with cloud/proxy transport faked only."""

from contextlib import contextmanager
from datetime import datetime
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock
from uuid import uuid4

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
import sqlalchemy as sa
import yaml

from src.models.menu import MenuMaster, MenuFacilityOverride, MonthlyMenu, MonthlyMenuItem, MonthlyMenuEntry
from src.models.user import User, AuditLog


ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("stg_migration_runner", ROOT / "scripts/run_stg_menu_master_migration.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
import apply_menu_master_revision_migration as migration  # noqa: E402
from scripts import portal_prod_db_bootstrap as config_reader  # noqa: E402
import staging_db_target as db_target  # noqa: E402
from test_staging_db_target import install_cloud  # noqa: E402


CONTEXT = {
    "GITHUB_ACTIONS": "true", "GITHUB_RUN_ID": "12345", "GITHUB_REF": "refs/heads/develop",
    "GITHUB_REF_NAME": "develop", "PROJECT_ID": "sawahospitalsystem", "REGION": "asia-northeast2",
    "WEB_SERVICE": "web-stg", "WORKER_SERVICE": "worker-stg",
}
KEYS = "monthly_menu_id, name, COALESCE(daypart, ''), COALESCE(category, ''), COALESCE(diet_type, ''), COALESCE(facility_override, '')"
INDEX = "uq_monthly_menu_items_scope_identity"


def apply_prior(connection, filename):
    spec = importlib.util.spec_from_file_location("prior_migration", ROOT / "backend/migrations" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(connection)):
        module.upgrade()


@pytest.fixture
def context(monkeypatch):
    for key, value in CONTEXT.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def pg():
    raw = os.getenv("C1_STG_PG_URI")
    if not raw:
        pytest.skip("C1_STG_PG_URI required; no PostgreSQL proof without a dedicated cluster")
    url = sa.make_url(raw)
    assert url.drivername == "postgresql+psycopg2" and url.host is None
    assert url.database == "c1_stg_migration" and url.query["port"] == "55447"
    socket = Path(url.query["host"]).resolve()
    assert socket.parent == Path("/private/tmp") and socket.name.startswith("sawa-c1stg-pg.")
    assert (socket / "OWNER").read_text().strip() == str(ROOT)
    assert not url.query.get("options")
    owner = sa.create_engine(url)
    with owner.connect() as connection:
        assert Path(connection.execute(sa.text("SHOW data_directory")).scalar_one()).resolve() == ROOT / "tmp/stg-migration/pgdata"
        assert connection.execute(sa.text("SHOW listen_addresses")).scalar_one() == ""
    schema = "stgtest_" + uuid4().hex
    with owner.begin() as connection:
        connection.execute(sa.schema.CreateSchema(schema))
    engine = sa.create_engine(url.update_query_dict({"options": f"-csearch_path={schema}"}))
    try:
        yield engine
    finally:
        engine.dispose()
        with owner.begin() as connection:
            connection.execute(sa.schema.DropSchema(schema, cascade=True))
        owner.dispose()


@pytest.fixture
def prior(pg):
    metadata = sa.MetaData()
    master = sa.Table("menu_masters", metadata, *[
        sa.Column(column.name, column.type, primary_key=column.primary_key, nullable=column.nullable)
        for column in MenuMaster.__table__.columns if column.name != "revision"
    ])
    sa.Index("ix_menu_masters_normalized_name", master.c.normalized_name, unique=True)
    for model in (MenuFacilityOverride, MonthlyMenu, MonthlyMenuItem, MonthlyMenuEntry, User, AuditLog):
        model.__table__.to_metadata(metadata)
    # Already-migrated prior shape, NOT proof of the fresh historical chain.
    monthly = metadata.tables["monthly_menu_items"]
    monthly.constraints.difference_update({c for c in monthly.constraints if c.name == "uq_monthly_menu_item_scope"})
    metadata.create_all(pg)
    with pg.begin() as connection:
        connection.execute(metadata.tables["users"].insert(), {
            "id": "operator", "account": "operator@c1.invalid", "role": "operator", "status": "active",
        })
        apply_prior(connection, "0023_monthly_menu_item_identity_indexes.py")
        apply_prior(connection, "0026_user_system_access.py")
        connection.execute(master.insert(), [
            {"id": unit, "name": " Old " + unit, "normalized_name": "old-" + unit, "unit_type": unit,
             "qty_per_serving": 0 if unit == "count" else None, "bag_max_qty": 12, "bag_max_unit": unit,
             "temp_type": "hot", "daypart": "lunch", "category": "Legacy", "condiments": [" Sauce "],
             "updated_at": datetime(2020, 1, 2, 3, 4, 5)} for unit in ("g", "cut", "count")
        ])
    return pg


def snapshot(engine):
    with engine.connect() as connection:
        return {name: connection.execute(sa.text(f'SELECT * FROM "{name}" ORDER BY 1')).all()
                for name in sa.inspect(connection).get_table_names()}


def test_real_0027_preserves_old_menu_and_operator_data_and_noop_is_readonly(prior):
    before = snapshot(prior)
    with prior.begin() as connection:
        migration.upgrade_staging(connection)
    after = snapshot(prior)
    for name in before:
        assert ([tuple(row[:-1]) for row in after[name]] if name == "menu_masters" else after[name]) == before[name]
    assert [row[-1] for row in after["menu_masters"]] == [1, 1, 1]
    with prior.begin() as connection:
        connection.execute(sa.text("UPDATE menu_masters SET revision=5 WHERE id='cut'"))
    after = snapshot(prior)
    statements = []
    sa.event.listen(prior, "before_cursor_execute", lambda conn, cursor, sql, *args: statements.append(sql))
    with prior.begin() as connection:
        migration.upgrade_staging(connection)
    assert snapshot(prior) == after
    assert not any(sql.lstrip().upper().startswith(("CREATE", "ALTER", "DROP", "INSERT", "UPDATE", "DELETE")) for sql in statements)


@pytest.mark.parametrize("table", ["menu_masters", "menu_facility_overrides", "monthly_menu_items",
    "monthly_menu_entries", "monthly_menus", "users", "audit_logs", "user_system_access"])
def test_missing_prerequisite_table_stops_without_ddl(prior, table):
    with prior.begin() as connection:
        connection.execute(sa.text(f'DROP TABLE "{table}" CASCADE'))
    before = snapshot(prior)
    with pytest.raises(migration.MigrationBlocked, match="prior table required"):
        with prior.begin() as connection:
            migration.upgrade_staging(connection)
    assert snapshot(prior) == before
    if table != "menu_masters":
        assert "revision" not in {col["name"] for col in sa.inspect(prior).get_columns("menu_masters")}


@pytest.mark.parametrize("definition", [None,
    f"CREATE INDEX {INDEX} ON monthly_menu_items ({KEYS})",
    f"CREATE UNIQUE INDEX {INDEX} ON monthly_menu_items ({KEYS}) WHERE category IS NOT NULL",
    f"CREATE UNIQUE INDEX {INDEX} ON monthly_menu_items ({KEYS.replace('monthly_menu_id, name', 'name, monthly_menu_id')})",
    f"CREATE UNIQUE INDEX {INDEX} ON monthly_menu_items (" + KEYS.replace("COALESCE(daypart, '')", "daypart") + ")",
    f"CREATE UNIQUE INDEX {INDEX} ON monthly_menu_items (" + KEYS.replace("COALESCE(daypart, '')", "COALESCE(daypart, 'missing')") + ")",
    f"CREATE UNIQUE INDEX {INDEX} ON monthly_menu_items ({KEYS}, id)",
    f"CREATE UNIQUE INDEX {INDEX} ON monthly_menu_items ({KEYS}) INCLUDE (id)",
    f"CREATE UNIQUE INDEX {INDEX} ON monthly_menu_items (monthly_menu_id, name)",
    f"CREATE UNIQUE INDEX {INDEX} ON monthly_menu_items ({KEYS.replace('monthly_menu_id,', 'monthly_menu_id DESC,')})",
])
def test_index_name_is_insufficient_exact_six_keys_required(prior, definition):
    with prior.begin() as connection:
        connection.execute(sa.text(f"DROP INDEX {INDEX}"))
        if definition:
            connection.execute(sa.text(definition))
    before = snapshot(prior)
    with pytest.raises(migration.MigrationBlocked, match="six-key NULL-coalescing"):
        with prior.begin() as connection:
            migration.upgrade_staging(connection)
    assert snapshot(prior) == before
    assert "revision" not in {col["name"] for col in sa.inspect(prior).get_columns("menu_masters")}


@pytest.mark.parametrize("sql", [
    "ALTER TABLE menu_masters DROP COLUMN condiments",
    "ALTER TABLE monthly_menu_items DROP COLUMN master_resolution_mode",
    "ALTER TABLE menu_masters ALTER COLUMN name DROP NOT NULL",
    "ALTER TABLE menu_masters ALTER COLUMN qty_per_serving TYPE TEXT",
    "DROP INDEX ix_menu_masters_normalized_name",
    "ALTER TABLE user_system_access ALTER COLUMN enabled DROP NOT NULL",
    "ALTER TABLE user_system_access DROP COLUMN enabled",
    "ALTER TABLE user_system_access ALTER COLUMN enabled DROP DEFAULT; "
    "ALTER TABLE user_system_access ALTER COLUMN enabled TYPE TEXT",
    "ALTER TABLE user_system_access DROP CONSTRAINT user_system_access_pkey",
])
def test_invalid_prior_schema_is_not_repaired(prior, sql):
    with prior.begin() as connection:
        connection.execute(sa.text(sql))
    before = snapshot(prior)
    with pytest.raises(migration.MigrationBlocked):
        with prior.begin() as connection:
            migration.upgrade_staging(connection)
    assert snapshot(prior) == before
    assert "revision" not in {col["name"] for col in sa.inspect(prior).get_columns("menu_masters")}


@pytest.mark.parametrize("extension", [
    "ALTER TABLE user_system_access ADD COLUMN description TEXT",
    "ALTER TABLE user_system_access ADD CHECK (length(user_id) > 0)",
    "ALTER TABLE user_system_access DROP CONSTRAINT ck_user_system_access_system_key; "
    "ALTER TABLE user_system_access ADD CHECK (system_key IN ('school-lunch','hospital','shift'))",
])
def test_compatible_access_extensions_do_not_block_revision_migration(prior, extension):
    with prior.begin() as connection:
        connection.execute(sa.text(extension))
    before = snapshot(prior)
    with prior.begin() as connection:
        migration.upgrade_staging(connection)
    after = snapshot(prior)
    assert after["user_system_access"] == before["user_system_access"]
    assert after["users"] == before["users"]
    assert [tuple(row[:-1]) for row in after["menu_masters"]] == before["menu_masters"]


@pytest.mark.parametrize("definition", ["TEXT NOT NULL DEFAULT '1'", "INTEGER DEFAULT 1", "INTEGER NOT NULL DEFAULT 2"])
def test_invalid_revision_not_overwritten(prior, definition):
    with prior.begin() as connection:
        connection.execute(sa.text("ALTER TABLE menu_masters ADD COLUMN revision " + definition))
    before = snapshot(prior)
    with pytest.raises(migration.MigrationBlocked, match="revision must be"):
        with prior.begin() as connection:
            migration.upgrade_staging(connection)
    assert snapshot(prior) == before


@pytest.mark.parametrize("key,value", [(key, "") for key in CONTEXT] + [
    ("GITHUB_ACTIONS", "false"), ("GITHUB_RUN_ID", "not-numeric"),
    ("GITHUB_REF", "refs/tags/develop"), ("GITHUB_REF_NAME", "main"),
    ("PROJECT_ID", "other-project"), ("REGION", "asia-northeast1"),
    ("WEB_SERVICE", "web-prod"), ("WORKER_SERVICE", "worker-prod"),
])
def test_guard_rejections_precede_all_cloud_proxy_and_db_calls(context, monkeypatch, key, value, capsys):
    monkeypatch.setenv(key, value)
    cloud, proxy, engine = Mock(), Mock(), Mock()
    monkeypatch.setattr(runner, "_load_service_db_config", cloud)
    monkeypatch.setattr(runner, "cloud_sql_proxy", proxy)
    monkeypatch.setattr(runner, "create_engine", engine)
    assert runner.main() == 1
    cloud.assert_not_called()
    proxy.assert_not_called()
    engine.assert_not_called()
    assert "0027 blocked" in capsys.readouterr().err


@pytest.fixture
def cloud_transport(context, monkeypatch):
    _, _, calls, secret = install_cloud(monkeypatch)
    # The proxy fixture redirects only to our independently owned PG database.
    monkeypatch.setattr(runner, 'verify_connection_target', lambda connection:
        db_target.verify_connection_target(connection, database='c1_stg_migration', role=sa.make_url(os.environ['C1_STG_PG_URI']).username))

    @contextmanager
    def proxy(instance):
        assert instance == "sawahospitalsystem:asia-northeast2:orders-stg"
        yield

    monkeypatch.setattr(runner, "cloud_sql_proxy", proxy)
    return calls, secret


def test_full_runner_real_db_transaction_reader_reuse_and_secretless_url(prior, cloud_transport, monkeypatch, capsys):
    before = snapshot(prior)
    urls = []

    def connect(url, **kwargs):
        urls.append(url)
        assert kwargs["hide_parameters"] is True
        return prior  # Only redirect the transport; upgrade/transaction/DDL are real.

    monkeypatch.setattr(runner, "create_engine", connect)
    assert runner.main() == 0
    after = snapshot(prior)
    assert [tuple(row[:-1]) for row in after["menu_masters"]] == before["menu_masters"]
    assert after["users"] == before["users"] and after["user_system_access"] == before["user_system_access"]
    assert runner.main() == 0 and snapshot(prior) == after
    calls, secret = cloud_transport
    assert [call[3] for call in calls] == ["web-stg", "web-stg-r1", "worker-stg", "worker-stg-r1"] * 2
    assert secret.call_count == 8
    assert urls[0].username == "orders_app" and urls[0].password == "private-password-sentinel"
    assert (urls[0].host, urls[0].port, urls[0].database) == ("127.0.0.1", 5432, "orders")
    output = capsys.readouterr()
    assert "private-password" not in output.out + output.err


def test_runner_schema_failure_exit_one_and_no_revision(prior, cloud_transport, monkeypatch, capsys):
    with prior.begin() as connection:
        connection.execute(sa.text(f"DROP INDEX {INDEX}"))
    before = snapshot(prior)
    monkeypatch.setattr(runner, "create_engine", lambda *args, **kwargs: prior)
    assert runner.main() == 1
    assert "0023" in capsys.readouterr().err and snapshot(prior) == before


def test_real_connected_target_mismatch_stops_before_migration(prior, cloud_transport, monkeypatch, capsys):
    before = snapshot(prior)
    monkeypatch.setattr(runner, "create_engine", lambda *args, **kwargs: prior)
    monkeypatch.setattr(runner, "verify_connection_target", db_target.verify_connection_target)
    assert runner.main() == 1  # Own c1_stg_migration is deliberately not orders/orders_app.
    assert 'connected-database-or-role-mismatch' in capsys.readouterr().err
    assert snapshot(prior) == before
    assert "revision" not in {col["name"] for col in sa.inspect(prior).get_columns("menu_masters")}


def test_real_migration_lock_failure_fails_runner_and_rolls_back(prior, cloud_transport, monkeypatch, capsys):
    before = snapshot(prior)
    monkeypatch.setattr(runner, "create_engine", lambda *args, **kwargs: prior)
    settings = []

    def observe(conn, cursor, sql, *args):
        if sql.startswith("ALTER TABLE menu_masters ADD COLUMN"):
            cursor.execute("SELECT current_setting('lock_timeout'), current_setting('statement_timeout')")
            settings.append(cursor.fetchone())

    sa.event.listen(prior, "before_cursor_execute", observe)
    with prior.begin() as blocker:
        blocker.execute(sa.text("LOCK TABLE menu_masters IN SHARE MODE"))
        started = time.monotonic()
        assert runner.main() == 1  # The real canonical ALTER, not a fake DDL failure.
        elapsed = time.monotonic() - started
    assert settings == [("5s", "1min")]
    assert 4 <= elapsed < 20, elapsed
    assert "cloud/DB details suppressed" in capsys.readouterr().err
    assert snapshot(prior) == before
    assert "revision" not in {col["name"] for col in sa.inspect(prior).get_columns("menu_masters")}


@pytest.mark.parametrize("mismatch", ["instance", "database", "wrong-project", "multiple-instances", "prod", "other", "other-database"])
def test_cloud_targets_must_match_before_proxy_or_database(context, monkeypatch, mismatch):
    first = SimpleNamespace(instance_connection_name="sawahospitalsystem:asia-northeast2:orders-stg", db_name="orders", db_user="orders_app")
    second = SimpleNamespace(**vars(first))
    if mismatch == "instance":
        second.instance_connection_name += "-other"
    elif mismatch == "database":
        second.db_name = "different"
    elif mismatch == "other-database":
        first.db_name = second.db_name = "different"
    elif mismatch in {"prod", "other"}:
        first.instance_connection_name = second.instance_connection_name = "sawahospitalsystem:asia-northeast2:" + ("orders-prod" if mismatch == "prod" else "other-instance")
    else:
        first.instance_connection_name = second.instance_connection_name = (
            "other:asia-northeast2:orders-stg" if mismatch == "wrong-project" else first.instance_connection_name + ",extra")
    monkeypatch.setattr(runner, "_load_service_db_config", Mock(side_effect=[first, second]))
    proxy, engine = Mock(), Mock()
    monkeypatch.setattr(runner, "cloud_sql_proxy", proxy)
    monkeypatch.setattr(runner, "create_engine", engine)
    assert runner.main() == 1
    proxy.assert_not_called()
    engine.assert_not_called()


@pytest.mark.parametrize("instance", ["orders-prod", "another-instance"])
def test_actual_reader_rejects_misdirected_stg_metadata_before_proxy(cloud_transport, monkeypatch, instance):
    describe = config_reader._run_gcloud_json

    def misdirected(*args):
        data = describe(*args)
        if args[1] == 'services':
            data["spec"]["template"]["metadata"]["annotations"]["run.googleapis.com/cloudsql-instances"] = (
                "sawahospitalsystem:asia-northeast2:" + instance)
        return data

    monkeypatch.setattr(config_reader, "_run_gcloud_json", misdirected)
    proxy, engine = Mock(), Mock()
    monkeypatch.setattr(runner, "cloud_sql_proxy", proxy)
    monkeypatch.setattr(runner, "create_engine", engine)
    assert runner.main() == 1
    assert [call[3] for call in cloud_transport[0]] == ["web-stg", "web-stg-r1"]
    proxy.assert_not_called()
    engine.assert_not_called()


@pytest.mark.parametrize("failure", ["config", "proxy", "engine", "connection"])
def test_secret_redaction_on_transport_errors(cloud_transport, monkeypatch, capsys, failure):
    error = RuntimeError("postgresql://user:private-password-sentinel@host/db SECRET_ENV_SENTINEL")
    if failure == "config":
        monkeypatch.setattr(runner, "_load_service_db_config", Mock(side_effect=error))
    elif failure == "proxy":
        monkeypatch.setattr(runner, "cloud_sql_proxy", Mock(side_effect=error))
    elif failure == "engine":
        monkeypatch.setattr(runner, "create_engine", Mock(side_effect=error))
    else:
        engine = Mock()
        engine.begin.side_effect = error
        monkeypatch.setattr(runner, "create_engine", Mock(return_value=engine))
    assert runner.main() == 1
    output = capsys.readouterr()
    assert output.out == "" and output.err == "0027 failed: staging migration error; cloud/DB details suppressed\n"
    if failure == "connection":
        engine.dispose.assert_called_once()


@pytest.mark.parametrize("failure", [None, "checksum", "port", "exit", "timeout", "body"])
def test_proxy_pinning_ownership_failure_and_cleanup(monkeypatch, failure):
    content = b"fake-proxy-transport"
    download = Mock(return_value=io.BytesIO(content))
    monkeypatch.setattr(runner.urllib.request, "urlopen", download)
    monkeypatch.setattr(runner, "PROXY_SHA256", hashlib.sha256(content).hexdigest() if failure != "checksum" else "bad")
    probe = MagicMock()
    if failure == "port":
        probe.__enter__.return_value.bind.side_effect = OSError("occupied")
    monkeypatch.setattr(runner.socket, "socket", Mock(return_value=probe))
    ready = Mock(return_value=MagicMock())
    if failure == "timeout":
        ready.side_effect = OSError("not ready")
    monkeypatch.setattr(runner.socket, "create_connection", ready)
    monkeypatch.setattr(runner.time, "sleep", Mock())
    process = Mock()
    process.poll.return_value = 1 if failure == "exit" else None
    popen = Mock(return_value=process)
    monkeypatch.setattr(runner.subprocess, "Popen", popen)

    def exercise():
        with runner.cloud_sql_proxy("test-instance"):
            if failure == "body":
                raise RuntimeError("migration failed")

    if failure:
        with pytest.raises((migration.MigrationBlocked, OSError, RuntimeError)):
            exercise()
    else:
        exercise()
    if failure in {"port", "checksum"}:
        popen.assert_not_called()
    else:
        argv = popen.call_args.args[0]
        assert argv[1:] == ["--address", "127.0.0.1", "--port", "5432", "test-instance"]
        assert not Path(argv[0]).exists()
        assert popen.call_args.kwargs["stdout"] == popen.call_args.kwargs["stderr"] == subprocess.DEVNULL
        if failure != "exit":
            process.terminate.assert_called_once()
        process.wait.assert_called_once_with(timeout=10)
    if failure == "port":
        download.assert_not_called()
    else:
        download.assert_called_once_with(runner.PROXY_URL, timeout=60)


def test_workflow_gates_both_deploy_surfaces_and_retains_bootstraps():
    workflow = yaml.load((ROOT / ".github/workflows/deploy-stg.yml").read_text(), Loader=yaml.BaseLoader)
    jobs = workflow["jobs"]
    gate = jobs["menu-master-migration"]
    assert gate["timeout-minutes"] == "10"
    assert gate["needs"] == ["source-gate", "automation-bootstrap"]
    assert gate["if"] == "needs.source-gate.outputs.backend_changed == 'true'"
    assert gate["permissions"] == {"contents": "read", "id-token": "write"}
    auth_step = next(step for step in gate["steps"] if step.get("uses") == "google-github-actions/auth@v3")
    assert auth_step["with"]["workload_identity_provider"] == "${{ secrets.GCP_WORKLOAD_IDENTITY_PROVIDER_STG }}"
    assert auth_step["with"]["service_account"] == "${{ secrets.GCP_DEPLOY_SERVICE_ACCOUNT_STG }}"
    assert gate["steps"][-1]["run"] == "uv run --project backend --extra dev --frozen python scripts/run_stg_menu_master_migration.py"
    for name in ("build-backend", "deploy-backend", "deploy-frontend"):
        assert "menu-master-migration" in jobs[name]["needs"]
    for name in ("build-backend", "deploy-backend"):
        assert jobs[name]["if"] == "needs.source-gate.outputs.backend_changed == 'true'"
        assert "continue-on-error" not in jobs[name]
    assert not any(step.get("continue-on-error") for step in gate["steps"])
    commands = [step["run"] for step in jobs["automation-bootstrap"]["steps"] if "run" in step]
    assert commands == ["bash scripts/bootstrap_automation_user.sh stg", "bash scripts/bootstrap_automation_user.sh stg school-lunch"]
    source = next(step["run"] for step in jobs["source-gate"]["steps"] if step.get("name") == "Assert staging source")
    for assertion in ('test "${GITHUB_REF_NAME}" = "develop"', 'test -z "$(git status --porcelain)"',
                      'test "$(git rev-parse HEAD)" = "$(git rev-parse origin/develop)"'):
        assert assertion in source
    assert workflow["on"]["push"]["branches"] == ["develop"]
    bootstrap = (ROOT / "scripts/bootstrap_automation_user.sh").read_text()
    assert runner.PROXY_URL in bootstrap and runner.PROXY_SHA256 in bootstrap


@pytest.mark.parametrize("backend_changed", [True, False])
@pytest.mark.parametrize("failed", [None, "source-gate", "build-frontend", "automation-bootstrap", "menu-master-migration", "deploy-backend"])
@pytest.mark.parametrize("failure_result", ["failure", "cancelled", "skipped"])
def test_frontend_condition_allows_only_compatible_dependency_results(backend_changed, failed, failure_result):
    jobs = yaml.load((ROOT / ".github/workflows/deploy-stg.yml").read_text(), Loader=yaml.BaseLoader)["jobs"]
    expression = jobs["deploy-frontend"]["if"]
    for job in ("source-gate", "build-frontend", "automation-bootstrap", "menu-master-migration", "deploy-backend"):
        result = failure_result if failed == job else "skipped" if not backend_changed and job in {"automation-bootstrap", "menu-master-migration", "deploy-backend"} else "success"
        expression = expression.replace(f"needs.{job}.result", repr(result))
    expression = expression.replace("needs.source-gate.outputs.backend_changed", repr(str(backend_changed).lower()))
    expression = expression.replace("needs.source-gate.outputs.frontend_changed", "'true'")
    expression = expression.replace("always()", "True").replace("&&", " and ").replace("||", " or ")
    assert "needs." not in expression
    actual = eval(" ".join(expression.split()), {"__builtins__": {}})
    expected = failed is None or (not backend_changed and failed in {"automation-bootstrap", "menu-master-migration", "deploy-backend"})
    assert actual == expected
