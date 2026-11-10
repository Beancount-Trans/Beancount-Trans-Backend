import os

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project.settings.test')
django.setup()

from django.contrib.auth.models import User
from django.test import TestCase

from project.apps.account.models import Account
from project.apps.external_accounts.legacy_sync import sync_owner
from project.apps.external_accounts.models import (
    ExternalAccount,
    ExternalAccountAlias,
    ExternalAccountBinding,
    Institution,
    InstitutionAlias,
    InstrumentKind,
    PlatformSlot,
)
from project.apps.maps.models import Assets


class LegacySyncTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='pass')
        for code, name, aliases in [
            ('BOC', '中国银行', ['中国银行', '中行']),
            ('ALIPAY', '支付宝', ['支付宝']),
            ('CITIC', '中信银行', ['中信银行']),
        ]:
            institution = Institution.objects.create(code=code, name=name, is_platform=code == 'ALIPAY')
            for text in aliases:
                InstitutionAlias.objects.create(institution=institution, text=text)

    def _account(self, path):
        return Account.objects.create(account=path, owner=self.user)

    def _mapping(self, key, account):
        return Assets.objects.create(owner=self.user, key=key, assets=account)

    def test_card_mapping_becomes_external_account(self):
        account = self._account('Assets:Savings:Bank:BOC:C0814')
        self._mapping('0814', account)

        stats = sync_owner(self.user.id)

        external = ExternalAccount.objects.get(owner=self.user)
        self.assertEqual(external.institution.code, 'BOC')
        self.assertEqual(external.kind, InstrumentKind.DEBIT_CARD)
        self.assertEqual(external.tail, '0814')
        self.assertEqual(external.canonical_name, '中国银行储蓄卡(0814)')
        self.assertEqual(ExternalAccountBinding.objects.get(external_account=external).account, account)
        self.assertEqual(stats.created, 1)
        self.assertEqual(stats.bound, 1)

    def test_platform_slot_mapping(self):
        account = self._account('Assets:Savings:Web:AliFund')
        self._mapping('余额宝', account)

        sync_owner(self.user.id)

        external = ExternalAccount.objects.get(owner=self.user)
        self.assertEqual(external.slot, PlatformSlot.ALIPAY_FUND)
        self.assertEqual(external.kind, InstrumentKind.FUND)
        self.assertEqual(external.display_name, '支付宝余额宝')
        self.assertTrue(ExternalAccountAlias.objects.filter(owner=self.user, text='余额宝').exists())

    def test_same_ledger_account_conflict_is_reported(self):
        account = self._account('Assets:Savings:Bank:BOC:C0814')
        other_institution = Institution.objects.get(code='CITIC')
        other = ExternalAccount.objects.create(
            owner=self.user,
            institution=other_institution,
            kind=InstrumentKind.CREDIT_CARD,
            tail='6428',
        )
        ExternalAccountBinding.objects.create(
            owner=self.user, external_account=other, account=account
        )
        self._mapping('0814', account)

        stats = sync_owner(self.user.id)

        self.assertEqual(len(stats.conflicts), 1)
        self.assertIn('已被', stats.conflicts[0])

    def test_mapping_without_identity_is_skipped(self):
        account = self._account('Assets:Savings:Recharge:HaLuo')
        self._mapping('单车骑行卡抵扣', account)

        stats = sync_owner(self.user.id)

        self.assertEqual(stats.skipped, 1)
        self.assertEqual(ExternalAccount.objects.count(), 0)

    def test_sync_is_idempotent(self):
        account = self._account('Assets:Savings:Bank:BOC:C0814')
        self._mapping('0814', account)
        sync_owner(self.user.id)

        stats = sync_owner(self.user.id)

        self.assertEqual(stats.created, 0)
        self.assertEqual(stats.bound, 0)
        self.assertEqual(stats.aliases, 0)
        self.assertEqual(ExternalAccount.objects.count(), 1)
        self.assertEqual(ExternalAccountBinding.objects.count(), 1)
