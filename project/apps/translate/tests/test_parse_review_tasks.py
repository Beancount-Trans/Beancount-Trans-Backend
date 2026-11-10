"""
条目审核任务处理逻辑测试

统一条目审核改造后：
- 审核模式不再创建/激活文件级审核待办，而是把保留条目写入用户级
  统一审核队列（EntryReviewQueueService）并激活 entry_review 待办；
- 到期自动确认任务为 auto_confirm_expired_entry_reviews。
"""
import pytest
import time
from pathlib import Path
from unittest.mock import patch, MagicMock, Mock

from django.core.files.uploadedfile import InMemoryUploadedFile
from django.contrib.contenttypes.models import ContentType

from project.apps.account.models import Account
from project.apps.translate.models import ParseFile, FormatConfig
from project.apps.translate.services.parse_review_service import ParseReviewService
from project.apps.translate.services.copilot_bookkeeping_service import (
    CopilotBookkeepingService,
)
from project.apps.translate.services.entry_review_queue_service import EntryReviewQueueService
from project.apps.reconciliation.models import ScheduledTask
from project.apps.translate.tasks import parse_single_file_task, auto_confirm_expired_entry_reviews
from project.utils.file import BeanFileManager


@pytest.mark.django_db
class TestParseSingleFileTask:
    """parse_single_file_task 任务测试"""
    
    def setup_method(self):
        """设置测试环境"""
        pass
    
    @patch('project.apps.translate.tasks.get_storage_client')
    @patch('project.apps.translate.tasks.AnalyzeService')
    @patch('project.utils.tools.get_user_config')
    def test_parse_task_review_mode(self, mock_get_config, mock_analyze_service, mock_storage_client, user, parse_file, entry_review_task_inactive):
        """测试审核模式下不写入文件，缓存数据保存，条目入队并激活审核待办"""
        # Mock 存储客户端
        mock_storage = MagicMock()
        mock_file_data = Mock()
        mock_file_data.read.return_value = b'test,file,content'
        mock_storage.download_file.return_value = mock_file_data
        mock_storage_client.return_value = mock_storage
        
        # Mock 配置 - 使用 get_or_create 避免唯一约束冲突
        from project.apps.translate.models import FormatConfig
        config, _ = FormatConfig.objects.get_or_create(
            owner=user,
            defaults={'parsing_mode_preference': 'review'}
        )
        mock_get_config.return_value = config
        
        # Mock AnalyzeService
        mock_service = MagicMock()
        mock_service.analyze_single_file.return_value = {
            'formatted_data': [
                {
                    'id': 'cache-key-1',
                    'formatted': '2025-01-20 * "Test" "Transaction"\n    Expenses:Test  100.00 CNY\n    Assets:Test  -100.00 CNY\n',
                    'selected_expense_key': 'Expenses:Test',
                    'expense_candidates_with_score': []
                }
            ],
            'parsed_data': [
                {
                    'cache_key': 'cache-key-1',
                    'uuid': 'uuid-1'
                }
            ]
        }
        mock_analyze_service.return_value = mock_service
        
        # Mock cache.get 返回 original_row
        from django.core.cache import cache
        cache.set('cache-key-1', {'original_row': {'date': '2025-01-20', 'description': 'Test'}}, timeout=3600)
        
        # 执行任务（审核模式）
        args = {
            'write': False,  # 审核模式
            'cmb_credit_ignore': True,
            'boc_debit_ignore': True,
            'password': None,
        }
        
        # 创建模拟的 Celery 任务请求
        mock_request = MagicMock()
        mock_request.id = 'test-task-id'
        
        # 手动调用任务函数（不使用 .delay）
        # 对于绑定任务，由于 CELERY_TASK_ALWAYS_EAGER=True，使用 apply() 会同步执行
        # apply() 会自动创建任务实例并设置 self.request
        result = parse_single_file_task.apply(
            args=[parse_file.file_id, user.id, args],
            task_id='test-task-id'
        )
        # 在 CELERY_TASK_ALWAYS_EAGER=True 模式下，apply() 返回的结果可以直接使用
        if hasattr(result, 'result'):
            result = result.result
        
        # 验证返回结果
        assert result['status'] == 'pending_review'
        assert result['file_id'] == parse_file.file_id
        
        # 验证 ParseFile 状态更新为 pending_review
        parse_file.refresh_from_db()
        assert parse_file.status == 'pending_review'
        
        # 验证该用户唯一的 entry_review 待办被激活为 pending
        entry_review_task_inactive.refresh_from_db()
        assert entry_review_task_inactive.status == 'pending'
        assert entry_review_task_inactive.task_type == 'entry_review'
        
        # 验证保留条目已进入用户级审核队列（uuid 使用 cache_key）
        refs = EntryReviewQueueService.list_refs(user.id)
        assert refs == [{'file_id': parse_file.file_id, 'uuid': 'cache-key-1'}]
        
        # 验证缓存数据保存
        cached_data = ParseReviewService.get_parse_result(parse_file.file_id)
        assert cached_data is not None
        assert cached_data['file_id'] == parse_file.file_id
        assert len(cached_data['formatted_data']) == 1
        assert 'review_expires_at' in cached_data
        assert cached_data['review_expires_at'] > time.time()
        
        # 验证缓存数据包含必要的字段；审核身份使用 cache_key 而非原始订单号
        entry = cached_data['formatted_data'][0]
        assert 'uuid' in entry
        assert entry['uuid'] == 'cache-key-1'
        assert 'formatted' in entry
        assert 'edited_formatted' in entry
        assert 'original_row' in entry

    @patch('project.apps.translate.tasks.get_storage_client')
    @patch('project.apps.translate.tasks.AnalyzeService')
    @patch('project.utils.tools.get_user_config')
    def test_parse_task_review_mode_keeps_installment_fields(
        self, mock_get_config, mock_analyze_service, mock_storage_client, user, parse_file
    ):
        mock_storage = MagicMock()
        mock_file_data = Mock()
        mock_file_data.read.return_value = b'test,file,content'
        mock_storage.download_file.return_value = mock_file_data
        mock_storage_client.return_value = mock_storage

        config, _ = FormatConfig.objects.get_or_create(
            owner=user,
            defaults={'parsing_mode_preference': 'review'}
        )
        mock_get_config.return_value = config

        mock_service = MagicMock()
        mock_service.analyze_single_file.return_value = {
            'formatted_data': [
                {
                    'id': 'ORDER--2',
                    'formatted': 'installment',
                    'selected_expense_key': None,
                    'expense_candidates_with_score': [],
                    'installment_role': 'installment',
                    'installment_period': 0,
                }
            ],
            'parsed_data': [
                {
                    'cache_key': 'ORDER--2',
                    'uuid': 'ORDER',
                    'installment_role': 'installment',
                    'installment_period': 0,
                    'tag_details': [],
                }
            ]
        }
        mock_analyze_service.return_value = mock_service
        from django.core.cache import cache
        cache.set('ORDER--2', {'original_row': {'uuid': 'ORDER'}}, timeout=3600)

        args = {
            'write': False,
            'cmb_credit_ignore': True,
            'boc_debit_ignore': True,
            'password': None,
        }
        result = parse_single_file_task.apply(
            args=[parse_file.file_id, user.id, args],
            task_id='installment-review-task',
        )
        if hasattr(result, 'result'):
            result = result.result
        assert result['status'] == 'pending_review'
        cached_data = ParseReviewService.get_parse_result(parse_file.file_id)
        entry = cached_data['formatted_data'][0]
        assert entry['installment_role'] == 'installment'
        assert entry['installment_period'] == 0
        # 审核身份使用 cache_key，条目已进入用户级队列
        assert EntryReviewQueueService.list_refs(user.id) == [
            {'file_id': parse_file.file_id, 'uuid': 'ORDER--2'}
        ]

    @patch('project.apps.translate.tasks.get_storage_client')
    @patch('project.apps.translate.tasks.AnalyzeService')
    @patch('project.utils.tools.get_user_config')
    def test_parse_task_review_mode_duplicate_order_uuid(
        self, mock_get_config, mock_analyze_service, mock_storage_client, user, parse_file
    ):
        """相同交易订单号时，审核条目 uuid 使用互不碰撞的 cache_key。"""
        mock_storage = MagicMock()
        mock_file_data = Mock()
        mock_file_data.read.return_value = b'test,file,content'
        mock_storage.download_file.return_value = mock_file_data
        mock_storage_client.return_value = mock_storage

        from project.apps.translate.models import FormatConfig
        FormatConfig.objects.get_or_create(
            owner=user,
            defaults={'parsing_mode_preference': 'review'}
        )
        mock_get_config.return_value = MagicMock()

        mock_service = MagicMock()
        mock_service.analyze_single_file.return_value = {
            'formatted_data': [
                {
                    'id': 'ORDER123',
                    'formatted': 'entry-a',
                    'selected_expense_key': 'A',
                    'expense_candidates_with_score': [],
                },
                {
                    'id': 'ORDER123--2',
                    'formatted': 'entry-b',
                    'selected_expense_key': 'B',
                    'expense_candidates_with_score': [],
                },
            ],
            'parsed_data': [
                {'cache_key': 'ORDER123', 'uuid': 'ORDER123', 'tag_details': []},
                {'cache_key': 'ORDER123--2', 'uuid': 'ORDER123', 'tag_details': []},
            ],
        }
        mock_analyze_service.return_value = mock_service

        from django.core.cache import cache
        cache.set('ORDER123', {'original_row': {'amount': 80}}, timeout=3600)
        cache.set('ORDER123--2', {'original_row': {'amount': 20}}, timeout=3600)

        args = {
            'write': False,
            'cmb_credit_ignore': True,
            'boc_debit_ignore': True,
            'password': None,
        }
        parse_single_file_task.apply(
            args=[parse_file.file_id, user.id, args],
            task_id='test-task-dup-uuid',
        )

        cached_data = ParseReviewService.get_parse_result(parse_file.file_id)
        uuids = [e['uuid'] for e in cached_data['formatted_data']]
        assert uuids == ['ORDER123', 'ORDER123--2']
        assert cached_data['formatted_data'][0]['original_row']['amount'] == 80
        assert cached_data['formatted_data'][1]['original_row']['amount'] == 20
        # 两条互不碰撞的条目都已入队
        assert EntryReviewQueueService.list_refs(user.id) == [
            {'file_id': parse_file.file_id, 'uuid': 'ORDER123'},
            {'file_id': parse_file.file_id, 'uuid': 'ORDER123--2'},
        ]

    @patch('project.apps.translate.tasks.get_storage_client')
    @patch('project.apps.translate.tasks.AnalyzeService')
    @patch('project.utils.tools.get_user_config')
    def test_parse_task_direct_write_mode(self, mock_get_config, mock_analyze_service, mock_storage_client, user, parse_file):
        """测试直接写入模式下立即写入文件，状态更新，且不创建条目审核待办"""
        # Mock 存储客户端
        mock_storage = MagicMock()
        mock_file_data = Mock()
        mock_file_data.read.return_value = b'test,file,content'
        mock_storage.download_file.return_value = mock_file_data
        mock_storage_client.return_value = mock_storage
        
        # Mock 配置 - 使用 get_or_create 避免唯一约束冲突
        from project.apps.translate.models import FormatConfig
        config, _ = FormatConfig.objects.get_or_create(
            owner=user,
            defaults={'parsing_mode_preference': 'direct_write'}
        )
        mock_get_config.return_value = config
        
        # Mock AnalyzeService
        mock_service = MagicMock()
        mock_service.analyze_single_file.return_value = {
            'formatted_data': [],
            'parsed_data': []
        }
        mock_analyze_service.return_value = mock_service
        
        # 执行任务（直接写入模式）
        args = {
            'write': True,  # 直接写入模式
            'cmb_credit_ignore': True,
            'boc_debit_ignore': True,
            'password': None,
        }
        
        # 手动调用任务函数
        # 对于绑定任务，由于 CELERY_TASK_ALWAYS_EAGER=True，使用 apply() 会同步执行
        result = parse_single_file_task.apply(
            args=[parse_file.file_id, user.id, args],
            task_id='test-task-id'
        )
        # 在 CELERY_TASK_ALWAYS_EAGER=True 模式下，apply() 返回的结果可以直接使用
        if hasattr(result, 'result'):
            result = result.result
        
        # 验证返回结果：空解析结果视为失败，不写入
        assert result['status'] == 'failed'
        assert result['file_id'] == parse_file.file_id
        assert result.get('error') == '未解析到有效交易记录'

        # 验证 ParseFile 状态更新为 failed
        parse_file.refresh_from_db()
        assert parse_file.status == 'failed'
        assert parse_file.error_message == '未解析到有效交易记录'

        # 统一改造后：不再创建任何 entry_review 待办
        assert not ScheduledTask.objects.filter(
            task_type='entry_review',
            object_id=user.id,
        ).exists()
    

@pytest.mark.django_db
class TestAutoConfirmExpiredEntryReviews:
    """auto_confirm_expired_entry_reviews 定时任务测试"""
    
    def setup_method(self):
        """设置测试环境"""
        pass
    
    @patch('project.apps.translate.utils.beancount_validator.BeancountValidator.validate_entries')
    @patch('project.utils.file.BeanFileManager.get_bean_file_path')
    def test_auto_confirm_expired_tasks(self, mock_get_bean_path, mock_validate, user, entry_review_task, parse_file, tmp_path):
        """测试自动确认过期条目审核任务（基于 review_expires_at）"""
        expired_data = {
            'file_id': parse_file.file_id,
            'formatted_data': [
                {
                    'uuid': 'entry-1',
                    'formatted': '2025-01-20 * "Test" "Transaction"\n    Expenses:Test  100.00 CNY\n    Assets:Test  -100.00 CNY\n',
                    'edited_formatted': '2025-01-20 * "Test" "Transaction"\n    Expenses:Test  100.00 CNY\n    Assets:Test  -100.00 CNY\n',
                    'original_row': {}
                }
            ],
            'created_at': time.time() - 86400,
            'review_expires_at': time.time() - 3600,
        }
        ParseReviewService.save_parse_result(parse_file.file_id, expired_data)
        # 条目进入用户级审核队列
        EntryReviewQueueService.enqueue(
            user.id, [{'file_id': parse_file.file_id, 'uuid': 'entry-1'}]
        )
        
        # Mock Beancount 校验
        mock_validate.return_value = (True, None, [])
        
        # Mock bean 文件路径
        bean_file = tmp_path / 'test_file.bean'
        bean_file.write_text('', encoding='utf-8')
        mock_get_bean_path.return_value = str(bean_file)
        
        # 执行定时任务
        result = auto_confirm_expired_entry_reviews()
        
        # 验证 ParseFile 状态更新为 parsed
        parse_file.refresh_from_db()
        assert parse_file.status == 'parsed'
        
        # 验证 entry_review 待办状态更新为 completed
        entry_review_task.refresh_from_db()
        assert entry_review_task.status == 'completed'
        
        # 验证用户级审核队列已清空
        assert EntryReviewQueueService.is_empty(user.id) is True
        
        # 验证文件已写入
        assert bean_file.read_text(encoding='utf-8') != ''
        assert result == {'confirmed_count': 1, 'error_count': 0}
    
    @patch('project.apps.translate.utils.beancount_validator.BeancountValidator.validate_entries')
    @patch('project.utils.file.BeanFileManager.get_bean_file_path')
    def test_auto_confirm_expired_tasks_validation_error(self, mock_get_bean_path, mock_validate, user, entry_review_task, parse_file, tmp_path, caplog):
        """测试自动确认过期任务时 Beancount 语法错误，日志记录具体错误条目"""
        expired_data = {
            'file_id': parse_file.file_id,
            'formatted_data': [
                {
                    'uuid': 'entry-1',
                    'formatted': 'invalid beancount syntax',
                    'edited_formatted': 'invalid beancount syntax',
                    'original_row': {}
                }
            ],
            'created_at': time.time() - 86400,
            'review_expires_at': time.time() - 3600,
        }
        ParseReviewService.save_parse_result(parse_file.file_id, expired_data)
        EntryReviewQueueService.enqueue(
            user.id, [{'file_id': parse_file.file_id, 'uuid': 'entry-1'}]
        )
        
        # Mock Beancount 校验返回错误
        mock_validate.return_value = (False, 'Syntax error', ['Error message'])
        
        # Mock bean 文件路径（避免实际写入文件）
        bean_file = tmp_path / 'test_file.bean'
        mock_get_bean_path.return_value = str(bean_file)
        
        import logging
        with caplog.at_level(logging.ERROR, logger='project.apps.translate.tasks'):
            result = auto_confirm_expired_entry_reviews()
        
        # 验证 ParseFile 状态未更新（因为校验失败）
        parse_file.refresh_from_db()
        assert parse_file.status == 'pending_review'  # 保持原状态
        
        # 验证 entry_review 待办状态未更新
        entry_review_task.refresh_from_db()
        assert entry_review_task.status == 'pending'  # 保持原状态
        
        # 验证未写盘
        assert not bean_file.exists()
        
        # 验证队列中引用仍在
        assert EntryReviewQueueService.list_refs(user.id) == [
            {'file_id': parse_file.file_id, 'uuid': 'entry-1'}
        ]
        assert result['error_count'] == 1
        
        # 验证日志中包含具体错误条目信息（index、uuid）
        assert any('错误条目' in rec.message for rec in caplog.records)
        assert any('index=0' in rec.message for rec in caplog.records)
        assert any('entry-1' in rec.message for rec in caplog.records)
    
    def test_auto_confirm_expired_tasks_not_expired(self, user, entry_review_task, parse_file):
        """测试未过期的任务不会被处理（基于 review_expires_at）"""
        future_data = {
            'file_id': parse_file.file_id,
            'formatted_data': [
                {
                    'uuid': 'entry-1',
                    'formatted': '2025-01-20 * "Test" "Transaction"\n    Expenses:Test  100.00 CNY\n    Assets:Test  -100.00 CNY\n',
                    'edited_formatted': '2025-01-20 * "Test" "Transaction"\n    Expenses:Test  100.00 CNY\n    Assets:Test  -100.00 CNY\n',
                    'original_row': {}
                }
            ],
            'created_at': time.time(),
            'review_expires_at': time.time() + 86400,
        }
        ParseReviewService.save_parse_result(parse_file.file_id, future_data)
        EntryReviewQueueService.enqueue(
            user.id, [{'file_id': parse_file.file_id, 'uuid': 'entry-1'}]
        )
        
        # 执行定时任务
        auto_confirm_expired_entry_reviews()
        
        # 验证任务未处理（因为未过期）
        parse_file.refresh_from_db()
        assert parse_file.status == 'pending_review'  # 保持原状态
        
        entry_review_task.refresh_from_db()
        assert entry_review_task.status == 'pending'  # 保持原状态
        
        # 队列引用保持不变
        assert EntryReviewQueueService.list_refs(user.id) == [
            {'file_id': parse_file.file_id, 'uuid': 'entry-1'}
        ]


# ======================================================================
# Copilot 记账来源的到期自动写入测试
# ======================================================================
COPILOT_EXPENSE_ACCOUNT = 'Expenses:Shopping:Food'
COPILOT_ASSET_ACCOUNT = 'Assets:Bank:CMB'

COLLECT_HEADER = (
    '; Trans directory - Auto-generated includes\n'
    '; This file is automatically generated by the platform\n\n'
)


def _ensure_copilot_accounts(user):
    """创建 Copilot 记账所需的启用账户。"""
    for path in (COPILOT_EXPENSE_ACCOUNT, COPILOT_ASSET_ACCOUNT):
        Account.objects.get_or_create(account=path, owner=user)


def _copilot_entry(**overrides):
    """构造一笔合法的 Copilot 记账入参（默认支出）。"""
    entry = {
        'type': 'expense',
        'date': '2025-01-20',
        'amount': 35.0,
        'narration': '午餐',
        'payee': '食堂',
        'account': COPILOT_EXPENSE_ACCOUNT,
        'payment_account': COPILOT_ASSET_ACCOUNT,
    }
    entry.update(overrides)
    return entry


def _create_copilot_entries(user, entries=None):
    """写入 Copilot 暂存区并入队。"""
    _ensure_copilot_accounts(user)
    result = CopilotBookkeepingService.create_entries(user, entries or [_copilot_entry()])
    assert result['ok'] is True, result
    return result


def _expire_copilot_staging(user):
    """把 Copilot 暂存区审核截止时间改到过去。"""
    staging_key = CopilotBookkeepingService.staging_key(user.id)
    data = ParseReviewService.get_parse_result(staging_key)
    data['created_at'] = time.time() - 90000
    data['review_expires_at'] = time.time() - 3600
    assert ParseReviewService.save_parse_result(staging_key, data) is True


@pytest.fixture
def temp_assets(tmp_path, monkeypatch):
    """把 ASSETS_BASE_PATH 指向临时目录，隔离 collect.bean 写入。"""
    from django.conf import settings

    base = tmp_path / 'Assets'
    base.mkdir()
    monkeypatch.setattr(settings, 'ASSETS_BASE_PATH', str(base))
    return base


def _collect_path(user):
    return Path(BeanFileManager.get_collect_bean_path(user))


def _collect_text(user):
    path = _collect_path(user)
    return path.read_text(encoding='utf-8') if path.exists() else None


@pytest.mark.django_db
class TestAutoConfirmExpiredCopilotEntries:
    """Copilot 暂存区到期自动写入 collect.bean"""

    def test_expired_copilot_appends_to_collect_bean(self, user, entry_review_task, temp_assets):
        _create_copilot_entries(user, [
            _copilot_entry(narration='第一笔'),
            _copilot_entry(narration='第二笔'),
        ])
        staging_key = CopilotBookkeepingService.staging_key(user.id)
        _expire_copilot_staging(user)
        directives = [
            entry['formatted'].rstrip()
            for entry in ParseReviewService.get_parse_result(staging_key)['formatted_data']
        ]

        collect_path = _collect_path(user)
        collect_path.parent.mkdir(parents=True, exist_ok=True)
        existing = (
            '2025-01-01 * "已有" "历史条目"\n'
            '    Expenses:Old  1.00 CNY\n'
            '    Assets:Old  -1.00 CNY\n\n'
        )
        collect_path.write_text(COLLECT_HEADER + existing, encoding='utf-8')

        result = auto_confirm_expired_entry_reviews()

        assert result == {'confirmed_count': 1, 'error_count': 0}
        text = collect_path.read_text(encoding='utf-8')
        assert text.startswith(COLLECT_HEADER)
        assert existing in text
        for directive in directives:
            assert directive in text

        # 暂存区与引用被清理，待办完成
        assert CopilotBookkeepingService.has_staging(user.id) is False
        assert EntryReviewQueueService.is_empty(user.id) is True
        entry_review_task.refresh_from_db()
        assert entry_review_task.status == 'completed'

    def test_not_expired_copilot_keeps_staging(self, user, entry_review_task, temp_assets):
        _create_copilot_entries(user)
        # 建账户本身会写入 open/note，这里比较任务执行前后账本是否被追加内容
        before = _collect_text(user) or ''

        result = auto_confirm_expired_entry_reviews()

        assert result == {'confirmed_count': 0, 'error_count': 0}
        assert CopilotBookkeepingService.has_staging(user.id) is True
        assert len(EntryReviewQueueService.list_refs(user.id)) == 1
        assert (_collect_text(user) or '') == before
        entry_review_task.refresh_from_db()
        assert entry_review_task.status == 'pending'

    def test_expired_copilot_syntax_error_keeps_staging(self, user, entry_review_task, temp_assets):
        _create_copilot_entries(user)
        staging_key = CopilotBookkeepingService.staging_key(user.id)
        entry_uuid = CopilotBookkeepingService.list_entries(user.id)[0]['uuid']
        ParseReviewService.update_entry_edited_formatted(
            staging_key, entry_uuid, 'invalid beancount syntax'
        )
        _expire_copilot_staging(user)

        result = auto_confirm_expired_entry_reviews()

        assert result == {'confirmed_count': 0, 'error_count': 1}
        assert CopilotBookkeepingService.has_staging(user.id) is True
        assert len(EntryReviewQueueService.list_refs(user.id)) == 1
        assert 'invalid beancount syntax' not in (_collect_text(user) or '')
        entry_review_task.refresh_from_db()
        assert entry_review_task.status == 'pending'
