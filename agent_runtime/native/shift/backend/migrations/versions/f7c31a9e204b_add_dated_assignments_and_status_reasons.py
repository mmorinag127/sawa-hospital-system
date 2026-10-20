"""Add authored dated assignments and manual staff-day explanations."""

from alembic import op
import sqlalchemy as sa

revision = "f7c31a9e204b"
down_revision = "d5e831a7c902"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("position_assignment_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("position_id", sa.Integer(), sa.ForeignKey("positions.id"), nullable=False),
        sa.Column("staff_id", sa.Integer(), sa.ForeignKey("staff.id"), nullable=False),
        sa.Column("shift_template_id", sa.Integer(), sa.ForeignKey("shift_templates.id"), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.CheckConstraint("effective_to IS NULL OR effective_to >= effective_from", name="ck_position_assignment_rule_dates"))
    for column in ("position_id", "staff_id", "shift_template_id", "effective_from", "effective_to"):
        op.create_index(f"ix_position_assignment_rules_{column}", "position_assignment_rules", [column])
    op.create_table("candidate_staff_status_explanations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("status_id", sa.Integer(), sa.ForeignKey("candidate_staff_statuses.id"), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("message", sa.String(500), nullable=False),
        sa.UniqueConstraint("status_id", name="uq_candidate_staff_status_explanation"))
    op.create_index("ix_candidate_staff_status_explanations_status_id", "candidate_staff_status_explanations", ["status_id"])
    op.bulk_insert(sa.table("editor_revisions", sa.column("resource", sa.String), sa.column("revision", sa.Integer)),
        [{"resource": "position_assignment_rules", "revision": 1}])


def downgrade():
    op.execute(sa.text("DELETE FROM editor_revisions WHERE resource = 'position_assignment_rules'"))
    op.drop_table("candidate_staff_status_explanations")
    op.drop_table("position_assignment_rules")
