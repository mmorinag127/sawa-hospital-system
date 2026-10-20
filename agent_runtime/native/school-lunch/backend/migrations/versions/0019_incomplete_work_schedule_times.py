"""Keep unknown task times absent in saved engineering drafts."""
from alembic import op
import sqlalchemy as sa

revision = "0019_incomplete_schedule_times"
down_revision = "0018_distinct_ingredient_rows"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("school_lunch_work_schedule_tasks") as batch:
        batch.alter_column("start_time", existing_type=sa.Time(), nullable=True)
        batch.alter_column("end_time", existing_type=sa.Time(), nullable=True)


def downgrade():
    if op.get_bind().execute(sa.text(
        "SELECT 1 FROM school_lunch_work_schedule_tasks "
        "WHERE start_time IS NULL OR end_time IS NULL LIMIT 1"
    )).first():
        raise RuntimeError("時刻未入力の下書きがあるためダウングレードできません")
    with op.batch_alter_table("school_lunch_work_schedule_tasks") as batch:
        batch.alter_column("start_time", existing_type=sa.Time(), nullable=False)
        batch.alter_column("end_time", existing_type=sa.Time(), nullable=False)
