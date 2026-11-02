from types import SimpleNamespace

import pytest
from django.test import override_settings

from project.apps.translate.models import FormatConfig
from project.apps.translate.serializers import FormatConfigSerializer
from project.apps.translate.services.parse_api_key import (
    ParseLlmConfig,
    resolve_parse_llm,
)
from project.apps.translate.services.similarity import (
    BertSimilarity,
    FallbackSimilarity,
    build_similarity_model,
)


class TestResolveParseLlm:
    @override_settings(
        ASSISTANT_DEEPSEEK_API_KEY='platform-sk-test',
        ASSISTANT_BASE_URL='https://api.deepseek.com/',
        ASSISTANT_MODEL='deepseek-v4-flash',
    )
    def test_platform_engine_uses_platform_key(self):
        config = SimpleNamespace(
            ai_model='Platform',
            assistant_api_key='user-sk-test',
            assistant_base_url='http://127.0.0.1:11434/v1',
            assistant_model='llama3.1',
        )

        llm = resolve_parse_llm(config)

        assert llm.api_key == 'platform-sk-test'
        assert llm.base_url == 'https://api.deepseek.com'
        assert llm.model == 'deepseek-v4-flash'
        assert llm.source == 'platform'

    @override_settings(ASSISTANT_DEEPSEEK_API_KEY='')
    def test_platform_engine_without_platform_key_returns_none(self):
        config = SimpleNamespace(ai_model='Platform')

        assert resolve_parse_llm(config) is None

    @override_settings(ASSISTANT_DEEPSEEK_API_KEY='platform-sk-test')
    def test_deepseek_engine_uses_user_key(self):
        config = SimpleNamespace(
            ai_model='DeepSeek',
            assistant_api_key=' user-sk-test ',
            assistant_base_url='https://custom.example.com/',
            assistant_model='custom-model',
        )

        llm = resolve_parse_llm(config)

        assert llm.api_key == 'user-sk-test'
        assert llm.base_url == 'https://custom.example.com'
        assert llm.model == 'custom-model'
        assert llm.source == 'user'

    @override_settings(ASSISTANT_DEEPSEEK_API_KEY='platform-sk-test')
    def test_deepseek_engine_without_user_key_falls_back_to_platform(self):
        config = SimpleNamespace(
            ai_model='DeepSeek',
            assistant_api_key='',
            assistant_base_url='',
            assistant_model='',
        )

        llm = resolve_parse_llm(config)

        assert llm.api_key == 'platform-sk-test'
        assert llm.source == 'platform'

    @pytest.mark.parametrize('engine', ['BERT', 'None', 'spaCy', ''])
    def test_local_engines_do_not_resolve_llm(self, engine):
        config = SimpleNamespace(ai_model=engine)

        assert resolve_parse_llm(config) is None

    @override_settings(ASSISTANT_DEEPSEEK_API_KEY='platform-sk-test')
    def test_missing_engine_defaults_to_bert(self):
        config = SimpleNamespace(ai_model='')

        assert resolve_parse_llm(config) is None


class TestApplyParseEngine:
    def test_missing_engine_defaults_to_bert_ignoring_user_config(self):
        from project.apps.translate.views.views import _apply_parse_engine

        config = SimpleNamespace(ai_model='DeepSeek')

        assert _apply_parse_engine(config).ai_model == 'BERT'

    def test_platform_parameter_overrides_user_config(self):
        from project.apps.translate.views.views import _apply_parse_engine

        config = SimpleNamespace(ai_model='DeepSeek')

        assert _apply_parse_engine(config, 'Platform').ai_model == 'Platform'

    def test_bert_parameter_keeps_bert(self):
        from project.apps.translate.views.views import _apply_parse_engine

        config = SimpleNamespace(ai_model='DeepSeek')

        assert _apply_parse_engine(config, 'BERT').ai_model == 'BERT'


class _FailingPrimary:
    def __init__(self):
        self.calls = 0

    def calculate_similarity(self, text, candidates):
        self.calls += 1
        raise ValueError('primary unavailable')


class _RecordingFallback:
    def __init__(self):
        self.calls = 0

    def calculate_similarity(self, text, candidates):
        self.calls += 1
        return {'best_match': candidates[0], 'scores': {candidates[0]: 1.0}}


class TestFallbackSimilarity:
    def test_primary_failure_switches_to_fallback_for_remaining_calls(self):
        primary = _FailingPrimary()
        fallback = _RecordingFallback()
        model = FallbackSimilarity(primary, fallback)

        first = model.calculate_similarity('first', ['a', 'b'])
        second = model.calculate_similarity('second', ['c', 'd'])

        assert first['best_match'] == 'a'
        assert second['best_match'] == 'c'
        assert primary.calls == 1
        assert fallback.calls == 2

    def test_build_platform_model_without_llm_falls_back_to_bert(self):
        assert isinstance(build_similarity_model('Platform', None), BertSimilarity)

    def test_build_empty_engine_falls_back_to_bert(self):
        assert isinstance(build_similarity_model('', None), BertSimilarity)

    def test_build_none_engine_returns_none(self):
        assert build_similarity_model('None', None) is None

    def test_build_platform_model_with_llm_wraps_fallback(self):
        llm = ParseLlmConfig(
            api_key='platform-sk-test',
            base_url='https://api.deepseek.com',
            model='deepseek-v4-flash',
            source='platform',
        )

        model = build_similarity_model('Platform', llm)

        assert isinstance(model, FallbackSimilarity)
        assert isinstance(model.fallback, BertSimilarity)


@pytest.mark.django_db
class TestFormatConfigDeepSeekKey:
    def test_default_engine_is_bert(self, user):
        assert FormatConfig.get_user_config(user).ai_model == 'BERT'

    def test_deepseek_without_copilot_key_is_rejected(self, user):
        config = FormatConfig.get_user_config(user)
        serializer = FormatConfigSerializer(
            config,
            data={'ai_model': 'DeepSeek', 'assistant_api_key': ''},
            partial=True,
        )
        assert serializer.is_valid() is False
        assert 'ai_model' in serializer.errors

    def test_platform_config_is_normalized_to_bert(self, user):
        config = FormatConfig.get_user_config(user)
        serializer = FormatConfigSerializer(
            config,
            data={'ai_model': 'Platform', 'assistant_api_key': ''},
            partial=True,
        )
        assert serializer.is_valid(), serializer.errors
        serializer.save()
        config.refresh_from_db()
        assert config.ai_model == 'BERT'

    def test_deepseek_with_copilot_key_syncs_legacy_field(self, user):
        config = FormatConfig.get_user_config(user)
        serializer = FormatConfigSerializer(
            config,
            data={'ai_model': 'DeepSeek', 'assistant_api_key': 'user-sk-test'},
            partial=True,
        )
        assert serializer.is_valid(), serializer.errors
        serializer.save()
        config.refresh_from_db()
        assert config.assistant_api_key == 'user-sk-test'
        assert config.deepseek_apikey == 'user-sk-test'
