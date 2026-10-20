"""Track schedule input revisions and operator-selected time profile."""
from alembic import op
import sqlalchemy as sa

revision = "0008_schedule_menu_versions"
down_revision = "0007_monthly_procurement"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("school_lunch_daily_work_schedules", sa.Column("time_profile", sa.String(16), nullable=True))
    op.add_column("school_lunch_daily_work_schedules", sa.Column("source_menu_versions", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("school_lunch_daily_work_schedules", "source_menu_versions")
    op.drop_column("school_lunch_daily_work_schedules", "time_profile")
