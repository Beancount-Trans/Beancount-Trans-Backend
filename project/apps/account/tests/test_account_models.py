import os
import django

# 设置Django环境
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project.settings.test')
django.setup()

from django.test import TestCase
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from project.apps.account.models import Account
from project.apps.external_accounts.models import (
    ExternalAccount,
    ExternalAccountBinding,
    Institution,
    InstrumentKind,
)
from project.apps.maps.models import Expense, Assets, Income


class AccountEnableSyncTest(TestCase):
    """测试账户enable字段同步映射状态功能"""

    def setUp(self):
        """设置测试数据"""
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )

        self.account = Account.objects.create(
            account='Assets:Bank:Test',
            owner=self.user
        )

        # 创建测试映射
        self.expense = Expense.objects.create(
            key='test_expense',
            payee='测试收款方',
            expend=self.account,
            owner=self.user,
            currency='CNY',
            enable=True
        )

        self.assets = Assets.objects.create(
            key='test_assets',
            assets=self.account,
            owner=self.user,
            enable=True
        )

        self.income = Income.objects.create(
            key='test_income',
            payer='测试付款方',
            income=self.account,
            owner=self.user,
            enable=True
        )

    def test_disable_account_syncs_mappings(self):
        """测试禁用账户时同步禁用所有映射"""
        # 禁用账户
        self.account.enable = False
        self.account.save()

        # 刷新映射对象
        self.expense.refresh_from_db()
        self.assets.refresh_from_db()
        self.income.refresh_from_db()

        # 验证映射都被禁用
        self.assertFalse(self.expense.enable)
        self.assertFalse(self.assets.enable)
        self.assertFalse(self.income.enable)

    def test_enable_account_syncs_mappings(self):
        """测试启用账户时同步启用所有映射"""
        # 先禁用账户和映射
        self.account.enable = False
        self.account.save()

        # 重新启用账户
        self.account.enable = True
        self.account.save()

        # 刷新映射对象
        self.expense.refresh_from_db()
        self.assets.refresh_from_db()
        self.income.refresh_from_db()

        # 验证映射都被启用
        self.assertTrue(self.expense.enable)
        self.assertTrue(self.assets.enable)
        self.assertTrue(self.income.enable)


class AccountDeleteMigrationTest(TestCase):
    """测试删除账户时的迁移功能"""

    def setUp(self):
        """设置测试数据"""
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )

        self.source_account = Account.objects.create(
            account='Assets:Bank:Source',
            owner=self.user
        )

        self.target_account = Account.objects.create(
            account='Assets:Bank:Target',
            owner=self.user
        )

        # 创建测试映射
        self.expense = Expense.objects.create(
            key='test_expense',
            payee='测试收款方',
            expend=self.source_account,
            owner=self.user,
            currency='CNY',
            enable=True
        )

        self.assets = Assets.objects.create(
            key='test_assets',
            assets=self.source_account,
            owner=self.user,
            enable=True
        )

        self.income = Income.objects.create(
            key='test_income',
            payer='测试付款方',
            income=self.source_account,
            owner=self.user,
            enable=True
        )

    def test_delete_with_migration(self):
        """测试删除账户并迁移映射"""
        # 执行删除迁移
        result = self.source_account.delete_with_migration(migrate_to=self.target_account)

        # 验证返回结果
        self.assertTrue(result['account_deleted'])
        self.assertTrue(result['mappings_migrated'])
        self.assertEqual(result['migrated_to'], self.target_account.id)
        self.assertEqual(result['mapping_counts']['total'], 3)

        # 验证源账户已被删除
        self.assertFalse(Account.objects.filter(id=self.source_account.id).exists())

        # 刷新映射对象
        self.expense.refresh_from_db()
        self.assets.refresh_from_db()
        self.income.refresh_from_db()

        # 验证映射已迁移到目标账户
        self.assertEqual(self.expense.expend, self.target_account)
        self.assertEqual(self.assets.assets, self.target_account)
        self.assertEqual(self.income.income, self.target_account)

    def test_delete_without_migration_target(self):
        """测试删除账户时不提供迁移目标应该失败"""
        with self.assertRaises(ValidationError):
            self.source_account.delete_with_migration(migrate_to=None)

    def test_delete_with_disabled_target(self):
        """测试删除账户时目标账户已禁用应该失败"""
        self.target_account.enable = False
        self.target_account.save()

        with self.assertRaises(ValidationError):
            self.source_account.delete_with_migration(migrate_to=self.target_account)

    def test_delete_with_children(self):
        """测试删除有子账户的账户应该失败"""
        # 创建子账户
        child_account = Account.objects.create(
            account='Assets:Bank:Source:Child',
            parent=self.source_account,
            owner=self.user
        )

        with self.assertRaises(ValidationError):
            self.source_account.delete_with_migration(migrate_to=self.target_account)


class AccountDeleteExternalBindingTest(TestCase):
    """删除带外部账户绑定的账户不应被 PROTECT 阻断。"""

    def setUp(self):
        self.user = User.objects.create_user(
            username='bindinguser', password='testpass123'
        )
        self.source = Account.objects.create(
            account='Assets:Savings:Bank:CMB:C8837', owner=self.user
        )
        self.target = Account.objects.create(
            account='Assets:Savings:Bank:CMB:C6428', owner=self.user
        )
        institution = Institution.objects.create(code='CMB', name='招商银行')
        self.external = ExternalAccount.objects.create(
            owner=self.user,
            institution=institution,
            kind=InstrumentKind.DEBIT_CARD,
            tail='8837',
        )
        self.binding = ExternalAccountBinding.objects.create(
            owner=self.user, external_account=self.external, account=self.source
        )

    def test_delete_moves_binding_to_migration_target(self):
        Assets.objects.create(
            key='招商银行储蓄卡(8837)', assets=self.source, owner=self.user
        )

        self.source.delete_with_migration(migrate_to=self.target)

        self.assertFalse(Account.objects.filter(id=self.source.id).exists())
        self.binding.refresh_from_db()
        self.assertEqual(self.binding.account_id, self.target.id)

    def test_delete_without_target_removes_binding(self):
        self.source.delete_with_migration()

        self.assertFalse(Account.objects.filter(id=self.source.id).exists())
        self.assertFalse(
            ExternalAccountBinding.objects.filter(external_account=self.external).exists()
        )
        self.assertTrue(ExternalAccount.objects.filter(id=self.external.id).exists())


class AccountCloseTest(TestCase):
    """测试关闭账户功能"""

    def setUp(self):
        """设置测试数据"""
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )

        self.account = Account.objects.create(
            account='Assets:Bank:Test',
            owner=self.user
        )

        self.target_account = Account.objects.create(
            account='Assets:Bank:Target',
            owner=self.user
        )

        # 创建测试映射
        self.expense = Expense.objects.create(
            key='test_expense',
            payee='测试收款方',
            expend=self.account,
            owner=self.user,
            currency='CNY',
            enable=True
        )

    def test_close_without_migration(self):
        """测试关闭账户不迁移映射"""
        result = self.account.close(migrate_to=None)

        # 验证返回结果
        self.assertTrue(result['account_closed'])
        self.assertTrue(result['mappings_disabled'])
        self.assertFalse(result['mappings_migrated'])

        # 验证账户被禁用
        self.account.refresh_from_db()
        self.assertFalse(self.account.enable)

        # 验证映射被禁用
        self.expense.refresh_from_db()
        self.assertFalse(self.expense.enable)

    def test_close_with_migration(self):
        """测试关闭账户并迁移映射"""
        result = self.account.close(migrate_to=self.target_account)

        # 验证返回结果
        self.assertTrue(result['account_closed'])
        self.assertTrue(result['mappings_migrated'])
        self.assertFalse(result['mappings_disabled'])
        self.assertEqual(result['migrated_to'], self.target_account.id)

        # 验证账户被禁用
        self.account.refresh_from_db()
        self.assertFalse(self.account.enable)

        # 验证映射已迁移
        self.expense.refresh_from_db()
        self.assertEqual(self.expense.expend, self.target_account)
        self.assertTrue(self.expense.enable)  # 映射保持启用状态

