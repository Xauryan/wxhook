from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String, Text, create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


def uid():
    return str(uuid4())


def now():
    return int(datetime.now(timezone.utc).timestamp())


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = 'tc_project'
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    # Retained only to preserve historical data from the superseded GitHub prototype.
    repository_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    owner_github_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    installation_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    owner_user_id: Mapped[str | None] = mapped_column(ForeignKey('tc_users.id'), nullable=True)
    sequence: Mapped[int] = mapped_column(BigInteger, default=0)


class User(Base):
    __tablename__ = 'tc_users'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    github_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    git_email: Mapped[str | None] = mapped_column(String(254), unique=True, nullable=True)
    login: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    access_until: Mapped[int] = mapped_column(BigInteger, default=0)


class Session(Base):
    __tablename__ = 'tc_sessions'
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('tc_users.id'), index=True)
    csrf: Mapped[str] = mapped_column(String(100))
    expires: Mapped[int] = mapped_column(BigInteger)
    last_seen: Mapped[int] = mapped_column(BigInteger)


class Login(Base):
    __tablename__ = 'tc_logins'
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    browser_hash: Mapped[str] = mapped_column(String(64))
    verifier: Mapped[str] = mapped_column(String(100))
    expires: Mapped[int] = mapped_column(BigInteger)


class Invite(Base):
    __tablename__ = 'tc_invites'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    login: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    created_by: Mapped[str] = mapped_column(ForeignKey('tc_users.id'))
    created_at: Mapped[int] = mapped_column(BigInteger, default=now)
    expires: Mapped[int] = mapped_column(BigInteger)
    used_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class Task(Base):
    __tablename__ = 'tc_tasks'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    number: Mapped[int] = mapped_column(BigInteger, unique=True)
    title: Mapped[str] = mapped_column(String(200))
    month: Mapped[str] = mapped_column(String(7), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True, default='draft')
    created_by: Mapped[str | None] = mapped_column(ForeignKey('tc_users.id'), nullable=True, index=True)
    lead_id: Mapped[str | None] = mapped_column(ForeignKey('tc_users.id'), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now)
    updated_at: Mapped[int] = mapped_column(BigInteger, default=now)
    completed_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # A task is a bounded aggregate. A project transaction lock guards all mutations.
    data: Mapped[dict] = mapped_column(JSONB)


class PullBinding(Base):
    __tablename__ = 'tc_pull_bindings'
    number: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey('tc_tasks.id'), index=True)


class Event(Base):
    __tablename__ = 'tc_events'
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    task_id: Mapped[str | None] = mapped_column(ForeignKey('tc_tasks.id'), nullable=True, index=True)
    actor: Mapped[str] = mapped_column(String(60))
    action: Mapped[str] = mapped_column(String(80))
    at: Mapped[int] = mapped_column(BigInteger, default=now)
    owner_only: Mapped[bool] = mapped_column(Boolean)
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)


class Receipt(Base):
    __tablename__ = 'tc_receipts'
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    task_id: Mapped[str | None] = mapped_column(ForeignKey('tc_tasks.id'), nullable=True)
    response: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[int] = mapped_column(BigInteger, default=now)


class Delivery(Base):
    __tablename__ = 'tc_deliveries'
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String(60))
    state: Mapped[str] = mapped_column(String(20), default='pending')
    created_at: Mapped[int] = mapped_column(BigInteger, default=now)


class SyncJob(Base):
    __tablename__ = 'tc_sync_jobs'
    task_id: Mapped[str] = mapped_column(ForeignKey('tc_tasks.id'), primary_key=True)
    due: Mapped[int] = mapped_column(BigInteger, default=0)
    lease_until: Mapped[int] = mapped_column(BigInteger, default=0)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Database:
    def __init__(self, url):
        self.engine = create_engine(url, pool_pre_ping=True)
        self.session = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def transaction(self):
        with self.session.begin() as db:
            yield db

    def lock(self, db):
        # One private project: serializing short writes also makes event cursors commit ordered.
        return db.scalar(select(Project).where(Project.id == 1).with_for_update())

    def initialize(self, settings):
        with self.transaction() as db:
            project = self.lock(db)
            if project is None:
                db.add(Project(id=1))

    def event(self, db, actor, action, task=None, detail=None):
        project = db.get(Project, 1)
        project.sequence += 1
        db.add(Event(id=project.sequence, task_id=task.id if task else None, actor=actor,
                     action=action, owner_only=task is None or task.status == 'draft', detail=detail or {}))
        return project.sequence
