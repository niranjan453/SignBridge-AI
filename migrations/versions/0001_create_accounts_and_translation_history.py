"""Create accounts and translation history"""

from alembic import op
import sqlalchemy as sa


revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'users',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('email', sa.String(length=254), nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column('token_version', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email')
    )
    op.create_table(
        'translations',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('mode', sa.String(length=32), nullable=False),
        sa.Column('input_text', sa.Text(), nullable=True),
        sa.Column('output_text', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('model_version', sa.String(length=100), nullable=False),
        sa.Column('is_mock', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
        sa.CheckConstraint("status IN ('completed', 'failed', 'timed_out')", name='ck_translation_status'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_translations_user_created_id', 'translations', ['user_id', 'created_at', 'id'], unique=False)
    op.create_table(
        'user_settings',
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('preferred_language', sa.String(length=35), nullable=False),
        sa.Column('save_history', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('user_id')
    )
    op.create_table(
        'translation_feedback',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('translation_id', sa.Uuid(), nullable=False),
        sa.Column('rating', sa.Integer(), nullable=False),
        sa.Column('comment', sa.String(length=500), nullable=True),
        sa.CheckConstraint('rating BETWEEN 1 AND 5', name='ck_feedback_rating'),
        sa.ForeignKeyConstraint(['translation_id'], ['translations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('translation_id')
    )


def downgrade() -> None:
    op.drop_table('translation_feedback')
    op.drop_table('user_settings')
    op.drop_index('ix_translations_user_created_id', table_name='translations')
    op.drop_table('translations')
    op.drop_table('users')
