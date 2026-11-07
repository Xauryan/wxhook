from typing import Literal
from urllib.parse import urlsplit

from .capabilities import capabilities

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator


ApiFormat = Literal['auto', 'chat_completions', 'responses', 'anthropic', 'openai_decisions', 'typesafe_decisions', 'typesafe_systemone']
ProviderKind = Literal['compatible', 'openai', 'deepseek', 'anthropic']
DECISION_PROTOCOLS = {'openai_decisions', 'typesafe_decisions', 'typesafe_systemone'}


def endpoint(value: str) -> str:
    value = value.strip().rstrip('/')
    url = urlsplit(value)
    if url.scheme not in {'http', 'https'} or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('请填写有效的接口根地址')
    if url.scheme == 'http' and url.hostname not in {'localhost', '127.0.0.1', '::1'}:
        raise ValueError('远程模型接口须使用 HTTPS，本机接口可使用 HTTP')
    return value


def check_model_limits(model: str, max_output_tokens: int, reasoning_effort: str | None):
    meta = capabilities(model)
    if meta:
        if max_output_tokens > meta['max_output_tokens']:
            raise ValueError(f"该模型最大输出为 {meta['max_output_tokens']} Token")
        if reasoning_effort is not None and reasoning_effort not in meta['reasoning_efforts']:
            raise ValueError('该模型支持的推理强度为 ' + ', '.join(meta['reasoning_efforts']))


def default_output_tokens(value):
    if isinstance(value, dict) and 'max_output_tokens' not in value:
        meta = capabilities(str(value.get('model', '')))
        if meta:
            value = {**value, 'max_output_tokens': meta['max_output_tokens']}
    return value


class ModelProfile(BaseModel):
    """Runtime view of one model: its provider's connection merged with the model's own settings."""
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=130)
    base_url: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=120)
    api_format: ApiFormat = 'auto'
    provider: Literal['compatible', 'openai', 'deepseek', 'anthropic', 'openai_decisions', 'typesafe_decisions', 'typesafe_systemone'] = 'compatible'
    search_fetch: bool = False
    reasoning_effort: Literal['none', 'low', 'medium', 'high', 'xhigh', 'max'] | None = None
    enabled: bool = True
    timeout_seconds: int = Field(default=120, ge=10, le=600)
    max_output_tokens: int = Field(default=32768, ge=128, le=1048576)
    context_chars: int | None = Field(default=None, ge=4000)
    token_parameter: Literal['max_tokens', 'max_completion_tokens'] = 'max_tokens'
    json_mode: bool = False
    temperature: float | None = Field(default=None, ge=0, le=2)

    @model_validator(mode='before')
    @classmethod
    def model_defaults(cls, value):
        return default_output_tokens(value)

    @property
    def protocol(self):
        if self.api_format != 'auto':
            return self.api_format
        if self.provider in DECISION_PROTOCOLS:
            return self.provider
        if self.provider == 'deepseek':
            return 'chat_completions'
        if self.provider == 'anthropic':
            return 'anthropic'
        if self.provider == 'openai' or self.model.startswith('gpt-6'):
            return 'responses'
        return 'chat_completions'

    @property
    def decision_only(self):
        return self.protocol in DECISION_PROTOCOLS

    @model_validator(mode='after')
    def validate_endpoint(self):
        self.name, self.model = self.name.strip(), self.model.strip()
        self.base_url = self.base_url.strip().rstrip('/')
        url = urlsplit(self.base_url)
        if (url.scheme not in {'http', 'https'} or not url.hostname or url.username
                or url.password or url.query or url.fragment or not self.name or not self.model):
            raise ValueError('请填写有效的接口根地址、名称和模型名')
        if url.scheme == 'http' and url.hostname not in {'localhost', '127.0.0.1', '::1'}:
            raise ValueError('远程模型接口须使用 HTTPS，本机接口可使用 HTTP')
        return self


class ProfileEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(default=0, ge=0)
    config: ModelProfile
    api_key: SecretStr | None = None
    clear_api_key: bool = False

    @model_validator(mode='after')
    def validate_secret(self):
        check_model_limits(self.config.model, self.config.max_output_tokens, self.config.reasoning_effort)
        if self.api_key and (len(self.api_key.get_secret_value()) > 8192 or self.clear_api_key):
            raise ValueError('密钥设置无效')
        return self


class ProviderSettings(BaseModel):
    """One endpoint and credential, shared by every model listed under it."""
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=60)
    kind: ProviderKind = 'compatible'
    base_url: str = Field(min_length=1, max_length=500)
    search_fetch: bool = False
    enabled: bool = True

    @model_validator(mode='after')
    def validate_endpoint(self):
        self.name = self.name.strip()
        if not self.name:
            raise ValueError('请填写供应商名称')
        self.base_url = endpoint(self.base_url)
        if self.kind != 'deepseek':
            self.search_fetch = False
        return self


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    label: str | None = Field(default=None, max_length=60)
    model: str = Field(min_length=1, max_length=120)
    api_format: ApiFormat = 'auto'
    reasoning_effort: Literal['none', 'low', 'medium', 'high', 'xhigh', 'max'] | None = None
    enabled: bool = True
    timeout_seconds: int = Field(default=120, ge=10, le=600)
    max_output_tokens: int = Field(default=32768, ge=128, le=1048576)
    context_chars: int | None = Field(default=None, ge=4000)
    token_parameter: Literal['max_tokens', 'max_completion_tokens'] = 'max_tokens'
    json_mode: bool = False
    temperature: float | None = Field(default=None, ge=0, le=2)

    @model_validator(mode='before')
    @classmethod
    def model_defaults(cls, value):
        return default_output_tokens(value)

    @model_validator(mode='after')
    def normalize(self):
        self.model = self.model.strip()
        self.label = (self.label or '').strip() or None
        if not self.model:
            raise ValueError('请填写模型名')
        return self


def compose(provider: ProviderSettings, model: ModelSettings) -> ModelProfile:
    return ModelProfile(name=f'{provider.name} · {model.label or model.model}', base_url=provider.base_url,
                        provider=provider.kind, search_fetch=provider.search_fetch,
                        enabled=provider.enabled and model.enabled,
                        **model.model_dump(exclude={'label', 'enabled'}))


def split(config: ModelProfile) -> tuple[ProviderSettings, ModelSettings]:
    """Inverse of compose for flat legacy profiles; decision 'providers' become a model protocol."""
    kind, api_format = config.provider, config.api_format
    if kind in DECISION_PROTOCOLS:
        api_format = kind if api_format == 'auto' else api_format
        kind = 'openai' if kind == 'openai_decisions' else 'compatible'
    name, _, label = config.name.partition(' · ')
    label = label.strip()
    provider = ProviderSettings(name=name.strip() or config.name, kind=kind, base_url=config.base_url,
                                search_fetch=config.search_fetch)
    fields = config.model_dump(include=set(ModelSettings.model_fields) - {'label', 'enabled'})
    model = ModelSettings(**{**fields, 'api_format': api_format},
                          label=None if label in {'', config.model} else label, enabled=config.enabled)
    return provider, model


class ProviderEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(default=0, ge=0)
    config: ProviderSettings
    api_key: SecretStr | None = None
    clear_api_key: bool = False

    @model_validator(mode='after')
    def validate_secret(self):
        if self.api_key and (len(self.api_key.get_secret_value()) > 8192 or self.clear_api_key):
            raise ValueError('密钥设置无效')
        return self


class ModelEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(default=0, ge=0)
    provider_id: str = Field(min_length=1, max_length=64)
    config: ModelSettings

    @model_validator(mode='after')
    def validate_limits(self):
        check_model_limits(self.config.model, self.config.max_output_tokens, self.config.reasoning_effort)
        return self


class ModelBatch(BaseModel):
    model_config = ConfigDict(extra='forbid')
    models: list[ModelSettings] = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def validate_limits(self):
        for model in self.models:
            check_model_limits(model.model, model.max_output_tokens, model.reasoning_effort)
        return self


class ChatMessage(BaseModel):
    role: Literal['system', 'user', 'assistant']
    content: str


class Completion(BaseModel):
    text: str
    model: str
    usage: dict[str, int]
    elapsed_ms: int
