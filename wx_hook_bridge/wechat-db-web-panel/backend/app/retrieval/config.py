from pydantic import BaseModel, ConfigDict, Field, model_validator

# Options of the removed BWIKI API source; saved configs may still contain them.
LEGACY_KEYS = {'wiki_titles', 'wiki_search_count', 'max_wiki_documents', 'wiki_cache_seconds'}


class RetrievalConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    search_queries: int = Field(default=2, ge=1, le=10)
    deepseek_count: int = Field(default=6, ge=1, le=50)
    max_documents: int = Field(default=5, ge=1, le=20)
    evidence_limit: int = Field(default=10, ge=1, le=40)
    chunk_chars: int = Field(default=1100, ge=300, le=10000)
    chunks_per_document: int = Field(default=3, ge=1, le=10)
    search_cache_seconds: int = Field(default=900, ge=0, le=86400)
    time_budget_seconds: int = Field(default=40, ge=5, le=300)
    page_cache_seconds: int = Field(default=1800, ge=0, le=86400)
    allow_unverified_sources: bool = False

    @model_validator(mode='before')
    @classmethod
    def drop_legacy(cls, value):
        return {key: item for key, item in value.items() if key not in LEGACY_KEYS} if isinstance(value, dict) else value


SEARCH_PROMPT = '检索以下问题的直接资料来源，优先官方原文和专业资料站。只提供来源及简短摘录。问题所处时间为 {at}，排除其后发布的资料。\n问题：{question}'
