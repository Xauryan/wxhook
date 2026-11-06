import hashlib
import secrets
from dataclasses import dataclass

from sqlalchemy import delete, select

from .db import Project, Session, User, now, uid
from .errors import require


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    derived = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return f'scrypt${salt}${derived}'


DUMMY_HASH = password_hash('not-an-account-password', '01' * 16)


def password_matches(password, encoded):
    try:
        algorithm, salt, _ = (encoded or DUMMY_HASH).split('$')
        return algorithm == 'scrypt' and secrets.compare_digest(password_hash(password, salt), encoded or DUMMY_HASH)
    except (ValueError, TypeError):
        return False


@dataclass(frozen=True)
class Actor:
    id: str
    login: str
    name: str
    owner: bool
    token_hash: str
    csrf: str

    def public(self):
        return {'id': self.id, 'login': self.login, 'name': self.name, 'owner': self.owner, 'csrf': self.csrf}


class Auth:
    def __init__(self, db, settings):
        self.db, self.settings = db, settings

    def bootstrap(self, login, name, password):
        from .schemas import AccountCredentials
        data = AccountCredentials(login=login, password=password)
        encoded = password_hash(data.password)
        with self.db.transaction() as db:
            project = self.db.lock(db)
            require(project.owner_user_id is None, 'owner_exists', '所有者已初始化。', 409)
            require(db.scalar(select(User.id).where(User.login == data.login)) is None,
                    'login_exists', '账号已存在，请使用新的所有者账号。', 409)
            user = User(login=data.login, name=name.strip()[:200] or data.login, password_hash=encoded)
            db.add(user)
            db.flush()
            project.owner_user_id = user.id
            self.db.event(db, user.id, 'owner.initialized')
            return user.id

    def issue(self, db, user):
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        db.add(Session(token_hash=digest(token), user_id=user.id, csrf=csrf,
                       expires=now() + 7 * 86400, last_seen=now()))
        return token

    def login(self, login, password):
        with self.db.transaction() as db:
            user = db.scalar(select(User).where(User.login == login))
            encoded = user.password_hash if user else None
            valid = password_matches(password, encoded)
            require(user is not None and encoded and valid and not user.disabled and
                    db.get(Project, 1).owner_user_id == user.id,
                    'invalid_credentials', '账号或密码错误，或账号已停用。', 401)
            user_id = user.id
        with self.db.transaction() as db:
            self.db.lock(db)
            user = db.get(User, user_id)
            require(user is not None and not user.disabled and user.password_hash == encoded,
                    'invalid_credentials', '账号或密码错误，或账号已停用。', 401)
            return self.issue(db, user)

    def identify(self, email):
        from .schemas import GitIdentity
        email = GitIdentity(email=email).email
        with self.db.transaction() as db:
            project = self.db.lock(db)
            user = db.scalar(select(User).where(User.git_email == email))
            if user is None:
                user = User(login='git-' + uid(), name=email[:200], git_email=email)
                db.add(user)
                db.flush()
                self.db.event(db, user.id, 'member.identified')
            require(user.id != project.owner_user_id, 'owner_identity_protected',
                    '该邮箱不能用于取得所有者权限，请使用已有管理会话。', 403)
            require(not user.disabled, 'member_disabled', '此邮箱已被停用，请联系所有者。', 403)
            return self.issue(db, user)

    def guard(self, db, actor):
        session, user = db.get(Session, actor.token_hash), db.get(User, actor.id)
        require(session is not None and user is not None and not user.disabled and (user.password_hash or user.git_email) and
                session.user_id == actor.id and session.expires > now() and session.last_seen > now() - 86400,
                'session_expired', '请重新填写 Git 邮箱进入。', 401)
        return user

    def authenticate(self, token, *, write=False, origin=None, csrf=None, touch=True):
        require(bool(token), 'identity_required', '请填写 Git 邮箱进入。', 401)
        with self.db.transaction() as db:
            self.db.lock(db)
            session = db.get(Session, digest(token))
            require(session is not None, 'session_expired', '请重新登录任务中心。', 401)
            user = db.get(User, session.user_id)
            actor = Actor(user.id, user.git_email or user.login, user.git_email or user.name, db.get(Project, 1).owner_user_id == user.id,
                          session.token_hash, session.csrf)
            self.guard(db, actor)
            if write:
                require(self.settings.allows_origin(origin) and isinstance(csrf, str) and secrets.compare_digest(actor.csrf, csrf),
                        'csrf_failed', '请求来源无效，请刷新后重试。', 403)
            if touch:
                session.last_seen = now()
            return actor

    def logout(self, token, origin, csrf):
        require(self.settings.allows_origin(origin), 'csrf_failed', '请求来源无效。', 403)
        with self.db.transaction() as db:
            session = db.get(Session, digest(token)) if token else None
            if session:
                require(isinstance(csrf, str) and secrets.compare_digest(session.csrf, csrf), 'csrf_failed', '请求校验失败。', 403)
                db.delete(session)

    def require_owner(self, db, actor):
        self.guard(db, actor)
        require(actor.owner and db.get(Project, 1).owner_user_id == actor.id, 'owner_required', '仅所有者可以操作。', 403)

    def set_enabled(self, actor, user_id, enabled, reason):
        with self.db.transaction() as db:
            self.db.lock(db)
            self.require_owner(db, actor)
            user = db.get(User, user_id)
            require(user is not None, 'not_found', '成员不存在。', 404)
            require(user.id != db.get(Project, 1).owner_user_id, 'owner_protected', '不能停用所有者。')
            require(user.password_hash is not None or user.git_email, 'legacy_account', '历史账号尚未建立有效身份。')
            if user.disabled == enabled:
                user.disabled = not enabled
                db.execute(delete(Session).where(Session.user_id == user_id))
                self.db.event(db, actor.id, 'member.enabled' if enabled else 'member.disabled',
                              detail={'user_id': user_id, 'reason': reason})
