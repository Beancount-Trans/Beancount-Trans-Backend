from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.test import override_settings
from django.utils import timezone

from project.apps.usage.models import Dimension, OwnerType, UsageEvent
from project.apps.usage.services import (
    aggregate,
    record_bql,
    record_event,
    record_llm_tokens,
    record_parse,
    record_snapshot,
)


@pytest.fixture
def user(db):
    return User.objects.create_user(username='usage_owner', password='pass12345')


@pytest.mark.django_db
class TestRecordEvent:
    def test_creates_event_with_default_unit(self, user):
        event = record_event(
            event_key='manual:1',
            dimension=Dimension.PARSE,
            quantity=1,
            source_type='parse_job',
            owner_id=user.id,
        )
        assert event is not None
        assert event.unit == 'count'
        assert event.status == 'ok'

    def test_duplicate_event_key_is_idempotent(self, user):
        first = record_event(
            event_key='parse:task-1',
            dimension=Dimension.PARSE,
            quantity=1,
            source_type='parse_job',
            owner_id=user.id,
        )
        second = record_event(
            event_key='parse:task-1',
            dimension=Dimension.PARSE,
            quantity=1,
            source_type='parse_job',
            owner_id=user.id,
        )
        assert first is not None
        assert second is None
        assert UsageEvent.objects.filter(event_key='parse:task-1').count() == 1

    @override_settings(USAGE_METERING_ENABLED=False)
    def test_disabled_metering_writes_nothing(self, user):
        event = record_event(
            event_key='manual:2',
            dimension=Dimension.PARSE,
            quantity=1,
            source_type='parse_job',
            owner_id=user.id,
        )
        assert event is None
        assert UsageEvent.objects.count() == 0


@pytest.mark.django_db
class TestRecordHelpers:
    def test_record_parse_keeps_entry_count(self, user):
        record_parse(task_id='task-9', user_id=user.id, status='ok', entries=12)
        event = UsageEvent.objects.get(dimension=Dimension.PARSE)
        assert event.event_key == 'parse:task-9'
        assert event.meta['entries'] == 12
        assert event.owner_type == OwnerType.USER

    def test_record_parse_failed_status(self, user):
        record_parse(task_id='task-10', user_id=user.id, status='failed', error='解密失败')
        event = UsageEvent.objects.get(dimension=Dimension.PARSE)
        assert event.status == 'failed'
        assert event.meta['error'] == '解密失败'

    def test_record_bql_counts_single_attempt(self, user):
        record_bql(user_id=user.id, ok=False, error='仅允许 SELECT 查询')
        event = UsageEvent.objects.get(dimension=Dimension.BQL)
        assert event.quantity == 1
        assert event.status == 'failed'

    def test_record_llm_tokens_sums_prompt_and_completion(self, user):
        record_llm_tokens(
            user_id=user.id,
            prompt_tokens=100,
            completion_tokens=50,
            model='deepseek-v4-flash',
            byok=False,
        )
        event = UsageEvent.objects.get(dimension=Dimension.LLM_TOKENS)
        assert event.quantity == 150
        assert event.unit == 'tokens'
        assert event.meta['prompt'] == 100
        assert event.meta['completion'] == 50

    def test_record_llm_tokens_without_usage_is_flagged(self, user):
        record_llm_tokens(
            user_id=user.id,
            model='unknown',
            byok=False,
            status='failed',
            reason='no_usage',
        )
        event = UsageEvent.objects.get(dimension=Dimension.LLM_TOKENS)
        assert event.quantity == 0
        assert event.status == 'failed'
        assert event.meta['reason'] == 'no_usage'


@pytest.mark.django_db
class TestAggregate:
    def test_sums_events_in_range(self, user):
        now = timezone.now()
        record_event(
            event_key='agg:1', dimension=Dimension.LLM_TOKENS, quantity=100,
            source_type='assistant_turn', owner_id=user.id, occurred_at=now,
        )
        record_event(
            event_key='agg:2', dimension=Dimension.LLM_TOKENS, quantity=40,
            source_type='assistant_turn', owner_id=user.id, occurred_at=now,
        )
        result = aggregate(
            owner_id=user.id,
            dimension=Dimension.LLM_TOKENS,
            start=now - timedelta(hours=1),
            end=now + timedelta(hours=1),
        )
        assert result['quantity'] == 140
        assert result['events'] == 2
        assert result['source'] == 'event'

    def test_excludes_out_of_range_events(self, user):
        old = timezone.now() - timedelta(days=40)
        record_event(
            event_key='agg:old', dimension=Dimension.LLM_TOKENS, quantity=999,
            source_type='assistant_turn', owner_id=user.id, occurred_at=old,
        )
        now = timezone.now()
        result = aggregate(
            owner_id=user.id,
            dimension=Dimension.LLM_TOKENS,
            start=now - timedelta(days=1),
            end=now,
        )
        assert result['quantity'] == 0

    def test_storage_dimension_reads_latest_snapshot(self, user):
        today = timezone.localdate()
        record_snapshot(
            dimension=Dimension.STORAGE_FILE, amount=1024,
            owner_id=user.id, period=today - timedelta(days=1),
        )
        record_snapshot(
            dimension=Dimension.STORAGE_FILE, amount=4096,
            owner_id=user.id, period=today,
        )
        result = aggregate(
            owner_id=user.id,
            dimension=Dimension.STORAGE_FILE,
            start=timezone.now() - timedelta(days=2),
            end=timezone.now(),
        )
        assert result['quantity'] == 4096
        assert result['source'] == 'snapshot'
