"""Self-declared Git email identities; never infer owner privileges from email."""
from alembic import op
import sqlalchemy as sa

revision = '20261011_git_email'
down_revision = '20261011_invites'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('tc_users', sa.Column('git_email', sa.String(254), nullable=True))
    op.create_unique_constraint('uq_tc_users_git_email', 'tc_users', ['git_email'])


def downgrade():
    raise RuntimeError('Forward-only migration; preserve identity and contribution history')
