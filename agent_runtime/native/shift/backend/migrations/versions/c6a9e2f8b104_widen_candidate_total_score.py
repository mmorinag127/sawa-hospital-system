"""Widen only candidate total_score to signed 64-bit storage.

SQLite INTEGER already stores signed 64-bit integers. Leave that table intact
instead of rebuilding a parent table with inbound foreign keys.
"""

from alembic import op
import sqlalchemy as sa

revision = "c6a9e2f8b104"
down_revision = "f7c31a9e204b"
branch_labels = None
depends_on = None


def _dialect():
    name = op.get_bind().dialect.name
    if name not in {"postgresql", "sqlite"}:
        raise RuntimeError("candidate_score_migration_dialect_unsupported")
    return name


def upgrade():
    if _dialect() == "postgresql":
        op.alter_column("candidate_schedules", "total_score",
            existing_type=sa.Integer(), type_=sa.BigInteger(), existing_nullable=False)


def downgrade():
    connection = op.get_bind()
    dialect = _dialect()
    # The formal SQLite runner owns BEGIN IMMEDIATE; PostgreSQL must also keep
    # writes excluded between the range check and the narrowing DDL.
    if dialect == "postgresql":
        connection.execute(sa.text("LOCK TABLE candidate_schedules IN ACCESS EXCLUSIVE MODE"))
    outside = connection.execute(sa.text(
        "SELECT 1 FROM candidate_schedules "
        "WHERE total_score < :minimum OR total_score > :maximum LIMIT 1"),
        {"minimum": -(2**31), "maximum": 2**31 - 1}).first()
    if outside is not None:
        raise RuntimeError("candidate_score_downgrade_out_of_int32_range")
    if dialect == "postgresql":
        op.alter_column("candidate_schedules", "total_score",
            existing_type=sa.BigInteger(), type_=sa.Integer(), existing_nullable=False)
