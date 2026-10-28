"""Read-only revision catalogs; contracts come from real Alembic upgrades."""
import json
import os
import re
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.engine.reflection import ObjectKind, ObjectScope
from sqlalchemy.engine import URL, make_url

CATALOG_HEAD = "0032_route_source_references"
HEAD = "0032_route_source_references"
AUTH_TABLES = ("users", "audit_logs", "notifications")
CONTRACT_PATH = Path(__file__).with_name("migration_catalog_contracts.json")


def explicit_database_uri(required=True):
    values = [os.environ[key].strip() for key in ("ALEMBIC_DB_URI", "DATABASE_URL", "DB_URI") if key in os.environ]
    if values and (any(not value for value in values) or len(set(values)) != 1):
        raise RuntimeError("Specify one explicit, consistent migration database URI")
    if not values:
        if required:
            raise RuntimeError("Specify one explicit, consistent migration database URI")
        return None
    require_consistent_components(values[0])
    return values[0]


def require_consistent_components(uri):
    keys = ("DB_USER", "DB_PASSWORD", "DB_NAME", "DB_HOST", "DB_PORT", "DB_DRIVER")
    if not any(key in os.environ for key in keys):
        return
    if any(not os.getenv(key, "").strip() for key in keys[:4]):
        raise RuntimeError("Explicit database URI/components are incomplete or inconsistent")
    host = os.environ["DB_HOST"]
    port = os.getenv("DB_PORT")
    try:
        component = URL.create(os.getenv("DB_DRIVER", "postgresql+psycopg2"),
            username=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
            database=os.environ["DB_NAME"], host=None if host.startswith("/cloudsql/") else host,
            port=int(port) if port else None,
            query={"host": host} if host.startswith("/cloudsql/") else {})
        consistent = make_url(uri) == component
    except (ValueError, TypeError):
        consistent = False
    if not consistent:
        raise RuntimeError("Explicit database URI/components are incomplete or inconsistent")


def _ordered(values):
    return sorted(values, key=lambda value: json.dumps(value, sort_keys=True))


def _table_tokens(sql):
    # Reflection can reorder independent table constraints, not columns or literals.
    tokens = re.findall(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|\[[^\]]*\]|`(?:``|[^`])*`|[A-Za-z_][A-Za-z_0-9]*|\d+|[^\s]", sql)
    opening = tokens.index("(")
    depth = 1
    clauses = []
    start = opening + 1
    closing = None
    for position in range(start, len(tokens)):
        token = tokens[position]
        if token == "(":
            depth += 1
        elif token == ")":
            depth -= 1
            if depth == 0:
                clauses.append(tokens[start:position])
                closing = position
                break
        elif token == "," and depth == 1:
            clauses.append(tokens[start:position])
            start = position + 1
    if closing is None:
        raise RuntimeError("Unrecognized SQLite table definition")
    columns, constraints = [], []
    for clause in clauses:
        target = constraints if clause[0].upper() in {"CONSTRAINT", "PRIMARY", "UNIQUE", "FOREIGN", "CHECK"} else columns
        target.append(clause)
    return [tokens[:opening], columns, sorted(constraints), tokens[closing + 1:]]


def _restored_text_array_casts(expression):
    """pg_dump may distribute a text[] cast over varchar literal elements."""
    literal = r"'(?:''|[^'])*'"
    element = literal + r"::character varying::text"
    pattern = re.compile(r"ARRAY\[(" + element + r"(?:, " + element + r")*)\]")

    def array(match):
        values = re.sub("(" + literal + r")::character varying::text",
                        lambda item: item.group(1) + "::character varying", match.group(1))
        return "ARRAY[" + values + "]::text[]"

    # Do not interpret ARRAY-looking text inside SQL literals or quoted names.
    tokens = re.finditer(literal + r'|"(?:""|[^"])*"|[A-Za-z_][A-Za-z_0-9]*|.', expression, re.S)
    replacements = []
    for token in tokens:
        if token.group() == "ARRAY":
            match = pattern.match(expression, token.start())
            if match:
                replacements.append((match.start(), match.end(), array(match)))
    for start, end, value in reversed(replacements):
        expression = expression[:start] + value + expression[end:]
    return expression


def _comparison_catalog(catalog):
    result = json.loads(json.dumps(catalog))
    for shape in result["tables"].values():
        for constraint in shape.get("checks", []):
            if isinstance(constraint, dict) and isinstance(constraint.get("sqltext"), str):
                constraint["sqltext"] = _restored_text_array_casts(constraint["sqltext"])
        for constraint in shape.get("native_constraints", []):
            if (isinstance(constraint, list) and len(constraint) == 9
                    and constraint[1] == "c" and isinstance(constraint[-1], str)):
                constraint[-1] = _restored_text_array_casts(constraint[-1])
        for index in shape.get("indexes", []):
            options = index.get("dialect_options", {})
            if "include_columns" in index and "postgresql_include" in options:
                if index["include_columns"] != options["postgresql_include"]:
                    raise RuntimeError("Inconsistent PostgreSQL INCLUDE reflection aliases")
                # Reflection versions may repeat the explicit empty PG INCLUDE
                # list at top level. Preserve every nonempty or unpaired value.
                if index["include_columns"] == []:
                    del index["include_columns"]
        for entry in shape.get("native", []):
            if entry[0] == "table" and entry[2] is not None:
                entry[2] = _table_tokens(entry[2])
    return result


def _postgresql_sequences(connection):
    rows = connection.execute(text(
        "SELECT sequence.relname,pg_catalog.format_type(definition.seqtypid,NULL),"
        "definition.seqstart::text,definition.seqmin::text,definition.seqmax::text,"
        "definition.seqincrement::text,definition.seqcache::text,definition.seqcycle,"
        "owner_namespace.nspname,owner_relation.relname,owner_attribute.attname "
        "FROM pg_class sequence JOIN pg_namespace sequence_namespace ON sequence_namespace.oid=sequence.relnamespace "
        "JOIN pg_sequence definition ON definition.seqrelid=sequence.oid "
        "LEFT JOIN pg_depend dependency ON dependency.classid='pg_class'::regclass "
        "AND dependency.objid=sequence.oid AND dependency.objsubid=0 "
        "AND dependency.refclassid='pg_class'::regclass AND dependency.deptype IN ('a','i') "
        "LEFT JOIN pg_class owner_relation ON owner_relation.oid=dependency.refobjid "
        "LEFT JOIN pg_namespace owner_namespace ON owner_namespace.oid=owner_relation.relnamespace "
        "LEFT JOIN pg_attribute owner_attribute ON owner_attribute.attrelid=owner_relation.oid "
        "AND owner_attribute.attnum=dependency.refobjsubid "
        "WHERE sequence_namespace.nspname=current_schema() AND sequence.relkind='S' "
        "ORDER BY sequence.relname")).all()
    sequences = {}
    current_schema = connection.scalar(text("SELECT current_schema()")) if rows else None
    for row in rows:
        (name, value_type, start, minimum, maximum, increment, cache, cycle,
         owner_schema, owner_table, owner_column) = row
        if owner_schema != current_schema or not owner_table or not owner_column:
            raise RuntimeError("Unsupported catalog object: standalone or cross-schema sequence: " + name)
        sequences[name] = {
            "type": value_type,
            "start": start,
            "min": minimum,
            "max": maximum,
            "increment": increment,
            "cache": cache,
            "cycle": cycle,
            "owner": {"table": owner_table, "column": owner_column},
        }
    return sequences


def _require_supported_postgresql_catalog(connection, inspector):
    if inspector.get_materialized_view_names():
        raise RuntimeError("Unsupported catalog object: materialized view")
    unsupported = connection.execute(text(
        "SELECT t.relname FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace "
        "WHERE n.nspname=current_schema() AND (t.relrowsecurity OR t.relforcerowsecurity "
        "OR t.relispartition OR t.relkind='p' "
        "OR EXISTS (SELECT 1 FROM pg_trigger g WHERE g.tgrelid=t.oid AND NOT g.tgisinternal) "
        "OR EXISTS (SELECT 1 FROM pg_policy p WHERE p.polrelid=t.oid) "
        "OR EXISTS (SELECT 1 FROM pg_inherits i WHERE i.inhrelid=t.oid OR i.inhparent=t.oid)) "
        "ORDER BY t.relname")).scalars().all()
    if unsupported:
        raise RuntimeError("Unsupported catalog object: trigger/RLS/partition/inheritance: " + ", ".join(unsupported))
    invalid_sequences = connection.execute(text(
        "SELECT sequence.relname FROM pg_class sequence JOIN pg_namespace sequence_namespace "
        "ON sequence_namespace.oid=sequence.relnamespace LEFT JOIN pg_depend dependency "
        "ON dependency.classid='pg_class'::regclass AND dependency.objid=sequence.oid "
        "AND dependency.deptype IN ('a','i') "
        "LEFT JOIN pg_class owner_relation ON owner_relation.oid=dependency.refobjid "
        "LEFT JOIN pg_namespace owner_namespace ON owner_namespace.oid=owner_relation.relnamespace "
        "LEFT JOIN pg_attribute owner_attribute ON owner_attribute.attrelid=owner_relation.oid "
        "AND owner_attribute.attnum=dependency.refobjsubid "
        "WHERE sequence_namespace.nspname=current_schema() AND sequence.relkind='S' "
        "GROUP BY sequence.oid,sequence.relname,sequence_namespace.nspname "
        "HAVING count(dependency.objid) <> 1 OR NOT bool_and(dependency.objsubid=0 "
        "AND dependency.refclassid='pg_class'::regclass AND dependency.refobjsubid>0 "
        "AND dependency.deptype IN ('a','i') AND owner_namespace.nspname=sequence_namespace.nspname "
        "AND owner_attribute.attnum IS NOT NULL AND NOT owner_attribute.attisdropped) ORDER BY sequence.relname")).scalars().all()
    if invalid_sequences:
        raise RuntimeError("Unsupported catalog object: standalone or invalid owned sequence: " + ", ".join(invalid_sequences))
    undefined_types = connection.execute(text(
        "SELECT t.typname FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace "
        "WHERE n.nspname=current_schema() AND NOT t.typisdefined ORDER BY t.typname")).scalars().all()
    if undefined_types:
        raise RuntimeError("Unsupported catalog object: undefined shell type: " + ", ".join(undefined_types))
    unsupported = connection.execute(text(
        "SELECT kind || ':' || name FROM ("
        "SELECT 'foreign table' AS kind,c.relname AS name FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname=current_schema() AND c.relkind='f' "
        "UNION ALL SELECT 'routine',p.proname || '(' || pg_get_function_identity_arguments(p.oid) || ')' "
        "FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=current_schema() "
        "UNION ALL SELECT 'type',t.typname FROM pg_type t JOIN pg_namespace n ON n.oid=t.typnamespace "
        "LEFT JOIN pg_class composite_relation ON composite_relation.oid=t.typrelid "
        "WHERE n.nspname=current_schema() AND ((t.typtype='b' AND t.typelem=0) OR t.typtype IN ('d','e','m','r') "
        "OR (t.typtype='c' AND composite_relation.relkind='c')) "
        "UNION ALL SELECT 'rewrite rule',r.rulename FROM pg_rewrite r JOIN pg_class c ON c.oid=r.ev_class "
        "JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND r.rulename <> '_RETURN'"
        ") unsupported ORDER BY kind,name")).scalars().all()
    if unsupported:
        raise RuntimeError("Unsupported catalog object: " + ", ".join(unsupported))


def read_catalog(connection):
    inspector = inspect(connection)
    if connection.dialect.name == "postgresql":
        _require_supported_postgresql_catalog(connection, inspector)
    tables = {}
    names = sorted(inspector.get_table_names())
    reflected, native = None, {}
    if connection.dialect.name == "postgresql" and names:
        options = dict(schema=None, filter_names=names, scope=ObjectScope.ANY, kind=ObjectKind.ANY)
        reflected = {
            "columns": inspector.get_multi_columns(**options),
            "pk": inspector.get_multi_pk_constraint(**options),
            "unique": inspector.get_multi_unique_constraints(**options),
            "foreign_keys": inspector.get_multi_foreign_keys(**options),
            "checks": inspector.get_multi_check_constraints(**options),
            "indexes": inspector.get_multi_indexes(**options),
        }
        statements = {
            "native_indexes": (
                "SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname=current_schema() "
                "ORDER BY tablename,indexname"),
            "native_columns": (
                "SELECT t.relname,a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod),"
                "a.attcollation::regcollation::text,a.attidentity,a.attgenerated "
                "FROM pg_attribute a JOIN pg_class t ON t.oid=a.attrelid "
                "JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname=current_schema() "
                "AND a.attnum>0 AND NOT a.attisdropped ORDER BY t.relname,a.attnum"),
            "native_constraints": (
                "SELECT t.relname,c.conname,c.contype,c.convalidated,c.condeferrable,c.condeferred,"
                "c.conislocal,c.coninhcount,c.connoinherit,pg_get_constraintdef(c.oid,true) "
                "FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid "
                "JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname=current_schema() "
                "ORDER BY t.relname,c.conname"),
        }
        for field, statement in statements.items():
            native[field] = {name: [] for name in names}
            for row in connection.execute(text(statement)):
                if row[0] in native[field]:
                    native[field][row[0]].append(list(row[1:]))
    for name in names:
        shape = ({key: values[(None, name)] for key, values in reflected.items()} if reflected is not None else {
            "columns": inspector.get_columns(name),
            "pk": inspector.get_pk_constraint(name),
            "unique": inspector.get_unique_constraints(name),
            "foreign_keys": inspector.get_foreign_keys(name),
            "checks": inspector.get_check_constraints(name),
            "indexes": inspector.get_indexes(name),
        })
        columns = []
        for column in shape["columns"]:
            columns.append({key: column.get(key) for key in
                ("name", "nullable", "default", "autoincrement", "computed", "identity")}
                | {"type": str(column["type"].compile(dialect=connection.dialect))})
        tables[name] = {
            "columns": columns,
            "pk": shape["pk"],
            "unique": _ordered(shape["unique"]),
            "foreign_keys": _ordered(shape["foreign_keys"]),
            "checks": _ordered(shape["checks"]),
            "indexes": _ordered(shape["indexes"]),
        }
        if connection.dialect.name == "sqlite":
            tables[name]["native"] = [list(row) for row in connection.execute(text(
                "SELECT type, name, sql FROM sqlite_master WHERE tbl_name=:name "
                "AND type IN ('table','index','trigger') ORDER BY type,name"), {"name": name})]
        elif connection.dialect.name == "postgresql":
            for field, values in native.items():
                tables[name][field] = values[name]
        else:
            raise RuntimeError("Unsupported migration dialect")
    return {"tables": tables, "views": {
        name: inspector.get_view_definition(name) for name in sorted(inspector.get_view_names())},
        "sequences": _postgresql_sequences(connection) if connection.dialect.name == "postgresql" else {}}


def require_revision_catalog(connection, revision):
    contracts = json.loads(CONTRACT_PATH.read_text())
    revision_contract = contracts["revisions"].get(connection.dialect.name, {}).get(revision)
    if revision_contract is None:
        raise RuntimeError("Unknown migration revision catalog")
    expected = {"tables": {name: contracts["shapes"][key] for name, key in revision_contract["tables"].items()},
        "views": {}, "sequences": revision_contract["sequences"]}
    actual = _comparison_catalog(read_catalog(connection))
    expected = _comparison_catalog(expected)
    if revision == "0025_school_lunch_documents":
        head = contracts["revisions"][connection.dialect.name][CATALOG_HEAD]["tables"]
        for name in AUTH_TABLES:
            if name in actual["tables"]:
                shape = _comparison_catalog({"tables": {name: contracts["shapes"][head[name]]}, "views": {}})
                expected["tables"][name] = shape["tables"][name]
    if actual != expected:
        changed = sorted(set(actual["tables"]) | set(expected["tables"]))
        changed = [name for name in changed if actual["tables"].get(name) != expected["tables"].get(name)]
        details = {name: {key: {"actual": actual["tables"].get(name, {}).get(key),
            "expected": expected["tables"].get(name, {}).get(key)}
            for key in set(actual["tables"].get(name, {})) | set(expected["tables"].get(name, {}))
            if actual["tables"].get(name, {}).get(key) != expected["tables"].get(name, {}).get(key)} for name in changed}
        raise RuntimeError("Migration revision catalog mismatch: " + json.dumps(details or {"views": actual["views"]}, sort_keys=True))


def require_auth_table_definitions(connection):
    actual = _comparison_catalog(read_catalog(connection))
    if not any(name in actual["tables"] for name in AUTH_TABLES):
        return
    contracts = json.loads(CONTRACT_PATH.read_text())
    head = contracts["revisions"][connection.dialect.name][CATALOG_HEAD]["tables"]
    for name in AUTH_TABLES:
        if name in actual["tables"]:
            expected = _comparison_catalog({"tables": {name: contracts["shapes"][head[name]]}, "views": {}})
            if actual["tables"][name] != expected["tables"][name]:
                raise RuntimeError("Unrecognized authentication support table: " + name)


def require_runtime_schema(connection):
    inspector = inspect(connection)
    if "alembic_version" not in inspector.get_table_names():
        raise RuntimeError("Run explicit Alembic migration before startup")
    versions = connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
    if versions != [HEAD]:
        raise RuntimeError("Run explicit Alembic upgrade head before startup")
    require_revision_catalog(connection, HEAD)
