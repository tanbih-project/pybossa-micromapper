"""Private project media storage and file registry.

Revision ID: b27a40e7819f
Revises: 31c3c2ff9fab
"""
from alembic import op
import sqlalchemy as sa

revision = 'b27a40e7819f'
down_revision = '31c3c2ff9fab'


def upgrade():
    op.create_table('project_media_storage',
        sa.Column('project_id', sa.Integer, sa.ForeignKey('project.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('root_key', sa.Text, nullable=False),
        sa.Column('directory', sa.Text, nullable=False),
        sa.Column('source_root_key', sa.Text, nullable=False),
        sa.Column('source_directory', sa.Text, nullable=False))
    op.create_table('media_asset',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('project_id', sa.Integer, sa.ForeignKey('project.id', ondelete='CASCADE'), nullable=False),
        sa.Column('owner_id', sa.Integer, sa.ForeignKey('user.id'), nullable=False),
        sa.Column('task_run_id', sa.Integer, sa.ForeignKey('task_run.id', ondelete='CASCADE')),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('root_key', sa.Text, nullable=False),
        sa.Column('relative_path', sa.Text, nullable=False),
        sa.Column('original_name', sa.Text, nullable=False),
        sa.Column('mimetype', sa.Text, nullable=False),
        sa.Column('size', sa.BigInteger, nullable=False),
        sa.Column('uploaded', sa.Boolean, nullable=False),
        sa.Column('created', sa.Text, nullable=False))
    op.create_index('ix_media_asset_project_id', 'media_asset', ['project_id'])
    op.create_index('ix_media_asset_task_run_id', 'media_asset', ['task_run_id'])


def downgrade():
    op.drop_table('media_asset')
    op.drop_table('project_media_storage')
