import re
from typing import Literal

from ..retrieval.config import RetrievalConfig

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Checked against 129 real questions from the group: none lacks one of these signals.
QUESTION_PATTERN = (r'[？?]|吗|么|呢|啥|什么|怎么|怎样|如何|为什么|为啥|几|多少|哪|谁|是不是|能不能|可不可以|会不会|'
                    r'有没有|区别|意思|咋|想问|请问|问下|问一下|求助|求教|帮忙|帮看|有人知道|谁知道')
SearchMode = Literal['bwiki', 'deepseek', 'gpt', 'hybrid', 'deepseek_direct']


class ReplyConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = False
    proactive: bool = True
    ai_profile_id: str = Field(min_length=1, max_length=64)
    planning_profile_id: str | None = Field(default=None, min_length=1, max_length=64)
    review_profile_id: str | None = Field(default=None, min_length=1, max_length=64)
    local_min_text_chars: int = Field(default=1, ge=1, le=100)
    local_require_question_signal: bool = True
    local_question_pattern: str = Field(default=QUESTION_PATTERN, min_length=1, max_length=500)
    context_message_chars: int = Field(default=1600, ge=100, le=50000)
    context_quote_chars: int = Field(default=1200, ge=100, le=50000)
    exclude_same_second: bool = True
    decision_prompt: str | None = Field(default=None, min_length=1, max_length=20000)
    plan_prompt: str | None = Field(default=None, min_length=1, max_length=20000)
    answer_prompt: str | None = Field(default=None, min_length=1, max_length=20000)
    review_prompt: str | None = Field(default=None, min_length=1, max_length=20000)
    search_prompt: str | None = Field(default=None, min_length=1, max_length=20000)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    decision_profile_ids: list[str] = Field(default_factory=list, max_length=10)
    decision_skip_threshold: float = Field(default=.9, ge=.5, le=1)
    search_profile_id: str | None = Field(default=None, min_length=1, max_length=64)
    search_mode: SearchMode = 'hybrid'
    context_messages: int = Field(default=24, ge=4, le=60)
    context_minutes: int = Field(default=15, ge=1, le=60)
    processing_timeout_seconds: int = Field(default=360, ge=30, le=3600)
    live_expiry_seconds: int = Field(default=300, ge=30, le=3600)
    cooldown_seconds: int = Field(default=60, ge=0, le=600)
    confidence_threshold: float = Field(default=.9, ge=.8, le=1)
    max_reply_chars: int = Field(default=1200, ge=200, le=2400)
    # @-mentioning the account counts as an explicit request, like /ask.
    mention_trigger: bool = True
    mention_names: list[str] = Field(default_factory=list, max_length=10)
    reply_style: Literal['quote', 'plain'] = 'quote'
    # Tried in order when the stage's own model fails to connect or errors.
    fallback_profile_ids: list[str] = Field(default_factory=list, max_length=5)
    fast_stages: bool = True
    stale_check: bool = True
    vision_profile_id: str | None = Field(default=None, min_length=1, max_length=64)
    glossary: str = Field(default='', max_length=4000)
    source_style: Literal['links', 'one_link', 'names'] = 'one_link'
    faq_enabled: bool = True
    # How much evidence an answer needs: strict = official/wiki text only and an independent review;
    # balanced = guide sites and search excerpts count, review only when citations are not verbatim;
    # loose = answer whenever sources roughly support it.
    strictness: Literal['strict', 'balanced', 'loose'] = 'balanced'
    faq_ttl_days: int = Field(default=7, ge=1, le=90)

    @field_validator('mention_names')
    @classmethod
    def valid_mention_names(cls, values):
        values = list(dict.fromkeys(value.strip() for value in values if value.strip()))
        if any(len(value) > 40 for value in values):
            raise ValueError('群内称呼每个最多 40 字')
        return values

    @field_validator('fallback_profile_ids')
    @classmethod
    def unique_fallbacks(cls, values):
        if len(values) != len(set(values)) or any(not value or len(value) > 64 for value in values):
            raise ValueError('备用模型必须唯一且有效')
        return values

    @field_validator('local_question_pattern')
    @classmethod
    def valid_pattern(cls, value):
        try:
            re.compile(value)
        except re.error:
            raise ValueError('疑问信号正则表达式无效') from None
        return value

    @field_validator('decision_profile_ids')
    @classmethod
    def unique_decision_profiles(cls, values):
        if len(values) != len(set(values)) or any(not value or len(value) > 64 for value in values):
            raise ValueError('决策配置必须唯一且有效')
        return values


class ReplyRuleEdit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account: str = Field(min_length=1, max_length=240)
    room: str = Field(min_length=1, max_length=240, pattern=r'@chatroom$')
    revision: int = Field(ge=0)
    config: ReplyConfig


class ReplayRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    account: str = Field(min_length=1, max_length=240)
    room: str = Field(min_length=1, max_length=240, pattern=r'@chatroom$')
    message_ids: list[int] = Field(min_length=1, max_length=250)
    search_modes: list[SearchMode] = Field(default_factory=lambda: ['hybrid'], min_length=1, max_length=10)
    trigger_modes: list[Literal['original', 'ask']] = Field(default_factory=lambda: ['original'], min_length=1, max_length=2)
    request_id: str = Field(pattern=r'^[a-f0-9]{32}$')


class QueryPlan(BaseModel):
    need_answer: bool
    enough_context: bool
    question: str = Field(default='', max_length=500)
    reason: str = Field(default='', max_length=500)
    missing_context: str = Field(default='', max_length=300)
    domain: Literal['identityv', 'other_game', 'general', 'group_local'] = 'general'
    intent: Literal['fact', 'howto', 'opinion', 'coordination', 'rhetorical', 'chitchat', 'unknown'] = 'unknown'
    time_sensitive: bool = False
    queries: list[str] = Field(default_factory=list, max_length=10)
    wiki_titles: list[str] = Field(default_factory=list, max_length=10)


class Citation(BaseModel):
    source_id: str = Field(pattern=r'^S\d{1,2}$')
    quote: str = Field(min_length=10, max_length=500)


class AnswerDraft(BaseModel):
    confident: bool
    confidence: float = Field(ge=0, le=1)
    answer: str = Field(default='', max_length=5000)
    citations: list[Citation] = Field(default_factory=list, max_length=5)
    missing: str = Field(default='', max_length=300)


class AnswerReview(BaseModel):
    supported: bool
    answers_question: bool
    version_consistent: bool
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(max_length=500)


class StaleCheck(BaseModel):
    already_answered: bool
    still_relevant: bool
    reason: str = Field(default='', max_length=300)
