"""账户模板覆盖应用时应先释放外部账户绑定（Account 上的 PROTECT）。"""
import os

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project.settings.test')
django.setup()

from django.contrib.auth.models import User
from django.test import TestCase

from project.apps.account.models import Account, AccountTemplate, AccountTemplateItem
from project.apps.account.views import AccountTemplateViewSet
from project.apps.external_accounts.models import (
    ExternalAccount,
    ExternalAccountBinding,
    Institution,
    InstrumentKind,
)


class AccountTemplateOverwriteBindingTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='templateoverwrite', password='testpass123'
        )
        self.account = Account.objects.create(
            account='Assets:Savings:Web:AliPay', owner=self.user
        )
        institution = Institution.objects.create(
            code='ALIPAY', name='支付宝', is_platform=True
        )
        external = ExternalAccount.objects.create(
            owner=self.user,
            institution=institution,
            kind=InstrumentKind.WALLET,
            slot='alipay_balance',
            display_name='支付宝余额',
        )
        ExternalAccountBinding.objects.create(
            owner=self.user, external_account=external, account=self.account
        )
        self.template = AccountTemplate.objects.create(
            name='覆盖测试模板', owner=self.user
        )
        AccountTemplateItem.objects.create(
            template=self.template, account_path='Assets:New:Account'
        )

    def test_overwrite_releases_external_binding(self):
        result = AccountTemplateViewSet()._apply_account_template(
            self.template, self.user, 'overwrite', 'skip'
        )

        self.assertEqual(result['created'], 1)
        self.assertFalse(
            Account.objects.filter(
                owner=self.user, account='Assets:Savings:Web:AliPay'
            ).exists()
        )
        self.assertTrue(
            Account.objects.filter(
                owner=self.user, account='Assets:New:Account'
            ).exists()
        )
        self.assertFalse(ExternalAccount.objects.filter(owner=self.user).exists())
