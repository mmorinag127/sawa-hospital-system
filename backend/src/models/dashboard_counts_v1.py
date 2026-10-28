"""Transactionally maintained navigation counts, never execution authority."""
from sqlalchemy import BigInteger, CheckConstraint, Column, String, event

from src.db import Base


class DashboardCount(Base):
    __tablename__ = "order_dashboard_counts"
    __table_args__ = (CheckConstraint("row_count >= 0", name="ck_dashboard_count_nonnegative"),)

    group_key = Column(String, primary_key=True)
    source_kind = Column(String(20), nullable=False)
    facility = Column(String, nullable=True)
    week = Column(String, nullable=True)
    status = Column(String, nullable=True)
    row_count = Column(BigInteger, nullable=False)


def _source(table):
    if table == "orders":
        return "order", "facility_code", "week_code", "{row}.status", ("facility_code", "week_code", "status")
    return "uploaded_pdf", "facility_hint", "week_hint", "'アップロード済み'", (
        "facility_hint", "week_hint", "status", "current_order_id", "current_document_id")


def _visibility(row, table, dialect):
    if table == "orders":
        return "TRUE"
    from src.models.upload_visibility_v1 import unresolved_upload_conditions
    from sqlalchemy import and_, column, table as sql_table
    from sqlalchemy.sql import quoted_name
    from sqlalchemy.dialects import postgresql, sqlite
    dialect_type = postgresql.dialect() if dialect == "postgresql" else sqlite.dialect()
    source = sql_table(quoted_name(row, False), *(column(name) for name in (
        "status", "current_order_id", "current_document_id")))
    return str(and_(*unresolved_upload_conditions(source.c)).compile(
        dialect=dialect_type, compile_kwargs={"literal_binds": True}))


def _facts(row, table, dialect):
    kind, facility, week, status, _columns = _source(table)
    status = status.format(row=row)
    values = f"'{kind}', {row}.{facility}, {row}.{week}, {status}"
    array = "json_build_array" if dialect == "postgresql" else "json_array"
    key = f"{array}({values})" + ("::text" if dialect == "postgresql" else "")
    return values, key, _visibility(row, table, dialect)


def install(connection, *, populate=False):
    dialect = connection.dialect.name
    if dialect not in {"sqlite", "postgresql"}:
        raise ValueError("dashboard counts require SQLite or PostgreSQL")
    if populate:
        if dialect == "postgresql":
            connection.exec_driver_sql("LOCK TABLE orders, uploaded_pdfs IN SHARE ROW EXCLUSIVE MODE")
        connection.exec_driver_sql("DELETE FROM order_dashboard_counts")
    for table in ("orders", "uploaded_pdfs"):
        new_values, new_key, new_visible = _facts("NEW", table, dialect)
        _old_values, old_key, old_visible = _facts("OLD", table, dialect)
        columns = ", ".join(_source(table)[4])
        name = "sawa_dashboard_" + table
        if dialect == "postgresql":
            source_kind = _source(table)[0]
            connection.exec_driver_sql(f"""
CREATE OR REPLACE FUNCTION {name}() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE previous_key text; current_key text; lock_id bigint;
BEGIN
  IF TG_OP = 'TRUNCATE' THEN
    DELETE FROM order_dashboard_counts WHERE source_kind = '{source_kind}';
    RETURN NULL;
  END IF;
  IF TG_OP <> 'INSERT' THEN
    IF {old_visible} THEN previous_key := {old_key}; END IF;
  END IF;
  IF TG_OP <> 'DELETE' THEN
    IF {new_visible} THEN current_key := {new_key}; END IF;
  END IF;
  IF previous_key IS NOT DISTINCT FROM current_key THEN RETURN NULL; END IF;
  FOR lock_id IN SELECT DISTINCT hashtextextended(k, 0) FROM unnest(ARRAY[previous_key, current_key]) AS keys(k)
      WHERE k IS NOT NULL ORDER BY 1 LOOP
    PERFORM pg_advisory_xact_lock(lock_id);
  END LOOP;
  IF previous_key IS NOT NULL THEN
    UPDATE order_dashboard_counts SET row_count = row_count - 1 WHERE group_key = previous_key;
    IF NOT FOUND THEN RAISE EXCEPTION 'dashboard_count_original_missing'; END IF;
    DELETE FROM order_dashboard_counts WHERE group_key = previous_key AND row_count = 0;
  END IF;
  IF current_key IS NOT NULL THEN
    INSERT INTO order_dashboard_counts (group_key, source_kind, facility, week, status, row_count)
      VALUES (current_key, {new_values}, 1)
      ON CONFLICT (group_key) DO UPDATE SET row_count = order_dashboard_counts.row_count + 1;
  END IF;
  RETURN NULL;
END $$""")
            connection.exec_driver_sql(f"CREATE TRIGGER {name} AFTER INSERT OR DELETE OR UPDATE OF {columns} ON {table} FOR EACH ROW EXECUTE FUNCTION {name}()")
            connection.exec_driver_sql(f"CREATE TRIGGER {name}_truncate AFTER TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION {name}()")
        else:
            previous = f"CASE WHEN {old_visible} THEN {old_key} ELSE NULL END"
            current = f"CASE WHEN {new_visible} THEN {new_key} ELSE NULL END"
            insert = ("INSERT INTO order_dashboard_counts (group_key, source_kind, facility, week, status, row_count) "
                f"SELECT {new_key}, {new_values}, 1 WHERE {new_visible} "
                "ON CONFLICT(group_key) DO UPDATE SET row_count = row_count + 1;")
            remove = (f"SELECT CASE WHEN {old_visible} AND NOT EXISTS "
                f"(SELECT 1 FROM order_dashboard_counts WHERE group_key = {old_key}) "
                "THEN RAISE(ABORT, 'dashboard_count_original_missing') END; "
                f"UPDATE order_dashboard_counts SET row_count = row_count - 1 WHERE group_key = {previous}; "
                f"DELETE FROM order_dashboard_counts WHERE group_key = {previous} AND row_count = 0;")
            for action, statements, condition in (
                ("INSERT", insert, ""), ("DELETE", remove, ""),
                ("UPDATE OF " + columns, remove + insert, f" WHEN ({previous}) IS NOT ({current})"),
            ):
                suffix = action.split()[0].lower()
                connection.exec_driver_sql(f"CREATE TRIGGER {name}_{suffix} AFTER {action} ON {table} FOR EACH ROW{condition} BEGIN {statements} END")
        if populate:
            values, key, visible = _facts("source", table, dialect)
            _kind, facility, week, _status, _columns = _source(table)
            grouping = f"source.{facility}, source.{week}" + (", source.status" if table == "orders" else "")
            connection.exec_driver_sql("INSERT INTO order_dashboard_counts (group_key, source_kind, facility, week, status, row_count) "
                f"SELECT {key}, {values}, COUNT(*) FROM {table} source WHERE {visible} GROUP BY {grouping}")


def uninstall(connection):
    for table in ("orders", "uploaded_pdfs"):
        name = "sawa_dashboard_" + table
        if connection.dialect.name == "postgresql":
            connection.exec_driver_sql(f"DROP TRIGGER {name} ON {table}")
            connection.exec_driver_sql(f"DROP TRIGGER {name}_truncate ON {table}")
            connection.exec_driver_sql(f"DROP FUNCTION {name}()")
        else:
            for action in ("insert", "delete", "update"):
                connection.exec_driver_sql(f"DROP TRIGGER {name}_{action}")


@event.listens_for(Base.metadata, "after_create")
def _install_empty_database(_metadata, connection, tables, **_kwargs):
    created = {table.name for table in tables}
    if {"orders", "uploaded_pdfs", "order_dashboard_counts"} <= created:
        install(connection, populate=True)
