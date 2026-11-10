import os

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project.settings.test')
django.setup()

from django.contrib.auth.models import User
from django.test import TestCase

from project.apps.account.models import Account
from project.apps.external_accounts.models import (
    ExternalAccount,
    ExternalAccountBinding,
    Institution,
    InstrumentKind,
    PlatformSlot,
)
from project.apps.maps.models import Template, TemplateItem
from project.apps.translate.utils import get_default_assets


class DefaultAssetsResolutionTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='pass')
        self.alipay = Institution.objects.create(code='ALIPAY', name='支付宝', is_platform=True)

    def _account(self, path):
        return Account.objects.create(account=path, owner=self.user)

    def test_prefers_platform_slot_resolution(self):
        account = self._account('Assets:Savings:Web:AliPay')
        external = ExternalAccount.objects.create(
            owner=self.user,
            institution=self.alipay,
            kind=InstrumentKind.WALLET,
            display_name='支付宝余额',
            slot=PlatformSlot.ALIPAY_BALANCE,
        )
        ExternalAccountBinding.objects.create(
            owner=self.user, external_account=external, account=account
        )

        assets = get_default_assets(self.user.id)

        self.assertEqual(assets['ALIPAY'], 'Assets:Savings:Web:AliPay')
        self.assertEqual(assets['HUABEI'], 'Assets:Other')

    def test_falls_back_to_template_slot_when_slot_missing(self):
        template_owner = User.objects.create_user(username='template_owner', password='pass')
        template = Template.objects.create(
            name='官方资产映射', type='assets', is_official=True, owner=template_owner
        )
        TemplateItem.objects.create(
            template=template,
            key='余额',
            account='Assets:Savings:Web:AliPay',
            institution_code='ALIPAY',
            external_kind='wallet',
            slot='alipay_balance',
            display_name='支付宝余额',
        )
        self._account('Assets:Savings:Web:AliPay')

        assets = get_default_assets(self.user.id)

        self.assertEqual(assets['ALIPAY'], 'Assets:Savings:Web:AliPay')
