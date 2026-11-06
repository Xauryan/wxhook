import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .errors import AIConfigurationError
from .models import (ModelEdit, ModelProfile, ModelSettings, ProfileEdit, ProviderEdit, ProviderSettings,
                     compose, split)
from .secrets import protect


class AIStore:
    """Providers own the endpoint and credential; model rows reference a provider.

    Callers address models by profile id and receive the composed ModelProfile, so a
    provider change is visible to every model under it. Changing a provider's
    connection bumps those models' revisions, which in-flight tasks compare against.
    """

    def __init__(self, path: Path, secret_codec=protect):
        self.path, self.secret_codec = Path(path), secret_codec
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS ai_profiles (
                    id TEXT PRIMARY KEY, revision INTEGER NOT NULL,
                    config TEXT NOT NULL, secret BLOB, updated_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS ai_providers (
                    id TEXT PRIMARY KEY, revision INTEGER NOT NULL, config TEXT NOT NULL,
                    secret BLOB, updated_at REAL NOT NULL, deleted INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS ai_usage (
                    id INTEGER PRIMARY KEY, profile_id TEXT NOT NULL, purpose TEXT NOT NULL,
                    model TEXT NOT NULL, status TEXT NOT NULL, usage TEXT NOT NULL,
                    elapsed_ms INTEGER NOT NULL, created_at REAL NOT NULL);
            ''')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(ai_profiles)')}
            if 'deleted' not in columns:
                db.execute('ALTER TABLE ai_profiles ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0')
            if 'provider_id' not in columns:
                db.execute('ALTER TABLE ai_profiles ADD COLUMN provider_id TEXT')
            self._migrate_flat_profiles(db)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    # Migration -------------------------------------------------------------

    def _migrate_flat_profiles(self, db):
        """Group legacy self-contained profiles that share kind, endpoint and key into one provider."""
        db.execute("UPDATE ai_profiles SET provider_id='' WHERE provider_id IS NULL AND deleted=1")
        rows = db.execute('SELECT * FROM ai_profiles WHERE provider_id IS NULL ORDER BY rowid').fetchall()
        groups = {}
        for row in rows:
            provider, model = split(ModelProfile.model_validate_json(row['config']))
            try:
                key = self.secret_codec(row['secret'], decrypt=True) if row['secret'] else b''
            except Exception:  # an unreadable key must not merge with anything else
                key = row['id'].encode()
            group = (provider.kind, provider.base_url, key)
            if group not in groups:
                groups[group] = self._insert_provider(db, provider, row['secret'])
            db.execute('UPDATE ai_profiles SET provider_id=?,config=?,secret=NULL WHERE id=?',
                       (groups[group], model.model_dump_json(), row['id']))

    def _insert_provider(self, db, config: ProviderSettings, secret):
        ident = uuid4().hex
        db.execute('INSERT INTO ai_providers(id,revision,config,secret,updated_at) VALUES (?,?,?,?,?)',
                   (ident, 1, config.model_dump_json(), secret, time.time()))
        return ident

    def insert_flat(self, db, ident, config: ModelProfile, secret, revision=1):
        """Store a self-contained legacy profile under a new provider of its own."""
        provider, model = split(config)
        provider_id = self._insert_provider(db, provider, secret)
        db.execute('INSERT INTO ai_profiles(id,revision,config,secret,updated_at,deleted,provider_id) VALUES (?,?,?,NULL,?,0,?)',
                   (ident, revision, model.model_dump_json(), time.time(), provider_id))
        return ident

    # Reads -----------------------------------------------------------------

    @staticmethod
    def _provider(db, ident):
        return db.execute('SELECT * FROM ai_providers WHERE id=? AND deleted=0', (ident,)).fetchone()

    @staticmethod
    def _model(db, ident):
        return db.execute('SELECT * FROM ai_profiles WHERE id=? AND deleted=0', (ident,)).fetchone()

    @staticmethod
    def public_provider(row):
        return {'id': row['id'], 'revision': row['revision'],
                'config': ProviderSettings.model_validate_json(row['config']).model_dump(),
                'api_key_set': bool(row['secret']), 'updated_at': row['updated_at']}

    @staticmethod
    def public(row, provider):
        model = ModelSettings.model_validate_json(row['config'])
        config = compose(ProviderSettings.model_validate_json(provider['config']), model)
        return {'id': row['id'], 'revision': row['revision'], 'provider_id': provider['id'],
                'config': config.model_dump(), 'model': model.model_dump(),
                'api_key_set': bool(provider['secret']), 'updated_at': row['updated_at'],
                'decision_only': config.decision_only}

    def _public_by_id(self, db, ident):
        row = self._model(db, ident)
        return self.public(row, self._provider(db, row['provider_id']))

    def list(self):
        with self.db() as db:
            providers = {row['id']: row for row in db.execute('SELECT * FROM ai_providers WHERE deleted=0 ORDER BY rowid')}
            profiles = [self.public(row, providers[row['provider_id']])
                        for row in db.execute('SELECT * FROM ai_profiles WHERE deleted=0 ORDER BY rowid')
                        if row['provider_id'] in providers]
            usage = [dict(row) for row in db.execute('SELECT profile_id,purpose,model,status,usage,elapsed_ms,created_at FROM ai_usage ORDER BY id DESC LIMIT 20')]
        for row in usage:
            row['usage'] = json.loads(row['usage'])
        return {'providers': [self.public_provider(row) for row in providers.values()],
                'profiles': profiles, 'recent_usage': usage}

    def get(self, ident: str):
        with self.db() as db:
            row = self._model(db, ident)
            provider = self._provider(db, row['provider_id']) if row else None
        if row is None or provider is None:
            raise AIConfigurationError(404, '模型配置不存在')
        config = compose(ProviderSettings.model_validate_json(provider['config']),
                         ModelSettings.model_validate_json(row['config']))
        if not config.enabled:
            raise AIConfigurationError(409, '所选模型配置已停用')
        key = self.secret_codec(provider['secret'], decrypt=True).decode() if provider['secret'] else ''
        return config, key, row['revision']

    # Writes ----------------------------------------------------------------

    def _secret(self, old, old_config, config, edit):
        key = edit.api_key.get_secret_value().strip() if edit.api_key else ''
        if old and old_config.base_url != config.base_url and old['secret'] and not (key or edit.clear_api_key):
            raise AIConfigurationError(422, '更换接口地址后请重新填写密钥')
        secret = old['secret'] if old else None
        if edit.clear_api_key:
            secret = None
        if key:
            secret = self.secret_codec(key.encode())
        return secret

    def _write_provider(self, db, ident, old, config: ProviderSettings, secret, skip_model=None):
        now = time.time()
        db.execute('UPDATE ai_providers SET revision=revision+1,config=?,secret=?,updated_at=? WHERE id=?',
                   (config.model_dump_json(), secret, now, ident))
        before = ProviderSettings.model_validate_json(old['config'])
        connection = ('kind', 'base_url', 'search_fetch', 'enabled')
        if secret != old['secret'] or any(getattr(before, name) != getattr(config, name) for name in connection):
            db.execute('UPDATE ai_profiles SET revision=revision+1,updated_at=? WHERE provider_id=? AND deleted=0 AND id<>?',
                       (now, ident, skip_model or ''))

    def save_provider(self, ident: str | None, edit: ProviderEdit):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old = self._provider(db, ident) if ident else None
            if ident and old is None:
                raise AIConfigurationError(404, '供应商不存在')
            if edit.revision != (old['revision'] if old else 0):
                raise AIConfigurationError(409, '供应商配置已变化，请刷新后保存')
            old_config = ProviderSettings.model_validate_json(old['config']) if old else None
            secret = self._secret(old, old_config, edit.config, edit)
            if old:
                self._write_provider(db, ident, old, edit.config, secret)
            else:
                ident = self._insert_provider(db, edit.config, secret)
            return self.public_provider(self._provider(db, ident))

    def delete_provider(self, ident, revision):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._provider(db, ident)
            if row is None:
                raise AIConfigurationError(404, '供应商不存在')
            if row['revision'] != revision:
                raise AIConfigurationError(409, '供应商配置已变化，请刷新后删除')
            now = time.time()
            db.execute('UPDATE ai_providers SET deleted=1,secret=NULL,revision=revision+1,updated_at=? WHERE id=?', (now, ident))
            db.execute('UPDATE ai_profiles SET deleted=1,revision=revision+1,updated_at=? WHERE provider_id=? AND deleted=0',
                       (now, ident))
        return {'ok': True}

    def save_model(self, ident: str | None, edit: ModelEdit):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old = self._model(db, ident) if ident else None
            if ident and old is None:
                raise AIConfigurationError(404, '模型配置不存在')
            if edit.revision != (old['revision'] if old else 0):
                raise AIConfigurationError(409, '模型配置已变化，请刷新后保存')
            if self._provider(db, edit.provider_id) is None:
                raise AIConfigurationError(404, '供应商不存在')
            ident = ident or uuid4().hex
            db.execute('INSERT INTO ai_profiles(id,revision,config,secret,updated_at,deleted,provider_id) VALUES (?,?,?,NULL,?,0,?) '
                       'ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,config=excluded.config,'
                       'updated_at=excluded.updated_at,provider_id=excluded.provider_id',
                       (ident, edit.revision + 1, edit.config.model_dump_json(), time.time(), edit.provider_id))
            return self._public_by_id(db, ident)

    def save(self, ident: str | None, edit: ProfileEdit):
        """Flat compatibility API: one call edits a model together with its provider's connection."""
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old = self._model(db, ident) if ident else None
            if ident and old is None:
                raise AIConfigurationError(404, '模型配置不存在')
            if edit.revision != (old['revision'] if old else 0):
                raise AIConfigurationError(409, '模型配置已变化，请刷新后保存')
            provider, model = split(edit.config)
            if old is None:
                secret = self._secret(None, None, provider, edit)
                ident = self.insert_flat(db, uuid4().hex, edit.config, secret)
                return self._public_by_id(db, ident)
            provider_row = self._provider(db, old['provider_id'])
            old_provider = ProviderSettings.model_validate_json(provider_row['config'])
            secret = self._secret(provider_row, old_provider, provider, edit)
            provider = provider.model_copy(update={'enabled': old_provider.enabled})
            self._write_provider(db, provider_row['id'], provider_row, provider, secret, skip_model=ident)
            db.execute('UPDATE ai_profiles SET revision=?,config=?,updated_at=? WHERE id=?',
                       (edit.revision + 1, model.model_dump_json(), time.time(), ident))
            return self._public_by_id(db, ident)

    def delete(self, ident, revision):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT revision FROM ai_profiles WHERE id=? AND deleted=0', (ident,)).fetchone()
            if row is None:
                raise AIConfigurationError(404, '模型配置不存在')
            if row['revision'] != revision:
                raise AIConfigurationError(409, '模型配置已变化，请刷新后删除')
            db.execute('UPDATE ai_profiles SET deleted=1,secret=NULL,revision=revision+1,updated_at=? WHERE id=?',
                       (time.time(), ident))
        return {'ok': True}

    def record(self, ident, purpose, model, status, usage, elapsed_ms):
        with self.db() as db:
            db.execute('INSERT INTO ai_usage(profile_id,purpose,model,status,usage,elapsed_ms,created_at) VALUES (?,?,?,?,?,?,?)',
                       (ident, purpose[:80], model, status, json.dumps(usage), elapsed_ms, time.time()))
            db.execute('DELETE FROM ai_usage WHERE id NOT IN (SELECT id FROM ai_usage ORDER BY id DESC LIMIT 1000)')
