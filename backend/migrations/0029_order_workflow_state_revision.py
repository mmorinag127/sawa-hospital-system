"""Add an explicit optimistic concurrency revision to workflow state."""

from alembic import op
import sqlalchemy as sa


revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


class WorkflowRevisionSchemaError(RuntimeError):
    pass


def _revision_column(bind):
    inspector = sa.inspect(bind)
    if not inspector.has_table("order_workflow_states"):
        raise WorkflowRevisionSchemaError("0029 blocked: order_workflow_states is missing")
    return {column["name"]: column for column in inspector.get_columns("order_workflow_states")}.get("revision")


def upgrade():
    bind = op.get_bind()
    column = _revision_column(bind)
    added_revision_column = column is None
    if column is None:
        op.add_column(
            "order_workflow_states",
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        )
        column = _revision_column(bind)
    if column is None or column["nullable"] or column["type"].compile(dialect=bind.dialect).upper() not in {"INTEGER", "INT"}:
        raise WorkflowRevisionSchemaError("0029 blocked: revision must be INTEGER NOT NULL")
    if not added_revision_column:
        invalid_revision = bind.execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM order_workflow_states "
                "WHERE revision IS NULL OR revision < 1)"
            )
        ).scalar_one()
        if invalid_revision:
            raise WorkflowRevisionSchemaError(
                "0029 blocked: existing revision contains NULL, zero, or a negative value"
            )


def downgrade():
    bind = op.get_bind()
    if _revision_column(bind) is None:
        raise WorkflowRevisionSchemaError("0029 blocked: revision is missing")
    op.drop_column("order_workflow_states", "revision")
