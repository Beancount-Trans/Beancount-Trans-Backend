import os

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project.settings.test')
django.setup()

from django.contrib.auth.models import User
from django.test import TestCase

from project.apps.account.models import Account
from project.apps.external_accounts.legacy_split import split_legacy_platform_accounts
from project.apps.external_accounts.models import (
    ExternalAccount,
    ExternalAccountAlias,
    ExternalAccountBinding,
    Institution,
    InstrumentKind,
    PlatformSlot,
)
from project.apps.maps.models import Assets


class LegacySplitTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='pass')
        Institution.objects.create(code='ALIPAY', name='支付宝', is_platform=True)
        Institution.objects.create(code='CITIC', name='中信银行')
        self.legacy = Account.objects.create(
            account='Liabilities:CreditCard:Web:AliPay', owner=self.user
        )

    def _mapping(self, key, account=None):
        return Assets.objects.create(
            owner=self.user, key=key, assets=account or self.legacy
        )

    def test_splits_three_platform_products(self):
        for key in ('花呗', '借呗', '备用金'):
            self._mapping(key)

        stats = split_legacy_platform_accounts(self.user.id)

        self.assertEqual(stats.created_accounts, 4)  # 三个产品 + 中信目标账户
        self.assertEqual(stats.repointed_mappings, 3)
        self.assertEqual(stats.conflicts, [])

        paths = set(
            Account.objects.filter(owner=self.user).values_list('account', flat=True)
        )
        self.assertIn('Liabilities:CreditCard:Web:AliPay:Huabei', paths)
        self.assertIn('Liabilities:CreditCard:Web:AliPay:Jiebei', paths)
        self.assertIn('Liabilities:CreditCard:Web:AliPay:Beiyongjin', paths)

        huabei = ExternalAccount.objects.get(
            owner=self.user, slot=PlatformSlot.ALIPAY_HUABEI
        )
        self.assertEqual(huabei.kind, InstrumentKind.CREDIT_LINE)
        self.assertEqual(
            ExternalAccountBinding.objects.get(external_account=huabei).account.account,
            'Liabilities:CreditCard:Web:AliPay:Huabei',
        )

        jiebei = ExternalAccount.objects.get(
            owner=self.user, slot=PlatformSlot.ALIPAY_JIEBEI
        )
        self.assertEqual(jiebei.kind, InstrumentKind.LOAN)

    def test_does_not_overwrite_custom_account(self):
        custom = Account.objects.create(account='Liabilities:Other:MyHuabei', owner=self.user)
        self._mapping('花呗', custom)

        split_legacy_platform_accounts(self.user.id)

        mapping = Assets.objects.get(owner=self.user, key='花呗')
        self.assertEqual(mapping.assets, custom)

    def test_old_citic_tail_converges_to_current_card(self):
        old_account = Account.objects.create(
            account='Liabilities:CreditCard:Bank:CITIC:C7929', owner=self.user
        )
        self._mapping('7929', old_account)

        split_legacy_platform_accounts(self.user.id)

        mapping = Assets.objects.get(owner=self.user, key='7929')
        self.assertEqual(mapping.assets.account, 'Liabilities:CreditCard:Bank:CITIC:C6428')

        external = ExternalAccount.objects.get(
            owner=self.user, institution__code='CITIC', tail='6428'
        )
        self.assertTrue(
            ExternalAccountAlias.objects.filter(owner=self.user, text='7929').exists()
        )
        self.assertEqual(
            ExternalAccountBinding.objects.get(external_account=external).account.account,
            'Liabilities:CreditCard:Bank:CITIC:C6428',
        )

    def test_old_citic_tail_external_is_merged(self):
        """回填阶段按旧尾号建过的外部身份应并入当前卡号身份。"""
        citic = Institution.objects.get(code='CITIC')
        legacy = ExternalAccount.objects.create(
            owner=self.user, institution=citic, kind=InstrumentKind.CREDIT_CARD, tail='7929'
        )
        ExternalAccountAlias.objects.create(
            owner=self.user, external_account=legacy, text='7929'
        )
        ExternalAccountAlias.objects.create(
            owner=self.user, external_account=legacy, text='中信银行信用卡(7929)'
        )

        split_legacy_platform_accounts(self.user.id)

        current = ExternalAccount.objects.get(
            owner=self.user, institution=citic, tail='6428'
        )
        self.assertFalse(
            ExternalAccount.objects.filter(owner=self.user, institution=citic, tail='7929').exists()
        )
        self.assertEqual(
            set(
                ExternalAccountAlias.objects.filter(owner=self.user).values_list(
                    'text', flat=True
                )
            ),
            {'7929', '中信银行信用卡(7929)'},
        )
        self.assertFalse(
            ExternalAccountAlias.objects.filter(external_account=legacy).exists()
        )
        self.assertTrue(
            ExternalAccountBinding.objects.filter(external_account=current).exists()
        )

    def test_split_is_idempotent(self):
        self._mapping('花呗')
        split_legacy_platform_accounts(self.user.id)

        stats = split_legacy_platform_accounts(self.user.id)

        self.assertEqual(stats.created_accounts, 0)
        self.assertEqual(stats.repointed_mappings, 0)
        self.assertEqual(stats.bound, 0)
        self.assertEqual(ExternalAccountBinding.objects.count(), 2)  # 花呗 + 中信
