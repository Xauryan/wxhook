"""Record task creators independently of leads and collaborators."""
from alembic import op
import sqlalchemy as sa

revision = '20261011_task_creator'
down_revision = '20261011_git_email'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tc_tasks', sa.Column('created_by', sa.String(36), nullable=True))
    op.create_foreign_key('fk_tc_tasks_created_by', 'tc_tasks', 'tc_users', ['created_by'], ['id'])
    op.create_index('ix_tc_tasks_created_by', 'tc_tasks', ['created_by'])
    # Recover only recorded creation actors; never infer from the current lead or owner.
    op.execute("""
        UPDATE tc_tasks AS task SET created_by = origin.actor
        FROM (
            SELECT DISTINCT ON (event.task_id) event.task_id, event.actor
            FROM tc_events AS event JOIN tc_users AS member ON member.id = event.actor
            WHERE event.action IN ('task.created', 'task.copy')
            ORDER BY event.task_id, event.id
        ) AS origin
        WHERE task.id = origin.task_id
    """)


def downgrade():
    raise RuntimeError('Forward-only migration; preserve task attribution')
