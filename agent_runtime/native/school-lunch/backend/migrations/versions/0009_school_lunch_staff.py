"""Add editable staff candidates, preserving names on existing documents."""
from alembic import op
import sqlalchemy as sa

revision = "0009_school_lunch_staff"
down_revision = "0008_schedule_menu_versions"
branch_labels = None
depends_on = None


def upgrade():
    table = op.create_table(
        "school_lunch_staff",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_school_lunch_staff_name"),
        sa.CheckConstraint("version >= 1", name="ck_school_lunch_staff_version"),
    )
    # Initial roster supplied by the operator; subsequent changes use the staff API.
    names = ["乾", "谷", "内田", "篠原", "勇", "須原", "猪子", "武田", "森本", "人見", "池上", "竹田", "上田"]
    op.bulk_insert(table, [{"name": name, "active": True, "version": 1} for name in names])


def downgrade():
    op.drop_table("school_lunch_staff")
