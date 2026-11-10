# project/apps/translate/services/mapping_provider.py
"""
映射数据提供者
根据用户类型提供映射和账户数据：
- 已登录用户：使用自己的实例数据
- 匿名/新用户：使用官方模板数据
"""
from typing import List, Optional, Tuple
from dataclasses import dataclass
from django.contrib.auth import get_user_model
from project.apps.maps.models import Expense, Assets, Income, Template, TemplateItem
from project.apps.account.models import Account, AccountTemplate, AccountTemplateItem
import logging

logger = logging.getLogger(__name__)
User = get_user_model()


@dataclass
class MockAccountObject:
    """模拟账户对象"""
    account: str


class EmptyTagsManager:
    """空标签管理器"""
    def filter(self, enable=True):
        return []

    def all(self):
        return []


@dataclass
class TemplateExpenseMapping:
    """从模板创建的支出映射对象"""
    key: str
    payee: Optional[str]
    expend: Optional[MockAccountObject]
    currency: Optional[str]
    enable: bool = True
    tags: EmptyTagsManager = None

    def __post_init__(self):
        if self.tags is None:
            self.tags = EmptyTagsManager()


@dataclass
class TemplateAssetsMapping:
    """从模板创建的资产映射对象"""
    key: str
    assets: Optional[MockAccountObject]
    enable: bool = True
    tags: EmptyTagsManager = None

    def __post_init__(self):
        if self.tags is None:
            self.tags = EmptyTagsManager()


@dataclass
class TemplateIncomeMapping:
    """从模板创建的收入映射对象"""
    key: str
    payer: Optional[str]
    income: Optional[MockAccountObject]
    enable: bool = True
    tags: EmptyTagsManager = None

    def __post_init__(self):
        if self.tags is None:
            self.tags = EmptyTagsManager()


class MappingDataProvider:
    """映射数据提供者基类"""

    def __init__(self, user_id: int):
        self.user_id = user_id
        self.user = None
        self.use_templates = False

        # 判断是否使用模板数据
        if user_id:
            try:
                self.user = User.objects.get(id=user_id)
                # 检查用户是否有自己的映射数据
                has_own_data = (
                    Expense.objects.filter(owner=self.user).exists() or
                    Assets.objects.filter(owner=self.user).exists() or
                    Income.objects.filter(owner=self.user).exists()
                )
                # 如果用户没有自己的数据，使用模板
                self.use_templates = not has_own_data
            except User.DoesNotExist:
                self.use_templates = True
        else:
            # 匿名用户使用模板
            self.use_templates = True

    def get_expense_mappings(self, enable_only: bool = True) -> List:
        """获取支出映射数据"""
        if self.use_templates:
            return self._get_expense_from_template(enable_only)
        else:
            return self._get_expense_from_user(enable_only)

    def get_asset_mappings(self, enable_only: bool = True) -> List:
        """获取资产映射数据"""
        if self.use_templates:
            return self._get_assets_from_template(enable_only)
        else:
            return self._get_assets_from_user(enable_only)

    def get_income_mappings(self, enable_only: bool = True) -> List:
        """获取收入映射数据"""
        if self.use_templates:
            return self._get_income_from_template(enable_only)
        else:
            return self._get_income_from_user(enable_only)

    def get_account_by_path(self, account_path: str) -> Optional[str]:
        """根据账户路径获取账户对象"""
        if self.use_templates:
            # 从官方账户模板中查找
            template = AccountTemplate.objects.filter(is_official=True).first()
            if template:
                item = template.items.filter(account_path=account_path).first()
                if item:
                    return account_path
            return None
        else:
            # 从用户账户实例中查找
            account = Account.objects.filter(account=account_path, owner=self.user, enable=True).first()
            return account.account if account else None

    # === 从模板读取 ===

    def _get_expense_from_template(self, enable_only: bool) -> List:
        """从官方模板获取支出映射"""
        official_templates = Template.objects.filter(type='expense', is_official=True)

        # 转换模板项为类似 Expense 对象的结构
        mappings = []
        for template in official_templates:
            for item in template.items.all():
                mapping = TemplateExpenseMapping(
                    key=item.key,
                    payee=item.payee,
                    expend=MockAccountObject(
                        account=item.account if item.account else 'Expenses:Other'
                    ) if item.account else None,
                    currency=item.currency,
                    enable=True,
                    tags=EmptyTagsManager()
                )
                mappings.append(mapping)

        return mappings

    def _get_assets_from_template(self, enable_only: bool) -> List:
        """从官方模板获取资产映射"""
        official_templates = Template.objects.filter(type='assets', is_official=True)

        mappings = []
        for template in official_templates:
            for item in template.items.all():
                mapping = TemplateAssetsMapping(
                    key=item.key,
                    assets=MockAccountObject(
                        account=item.account if item.account else 'Assets:Other'
                    ) if item.account else None,
                    enable=True,
                    tags=EmptyTagsManager()
                )
                mappings.append(mapping)

        return mappings

    def _get_income_from_template(self, enable_only: bool) -> List:
        """从官方模板获取收入映射"""
        official_templates = Template.objects.filter(type='income', is_official=True)

        mappings = []
        for template in official_templates:
            for item in template.items.all():
                mapping = TemplateIncomeMapping(
                    key=item.key,
                    payer=item.payer,
                    income=MockAccountObject(
                        account=item.account if item.account else 'Income:Other'
                    ) if item.account else None,
                    enable=True,
                    tags=EmptyTagsManager()
                )
                mappings.append(mapping)

        return mappings

    # === 从用户实例读取 ===

    def _get_expense_from_user(self, enable_only: bool) -> List:
        """从用户实例获取支出映射"""
        queryset = Expense.objects.filter(owner=self.user)
        if enable_only:
            queryset = queryset.filter(enable=True)
        return list(queryset)

    def _get_assets_from_user(self, enable_only: bool) -> List:
        """从用户实例获取资产映射"""
        queryset = Assets.objects.filter(owner=self.user)
        if enable_only:
            queryset = queryset.filter(enable=True)
        return list(queryset)

    def _get_income_from_user(self, enable_only: bool) -> List:
        """从用户实例获取收入映射"""
        queryset = Income.objects.filter(owner=self.user)
        if enable_only:
            queryset = queryset.filter(enable=True)
        return list(queryset)


def get_mapping_provider(user_id: int) -> MappingDataProvider:
    """工厂函数：获取映射数据提供者"""
    return MappingDataProvider(user_id)


def extract_account_string(account_obj) -> str:
    """提取账户字符串

    兼容处理：
    - MockAccountObject: 返回 account 属性
    - Account 对象: 返回 account 属性
    - 字符串: 直接返回
    """
    if account_obj is None:
        return 'Assets:Other'

    if isinstance(account_obj, str):
        return account_obj

    if hasattr(account_obj, 'account'):
        return account_obj.account

    return str(account_obj)

