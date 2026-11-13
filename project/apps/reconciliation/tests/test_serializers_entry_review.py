"""
ScheduledTaskListSerializer 统一条目审核（entry_review）相关测试

统一改造后：每个用户全局唯一一个 entry_review 待办（content_type=User，
object_id=user.id），其待审核条目集合由 EntryReviewQueueService 管理。
序列化器对 entry_review 待办返回：
- entry_count：队列中有效引用数量
- review_expires_at：队列中最早的审核截止时间
其它类型待办 entry_count 恒为 None。
"""
import pytest
import time
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType

from project.apps.reconciliation.models import ScheduledTask
from project.apps.reconciliation.serializers import ScheduledTaskListSerializer
from project.apps.translate.services.parse_review_service import ParseReviewService
from project.apps.translate.services.entry_review_queue_service import (
    EntryReviewQueueService,
)
from project.apps.file_manager.models import File, Directory
from project.apps.translate.models import ParseFile

User = get_user_model()


@pytest.fixture(autouse=True)
def _clear_cache_between_tests():
    """每个测试前后清空默认缓存，避免 LocMem 队列跨测试污染"""
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


def _create_parse_file(user, name='test_file.csv', dir_name='test_dir'):
    """创建归属 user 的 ParseFile"""
    directory = Directory.objects.create(name=dir_name, owner=user)
    file_obj = File.objects.create(
        name=name,
        directory=directory,
        storage_name=f'storage_{name}',
        size=1024,
        owner=user,
        content_type='text/csv',
    )
    return ParseFile.objects.create(file=file_obj, status='pending_review')


def _save_parse_result(file_id, expires_at, uuid='entry-1'):
    """向缓存写入单条带 review_expires_at 的解析结果"""
    ParseReviewService.save_parse_result(file_id, {
        'file_id': file_id,
        'formatted_data': [
            {
                'uuid': uuid,
                'formatted': '2025-01-20 * "Test" "Transaction 1"\n    Expenses:Test  100.00 CNY\n    Assets:Test  -100.00 CNY\n',
                'edited_formatted': '2025-01-20 * "Test" "Transaction 1"\n    Expenses:Test  100.00 CNY\n    Assets:Test  -100.00 CNY\n',
                'selected_expense_key': 'Expenses:Test',
                'expense_candidates_with_score': [
                    {'key': 'Expenses:Test', 'score': 0.9}
                ],
                'original_row': {
                    'date': '2025-01-20',
                    'description': 'Test Transaction 1',
                    'amount': 100.00,
                },
            }
        ],
        'created_at': time.time(),
        'review_expires_at': expires_at,
    })


@pytest.fixture
def parse_file_for_serializer(user):
    """创建 ParseFile 用于序列化器测试"""
    return _create_parse_file(user)


@pytest.fixture
def entry_review_task_for_serializer(user):
    """创建用户级统一条目审核待办（content_type=User，object_id=user.id）"""
    content_type = ContentType.objects.get_for_model(User)
    return ScheduledTask.objects.create(
        task_type='entry_review',
        content_type=content_type,
        object_id=user.id,
        status='pending',
    )


@pytest.mark.django_db
class TestScheduledTaskListSerializerEntryReview:
    """ScheduledTaskListSerializer 统一条目审核相关测试"""

    def test_entry_count_and_expires_at_for_entry_review(
        self,
        user,
        entry_review_task_for_serializer,
        parse_file_for_serializer,
    ):
        """entry_review 待办返回队列引用数与最早到期时间"""
        expires_at = time.time() + 86400
        _save_parse_result(parse_file_for_serializer.file_id, expires_at)
        EntryReviewQueueService.enqueue(user.id, [
            {'file_id': parse_file_for_serializer.file_id, 'uuid': 'entry-1'},
        ])

        data = ScheduledTaskListSerializer(entry_review_task_for_serializer).data

        assert data['task_type'] == 'entry_review'
        assert data['entry_count'] == 1
        assert data['review_expires_at'] == pytest.approx(expires_at)

    def test_entry_count_reflects_number_of_queue_refs(
        self,
        user,
        entry_review_task_for_serializer,
        parse_file_for_serializer,
    ):
        """entry_count 等于队列中有效引用数量"""
        _save_parse_result(parse_file_for_serializer.file_id, time.time() + 86400)
        EntryReviewQueueService.enqueue(user.id, [
            {'file_id': parse_file_for_serializer.file_id, 'uuid': 'entry-1'},
            {'file_id': parse_file_for_serializer.file_id, 'uuid': 'entry-2'},
        ])

        data = ScheduledTaskListSerializer(entry_review_task_for_serializer).data

        assert data['entry_count'] == 2

    def test_attention_entry_count_excludes_auto_passed_entries(
        self,
        user,
        entry_review_task_for_serializer,
        parse_file_for_serializer,
    ):
        """attention_entry_count 只统计兜底账户等需要人工审核的条目"""
        formatted = (
            '2025-01-20 * "Test" "Transaction"\n'
            '    Expenses:Test  100.00 CNY\n'
            '    Assets:Test  -100.00 CNY\n'
        )
        fallback = (
            '2025-01-20 * "Test" "Fallback"\n'
            '    Expenses:Other  100.00 CNY\n'
            '    Assets:Test  -100.00 CNY\n'
        )
        ParseReviewService.save_parse_result(parse_file_for_serializer.file_id, {
            'file_id': parse_file_for_serializer.file_id,
            'formatted_data': [
                {'uuid': 'entry-1', 'formatted': formatted, 'edited_formatted': formatted},
                {'uuid': 'entry-2', 'formatted': fallback, 'edited_formatted': fallback},
            ],
            'created_at': time.time(),
            'review_expires_at': time.time() + 86400,
        })
        EntryReviewQueueService.enqueue(user.id, [
            {'file_id': parse_file_for_serializer.file_id, 'uuid': 'entry-1'},
            {'file_id': parse_file_for_serializer.file_id, 'uuid': 'entry-2'},
        ])

        data = ScheduledTaskListSerializer(entry_review_task_for_serializer).data

        assert data['entry_count'] == 2
        assert data['attention_entry_count'] == 1

    def test_attention_entry_count_zero_when_queue_empty(
        self,
        entry_review_task_for_serializer,
    ):
        """队列为空时 attention_entry_count 为 0"""
        data = ScheduledTaskListSerializer(entry_review_task_for_serializer).data

        assert data['entry_count'] == 0
        assert data['attention_entry_count'] == 0

    def test_review_expires_at_is_earliest_in_queue(
        self,
        user,
        entry_review_task_for_serializer,
    ):
        """review_expires_at 取队列中所有条目的最早到期时间"""
        first_file = _create_parse_file(user, name='first.csv', dir_name='dir_first')
        second_file = _create_parse_file(user, name='second.csv', dir_name='dir_second')

        later = time.time() + 2 * 86400
        earlier = time.time() + 3600
        _save_parse_result(first_file.file_id, later)
        _save_parse_result(second_file.file_id, earlier)
        EntryReviewQueueService.enqueue(user.id, [
            {'file_id': first_file.file_id, 'uuid': 'entry-1'},
            {'file_id': second_file.file_id, 'uuid': 'entry-1'},
        ])

        data = ScheduledTaskListSerializer(entry_review_task_for_serializer).data

        assert data['entry_count'] == 2
        assert data['review_expires_at'] == pytest.approx(earlier)

    def test_entry_review_empty_queue_returns_zero_and_none(
        self,
        user,
        entry_review_task_for_serializer,
    ):
        """entry_review 待办队列为空时 entry_count=0、review_expires_at=None"""
        data = ScheduledTaskListSerializer(entry_review_task_for_serializer).data

        assert data['entry_count'] == 0
        assert data['review_expires_at'] is None

    def test_entry_count_none_for_reconciliation_task(
        self,
        user,
        scheduled_task_pending,
    ):
        """非 entry_review（对账待办）entry_count 为 None"""
        data = ScheduledTaskListSerializer(scheduled_task_pending).data

        assert data['entry_count'] is None
        assert data['review_expires_at'] is None

    def test_account_name_and_account_type_for_reconciliation(
        self,
        user,
        scheduled_task_pending,
        account,
    ):
        """对账待办仍返回 account_name / account_type"""
        data = ScheduledTaskListSerializer(scheduled_task_pending).data

        assert data['account_name'] == account.account
        assert data['account_type'] == account.get_account_type()
