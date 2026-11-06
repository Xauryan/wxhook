"""Credentials for standalone AI tools, shared by all business features."""

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from .errors import AIConfigurationError


class ToolEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(default=0, ge=0)
    api_key: SecretStr | None = None
    clear_api_key: bool = False
    enabled: bool = True

    @model_validator(mode='after')
    def validate_key(self):
        if self.api_key and (self.clear_api_key or len(self.api_key.get_secret_value()) > 8192):
            raise ValueError('密钥设置无效')
        return self


class ToolStore:
    """Compatibility entry point backed by the shared AI provider profiles."""
    def __init__(self, models, profile_id=None):
        self.models, self.profile_id = models, profile_id
        with models.db() as db:
            db.execute('BEGIN IMMEDIATE')
            legacy = db.execute("SELECT name FROM sqlite_master WHERE name='ai_tool_settings'").fetchone()
            if legacy:
                row = db.execute("SELECT * FROM ai_tool_settings WHERE provider='deepseek'").fetchone()
                if row and row['secret']:
                    from .models import ModelProfile
                    config = ModelProfile(name='DeepSeek', provider='deepseek',
                        base_url='https://api.deepseek.com', model='deepseek-flash',
                        api_format='auto', search_fetch=True, enabled=bool(row['enabled']))
                    if not db.execute("SELECT 1 FROM ai_profiles WHERE id='deepseek-default'").fetchone():
                        models.insert_flat(db, 'deepseek-default', config, row['secret'])
                    db.execute("UPDATE ai_tool_settings SET secret=NULL,enabled=0 WHERE provider='deepseek'")
                    db.execute("UPDATE ai_usage SET profile_id='deepseek-default' WHERE profile_id='tool:deepseek'")

    def connection(self):
        ident = self.profile_id
        if not ident:
            profiles = [row for row in self.models.list()['profiles']
                        if row['config'].get('provider') == 'deepseek'
                        and row['config'].get('search_fetch') and row['config']['enabled']]
            if len(profiles) != 1:
                raise AIConfigurationError(409, '请在 AI 接入中启用一个 DeepSeek Search / Fetch 服务商配置')
            ident = profiles[0]['id']
        config, key, revision = self.models.get(ident)
        if config.provider != 'deepseek' or not config.search_fetch or not key:
            raise AIConfigurationError(409, '该 AI 配置尚未启用 DeepSeek Search / Fetch 或缺少密钥')
        # Both official model protocol roots share the same tools endpoint.
        from urllib.parse import urlsplit
        url = urlsplit(config.base_url)
        root = config.base_url
        if url.hostname == 'api.deepseek.com':
            root = 'https://api.deepseek.com'
        elif root.endswith('/v1'):
            root = root[:-3]
        return ident, config, key, root + '/v1/tools', revision

    def status(self):
        try:
            ident, config, _, _, revision = self.connection()
            return {'provider': 'deepseek', 'profile_id': ident, 'base_url': config.base_url,
                    'revision': revision, 'enabled': config.enabled, 'api_key_set': True}
        except AIConfigurationError:
            return {'provider': 'deepseek', 'revision': 0, 'enabled': False, 'api_key_set': False,
                    'base_url': 'https://api.deepseek.com'}

    def save(self, edit: ToolEdit):
        from .models import ProfileEdit
        ident, config, _, _, _ = self.connection()
        self.models.save(ident, ProfileEdit(revision=edit.revision,
            config=config.model_copy(update={'enabled': edit.enabled}),
            api_key=edit.api_key, clear_api_key=edit.clear_api_key))
        return self.status()

    def credential(self):
        return self.connection()[2]
