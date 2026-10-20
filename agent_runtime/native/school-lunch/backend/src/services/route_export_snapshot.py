"""Caller-owned real read transaction and detached export source values."""
from copy import deepcopy
from types import SimpleNamespace

from fastapi import HTTPException


def begin_export_read(session):
    if session.new or session.dirty or session.deleted:
        raise HTTPException(409, "未保存の変更があるSessionでは出力できません")
    transaction = session.get_transaction()
    if transaction is not None:
        if session.info.get("route_export_read_transaction") is transaction:
            return
        raise HTTPException(409, "出力は独立した読取transactionで開始してください")
    connection = session.connection()
    dialect = connection.dialect.name
    if dialect == "sqlite":
        # Python sqlite legacy mode does not BEGIN for SELECT.
        connection.exec_driver_sql("BEGIN")
    elif dialect == "postgresql":
        connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    else:
        raise HTTPException(409, "出力snapshotに対応していないdatabaseです")
    session.info["route_export_read_transaction"] = session.get_transaction()


def freeze_row(row):
    return SimpleNamespace(**{column.key: deepcopy(getattr(row, column.key)) for column in row.__table__.columns})
