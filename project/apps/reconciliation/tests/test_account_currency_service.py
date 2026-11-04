"""
AccountCurrencyService 账户货币服务测试
"""
import pytest
import os
import tempfile
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from project.apps.reconciliation.services.account_currency_service import AccountCurrencyService


@pytest.mark.django_db
class TestAccountCurrencyService:
    """AccountCurrencyService 账户货币服务测试"""
    
    def test_get_account_currencies_no_currency(self, user, tmp_path):
        """测试账户支持所有货币的情况（open 指令无货币声明）"""
        # 创建临时账本文件
        bean_file = tmp_path / "account.bean"
        bean_file.write_text("""
2025-01-01 open Expenses:Food:Dinner
2025-01-01 open Assets:Savings:Bank:ICBC CNY
""", encoding='utf-8')
        
        with patch('project.apps.reconciliation.services.account_currency_service.BeanFileManager.get_user_assets_path') as mock_path:
            mock_path.return_value = str(tmp_path)
            
            currencies = AccountCurrencyService.get_account_currencies(
                user, 'Expenses:Food:Dinner'
            )
            
            # 应该返回 None，表示支持所有货币
            assert currencies is None
    
    def test_get_account_currencies_single_currency(self, user, tmp_path):
        """测试账户支持单货币的情况"""
        bean_file = tmp_path / "account.bean"
        bean_file.write_text("""
2025-01-01 open Expenses:Food:Dinner CNY
2025-01-01 open Assets:Savings:Bank:ICBC CNY
""", encoding='utf-8')
        
        with patch('project.apps.reconciliation.services.account_currency_service.BeanFileManager.get_user_assets_path') as mock_path:
            mock_path.return_value = str(tmp_path)
            
            currencies = AccountCurrencyService.get_account_currencies(
                user, 'Expenses:Food:Dinner'
            )
            
            assert currencies == ['CNY']
    
    def test_get_account_currencies_multiple_currencies(self, user, tmp_path):
        """测试账户支持多货币的情况"""
        bean_file = tmp_path / "account.bean"
        bean_file.write_text("""
2025-01-01 open Expenses:Food:Dinner CNY, USD, EUR
2025-01-01 open Assets:Savings:Bank:ICBC CNY
""", encoding='utf-8')
        
        with patch('project.apps.reconciliation.services.account_currency_service.BeanFileManager.get_user_assets_path') as mock_path:
            mock_path.return_value = str(tmp_path)
            
            currencies = AccountCurrencyService.get_account_currencies(
                user, 'Expenses:Food:Dinner'
            )
            
            assert currencies == ['CNY', 'USD', 'EUR']
    
    def test_get_account_currencies_account_not_found(self, user, tmp_path):
        """测试账户不存在的情况（未找到 open 指令）"""
        bean_file = tmp_path / "account.bean"
        bean_file.write_text("""
2025-01-01 open Assets:Savings:Bank:ICBC CNY
""", encoding='utf-8')
        
        with patch('project.apps.reconciliation.services.account_currency_service.BeanFileManager.get_user_assets_path') as mock_path:
            mock_path.return_value = str(tmp_path)
            
            currencies = AccountCurrencyService.get_account_currencies(
                user, 'Expenses:Food:Dinner'
            )
            
            # 应该返回 None，表示账户不存在（认为可以使用任何货币）
            assert currencies is None
    
    def test_get_account_currencies_multiple_files(self, user, tmp_path):
        """测试在多个文件中查找账户"""
        # 创建多个 bean 文件
        bean_file1 = tmp_path / "account1.bean"
        bean_file1.write_text("""
2025-01-01 open Assets:Savings:Bank:ICBC CNY
""", encoding='utf-8')
        
        bean_file2 = tmp_path / "account2.bean"
        bean_file2.write_text("""
2025-01-01 open Expenses:Food:Dinner CNY
""", encoding='utf-8')
        
        with patch('project.apps.reconciliation.services.account_currency_service.BeanFileManager.get_user_assets_path') as mock_path:
            mock_path.return_value = str(tmp_path)
            
            currencies = AccountCurrencyService.get_account_currencies(
                user, 'Expenses:Food:Dinner'
            )
            
            assert currencies == ['CNY']
    
    def test_select_currency_supports_all_currencies(self, user, tmp_path):
        """测试账户支持所有货币时，返回源货币"""
        bean_file = tmp_path / "account.bean"
        bean_file.write_text("""
2025-01-01 open Expenses:Food:Dinner
""", encoding='utf-8')
        
        with patch('project.apps.reconciliation.services.account_currency_service.BeanFileManager.get_user_assets_path') as mock_path:
            mock_path.return_value = str(tmp_path)
            
            selected = AccountCurrencyService.select_currency_for_account(
                user, 'Expenses:Food:Dinner', 'COIN'
            )
            
            # 应该返回源货币 COIN
            assert selected == 'COIN'
    
    def test_select_currency_source_in_list(self, user, tmp_path):
        """测试源货币在支持列表中，返回源货币"""
        bean_file = tmp_path / "account.bean"
        bean_file.write_text("""
2025-01-01 open Expenses:Food:Dinner CNY, COIN, USD
""", encoding='utf-8')
        
        with patch('project.apps.reconciliation.services.account_currency_service.BeanFileManager.get_user_assets_path') as mock_path:
            mock_path.return_value = str(tmp_path)
            
            selected = AccountCurrencyService.select_currency_for_account(
                user, 'Expenses:Food:Dinner', 'COIN'
            )
            
            # 应该返回源货币 COIN
            assert selected == 'COIN'
    
    def test_select_currency_prefer_cny(self, user, tmp_path):
        """测试源货币不在支持列表中，但有 CNY，优先返回 CNY"""
        bean_file = tmp_path / "account.bean"
        bean_file.write_text("""
2025-01-01 open Expenses:Food:Dinner CNY, USD, EUR
""", encoding='utf-8')
        
        with patch('project.apps.reconciliation.services.account_currency_service.BeanFileManager.get_user_assets_path') as mock_path:
            mock_path.return_value = str(tmp_path)
            
            selected = AccountCurrencyService.select_currency_for_account(
                user, 'Expenses:Food:Dinner', 'COIN'
            )
            
            # 应该返回 CNY（优先选择）
            assert selected == 'CNY'
    
    def test_select_currency_first_currency_when_no_cny(self, user, tmp_path):
        """测试源货币不在支持列表中，且没有 CNY，返回第一个货币"""
        bean_file = tmp_path / "account.bean"
        bean_file.write_text("""
2025-01-01 open Expenses:Food:Dinner USD, EUR, GBP
""", encoding='utf-8')
        
        with patch('project.apps.reconciliation.services.account_currency_service.BeanFileManager.get_user_assets_path') as mock_path:
            mock_path.return_value = str(tmp_path)
            
            selected = AccountCurrencyService.select_currency_for_account(
                user, 'Expenses:Food:Dinner', 'COIN'
            )
            
            # 应该返回第一个货币 USD
            assert selected == 'USD'
    
    def test_select_currency_account_not_found(self, user, tmp_path):
        """测试账户不存在时，返回源货币（认为可以使用任何货币）"""
        bean_file = tmp_path / "account.bean"
        bean_file.write_text("""
2025-01-01 open Assets:Savings:Bank:ICBC CNY
""", encoding='utf-8')
        
        with patch('project.apps.reconciliation.services.account_currency_service.BeanFileManager.get_user_assets_path') as mock_path:
            mock_path.return_value = str(tmp_path)
            
            selected = AccountCurrencyService.select_currency_for_account(
                user, 'Expenses:Food:Dinner', 'COIN'
            )
            
            # 应该返回源货币 COIN（账户不存在，认为可以使用任何货币）
            assert selected == 'COIN'
    
    def test_get_account_currencies_with_comments(self, user, tmp_path):
        """测试 open 指令带注释的情况"""
        bean_file = tmp_path / "account.bean"
        bean_file.write_text("""
2025-01-01 open Expenses:Food:Dinner CNY ; 晚餐支出账户
2025-01-01 open Assets:Savings:Bank:ICBC CNY
""", encoding='utf-8')
        
        with patch('project.apps.reconciliation.services.account_currency_service.BeanFileManager.get_user_assets_path') as mock_path:
            mock_path.return_value = str(tmp_path)
            
            currencies = AccountCurrencyService.get_account_currencies(
                user, 'Expenses:Food:Dinner'
            )
            
            assert currencies == ['CNY']
    
    def test_get_account_currencies_with_spaces(self, user, tmp_path):
        """测试 open 指令中货币列表带空格的情况"""
        bean_file = tmp_path / "account.bean"
        bean_file.write_text("""
2025-01-01 open Expenses:Food:Dinner CNY , USD , EUR
2025-01-01 open Assets:Savings:Bank:ICBC CNY
""", encoding='utf-8')
        
        with patch('project.apps.reconciliation.services.account_currency_service.BeanFileManager.get_user_assets_path') as mock_path:
            mock_path.return_value = str(tmp_path)
            
            currencies = AccountCurrencyService.get_account_currencies(
                user, 'Expenses:Food:Dinner'
            )
            
            # 应该正确解析并去除空格
            assert currencies == ['CNY', 'USD', 'EUR']


class TestReconciliationCurrencies:
    """对账币种候选解析测试"""

    @staticmethod
    def _account(currency):
        return SimpleNamespace(currency=currency)

    def test_get_configured_currencies(self):
        assert AccountCurrencyService.get_configured_currencies(
            self._account('CNY,COIN')
        ) == ['CNY', 'COIN']
        assert AccountCurrencyService.get_configured_currencies(self._account('')) == []
        assert AccountCurrencyService.get_configured_currencies(self._account(None)) == []

    def test_configured_currencies_take_priority_with_zero_balance(self):
        """账户配置了币种时，全部返回，缺失余额按 0 处理"""
        result = AccountCurrencyService.resolve_reconciliation_currencies(
            self._account('CNY,COIN'),
            {'COIN': Decimal('10.00')},
        )

        assert result == [
            {'currency': 'CNY', 'expected_balance': Decimal('0.00')},
            {'currency': 'COIN', 'expected_balance': Decimal('10.00')},
        ]

    def test_fallback_to_ledger_non_zero_balances(self):
        """账户未配置币种时，回退账本非零余额"""
        result = AccountCurrencyService.resolve_reconciliation_currencies(
            self._account(''),
            {'CNY': Decimal('100.00'), 'COIN': Decimal('0.00')},
        )

        assert result == [{'currency': 'CNY', 'expected_balance': Decimal('100.00')}]

    def test_empty_configured_and_no_balance(self):
        """账户未配置币种且账本无余额时返回空列表"""
        result = AccountCurrencyService.resolve_reconciliation_currencies(
            self._account(''),
            {},
        )

        assert result == []

