from alembic import op
import sqlalchemy as sa
revision='0030_shared_menus';down_revision='0029_process_patterns';branch_labels=None;depends_on=None
def upgrade():
 op.create_table('school_lunch_shared_menus',sa.Column('id',sa.Integer(),primary_key=True),sa.Column('name',sa.String(200),nullable=False),sa.Column('version',sa.Integer(),nullable=False),sa.Column('head_revision_id',sa.Integer()))
 op.create_index('ix_school_lunch_shared_menus_name','school_lunch_shared_menus',['name'])
 op.create_table('school_lunch_shared_menu_revisions',sa.Column('id',sa.Integer(),primary_key=True),sa.Column('shared_menu_id',sa.Integer(),sa.ForeignKey('school_lunch_shared_menus.id'),nullable=False),sa.Column('number',sa.Integer(),nullable=False),sa.Column('parent_revision_id',sa.Integer(),sa.ForeignKey('school_lunch_shared_menu_revisions.id')),sa.Column('status',sa.String(12),nullable=False),sa.Column('source_month',sa.String(7),nullable=False),sa.Column('provenance',sa.JSON(),nullable=False),sa.Column('document',sa.JSON(),nullable=False),sa.Column('pattern_associations',sa.JSON(),nullable=False),sa.Column('change_note',sa.Text(),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.UniqueConstraint('shared_menu_id','number',name='uq_shared_menu_revision_number'))
 op.create_index('ix_school_lunch_shared_menu_revisions_shared_menu_id','school_lunch_shared_menu_revisions',['shared_menu_id']);op.create_index('ix_school_lunch_shared_menu_revisions_source_month','school_lunch_shared_menu_revisions',['source_month'])
 op.create_table('school_lunch_shared_menu_adoptions',sa.Column('id',sa.Integer(),primary_key=True),sa.Column('process_revision_id',sa.Integer(),sa.ForeignKey('school_lunch_process_revisions.id'),nullable=False),sa.Column('shared_menu_revision_id',sa.Integer(),sa.ForeignKey('school_lunch_shared_menu_revisions.id'),nullable=False),sa.Column('material_mapping',sa.JSON(),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.UniqueConstraint('process_revision_id',name='uq_shared_menu_adoption_process_revision'))
def downgrade():
 if op.get_bind().execute(sa.text('SELECT 1 FROM school_lunch_shared_menus LIMIT 1')).first(): raise RuntimeError('共有献立があるため削除できません')
 op.drop_table('school_lunch_shared_menu_adoptions');op.drop_table('school_lunch_shared_menu_revisions');op.drop_table('school_lunch_shared_menus')
