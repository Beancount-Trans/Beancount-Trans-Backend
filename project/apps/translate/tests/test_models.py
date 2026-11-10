# tests/test_models.py
import os
import django

# 设置Django环境
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project.settings.test')
django.setup()

from django.test import TestCase
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from project.apps.maps.models import Expense, Assets, Income
from project.apps.account.models import Account

class BaseModelTestMixin:
    """测试模型公共基类的共享逻辑"""
    @classmethod
    def setUpTestData(cls):
        # 创建测试用户
        cls.user = User.objects.create_user(
            username='testuser',
            password='testpass123'
        )

        # 创建测试账户
        cls.expense_account = Account.objects.create(
            account="Expenses:Groceries",
            owner=cls.user
        )
        cls.other_expense_account = Account.objects.create(
            account="Expenses:Other",
            owner=cls.user
        )
        cls.asset_account = Account.objects.create(
            account="Assets:Digital:Alipay",
            owner=cls.user
        )
        cls.other_asset_account = Account.objects.create(
            account="Assets:Other:Test",
            owner=cls.user
        )
        cls.income_account = Account.objects.create(
            account="Income:Salary",
            owner=cls.user
        )
        cls.other_income_account = Account.objects.create(
            account="Income:Other",
            owner=cls.user
        )

class ExpenseModelTest(BaseModelTestMixin, TestCase):
    def test_expense_creation(self):
        """测试支出映射模型基础功能"""
        expense = Expense.objects.create(
            key="food",
            payee="超市",
            expend=self.expense_account,
            currency="CNY",
            owner=self.user
        )

        # 基础字段验证
        self.assertEqual(expense.key, "food")
        self.assertEqual(expense.payee, "超市")
        self.assertEqual(expense.expend.account, "Expenses:Groceries")
        self.assertEqual(expense.currency, "CNY")
        self.assertEqual(expense.owner.username, "testuser")

        # 测试默认值
        expense_with_default = Expense.objects.create(
            key="default_test",
            expend=self.other_expense_account,
            owner=self.user
        )
        self.assertEqual(expense_with_default.expend.account, "Expenses:Other")

    def test_required_fields(self):
        """测试必填字段验证"""
        with self.assertRaises(ValidationError):
            obj = Expense(key=None, expend=self.expense_account, owner=self.user)
            obj.full_clean()  # 触发完整验证

    def test_meta_options(self):
        """验证模型元数据配置"""
        self.assertEqual(Expense._meta.db_table, 'maps_expense')
        self.assertEqual(Expense._meta.verbose_name, '支出映射')
        self.assertEqual(Expense._meta.verbose_name_plural, '支出映射')

    def test_string_representation(self):
        """测试字符串表示"""
        obj = Expense.objects.create(key="test_str", expend=self.expense_account, owner=self.user)
        self.assertEqual(str(obj), "test_str")

    def test_owner_relationship(self):
        """验证用户关联关系"""
        expense = Expense.objects.create(key="relation_test", expend=self.expense_account, owner=self.user)
        self.assertIn(expense, self.user.expense.all())

class AssetsModelTest(BaseModelTestMixin, TestCase):
    def test_assets_creation(self):
        """测试资产映射模型"""
        asset = Assets.objects.create(
            key="alipay",
            assets=self.asset_account,
            owner=self.user
        )

        self.assertEqual(asset.key, "alipay")
        self.assertEqual(asset.assets.account, "Assets:Digital:Alipay")

        # 测试默认值
        asset_default = Assets.objects.create(
            key="default_asset",
            assets=self.other_asset_account,
            owner=self.user
        )
        self.assertEqual(asset_default.assets.account, "Assets:Other:Test")

    def test_max_length_constraints(self):
        """测试字段最大长度"""
        with self.assertRaises(ValidationError):
            obj = Assets(
                key="a"*17,  # 超过 max_length=16
                assets=self.asset_account,
                owner=self.user
            )
            obj.full_clean()

class IncomeModelTest(BaseModelTestMixin, TestCase):
    def test_income_creation(self):
        """测试收入映射模型"""
        income = Income.objects.create(
            key="salary",
            payer="公司",
            income=self.income_account,
            owner=self.user
        )

        self.assertEqual(income.key, "salary")
        self.assertEqual(income.payer, "公司")
        self.assertEqual(income.income.account, "Income:Salary")

    def test_nullable_field(self):
        """测试可为空字段"""
        income = Income.objects.create(
            key="null_test",
            income=self.other_income_account,
            owner=self.user
        )
        self.assertIsNone(income.payer)
