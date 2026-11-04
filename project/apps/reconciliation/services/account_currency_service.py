"""
账户货币服务

解析 Beancount 账本文件，获取账户支持的货币信息。
"""
import logging
import os
import re
from decimal import Decimal
from typing import Any, Dict, List, Optional

from project.utils.file import BeanFileManager

logger = logging.getLogger(__name__)


class AccountCurrencyService:
    """账户货币服务：解析账户支持的货币"""
    
    # 匹配 open 指令的正则表达式
    # 格式：YYYY-MM-DD open Account:Name [Currency1, Currency2, ...]
    # 货币部分是可选的
    OPEN_DIRECTIVE_PATTERN = re.compile(
        r'^\s*(\d{4}-\d{2}-\d{2})\s+open\s+([A-Za-z][A-Za-z0-9:]+(?::[A-Za-z0-9-]+)*)(?:\s+([A-Z][A-Z0-9\',._-]*(?:\s*,\s*[A-Z][A-Z0-9\',._-]*)*))?\s*(?:;.*)?$',
        re.MULTILINE
    )
    
    @staticmethod
    def get_account_currencies(user, account_name: str) -> Optional[List[str]]:
        """
        解析 Beancount 账本文件，获取账户在 open 指令中声明的所有货币
        
        Args:
            user: 用户对象
            account_name: 账户名称，如 "Expenses:Food:Dinner"
            
        Returns:
            - None: 账户支持所有货币（open 指令无货币声明）或账户不存在
            - List[str]: 账户支持的货币列表
        """
        # 获取用户资产目录
        assets_path = BeanFileManager.get_user_assets_path(user)
        
        if not os.path.exists(assets_path):
            logger.warning(f"用户资产目录不存在: {assets_path}")
            return None  # 账户不存在，认为可以使用任何货币
        
        # 遍历所有 .bean 文件
        for root, dirs, files in os.walk(assets_path):
            # 跳过隐藏目录
            dirs[:] = [d for d in dirs if not d.startswith('.')]
            
            for file in files:
                if not file.endswith('.bean'):
                    continue
                
                bean_file_path = os.path.join(root, file)
                
                try:
                    with open(bean_file_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                        
                    # 查找匹配的 open 指令
                    currencies = AccountCurrencyService._parse_open_directive(
                        content, account_name
                    )
                    
                    # 如果返回 None，表示找到账户且无货币声明（支持所有货币），立即返回
                    if currencies is None:
                        return None
                    # 如果返回 False，表示未找到账户，继续查找
                    elif currencies is False:
                        continue
                    # 如果返回列表，表示找到账户且声明了货币，立即返回
                    elif isinstance(currencies, list):
                        return currencies
                        
                except Exception as e:
                    logger.warning(f"读取文件失败: {bean_file_path}, 错误: {e}")
                    continue
        
        # 未找到账户的 open 指令，认为可以使用任何货币
        logger.debug(f"未找到账户 {account_name} 的 open 指令，认为可以使用任何货币")
        return None
    
    @staticmethod
    def _parse_open_directive(content: str, account_name: str):
        """
        从文件内容中解析指定账户的 open 指令
        
        Args:
            content: 文件内容
            account_name: 账户名称
            
        Returns:
            - None: 找到账户且无货币声明（支持所有货币）
            - List[str]: 找到账户且声明了货币
            - False: 未找到该账户的 open 指令（用于区分）
        """
        for match in AccountCurrencyService.OPEN_DIRECTIVE_PATTERN.finditer(content):
            matched_account = match.group(2)
            # group(3) 可能不存在（如果货币部分为空）
            currencies_str = match.group(3) if match.lastindex >= 3 and match.group(3) else None
            
            # 检查账户名称是否匹配
            if matched_account == account_name:
                # 如果没有货币组或为空，表示支持所有货币
                if currencies_str is None or not currencies_str.strip():
                    return None  # 找到账户且支持所有货币
                
                # 解析货币列表（支持逗号分隔）
                currencies = [c.strip() for c in currencies_str.split(',')]
                # 过滤空字符串
                currencies = [c for c in currencies if c]
                
                return currencies if currencies else None
        
        return False  # 未找到账户，返回 False 作为标记
    
    @staticmethod
    def select_currency_for_account(user, account_name: str, source_currency: str) -> str:
        """
        为目标账户选择合适的货币
        
        Args:
            user: 用户对象
            account_name: 目标账户名称
            source_currency: 源货币（对账账户的货币）
            
        Returns:
            选择的货币代码
        """
        currencies = AccountCurrencyService.get_account_currencies(user, account_name)
        
        # 如果返回 None，表示账户支持所有货币或账户不存在，直接返回源货币
        if currencies is None:
            return source_currency
        
        # 如果返回列表，检查源货币是否在支持列表中
        if source_currency in currencies:
            return source_currency
        
        # 优先返回 CNY（如果存在）
        if 'CNY' in currencies:
            return 'CNY'
        
        # 否则返回第一个货币
        return currencies[0]

    @staticmethod
    def get_configured_currencies(account) -> List[str]:
        """账户在平台配置的币种列表（DB），空列表表示未限制"""
        raw = getattr(account, 'currency', '') or ''
        return [code.strip() for code in raw.split(',') if code.strip()]

    @staticmethod
    def resolve_reconciliation_currencies(account, balances) -> List[Dict[str, Any]]:
        """确定对账币种候选及预期余额

        - 账户配置了币种：以账户配置为准（含 0 余额币种）
        - 账户未配置币种：回退账本中余额非零的币种
        """
        configured = AccountCurrencyService.get_configured_currencies(account)
        if configured:
            return [
                {
                    'currency': code,
                    'expected_balance': balances.get(code, Decimal('0.00')),
                }
                for code in configured
            ]
        return [
            {'currency': currency, 'expected_balance': balance}
            for currency, balance in balances.items()
            if balance != Decimal('0.00')
        ]

