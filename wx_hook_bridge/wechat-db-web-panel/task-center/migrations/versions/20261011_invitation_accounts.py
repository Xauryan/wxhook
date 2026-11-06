"""Invitation accounts and manual task delivery; preserve all previous records."""
from alembic import op
import sqlalchemy as sa
import json

revision = '20261011_invites'
down_revision = 'fc531d1e9b61'
branch_labels = None
depends_on = None


def upgrade():
    for name in ('repository_id', 'owner_github_id', 'installation_id'):
        op.alter_column('tc_project', name, existing_type=sa.BigInteger(), nullable=True)
    op.alter_column('tc_users', 'github_id', existing_type=sa.BigInteger(), nullable=True)
    op.add_column('tc_users', sa.Column('password_hash', sa.Text(), nullable=True))
    op.add_column('tc_project', sa.Column('owner_user_id', sa.String(36), nullable=True))
    op.create_foreign_key('fk_tc_project_owner', 'tc_project', 'tc_users', ['owner_user_id'], ['id'])
    op.create_table('tc_invites',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('token_hash', sa.String(64), nullable=False, unique=True),
        sa.Column('login', sa.String(100), nullable=False),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('created_by', sa.String(36), sa.ForeignKey('tc_users.id'), nullable=False),
        sa.Column('created_at', sa.BigInteger(), nullable=False),
        sa.Column('expires', sa.BigInteger(), nullable=False),
        sa.Column('used_at', sa.BigInteger(), nullable=True),
        sa.Column('revoked', sa.Boolean(), nullable=False))
    # Legacy OAuth sessions have no account password and are intentionally invalidated by auth.guard.
    # Keep source JSON and retired queue tables as historical evidence, never process them again.
    op.execute("UPDATE tc_tasks SET data = data || '{\"deliveries\": []}'::jsonb WHERE NOT data ? 'deliveries'")
    connection = op.get_bind()
    for row in connection.execute(sa.text("SELECT id, status, data FROM tc_tasks WHERE status IN ('in_review','changes_requested')" )).mappings():
        data = dict(row['data'])
        if not data['deliveries']:
            previous = next((s for s in reversed(data.get('submissions', [])) if s.get('state') == 'active'), None)
            if previous:
                data['deliveries'] = [{'id': previous['id'], 'state': 'active', 'author': previous['author'],
                    'summary': previous['summary'], 'at': previous['at'], 'legacy': True,
                    'pr_links': [p['url'] for p in data.get('pulls', []) if p.get('number') in previous.get('required', [])]}]
                connection.execute(sa.text('UPDATE tc_tasks SET data=CAST(:data AS jsonb) WHERE id=:id'),
                                   {'id': row['id'], 'data': json.dumps(data)})


def downgrade():
    raise RuntimeError('Forward-only migration; retain account and delivery history')
