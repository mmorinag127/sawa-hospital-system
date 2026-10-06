"""Explicit database selection shared by runtime and formal migrations."""

import os
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError


def explicit_database_uri(*, migration=False):
    if migration and "ALEMBIC_DB_URI" not in os.environ:
        raise RuntimeError("ALEMBIC_DB_URI is required; automatic database adoption is disabled")
    names = ("ALEMBIC_DB_URI", "DB_URI", "DATABASE_URL")
    values = []
    for name in names:
        value = os.environ.get(name)
        if value is not None:
            if not value.strip() or value != value.strip():
                raise RuntimeError(f"{name} must be an explicit nonblank database URI")
            try:
                values.append(make_url(value))
            except (ValueError, ArgumentError):
                raise RuntimeError(f"{name} is not a valid explicit database URI") from None
    components = ("DB_USER", "DB_PASSWORD", "DB_NAME", "DB_HOST", "DB_PORT", "DB_DRIVER")
    if any(name in os.environ for name in components):
        if not all(os.environ.get(name, "").strip() for name in components[:4]):
            raise RuntimeError("database components must explicitly specify DB_USER, DB_PASSWORD, DB_NAME and DB_HOST")
        host = os.environ["DB_HOST"]
        port = os.environ.get("DB_PORT")
        if port is not None and (not port.strip() or not port.isdecimal()):
            raise RuntimeError("DB_PORT must be an explicit numeric port")
        if "DB_DRIVER" in os.environ and not os.environ["DB_DRIVER"].strip():
            raise RuntimeError("DB_DRIVER must not be blank")
        query = {"host": host} if host.startswith("/cloudsql/") else {}
        if host.startswith("/cloudsql/") and port is not None:
            query["port"] = port
        values.append(URL.create(
            os.environ.get("DB_DRIVER", "postgresql+psycopg2"),
            username=os.environ["DB_USER"], password=os.environ["DB_PASSWORD"],
            database=os.environ["DB_NAME"], host=None if host.startswith("/cloudsql/") else host,
            port=None if host.startswith("/cloudsql/") or port is None else int(port),
            query=query,
        ))
    if not values:
        raise RuntimeError("explicit database configuration is required; automatic database selection is disabled")
    if any(value != values[0] for value in values[1:]):
        raise RuntimeError("database configurations disagree; automatic database selection is disabled")
    return values[0].render_as_string(hide_password=False)
