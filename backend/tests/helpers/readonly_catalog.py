"""Exact non-SELECT catalog reads; no arbitrary PRAGMA or write allowance."""


def readonly_catalog_statements(engine):
    allowed = {"BEGIN"}
    if engine.dialect.name == "postgresql":
        allowed.add("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    elif engine.dialect.name == "sqlite":
        with engine.connect() as connection:
            objects = connection.exec_driver_sql(
                "SELECT type, name FROM sqlite_master WHERE type IN ('table', 'index')").all()
        for kind, name in objects:
            quoted = '"' + name.replace('"', '""') + '"'
            functions = ("table_info", "table_xinfo", "foreign_key_list", "index_list") if kind == "table" else (
                "index_info", "index_xinfo")
            allowed.update(f"PRAGMA main.{function}({quoted})" for function in functions)
    return allowed
