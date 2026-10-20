"""Preserve explicit meat/fish contamination across recipe and schedule steps."""
from alembic import op
import sqlalchemy as sa

revision = "0016_meat_fish_work_zone"
down_revision = "0015_route_source_sessions"
branch_labels = None
depends_on = None

TABLES = (
    ("school_lunch_process_steps", "ck_school_lunch_process_step_work_zone", True),
    ("school_lunch_work_schedule_tasks", "ck_school_lunch_work_schedule_task_work_zone", False),
)


def _replace(include_meat_fish):
    values = "'contaminated', 'non_contaminated'"
    if include_meat_fish:
        values += ", 'contaminated_meat_fish'"
    for table, constraint, nullable in TABLES:
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(constraint, type_="check")
            prefix = "work_zone IS NULL OR " if nullable else ""
            batch.create_check_constraint(constraint, prefix + f"work_zone IN ({values})")


def upgrade():
    _replace(True)


def downgrade():
    # Downgrading must not silently discard the explicitly saved distinction.
    for table, _, _ in TABLES:
        if op.get_bind().execute(sa.text(f"SELECT COUNT(*) FROM {table} WHERE work_zone = 'contaminated_meat_fish'")).scalar():
            raise RuntimeError("肉・魚下処理区分の保存データがあるためダウングレードできません")
    _replace(False)
