import os

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project.settings.test')
django.setup()

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase

from project.apps.account.models import Account
from project.apps.external_accounts.models import (
    ExternalAccount,
    ExternalAccountAlias,
    ExternalAccountBinding,
    Institution,
    InstrumentKind,
    PlatformSlot,
)


class ExternalAccountModelTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='pass')
        self.citic = Institution.objects.create(code='CITIC', name='中信银行')
        self.alipay = Institution.objects.create(code='ALIPAY', name='支付宝', is_platform=True)
        self.account = Account.objects.create(
            account='Liabilities:CreditCard:Bank:CITIC:C6428', owner=self.user
        )

    def _card(self, **kwargs):
        defaults = {
            'owner': self.user,
            'institution': self.citic,
            'kind': InstrumentKind.CREDIT_CARD,
            'tail': '6428',
        }
        defaults.update(kwargs)
        return ExternalAccount(**defaults)

    def test_card_canonical_name_is_generated(self):
        card = self._card()
        card.full_clean()
        card.save()
        self.assertEqual(card.canonical_name, '中信银行信用卡(6428)')

    def test_platform_account_uses_display_name(self):
        wallet = ExternalAccount(
            owner=self.user,
            institution=self.alipay,
            kind=InstrumentKind.WALLET,
            display_name='支付宝余额',
            slot=PlatformSlot.ALIPAY_BALANCE,
        )
        wallet.full_clean()
        self.assertEqual(wallet.canonical_name, '支付宝余额')

    def test_card_requires_four_digit_tail(self):
        with self.assertRaises(ValidationError):
            self._card(tail='').full_clean()
        with self.assertRaises(ValidationError):
            self._card(tail='123').full_clean()
        with self.assertRaises(ValidationError):
            self._card(tail='abcd').full_clean()

    def test_non_card_rejects_tail(self):
        wallet = ExternalAccount(
            owner=self.user, institution=self.alipay, kind=InstrumentKind.WALLET, tail='1234'
        )
        with self.assertRaises(ValidationError):
            wallet.full_clean()

    def test_identity_is_unique_per_owner(self):
        self._card().save()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self._card().save()

    def test_slot_is_unique_per_owner(self):
        ExternalAccount.objects.create(
            owner=self.user,
            institution=self.alipay,
            kind=InstrumentKind.WALLET,
            slot=PlatformSlot.ALIPAY_BALANCE,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ExternalAccount.objects.create(
                    owner=self.user,
                    institution=self.alipay,
                    kind=InstrumentKind.WALLET,
                    slot=PlatformSlot.ALIPAY_BALANCE,
                )

    def test_alias_text_is_unique_per_owner(self):
        card = self._card()
        card.save()
        ExternalAccountAlias.objects.create(owner=self.user, external_account=card, text='6428')
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ExternalAccountAlias.objects.create(owner=self.user, external_account=card, text='6428')

    def test_binding_is_one_to_one(self):
        card = self._card()
        card.save()
        other = ExternalAccount.objects.create(
            owner=self.user,
            institution=self.citic,
            kind=InstrumentKind.DEBIT_CARD,
            tail='8837',
        )
        ExternalAccountBinding.objects.create(
            owner=self.user, external_account=card, account=self.account
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ExternalAccountBinding.objects.create(
                    owner=self.user, external_account=other, account=self.account
                )

    def test_bound_account_cannot_be_deleted(self):
        card = self._card()
        card.save()
        ExternalAccountBinding.objects.create(
            owner=self.user, external_account=card, account=self.account
        )
        with self.assertRaises(ProtectedError):
            self.account.delete()
