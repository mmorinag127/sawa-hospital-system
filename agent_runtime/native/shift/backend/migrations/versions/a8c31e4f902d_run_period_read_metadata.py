"""Keep navigation and predecessor lookup out of archived execution JSON."""
from alembic import op
import sqlalchemy as sa
from shift_scheduler_backend.period_metadata_v1 import period_metadata, rotation_metadata

revision = 'a8c31e4f902d'
down_revision = 'c6a9e2f8b104'
branch_labels = None
depends_on = None


def upgrade():
    op.create_index('ix_rotation_queue_policy_date_id', 'rotation_queue_states', ['rotation_policy_id', 'effective_from', 'id'])
    fields = (
        ('period_summary_json', sa.JSON()), ('period_end_key', sa.String(10)),
        ('display_period_end_key', sa.String(10)),
        ('period_known', sa.Boolean()), ('period_display_error', sa.Boolean()),
        ('prior_candidate_start_key', sa.String(10)),
        ('rotation_end_key', sa.String(10)), ('rotation_reference_error', sa.Boolean()),
    )
    for name, kind in fields:
        op.add_column('solve_runs', sa.Column(name, kind, nullable=True))
    op.create_table('solve_run_rotation_references',
        sa.Column('policy_key', sa.String(128), primary_key=True),
        sa.Column('solve_run_id', sa.Integer(), sa.ForeignKey('solve_runs.id', ondelete='CASCADE'), primary_key=True))
    op.create_index('ix_solve_run_rotation_references_solve_run_id', 'solve_run_rotation_references', ['solve_run_id'])
    references_table = sa.table('solve_run_rotation_references', sa.column('policy_key', sa.String(128)),
                               sa.column('solve_run_id', sa.Integer()))
    table = sa.table('solve_runs', sa.column('id', sa.Integer()), sa.column('target_month', sa.String()),
                     sa.column('config_json', sa.JSON()), *(sa.column(name, kind) for name, kind in fields))
    connection = op.get_bind()
    rows = connection.execute(sa.select(table.c.id, table.c.target_month, table.c.config_json).execution_options(yield_per=1))
    try:
        for row in rows:
            rotation, references = rotation_metadata(row.config_json)
            connection.execute(table.update().where(table.c.id == row.id).values(
                **period_metadata(row.target_month, row.config_json), **rotation))
            if references:
                connection.execute(references_table.insert(), [{'solve_run_id': row.id, 'policy_key': key} for key in references])
    finally:
        rows.close()
    with op.batch_alter_table('solve_runs') as batch:
        for name, kind in fields:
            if name in ('period_summary_json', 'period_known', 'period_display_error', 'rotation_reference_error'):
                batch.alter_column(name, existing_type=kind, nullable=False)
        batch.create_index('ix_solve_runs_period_end_status_id', ['period_end_key', 'status', 'id'])
        batch.create_index('ix_solve_runs_period_integrity', ['period_display_error', 'period_known', 'status', 'id'])
        batch.create_index('ix_solve_runs_confirmed_period', ['status', 'period_known', 'display_period_end_key', 'id'])
        batch.create_index('ix_solve_runs_rotation_integrity', ['rotation_reference_error', 'id'])


def downgrade():
    op.drop_table('solve_run_rotation_references')
    op.drop_index('ix_rotation_queue_policy_date_id', table_name='rotation_queue_states')
    with op.batch_alter_table('solve_runs') as batch:
        batch.drop_index('ix_solve_runs_rotation_integrity')
        batch.drop_index('ix_solve_runs_confirmed_period')
        batch.drop_index('ix_solve_runs_period_integrity')
        batch.drop_index('ix_solve_runs_period_end_status_id')
        for name in ('rotation_reference_error', 'rotation_end_key', 'prior_candidate_start_key', 'period_display_error', 'period_known', 'display_period_end_key', 'period_end_key', 'period_summary_json'):
            batch.drop_column(name)
