"""
上传账单直接解析并生成解析审核待办接口测试

POST /api/translate/upload-parse

- 不创建 File / ParseFile（文件不入文件管理，也不生成 .bean）；
- 条目写入 Copilot 用户级暂存区并入队，激活 entry_review 待办；
- 落库策略固定为审核模式，不随 parsing_mode_preference 变化；
- 确认写入时追加到 trans/collect.bean。
"""
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework import status
from rest_framework.test import APIClient

from project.apps.file_manager.models import File
from project.apps.reconciliation.models import ScheduledTask
from project.apps.translate.models import FormatConfig, ParseFile
from project.apps.translate.services.copilot_bookkeeping_service import (
    CopilotBookkeepingService,
)
from project.apps.translate.services.entry_review_queue_service import (
    EntryReviewQueueService,
)
from project.apps.translate.services.parse_review_service import ParseReviewService
from project.utils.file import BeanFileManager

CACHE_KEY = 'cache-key-upload-1'
ENTRY_TEXT = (
    '2025-01-20 * "测试商户" "测试交易"\n'
    '    Expenses:Test  100.00 CNY\n'
    '    Assets:Test  -100.00 CNY\n'
)
ORIGINAL_ROW = {
    'date': '2025-01-20',
    'transaction_time': '2025-01-20 10:00:00',
    'uuid': 'order-1',
    'amount': 100.0,
    'transaction_type': '支出',
    'counterparty': '测试商户',
    'commodity': '测试商品',
}


def _fake_context():
    """构造 AnalyzeService.analyze_single_file 的审核模式输出。"""
    return {
        'formatted_data': [
            {
                'id': CACHE_KEY,
                'formatted': ENTRY_TEXT,
                'selected_expense_key': 'Expenses:Test',
                'expense_candidates_with_score': [
                    {'key': 'Expenses:Test', 'score': 0.9}
                ],
            }
        ],
        'parsed_data': [{'cache_key': CACHE_KEY, 'uuid': 'order-1'}],
    }


@pytest.fixture
def temp_assets(tmp_path, monkeypatch):
    """把 ASSETS_BASE_PATH 指向临时目录，隔离 collect.bean 写入。"""
    from django.conf import settings

    base = tmp_path / 'Assets'
    base.mkdir()
    monkeypatch.setattr(settings, 'ASSETS_BASE_PATH', str(base))
    return base


@pytest.fixture(autouse=True)
def _no_dedup_llm(monkeypatch):
    """关闭去重 LLM 判定，保证测试确定性。"""
    from project.apps.translate.services.entry_dedup_service import EntryDedupService

    monkeypatch.setattr(EntryDedupService, '_llm_judge_pairs', lambda user, pairs: None)


def _seed_entry_cache():
    """模拟 CacheStep 写入的逐条解析缓存（original_row / tag_details）。"""
    cache.set(
        CACHE_KEY,
        {
            'parsed_entry': {'tag_details': [], 'uuid': 'order-1'},
            'original_row': dict(ORIGINAL_ROW),
        },
        timeout=3600,
    )


@pytest.mark.django_db
class TestUploadParseReviewView:
    """POST /api/translate/upload-parse"""

    def setup_method(self):
        self.client = APIClient()

    def _upload(self, name='账单.csv', content=b'bill,content', context=None):
        _seed_entry_cache()
        with patch(
            'project.apps.translate.views.views.AnalyzeService'
        ) as mock_analyze_service:
            service = MagicMock()
            service.analyze_single_file.return_value = (
                _fake_context() if context is None else context
            )
            mock_analyze_service.return_value = service
            return self.client.post(
                '/api/translate/upload-parse',
                {'trans': SimpleUploadedFile(name, content, content_type='text/csv')},
                format='multipart',
            )

    def test_upload_creates_review_todo_without_file(self, user):
        """上传解析不落文件管理，条目入暂存区并激活审核待办"""
        self.client.force_authenticate(user=user)

        response = self._upload()

        assert response.status_code == status.HTTP_200_OK
        assert response.data['status'] == 'success'
        assert response.data['file_name'] == '账单.csv'
        assert response.data['entry_count'] == 1
        assert response.data['duplicate_count'] == 0
        assert response.data['pending_total'] == 1

        # 未创建任何文件管理 / 解析文件记录
        assert File.objects.count() == 0
        assert ParseFile.objects.count() == 0

        # 条目写入 Copilot 暂存区，并带上上传账单文件名
        staging_key = CopilotBookkeepingService.staging_key(user.id)
        staged = EntryReviewQueueService.list_entries(user.id)
        assert len(staged) == 1
        assert staged[0]['uuid'] == CACHE_KEY
        assert staged[0]['file_name'] == '账单.csv'
        assert staged[0]['source'] == 'copilot'
        assert staged[0]['file_id'] is None
        assert ParseReviewService.get_parse_result(staging_key) is not None

        # 入队：copilot 来源、无 file_id
        refs = EntryReviewQueueService.list_refs(user.id)
        assert refs == [{'source': 'copilot', 'file_id': None, 'uuid': CACHE_KEY}]

        # 待办被激活
        task = ScheduledTask.objects.get(
            task_type='entry_review', object_id=user.id
        )
        assert task.status == 'pending'
        assert response.data['entry_review_task_id'] == task.id

    def test_upload_without_file_rejected(self, user):
        """缺少文件时返回 400"""
        self.client.force_authenticate(user=user)

        response = self.client.post('/api/translate/upload-parse', {}, format='multipart')

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data['error'] == 'No file uploaded'

    def test_upload_without_valid_entries_rejected(self, user):
        """过滤后无有效交易记录时返回 400，不产生待办"""
        self.client.force_authenticate(user=user)

        response = self._upload(context={'formatted_data': []})

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data['error'] == '未解析到有效条目'
        assert EntryReviewQueueService.is_empty(user.id) is True
        assert CopilotBookkeepingService.has_staging(user.id) is False

    def test_duplicate_upload_skipped(self, user):
        """重复上传同一账单：条目被去重，不重复入队"""
        self.client.force_authenticate(user=user)

        first = self._upload()
        assert first.data['entry_count'] == 1

        second = self._upload()

        assert second.status_code == status.HTTP_200_OK
        assert second.data['entry_count'] == 0
        assert second.data['duplicate_count'] == 1
        assert second.data['pending_total'] == 1
        assert len(EntryReviewQueueService.list_refs(user.id)) == 1
        assert len(EntryReviewQueueService.list_entries(user.id)) == 1

    def test_confirm_appends_to_collect_bean(
        self, user, temp_assets
    ):
        """确认写入后条目追加到 trans/collect.bean"""
        self.client.force_authenticate(user=user)
        self._upload()

        collect_path = Path(BeanFileManager.get_collect_bean_path(user))
        header = collect_path.read_text(encoding='utf-8') if collect_path.exists() else ''

        response = self.client.post('/api/translate/entry-review/confirm')

        assert response.status_code == status.HTTP_200_OK
        assert response.data['files'] == [
            {'source': 'copilot', 'entry_count': 1, 'bean': 'trans/collect.bean'}
        ]

        text = collect_path.read_text(encoding='utf-8')
        assert text.startswith(header)
        assert 'Expenses:Test  100.00 CNY' in text

        assert CopilotBookkeepingService.has_staging(user.id) is False
        assert EntryReviewQueueService.is_empty(user.id) is True
        task = ScheduledTask.objects.get(
            task_type='entry_review', object_id=user.id
        )
        assert task.status == 'completed'

    def test_always_review_mode_ignores_direct_write_preference(self, user):
        """移动端上传解析固定走审核：即使用户开启直接写入模式也不直接入账"""
        self.client.force_authenticate(user=user)
        config = FormatConfig.get_user_config(user)
        config.parsing_mode_preference = 'direct_write'
        config.save(update_fields=['parsing_mode_preference'])

        response = self._upload()

        assert response.status_code == status.HTTP_200_OK
        assert response.data['entry_count'] == 1
        assert 'bean' not in response.data

        # 仍进审核队列并激活待办，且未写入 collect.bean
        refs = EntryReviewQueueService.list_refs(user.id)
        assert refs == [{'source': 'copilot', 'file_id': None, 'uuid': CACHE_KEY}]
        task = ScheduledTask.objects.get(
            task_type='entry_review', object_id=user.id
        )
        assert task.status == 'pending'
        assert response.data['entry_review_task_id'] == task.id
