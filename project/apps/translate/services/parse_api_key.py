"""账单解析的 LLM 供给：平台引擎固定用平台 Key，自有引擎优先用户 Key。"""
from dataclasses import dataclass
from typing import Optional

from django.conf import settings

DEFAULT_ASSISTANT_BASE_URL = 'https://api.deepseek.com'
DEFAULT_ASSISTANT_MODEL = 'deepseek-v4-flash'


@dataclass(frozen=True)
class ParseLlmConfig:
    api_key: str
    base_url: str
    model: str
    source: str  # 'platform' | 'user'


def _strip(value: Optional[str]) -> str:
    return (value or '').strip()


def _platform_llm() -> Optional[ParseLlmConfig]:
    api_key = _strip(getattr(settings, 'ASSISTANT_DEEPSEEK_API_KEY', ''))
    if not api_key:
        return None
    base_url = _strip(getattr(settings, 'ASSISTANT_BASE_URL', '')) or DEFAULT_ASSISTANT_BASE_URL
    model = _strip(getattr(settings, 'ASSISTANT_MODEL', '')) or DEFAULT_ASSISTANT_MODEL
    return ParseLlmConfig(
        api_key=api_key,
        base_url=base_url.rstrip('/'),
        model=model,
        source='platform',
    )


def resolve_parse_llm(config) -> Optional[ParseLlmConfig]:
    """按解析引擎返回可用的 LLM 配置；BERT / 单规则返回 None。"""
    engine = _strip(getattr(config, 'ai_model', None)) or 'BERT'
    if engine == 'Platform':
        return _platform_llm()
    if engine == 'DeepSeek':
        user_key = _strip(getattr(config, 'assistant_api_key', None))
        if not user_key:
            return _platform_llm()
        platform = _platform_llm()
        base_url = (
            _strip(getattr(config, 'assistant_base_url', None))
            or (platform.base_url if platform else DEFAULT_ASSISTANT_BASE_URL)
        )
        model = (
            _strip(getattr(config, 'assistant_model', None))
            or (platform.model if platform else DEFAULT_ASSISTANT_MODEL)
        )
        return ParseLlmConfig(
            api_key=user_key,
            base_url=base_url.rstrip('/'),
            model=model,
            source='user',
        )
    return None
