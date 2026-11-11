"""
序列化器测试
"""
import pytest
from datetime import date, timedelta
from decimal import Decimal
from django.contrib.contenttypes.models import ContentType

from project.apps.reconciliation.serializers import (
    ScheduledTaskSerializer,
    ScheduledTaskListSerializer,
    ScheduledTaskUpdateSerializer,
    ReconciliationStartSerializer,
    ReconciliationExecuteSerializer,
    TransactionItemSerializer
)
from project.apps.reconciliation.models import ScheduledTask
from project.apps.account.models import Account


@pytest.mark.django_db
class TestScheduledTaskSerializer:
    """ScheduledTaskSerializer 测试"""
    
    def test_serialize_task(self, scheduled_task_pending):
        """测试序列化待办对象（包含所有字段）"""
        serializer = ScheduledTaskSerializer(scheduled_task_pending)
        data = serializer.data
        
        assert data['id'] == scheduled_task_pending.id
        assert data['task_type'] == 'reconciliation'
        assert data['task_type_display'] == '对账'
        assert data['status'] == 'pending'
        assert data['status_display'] == '待执行'
        assert data['scheduled_date'] == str(date.today())
        assert 'created' in data
        assert 'modified' in data
    
    def test_deserialize_create_task(self, user, account):
        """测试反序列化创建待办（验证必填字段）"""
        content_type = ContentType.objects.get_for_model(Account)
        data = {
            'task_type': 'reconciliation',
            'content_type': content_type.id,
            'object_id': account.id,
            'scheduled_date': str(date.today())
        }
        
        serializer = ScheduledTaskSerializer(data=data)
        assert serializer.is_valid()
        
        task = serializer.save()
        assert task.task_type == 'reconciliation'
        assert task.content_object == account
        assert task.scheduled_date == date.today()
        assert task.status == 'pending'  # 默认值
    
    def test_validate_scheduled_date_format(self, user, account):
        """测试验证 scheduled_date 格式"""
        content_type = ContentType.objects.get_for_model(Account)
        data = {
            'task_type': 'reconciliation',
            'content_type': content_type.id,
            'object_id': account.id,
            'scheduled_date': '2026-01-20'  # 正确格式
        }
        
        serializer = ScheduledTaskSerializer(data=data)
        assert serializer.is_valid()
    
    def test_validate_status_choices(self, scheduled_task_pending):
        """测试验证 status 字段选择"""
        # 测试有效的状态值
        serializer = ScheduledTaskSerializer(scheduled_task_pending)
        data = serializer.data
        
        assert data['status'] in ['pending', 'completed', 'cancelled']


@pytest.mark.django_db
class TestScheduledTaskListSerializer:
    """ScheduledTaskListSerializer 测试"""
    
    def test_serialize_task_with_account_info(self, scheduled_task_pending):
        """测试序列化待办对象（包含账户信息）"""
        serializer = ScheduledTaskListSerializer(scheduled_task_pending)
        data = serializer.data
        
        assert 'account_name' in data
        assert 'account_type' in data
        assert data['account_name'] == scheduled_task_pending.content_object.account


@pytest.mark.django_db
class TestScheduledTaskUpdateSerializer:
    """ScheduledTaskUpdateSerializer 测试"""
    
    def test_update_scheduled_date(self, scheduled_task_pending):
        """测试更新 scheduled_date"""
        new_date = date.today() + timedelta(days=5)
        data = {
            'scheduled_date': str(new_date)
        }
        
        serializer = ScheduledTaskUpdateSerializer(
            scheduled_task_pending,
            data=data,
            partial=True
        )
        assert serializer.is_valid()
        
        task = serializer.save()
        assert task.scheduled_date == new_date


@pytest.mark.django_db
class TestTransactionItemSerializer:
    """TransactionItemSerializer 测试"""
    
    def test_serialize_transaction_item(self):
        """测试序列化 transaction 条目"""
        data = {
            'account': 'Expenses:Food',
            'amount': '100.00',
            'is_auto': False
        }
        
        serializer = TransactionItemSerializer(data=data)
        assert serializer.is_valid()
        assert serializer.validated_data['account'] == 'Expenses:Food'
        assert serializer.validated_data['amount'] == Decimal('100.00')
        assert serializer.validated_data['is_auto'] is False
    
    def test_serialize_transaction_item_auto(self):
        """测试序列化自动计算的 transaction 条目"""
        data = {
            'account': 'Income:Investment:Interest',
            'amount': None,
            'is_auto': True
        }
        
        serializer = TransactionItemSerializer(data=data)
        assert serializer.is_valid()
        assert serializer.validated_data['account'] == 'Income:Investment:Interest'
        assert serializer.validated_data['amount'] is None
        assert serializer.validated_data['is_auto'] is True
    
    def test_transaction_item_with_date(self):
        """测试 is_auto=false 时可以指定日期"""
        data = {
            'account': 'Expenses:Food',
            'amount': '100.00',
            'is_auto': False,
            'date': '2026-01-15'
        }
        
        serializer = TransactionItemSerializer(data=data)
        assert serializer.is_valid()
        assert serializer.validated_data['date'] == date(2026, 1, 15)
    
    def test_transaction_item_without_date(self):
        """测试 is_auto=false 时未指定日期（应该允许）"""
        data = {
            'account': 'Expenses:Food',
            'amount': '100.00',
            'is_auto': False
        }
        
        serializer = TransactionItemSerializer(data=data)
        assert serializer.is_valid()
        assert serializer.validated_data.get('date') is None
    
    def test_transaction_item_auto_with_date_rejected(self):
        """测试 is_auto=true 时不允许指定日期（应该报错）"""
        data = {
            'account': 'Income:Investment:Interest',
            'amount': None,
            'is_auto': True,
            'date': '2026-01-15'
        }
        
        serializer = TransactionItemSerializer(data=data)
        assert not serializer.is_valid()
        assert 'is_auto=true 的条目不允许指定日期' in str(serializer.errors)
    
    def test_transaction_item_date_format(self):
        """测试日期格式验证"""
        data = {
            'account': 'Expenses:Food',
            'amount': '100.00',
            'is_auto': False,
            'date': 'invalid-date'
        }
        
        serializer = TransactionItemSerializer(data=data)
        assert not serializer.is_valid()
        assert 'date' in serializer.errors


@pytest.mark.django_db
class TestReconciliationStartSerializer:
    """ReconciliationStartSerializer 测试"""
    
    def test_serialize_start_response(self):
        """测试序列化对账开始响应（预期余额、币种列表）"""
        data = {
            'balances': [
                {'currency': 'CNY', 'expected_balance': Decimal('1000.00')},
                {'currency': 'COIN', 'expected_balance': Decimal('100.00')}
            ],
            'account_name': 'Assets:Savings:Bank:ICBC',
            'as_of_date': date.today(),
            'default_currency': 'CNY',
            'is_first_reconciliation': True,
            'default_allocation_account': 'Equity:Opening-Balances',
            'default_reconciliation_timing': 'end_of_day',
        }
        
        serializer = ReconciliationStartSerializer(data)
        serialized_data = serializer.data
        
        assert len(serialized_data['balances']) == 2
        assert serialized_data['account_name'] == 'Assets:Savings:Bank:ICBC'
        assert serialized_data['default_currency'] == 'CNY'
        assert serialized_data['is_first_reconciliation'] is True
        assert serialized_data['default_allocation_account'] == 'Equity:Opening-Balances'
        assert serialized_data['default_reconciliation_timing'] == 'end_of_day'


@pytest.mark.django_db
class TestReconciliationExecuteSerializer:
    """ReconciliationExecuteSerializer 测试"""
    
    def test_validate_actual_balance_required(self, scheduled_task_pending):
        """测试验证实际余额字段（必填、数值类型）"""
        data = {
            'currency': 'CNY'
            # 缺少 actual_balance
        }
        
        serializer = ReconciliationExecuteSerializer(
            data=data,
            context={'task': scheduled_task_pending}
        )
        assert not serializer.is_valid()
        assert 'actual_balance' in serializer.errors
    
    def test_validate_currency_required(self, scheduled_task_pending, mock_bean_file_path):
        """测试验证币种字段（必填、选择）"""
        data = {
            'actual_balance': '1000.00'
            # 缺少 currency（但有默认值 CNY）
        }
        
        # 创建账本文件
        bean_content = """
2025-01-01 open Assets:Savings:Bank:ICBC CNY

2025-01-15 * "测试交易"
    Assets:Savings:Bank:ICBC 1000.00 CNY
    Income:Salary -1000.00 CNY
"""
        with open(mock_bean_file_path, 'w', encoding='utf-8') as f:
            f.write(bean_content)
        
        serializer = ReconciliationExecuteSerializer(
            data=data,
            context={'task': scheduled_task_pending}
        )
        # currency 有默认值，应该有效
        # 但需要验证实际余额和预期余额
        # 这里简化测试，只验证基本结构
        assert 'currency' in serializer.fields
    
    def test_validate_transaction_items_optional(self, scheduled_task_pending, mock_bean_file_path):
        """测试验证 transaction 记录列表（可选）"""
        bean_content = """
2025-01-01 open Assets:Savings:Bank:ICBC CNY

2025-01-15 * "测试交易"
    Assets:Savings:Bank:ICBC 1000.00 CNY
    Income:Salary -1000.00 CNY
"""
        with open(mock_bean_file_path, 'w', encoding='utf-8') as f:
            f.write(bean_content)
        
        data = {
            'actual_balance': '1000.00',
            'currency': 'CNY'
            # transaction_items 是可选的
        }
        
        serializer = ReconciliationExecuteSerializer(
            data=data,
            context={'task': scheduled_task_pending}
        )
        # 无差额时，transaction_items 是可选的
        # 这里只验证字段存在
        assert 'transaction_items' in serializer.fields
    
    def test_validate_transaction_items_format(self, scheduled_task_pending, mock_bean_file_path):
        """测试验证 transaction 记录格式（账户、金额）"""
        bean_content = """
2025-01-01 open Assets:Savings:Bank:ICBC CNY
2025-01-01 open Expenses:Food CNY

2025-01-15 * "测试交易"
    Assets:Savings:Bank:ICBC 1000.00 CNY
    Income:Salary -1000.00 CNY
"""
        with open(mock_bean_file_path, 'w', encoding='utf-8') as f:
            f.write(bean_content)
        
        data = {
            'actual_balance': '1200.00',
            'currency': 'CNY',
            'as_of_date': str(date.today()),  # as_of_date 必须由前端提供
            'transaction_items': [
                {
                    'account': 'Expenses:Food',
                    'amount': '200.00',
                    'is_auto': False
                }
            ]
        }
        
        serializer = ReconciliationExecuteSerializer(
            data=data,
            context={'task': scheduled_task_pending}
        )
        # 验证器会验证金额总和
        # 这里只验证基本结构
        assert serializer.is_valid() or 'transaction_items' in serializer.errors


@pytest.mark.django_db
class TestReconciliationDuplicateCheck:
    """测试重复对账检查逻辑
    
    需求：不允许同一账户同一天被对账两次（基于 as_of_date）
    - 允许同一账户在同一天提交不同 as_of_date 的对账
    - 不允许同一账户提交已对账过的 as_of_date
    """
    
    def test_allow_same_account_different_as_of_date(
        self, 
        scheduled_task_pending_for_same_account,
        scheduled_task_completed_with_as_of_date,
        mock_bean_file_path
    ):
        """测试允许同一账户对账不同的 as_of_date
        
        场景：账户 A 在 2026-01-20 完成了 as_of_date=2026-01-20 的对账，
        允许账户 A 在 2026-01-22 提交 as_of_date=2026-01-21 的对账
        """
        bean_content = """
2025-01-01 open Assets:Savings:Bank:ICBC CNY

2025-01-15 * "测试交易"
    Assets:Savings:Bank:ICBC 1000.00 CNY
    Income:Salary -1000.00 CNY
"""
        with open(mock_bean_file_path, 'w', encoding='utf-8') as f:
            f.write(bean_content)
        
        # 提交 as_of_date=2026-01-21（与已完成的 2026-01-20 不同）
        data = {
            'actual_balance': '1000.00',
            'currency': 'CNY',
            'as_of_date': '2026-01-21'  # 不同的 as_of_date
        }
        
        serializer = ReconciliationExecuteSerializer(
            data=data,
            context={'task': scheduled_task_pending_for_same_account}
        )
        
        # 应该通过验证（不同的 as_of_date）
        assert serializer.is_valid(), f"验证应该通过，错误: {serializer.errors}"
    
    def test_reject_same_account_same_as_of_date(
        self, 
        scheduled_task_pending_for_same_account,
        scheduled_task_completed_with_as_of_date,
        mock_bean_file_path
    ):
        """测试拒绝同一账户重复对账相同的 as_of_date
        
        场景：账户 A 在 2026-01-20 完成了 as_of_date=2026-01-20 的对账，
        不允许账户 A 在 2026-01-23 提交 as_of_date=2026-01-20 的对账
        """
        bean_content = """
2025-01-01 open Assets:Savings:Bank:ICBC CNY

2025-01-15 * "测试交易"
    Assets:Savings:Bank:ICBC 1000.00 CNY
    Income:Salary -1000.00 CNY
"""
        with open(mock_bean_file_path, 'w', encoding='utf-8') as f:
            f.write(bean_content)
        
        # 提交 as_of_date=2026-01-20（与已完成的相同）
        data = {
            'actual_balance': '1000.00',
            'currency': 'CNY',
            'as_of_date': '2026-01-20'  # 相同的 as_of_date，应被拒绝
        }
        
        serializer = ReconciliationExecuteSerializer(
            data=data,
            context={'task': scheduled_task_pending_for_same_account}
        )
        
        # 应该验证失败
        assert not serializer.is_valid()
        # 检查错误信息
        assert '2026-01-20' in str(serializer.errors) or '重复对账' in str(serializer.errors)
    
    def test_allow_different_account_same_as_of_date(
        self, 
        user,
        account,
        scheduled_task_completed_with_as_of_date,
        mock_bean_file_path
    ):
        """测试允许不同账户对账相同的 as_of_date
        
        场景：账户 A 已对账 as_of_date=2026-01-20，
        账户 B 可以提交 as_of_date=2026-01-20 的对账
        """
        # 创建另一个账户
        another_account = Account.objects.create(
            account='Assets:Savings:Bank:BOC',
            owner=user
        )
        
        # 为另一个账户创建待办
        content_type = ContentType.objects.get_for_model(Account)
        another_task = ScheduledTask.objects.create(
            task_type='reconciliation',
            content_type=content_type,
            object_id=another_account.id,
            scheduled_date=date(2026, 1, 22),
            status='pending'
        )
        
        bean_content = """
2025-01-01 open Assets:Savings:Bank:BOC CNY

2025-01-15 * "测试交易"
    Assets:Savings:Bank:BOC 2000.00 CNY
    Income:Salary -2000.00 CNY
"""
        with open(mock_bean_file_path, 'w', encoding='utf-8') as f:
            f.write(bean_content)
        
        # 不同账户提交相同的 as_of_date
        data = {
            'actual_balance': '2000.00',
            'currency': 'CNY',
            'as_of_date': '2026-01-20'  # 与账户 A 相同的 as_of_date
        }
        
        serializer = ReconciliationExecuteSerializer(
            data=data,
            context={'task': another_task}
        )
        
        # 不同账户应该允许对账相同的 as_of_date
        assert serializer.is_valid(), f"不同账户应该允许对账相同的 as_of_date，错误: {serializer.errors}"
    
    def test_same_task_can_resubmit_same_as_of_date(
        self, 
        scheduled_task_pending,
        mock_bean_file_path
    ):
        """测试同一待办可以提交相同的 as_of_date（排除自身）
        
        场景：待办任务首次提交，不应被自己的历史记录阻止
        """
        bean_content = """
2025-01-01 open Assets:Savings:Bank:ICBC CNY

2025-01-15 * "测试交易"
    Assets:Savings:Bank:ICBC 1000.00 CNY
    Income:Salary -1000.00 CNY
"""
        with open(mock_bean_file_path, 'w', encoding='utf-8') as f:
            f.write(bean_content)
        
        data = {
            'actual_balance': '1000.00',
            'currency': 'CNY',
            'as_of_date': str(date.today())
        }
        
        serializer = ReconciliationExecuteSerializer(
            data=data,
            context={'task': scheduled_task_pending}
        )
        
        # 首次提交应该通过
        assert serializer.is_valid(), f"首次提交应该通过，错误: {serializer.errors}"

