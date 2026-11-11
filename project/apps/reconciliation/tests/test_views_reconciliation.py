"""
对账执行 API 测试
"""
import pytest
from datetime import date, timedelta
from decimal import Decimal
from rest_framework.test import APIClient
from rest_framework import status

from project.apps.reconciliation.models import ScheduledTask
from project.apps.account.models import Account


@pytest.mark.django_db
class TestReconciliationAPI:
    """对账执行 API 测试"""
    
    def test_start_reconciliation_returns_expected_balance(self, user, account, scheduled_task_pending, mock_bean_file_path):
        """测试 POST /api/reconciliation/tasks/{id}/start/ 返回预期余额（多币种）"""
        # 创建包含多币种余额的账本文件
        bean_content = """
2025-01-01 open Assets:Savings:Bank:ICBC CNY
2025-01-01 open Assets:Savings:Bank:ICBC COIN

2025-01-15 * "测试交易"
    Assets:Savings:Bank:ICBC 1000.00 CNY
    Assets:Savings:Bank:ICBC 100.00 COIN
    Income:Salary -1000.00 CNY
    Income:Salary -100.00 COIN
"""
        with open(mock_bean_file_path, 'w', encoding='utf-8') as f:
            f.write(bean_content)
        
        client = APIClient()
        client.force_authenticate(user=user)
        
        response = client.post(f'/api/reconciliation/tasks/{scheduled_task_pending.id}/start/')
        
        assert response.status_code == status.HTTP_200_OK
        assert 'balances' in response.data
        assert len(response.data['balances']) == 2
        assert response.data['account_name'] == account.account
        assert 'default_currency' in response.data
        # 验证 is_first_reconciliation 字段存在且为布尔类型
        assert 'is_first_reconciliation' in response.data
        assert isinstance(response.data['is_first_reconciliation'], bool)
        # 首次对账应该返回 True（该账户还没有完成过对账任务）
        assert response.data['is_first_reconciliation'] is True
        assert response.data['default_allocation_account'] == 'Equity:Opening-Balances'
        # 没有任何已完成对账时，默认时间点为「当天最后一笔交易后」
        assert response.data['default_reconciliation_timing'] == 'end_of_day'

    def test_start_reconciliation_default_timing_follows_first_completed_today(
        self, user, account, scheduled_task_pending, mock_bean_file_path
    ):
        """当天首条已完成对账选「第二天第一笔交易前」时，后续待办默认沿用"""
        from django.contrib.contenttypes.models import ContentType

        content_type = ContentType.objects.get_for_model(Account)
        other_account = Account.objects.create(
            account='Assets:Savings:Web:AliPay',
            owner=user
        )
        ScheduledTask.objects.create(
            task_type='reconciliation',
            content_type=content_type,
            object_id=other_account.id,
            scheduled_date=date.today(),
            completed_date=date.today(),
            as_of_date=date.today() - timedelta(days=1),
            status='completed'
        )

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.post(f'/api/reconciliation/tasks/{scheduled_task_pending.id}/start/')

        assert response.status_code == status.HTTP_200_OK
        assert response.data['default_reconciliation_timing'] == 'start_of_next_day'

    def test_start_reconciliation_default_timing_follows_previous_completed_task(
        self, user, account, scheduled_task_pending, mock_bean_file_path
    ):
        """当天没有已完成对账时，默认时间点沿用上一条已完成对账的选择"""
        from django.contrib.contenttypes.models import ContentType

        content_type = ContentType.objects.get_for_model(Account)
        other_account = Account.objects.create(
            account='Assets:Savings:Web:AliPay',
            owner=user
        )
        ScheduledTask.objects.create(
            task_type='reconciliation',
            content_type=content_type,
            object_id=other_account.id,
            scheduled_date=date.today() - timedelta(days=30),
            completed_date=date.today() - timedelta(days=3),
            as_of_date=date.today() - timedelta(days=4),
            status='completed'
        )

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.post(f'/api/reconciliation/tasks/{scheduled_task_pending.id}/start/')

        assert response.status_code == status.HTTP_200_OK
        assert response.data['default_reconciliation_timing'] == 'start_of_next_day'
    
    def test_start_reconciliation_default_allocation_account_from_config(
        self, user, account, scheduled_task_pending, mock_bean_file_path,
        mock_reconciliation_bean_path, mock_ensure_reconciliation_included
    ):
        """测试非首次对账时 default_allocation_account 来自用户 FormatConfig"""
        from django.contrib.contenttypes.models import ContentType
        from project.apps.translate.models import FormatConfig

        bean_content = """
2025-01-01 open Assets:Savings:Bank:ICBC CNY

2025-01-15 * "测试交易"
    Assets:Savings:Bank:ICBC 1000.00 CNY
    Income:Salary -1000.00 CNY
"""
        with open(mock_bean_file_path, 'w', encoding='utf-8') as f:
            f.write(bean_content)

        client = APIClient()
        client.force_authenticate(user=user)

        execute_response = client.post(
            f'/api/reconciliation/tasks/{scheduled_task_pending.id}/execute/',
            {
                'actual_balance': '1000.00',
                'currency': 'CNY',
                'as_of_date': str(date.today())
            },
            format='json'
        )
        assert execute_response.status_code == status.HTTP_200_OK

        config = FormatConfig.get_user_config(user)
        config.reconciliation_fallback_account = 'Equity:Custom-Adjustments'
        config.save()

        content_type = ContentType.objects.get_for_model(Account)
        second_task = ScheduledTask.objects.create(
            task_type='reconciliation',
            content_type=content_type,
            object_id=account.id,
            scheduled_date=date.today(),
            status='pending'
        )

        response = client.post(f'/api/reconciliation/tasks/{second_task.id}/start/')
        assert response.status_code == status.HTTP_200_OK
        assert response.data['is_first_reconciliation'] is False
        assert response.data['default_allocation_account'] == 'Equity:Custom-Adjustments'

    def test_start_reconciliation_is_first_reconciliation_logic(self, user, account, scheduled_task_pending, mock_bean_file_path, mock_reconciliation_bean_path, mock_ensure_reconciliation_included):
        """测试 is_first_reconciliation 逻辑：首次对账返回 True，后续对账返回 False"""
        from django.contrib.contenttypes.models import ContentType
        
        bean_content = """
2025-01-01 open Assets:Savings:Bank:ICBC CNY

2025-01-15 * "测试交易"
    Assets:Savings:Bank:ICBC 1000.00 CNY
    Income:Salary -1000.00 CNY
"""
        with open(mock_bean_file_path, 'w', encoding='utf-8') as f:
            f.write(bean_content)
        
        client = APIClient()
        client.force_authenticate(user=user)
        
        # 第一次调用 start API（首次对账）
        response1 = client.post(f'/api/reconciliation/tasks/{scheduled_task_pending.id}/start/')
        assert response1.status_code == status.HTTP_200_OK
        assert response1.data['is_first_reconciliation'] is True
        
        # 完成第一次对账
        execute_response = client.post(
            f'/api/reconciliation/tasks/{scheduled_task_pending.id}/execute/',
            {
                'actual_balance': '1000.00',
                'currency': 'CNY',
                'as_of_date': str(date.today())
            },
            format='json'
        )
        assert execute_response.status_code == status.HTTP_200_OK
        
        # 创建第二个待办任务（后续对账）
        content_type = ContentType.objects.get_for_model(Account)
        second_task = ScheduledTask.objects.create(
            task_type='reconciliation',
            content_type=content_type,
            object_id=account.id,
            scheduled_date=date.today(),
            status='pending'
        )
        
        # 第二次调用 start API（后续对账）
        response2 = client.post(f'/api/reconciliation/tasks/{second_task.id}/start/')
        assert response2.status_code == status.HTTP_200_OK
        assert response2.data['is_first_reconciliation'] is False
        assert response2.data['default_allocation_account'] == 'Equity:Adjustments'
    
    def test_start_reconciliation_task_not_exists_returns_404(self, user):
        """测试待办不存在时返回 404"""
        client = APIClient()
        client.force_authenticate(user=user)
        
        response = client.post('/api/reconciliation/tasks/99999/start/')
        
        assert response.status_code == status.HTTP_404_NOT_FOUND
    
    def test_start_reconciliation_task_not_pending_returns_400(self, user, account, scheduled_task_completed, mock_bean_file_path):
        """测试待办状态不是 pending 时返回 400"""
        bean_content = """
2025-01-01 open Assets:Savings:Bank:ICBC CNY
"""
        with open(mock_bean_file_path, 'w', encoding='utf-8') as f:
            f.write(bean_content)
        
        client = APIClient()
        client.force_authenticate(user=user)
        
        response = client.post(f'/api/reconciliation/tasks/{scheduled_task_completed.id}/start/')
        
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert '已完成或已取消' in str(response.data)
    
    def test_execute_reconciliation_no_difference(self, user, account, scheduled_task_pending, mock_bean_file_path, mock_reconciliation_bean_path, mock_ensure_reconciliation_included):
        """测试 POST /api/reconciliation/tasks/{id}/execute/ 无差额场景"""
        bean_content = """
2025-01-01 open Assets:Savings:Bank:ICBC CNY

2025-01-15 * "测试交易"
    Assets:Savings:Bank:ICBC 1000.00 CNY
    Income:Salary -1000.00 CNY
"""
        with open(mock_bean_file_path, 'w', encoding='utf-8') as f:
            f.write(bean_content)
        
        client = APIClient()
        client.force_authenticate(user=user)
        
        response = client.post(
            f'/api/reconciliation/tasks/{scheduled_task_pending.id}/execute/',
            {
                'actual_balance': '1000.00',
                'currency': 'CNY',
                'as_of_date': str(date.today())  # as_of_date 必须由前端提供
            },
            format='json'
        )
        
        assert response.status_code == status.HTTP_200_OK
        assert response.data['status'] == 'success'
        assert len(response.data['directives']) == 1
        assert 'balance' in response.data['directives'][0]
        
        # 验证待办状态已更新
        scheduled_task_pending.refresh_from_db()
        assert scheduled_task_pending.status == 'completed'
    
    def test_execute_reconciliation_no_difference_with_transaction_items_ignored(self, user, account, scheduled_task_pending, mock_bean_file_path, mock_reconciliation_bean_path, mock_ensure_reconciliation_included):
        """测试无差额时即使提供 transaction_items 也进行忽略，对账成功"""
        bean_content = """
2025-01-01 open Assets:Savings:Bank:ICBC CNY

2025-01-15 * "测试交易"
    Assets:Savings:Bank:ICBC 1000.00 CNY
    Income:Salary -1000.00 CNY
"""
        with open(mock_bean_file_path, 'w', encoding='utf-8') as f:
            f.write(bean_content)
        
        client = APIClient()
        client.force_authenticate(user=user)
        
        # 无差额场景，但请求中附带 transaction_items（应被忽略）
        response = client.post(
            f'/api/reconciliation/tasks/{scheduled_task_pending.id}/execute/',
            {
                'actual_balance': '1000.00',
                'currency': 'CNY',
                'as_of_date': str(date.today()),
                'transaction_items': [
                    {'account': 'Expenses:Food', 'amount': '100.00', 'is_auto': False}
                ]
            },
            format='json'
        )
        
        assert response.status_code == status.HTTP_200_OK
        assert response.data['status'] == 'success'
        assert len(response.data['directives']) == 1
        assert 'balance' in response.data['directives'][0]
    
    def test_execute_reconciliation_with_transaction_only(self, user, account, scheduled_task_pending, mock_bean_file_path, mock_reconciliation_bean_path, mock_ensure_reconciliation_included):
        """测试执行对账 - 有差额（仅 transaction）"""
        bean_content = """
2025-01-01 open Assets:Savings:Bank:ICBC CNY
2025-01-01 open Expenses:Food CNY

2025-01-15 * "测试交易"
    Assets:Savings:Bank:ICBC 1000.00 CNY
    Income:Salary -1000.00 CNY
"""
        with open(mock_bean_file_path, 'w', encoding='utf-8') as f:
            f.write(bean_content)
        
        client = APIClient()
        client.force_authenticate(user=user)
        
        # 差额分配 = -200.00，验证器期望 total_allocated = -200.00（带符号）
        response = client.post(
            f'/api/reconciliation/tasks/{scheduled_task_pending.id}/execute/',
            {
                'actual_balance': '1200.00',
                'currency': 'CNY',
                'as_of_date': str(date.today()),  # as_of_date 必须由前端提供
                'transaction_items': [
                    {
                        'account': 'Expenses:Food',
                        'amount': '-200.00',  # 带符号，匹配验证器期望
                        'is_auto': False
                    }
                ]
            },
            format='json'
        )
        
        assert response.status_code == status.HTTP_200_OK
        assert response.data['status'] == 'success'
        assert len(response.data['directives']) == 2  # transaction + balance
        assert 'Expenses:Food' in response.data['directives'][0]
    
    def test_execute_reconciliation_with_pad_only(self, user, account, scheduled_task_pending, mock_bean_file_path, mock_reconciliation_bean_path, mock_ensure_reconciliation_included):
        """测试执行对账 - 有差额（仅 pad）"""
        bean_content = """
2025-01-01 open Assets:Savings:Bank:ICBC CNY
2025-01-01 open Income:Investment:Interest CNY

2025-01-15 * "测试交易"
    Assets:Savings:Bank:ICBC 1000.00 CNY
    Income:Salary -1000.00 CNY
"""
        with open(mock_bean_file_path, 'w', encoding='utf-8') as f:
            f.write(bean_content)
        
        client = APIClient()
        client.force_authenticate(user=user)
        
        response = client.post(
            f'/api/reconciliation/tasks/{scheduled_task_pending.id}/execute/',
            {
                'actual_balance': '1200.00',
                'currency': 'CNY',
                'as_of_date': str(date.today()),  # as_of_date 必须由前端提供
                'transaction_items': [
                    {
                        'account': 'Income:Investment:Interest',
                        'amount': None,
                        'is_auto': True
                    }
                ]
            },
            format='json'
        )
        
        assert response.status_code == status.HTTP_200_OK
        assert response.data['status'] == 'success'
        assert len(response.data['directives']) == 2  # pad + balance
        assert 'pad' in response.data['directives'][0]
    
    def test_execute_reconciliation_with_transaction_and_pad(self, user, account, scheduled_task_pending, mock_bean_file_path, mock_reconciliation_bean_path, mock_ensure_reconciliation_included):
        """测试执行对账 - 有差额（transaction + pad）"""
        bean_content = """
2025-01-01 open Assets:Savings:Bank:ICBC CNY
2025-01-01 open Expenses:Food CNY
2025-01-01 open Income:Investment:Interest CNY

2025-01-15 * "测试交易"
    Assets:Savings:Bank:ICBC 1000.00 CNY
    Income:Salary -1000.00 CNY
"""
        with open(mock_bean_file_path, 'w', encoding='utf-8') as f:
            f.write(bean_content)
        
        client = APIClient()
        client.force_authenticate(user=user)
        
        # 差额分配 = -200.00，手动分配 -100.00，剩余 -100.00 由 pad 兜底
        # 验证器期望 total_allocated = -100.00（带符号）
        response = client.post(
            f'/api/reconciliation/tasks/{scheduled_task_pending.id}/execute/',
            {
                'actual_balance': '1200.00',
                'currency': 'CNY',
                'as_of_date': str(date.today()),  # as_of_date 必须由前端提供
                'transaction_items': [
                    {
                        'account': 'Expenses:Food',
                        'amount': '-100.00',  # 带符号，匹配验证器期望
                        'is_auto': False
                    },
                    {
                        'account': 'Income:Investment:Interest',
                        'amount': None,
                        'is_auto': True
                    }
                ]
            },
            format='json'
        )
        
        assert response.status_code == status.HTTP_200_OK
        assert response.data['status'] == 'success'
        assert len(response.data['directives']) == 3  # transaction + pad + balance
        assert 'Expenses:Food' in response.data['directives'][0]
        assert 'pad' in response.data['directives'][1]
    
    def test_execute_reconciliation_multiple_currencies_cny(self, user, account, scheduled_task_pending, mock_bean_file_path, mock_reconciliation_bean_path, mock_ensure_reconciliation_included):
        """测试多币种对账：对账 CNY 币种生成 CNY 指令"""
        bean_content = """
2025-01-01 open Assets:Savings:Web:AliPay CNY
2025-01-01 open Assets:Savings:Web:AliPay COIN
2025-01-01 open Income:Investment:Interest CNY

2025-01-15 * "测试交易"
    Assets:Savings:Web:AliPay 500.00 CNY
    Assets:Savings:Web:AliPay 100.00 COIN
    Income:Investment:Interest -500.00 CNY
    Income:Investment:Interest -100.00 COIN
"""
        with open(mock_bean_file_path, 'w', encoding='utf-8') as f:
            f.write(bean_content)
        
        client = APIClient()
        client.force_authenticate(user=user)
        
        # 差额分配 = -100.00，使用 pad 兜底
        response = client.post(
            f'/api/reconciliation/tasks/{scheduled_task_pending.id}/execute/',
            {
                'actual_balance': '600.00',
                'currency': 'CNY',
                'as_of_date': str(date.today()),  # as_of_date 必须由前端提供
                'transaction_items': [
                    {
                        'account': 'Income:Investment:Interest',
                        'amount': None,
                        'is_auto': True
                    }
                ]
            },
            format='json'
        )
        
        assert response.status_code == status.HTTP_200_OK
        # pad 指令不包含币种，应该检查 balance 指令（最后一个）
        assert len(response.data['directives']) == 2  # pad + balance
        assert 'pad' in response.data['directives'][0]
        assert 'CNY' in response.data['directives'][1]  # balance 指令包含币种
        assert 'COIN' not in response.data['directives'][1]
    
    def test_execute_reconciliation_multiple_currencies_coin(self, user, account, scheduled_task_pending, mock_bean_file_path, mock_reconciliation_bean_path, mock_ensure_reconciliation_included):
        """测试多币种对账：对账 COIN 币种生成 COIN 指令"""
        bean_content = """
2025-01-01 open Assets:Savings:Web:AliPay CNY
2025-01-01 open Assets:Savings:Web:AliPay COIN
2025-01-01 open Income:Investment:Interest COIN

2025-01-15 * "测试交易"
    Assets:Savings:Web:AliPay 500.00 CNY
    Assets:Savings:Web:AliPay 100.00 COIN
    Income:Investment:Interest -500.00 CNY
    Income:Investment:Interest -100.00 COIN
"""
        with open(mock_bean_file_path, 'w', encoding='utf-8') as f:
            f.write(bean_content)
        
        client = APIClient()
        client.force_authenticate(user=user)
        
        # 差额分配 = -50.00，使用 pad 兜底
        response = client.post(
            f'/api/reconciliation/tasks/{scheduled_task_pending.id}/execute/',
            {
                'actual_balance': '150.00',
                'currency': 'COIN',
                'as_of_date': str(date.today()),  # as_of_date 必须由前端提供
                'transaction_items': [
                    {
                        'account': 'Income:Investment:Interest',
                        'amount': None,
                        'is_auto': True
                    }
                ]
            },
            format='json'
        )
        
        assert response.status_code == status.HTTP_200_OK
        # pad 指令不包含币种，应该检查 balance 指令（最后一个）
        assert len(response.data['directives']) == 2  # pad + balance
        assert 'pad' in response.data['directives'][0]
        assert 'COIN' in response.data['directives'][1]  # balance 指令包含币种
        assert '150.00 COIN' in response.data['directives'][1]
    
    def test_execute_reconciliation_actual_balance_format_error(self, user, account, scheduled_task_pending, mock_bean_file_path):
        """测试实际余额格式错误返回 400"""
        bean_content = """
2025-01-01 open Assets:Savings:Bank:ICBC CNY
"""
        with open(mock_bean_file_path, 'w', encoding='utf-8') as f:
            f.write(bean_content)
        
        client = APIClient()
        client.force_authenticate(user=user)
        
        response = client.post(
            f'/api/reconciliation/tasks/{scheduled_task_pending.id}/execute/',
            {
                'actual_balance': 'invalid',  # 无效格式
                'currency': 'CNY'
            },
            format='json'
        )
        
        assert response.status_code == status.HTTP_400_BAD_REQUEST
    
    def test_execute_reconciliation_transaction_amount_mismatch(self, user, account, scheduled_task_pending, mock_bean_file_path):
        """测试 transaction 记录金额总和与差额不匹配返回 400"""
        bean_content = """
2025-01-01 open Assets:Savings:Bank:ICBC CNY
2025-01-01 open Expenses:Food CNY

2025-01-15 * "测试交易"
    Assets:Savings:Bank:ICBC 1000.00 CNY
    Income:Salary -1000.00 CNY
"""
        with open(mock_bean_file_path, 'w', encoding='utf-8') as f:
            f.write(bean_content)
        
        client = APIClient()
        client.force_authenticate(user=user)
        
        # 差额分配 = -200.00，但只分配了 -100.00（不匹配）
        # 验证器期望 total_allocated = -200.00，但只提供了 -100.00
        response = client.post(
            f'/api/reconciliation/tasks/{scheduled_task_pending.id}/execute/',
            {
                'actual_balance': '1200.00',
                'currency': 'CNY',
                'as_of_date': str(date.today()),  # as_of_date 必须由前端提供
                'transaction_items': [
                    {
                        'account': 'Expenses:Food',
                        'amount': '-100.00',  # 不匹配，应该是 -200.00
                        'is_auto': False
                    }
                ]
            },
            format='json'
        )
        
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'transaction_items' in response.data
    
    def test_execute_reconciliation_with_date_in_transaction_item(self, user, account, scheduled_task_pending, mock_bean_file_path, mock_reconciliation_bean_path, mock_ensure_reconciliation_included):
        """测试 is_auto=false 的条目可以指定日期，且日期在指令中使用"""
        bean_content = """
2025-01-01 open Assets:Savings:Bank:ICBC CNY
2025-01-01 open Expenses:Food CNY

2025-01-15 * "测试交易"
    Assets:Savings:Bank:ICBC 1000.00 CNY
    Income:Salary -1000.00 CNY
"""
        with open(mock_bean_file_path, 'w', encoding='utf-8') as f:
            f.write(bean_content)
        
        client = APIClient()
        client.force_authenticate(user=user)
        
        as_of_date = date.today()
        item_date = as_of_date - timedelta(days=5)  # 条目日期早于 as_of_date
        
        response = client.post(
            f'/api/reconciliation/tasks/{scheduled_task_pending.id}/execute/',
            {
                'actual_balance': '1200.00',
                'currency': 'CNY',
                'as_of_date': str(as_of_date),
                'transaction_items': [
                    {
                        'account': 'Expenses:Food',
                        'amount': '-200.00',
                        'is_auto': False,
                        'date': str(item_date)  # 指定日期
                    }
                ]
            },
            format='json'
        )
        
        assert response.status_code == status.HTTP_200_OK
        assert response.data['status'] == 'success'
        # 验证指令中使用了指定的日期
        assert str(item_date) in response.data['directives'][0]
    
    def test_execute_reconciliation_without_date_uses_as_of_date(self, user, account, scheduled_task_pending, mock_bean_file_path, mock_reconciliation_bean_path, mock_ensure_reconciliation_included):
        """测试 is_auto=false 的条目未指定日期时使用 as_of_date"""
        bean_content = """
2025-01-01 open Assets:Savings:Bank:ICBC CNY
2025-01-01 open Expenses:Food CNY

2025-01-15 * "测试交易"
    Assets:Savings:Bank:ICBC 1000.00 CNY
    Income:Salary -1000.00 CNY
"""
        with open(mock_bean_file_path, 'w', encoding='utf-8') as f:
            f.write(bean_content)
        
        client = APIClient()
        client.force_authenticate(user=user)
        
        as_of_date = date.today()
        
        response = client.post(
            f'/api/reconciliation/tasks/{scheduled_task_pending.id}/execute/',
            {
                'actual_balance': '1200.00',
                'currency': 'CNY',
                'as_of_date': str(as_of_date),
                'transaction_items': [
                    {
                        'account': 'Expenses:Food',
                        'amount': '-200.00',
                        'is_auto': False
                        # 未指定日期
                    }
                ]
            },
            format='json'
        )
        
        assert response.status_code == status.HTTP_200_OK
        assert response.data['status'] == 'success'
        # 验证指令中使用了 as_of_date
        assert str(as_of_date) in response.data['directives'][0]
    
    def test_execute_reconciliation_date_exceeds_as_of_date_rejected(self, user, account, scheduled_task_pending, mock_bean_file_path):
        """测试 is_auto=false 的条目日期超过 as_of_date 时被拒绝"""
        bean_content = """
2025-01-01 open Assets:Savings:Bank:ICBC CNY
2025-01-01 open Expenses:Food CNY

2025-01-15 * "测试交易"
    Assets:Savings:Bank:ICBC 1000.00 CNY
    Income:Salary -1000.00 CNY
"""
        with open(mock_bean_file_path, 'w', encoding='utf-8') as f:
            f.write(bean_content)
        
        client = APIClient()
        client.force_authenticate(user=user)
        
        as_of_date = date.today()
        item_date = as_of_date + timedelta(days=1)  # 条目日期晚于 as_of_date
        
        response = client.post(
            f'/api/reconciliation/tasks/{scheduled_task_pending.id}/execute/',
            {
                'actual_balance': '1200.00',
                'currency': 'CNY',
                'as_of_date': str(as_of_date),
                'transaction_items': [
                    {
                        'account': 'Expenses:Food',
                        'amount': '-200.00',
                        'is_auto': False,
                        'date': str(item_date)  # 日期超过 as_of_date
                    }
                ]
            },
            format='json'
        )
        
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'transaction_items' in response.data
        assert '不能超过对账截止日期' in str(response.data)
    
    def test_execute_reconciliation_auto_item_with_date_rejected(self, user, account, scheduled_task_pending, mock_bean_file_path):
        """测试 is_auto=true 的条目不允许指定日期"""
        bean_content = """
2025-01-01 open Assets:Savings:Bank:ICBC CNY
2025-01-01 open Income:Investment:Interest CNY

2025-01-15 * "测试交易"
    Assets:Savings:Bank:ICBC 1000.00 CNY
    Income:Salary -1000.00 CNY
"""
        with open(mock_bean_file_path, 'w', encoding='utf-8') as f:
            f.write(bean_content)
        
        client = APIClient()
        client.force_authenticate(user=user)
        
        response = client.post(
            f'/api/reconciliation/tasks/{scheduled_task_pending.id}/execute/',
            {
                'actual_balance': '1200.00',
                'currency': 'CNY',
                'as_of_date': str(date.today()),
                'transaction_items': [
                    {
                        'account': 'Income:Investment:Interest',
                        'amount': None,
                        'is_auto': True,
                        'date': str(date.today())  # is_auto=true 时不允许指定日期
                    }
                ]
            },
            format='json'
        )
        
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert 'is_auto=true 的条目不允许指定日期' in str(response.data)
    
    def test_execute_reconciliation_auto_item_uses_as_of_date(self, user, account, scheduled_task_pending, mock_bean_file_path, mock_reconciliation_bean_path, mock_ensure_reconciliation_included):
        """测试 is_auto=true 的条目统一使用 as_of_date"""
        bean_content = """
2025-01-01 open Assets:Savings:Bank:ICBC CNY
2025-01-01 open Income:Investment:Interest CNY

2025-01-15 * "测试交易"
    Assets:Savings:Bank:ICBC 1000.00 CNY
    Income:Salary -1000.00 CNY
"""
        with open(mock_bean_file_path, 'w', encoding='utf-8') as f:
            f.write(bean_content)
        
        client = APIClient()
        client.force_authenticate(user=user)
        
        as_of_date = date.today()
        
        response = client.post(
            f'/api/reconciliation/tasks/{scheduled_task_pending.id}/execute/',
            {
                'actual_balance': '1000.01',
                'currency': 'CNY',
                'as_of_date': str(as_of_date),
                'transaction_items': [
                    {
                        'account': 'Income:Investment:Interest',
                        'amount': None,
                        'is_auto': True
                    }
                ]
            },
            format='json'
        )
        
        assert response.status_code == status.HTTP_200_OK
        assert response.data['status'] == 'success'
        # 验证自动计算的 transaction 指令使用了 as_of_date
        assert str(as_of_date) in response.data['directives'][0]


@pytest.mark.django_db
class TestReconciliationDuplicateCheckAPI:
    """测试重复对账检查 API
    
    需求：不允许同一账户同一天被对账两次（基于 as_of_date）
    """
    
    def test_api_allow_same_account_different_as_of_date(
        self, 
        user, 
        account,
        mock_bean_file_path, 
        mock_reconciliation_bean_path, 
        mock_ensure_reconciliation_included
    ):
        """测试 API：允许同一账户对账不同的 as_of_date"""
        from django.contrib.contenttypes.models import ContentType
        from datetime import date
        
        content_type = ContentType.objects.get_for_model(Account)
        
        # 创建已完成的对账任务（as_of_date=2026-01-20）
        ScheduledTask.objects.create(
            task_type='reconciliation',
            content_type=content_type,
            object_id=account.id,
            scheduled_date=date(2026, 1, 15),
            completed_date=date(2026, 1, 20),
            as_of_date=date(2026, 1, 20),
            status='completed'
        )
        
        # 创建待执行的对账任务
        pending_task = ScheduledTask.objects.create(
            task_type='reconciliation',
            content_type=content_type,
            object_id=account.id,
            scheduled_date=date(2026, 1, 22),
            status='pending'
        )
        
        bean_content = """
2025-01-01 open Assets:Savings:Bank:ICBC CNY

2025-01-15 * "测试交易"
    Assets:Savings:Bank:ICBC 1000.00 CNY
    Income:Salary -1000.00 CNY
"""
        with open(mock_bean_file_path, 'w', encoding='utf-8') as f:
            f.write(bean_content)
        
        client = APIClient()
        client.force_authenticate(user=user)
        
        # 提交不同的 as_of_date（2026-01-21）
        response = client.post(
            f'/api/reconciliation/tasks/{pending_task.id}/execute/',
            {
                'actual_balance': '1000.00',
                'currency': 'CNY',
                'as_of_date': '2026-01-21'  # 不同的 as_of_date
            },
            format='json'
        )
        
        assert response.status_code == status.HTTP_200_OK
        assert response.data['status'] == 'success'
    
    def test_api_reject_same_account_same_as_of_date(
        self, 
        user, 
        account,
        mock_bean_file_path
    ):
        """测试 API：拒绝同一账户重复对账相同的 as_of_date"""
        from django.contrib.contenttypes.models import ContentType
        from datetime import date
        
        content_type = ContentType.objects.get_for_model(Account)
        
        # 创建已完成的对账任务（as_of_date=2026-01-20）
        ScheduledTask.objects.create(
            task_type='reconciliation',
            content_type=content_type,
            object_id=account.id,
            scheduled_date=date(2026, 1, 15),
            completed_date=date(2026, 1, 20),
            as_of_date=date(2026, 1, 20),
            status='completed'
        )
        
        # 创建待执行的对账任务
        pending_task = ScheduledTask.objects.create(
            task_type='reconciliation',
            content_type=content_type,
            object_id=account.id,
            scheduled_date=date(2026, 1, 23),
            status='pending'
        )
        
        bean_content = """
2025-01-01 open Assets:Savings:Bank:ICBC CNY

2025-01-15 * "测试交易"
    Assets:Savings:Bank:ICBC 1000.00 CNY
    Income:Salary -1000.00 CNY
"""
        with open(mock_bean_file_path, 'w', encoding='utf-8') as f:
            f.write(bean_content)
        
        client = APIClient()
        client.force_authenticate(user=user)
        
        # 提交相同的 as_of_date（2026-01-20）
        response = client.post(
            f'/api/reconciliation/tasks/{pending_task.id}/execute/',
            {
                'actual_balance': '1000.00',
                'currency': 'CNY',
                'as_of_date': '2026-01-20'  # 相同的 as_of_date，应被拒绝
            },
            format='json'
        )
        
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert '2026-01-20' in str(response.data) or '重复对账' in str(response.data)


@pytest.mark.django_db
class TestReconciliationAccountCurrencyAPI:
    """测试对账币种优先取账户配置、账户未配置时回退账本"""

    def _set_currency(self, account, currency):
        """直接更新币种，避免触发账本同步写文件"""
        Account.objects.filter(pk=account.pk).update(currency=currency)
        account.refresh_from_db()

    def test_start_uses_account_currency_without_ledger_balance(
        self, user, account, scheduled_task_pending, mock_bean_file_path
    ):
        """账户配置 USD 且账本无交易时，仍返回 USD 候选，预期余额为 0"""
        self._set_currency(account, 'USD')

        with open(mock_bean_file_path, 'w', encoding='utf-8') as f:
            f.write("")

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.post(f'/api/reconciliation/tasks/{scheduled_task_pending.id}/start/')

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data['balances']) == 1
        item = response.data['balances'][0]
        assert item['currency'] == 'USD'
        assert Decimal(str(item['expected_balance'])) == Decimal('0.00')
        assert response.data['default_currency'] == 'USD'

    def test_start_lists_all_configured_currencies(
        self, user, account, scheduled_task_pending, mock_bean_file_path
    ):
        """账户配置 CNY,COIN 时，即使 CNY 无余额也作为候选，且默认 CNY"""
        self._set_currency(account, 'CNY,COIN')

        bean_content = """
2025-01-01 open Assets:Savings:Bank:ICBC CNY,COIN

2025-01-15 * "测试交易"
    Assets:Savings:Bank:ICBC 100.00 COIN
    Income:Salary -100.00 COIN
"""
        with open(mock_bean_file_path, 'w', encoding='utf-8') as f:
            f.write(bean_content)

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.post(f'/api/reconciliation/tasks/{scheduled_task_pending.id}/start/')

        assert response.status_code == status.HTTP_200_OK
        balances = {item['currency']: Decimal(str(item['expected_balance'])) for item in response.data['balances']}
        assert balances == {'CNY': Decimal('0.00'), 'COIN': Decimal('100.00')}
        assert response.data['default_currency'] == 'CNY'

    def test_start_falls_back_to_ledger_when_account_currency_empty(
        self, user, account, scheduled_task_pending, mock_bean_file_path
    ):
        """账户未配置币种时，仍按账本余额推导候选"""
        bean_content = """
2025-01-01 open Assets:Savings:Bank:ICBC CNY

2025-01-15 * "测试交易"
    Assets:Savings:Bank:ICBC 1000.00 CNY
    Income:Salary -1000.00 CNY
"""
        with open(mock_bean_file_path, 'w', encoding='utf-8') as f:
            f.write(bean_content)

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.post(f'/api/reconciliation/tasks/{scheduled_task_pending.id}/start/')

        assert response.status_code == status.HTTP_200_OK
        assert [item['currency'] for item in response.data['balances']] == ['CNY']
        assert response.data['default_currency'] == 'CNY'

    def test_execute_rejects_currency_outside_account_currency(
        self, user, account, scheduled_task_pending, mock_bean_file_path,
        mock_reconciliation_bean_path, mock_ensure_reconciliation_included
    ):
        """账户仅支持 USD 时，提交 CNY 应被拒绝"""
        self._set_currency(account, 'USD')

        bean_content = """
2025-01-01 open Assets:Savings:Bank:ICBC USD

2025-01-15 * "测试交易"
    Assets:Savings:Bank:ICBC 1000.00 USD
    Income:Salary -1000.00 USD
"""
        with open(mock_bean_file_path, 'w', encoding='utf-8') as f:
            f.write(bean_content)

        client = APIClient()
        client.force_authenticate(user=user)

        response = client.post(
            f'/api/reconciliation/tasks/{scheduled_task_pending.id}/execute/',
            {
                'actual_balance': '1000.00',
                'currency': 'CNY',
                'as_of_date': str(date.today())
            },
            format='json'
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert '不在账户可用币种' in str(response.data)

