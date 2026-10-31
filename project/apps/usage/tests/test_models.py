import pytest
from django.contrib.auth.models import User
from django.db import IntegrityError
from django.utils import timezone

from project.apps.usage.models import Dimension, OwnerType, UsageEvent, UsageSnapshot


@pytest.fixture
def user(db):
    return User.objects.create_user(username='usage_user', password='pass12345')


@pytest.mark.django_db
class TestUsageEvent:
    def test_create_defaults(self, user):
        event = UsageEvent.objects.create(
            event_key='manual:1',
            owner_type=OwnerType.USER,
            owner_id=user.id,
            dimension=Dimension.PARSE,
            quantity=1,
            unit='count',
            source_type='parse_job',
            occurred_at=timezone.now(),
        )
        assert event.status == 'ok'
        assert event.meta == {}
        assert event.created is not None

    def test_event_key_is_unique(self, user):
        UsageEvent.objects.create(
            event_key='dup:1',
            owner_id=user.id,
            dimension=Dimension.PARSE,
            quantity=1,
            unit='count',
            source_type='parse_job',
            occurred_at=timezone.now(),
        )
        with pytest.raises(IntegrityError):
            UsageEvent.objects.create(
                event_key='dup:1',
                owner_id=user.id,
                dimension=Dimension.PARSE,
                quantity=1,
                unit='count',
                source_type='parse_job',
                occurred_at=timezone.now(),
            )

    def test_platform_owner_has_no_owner_id(self):
        event = UsageEvent.objects.create(
            event_key='anon:1',
            owner_type=OwnerType.PLATFORM,
            owner_id=None,
            dimension=Dimension.PARSE,
            quantity=1,
            unit='count',
            source_type='parse_job',
            occurred_at=timezone.now(),
        )
        assert event.owner_id is None


@pytest.mark.django_db
class TestUsageSnapshot:
    def test_unique_per_owner_dimension_period(self, user):
        today = timezone.localdate()
        UsageSnapshot.objects.create(
            owner_id=user.id,
            dimension=Dimension.STORAGE_FILE,
            amount=100,
            unit='B',
            period=today,
            measured_at=timezone.now(),
        )
        with pytest.raises(IntegrityError):
            UsageSnapshot.objects.create(
                owner_id=user.id,
                dimension=Dimension.STORAGE_FILE,
                amount=200,
                unit='B',
                period=today,
                measured_at=timezone.now(),
            )
