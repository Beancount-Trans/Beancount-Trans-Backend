import os

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project.settings.test')
django.setup()

from django.contrib.auth.models import User
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
from project.apps.external_accounts.resolver import (
    CardRequirement,
    ResolutionStatus,
    SlotRequirement,
    AliasRequirement,
    resolve,
    resolve_alias,
    resolve_card,
    resolve_slot,
)


class ResolverTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='pass')
        self.alipay = Institution.objects.create(code='ALIPAY', name='支付宝', is_platform=True)
        self.citic = Institution.objects.create(code='CITIC', name='中信银行')

    def _account(self, path, enable=True):
        return Account.objects.create(account=path, owner=self.user, enable=enable)

    def _external(self, **kwargs):
        defaults = {
            'owner': self.user,
            'institution': self.alipay,
            'kind': InstrumentKind.WALLET,
        }
        defaults.update(kwargs)
        return ExternalAccount.objects.create(**defaults)

    def _bind(self, external, account):
        return ExternalAccountBinding.objects.create(
            owner=self.user, external_account=external, account=account
        )

    def test_slot_matched(self):
        account = self._account('Assets:Savings:Web:AliPay')
        external = self._external(slot=PlatformSlot.ALIPAY_BALANCE)
        self._bind(external, account)

        result = resolve_slot(self.user.id, PlatformSlot.ALIPAY_BALANCE)

        self.assertEqual(result.status, ResolutionStatus.MATCHED)
        self.assertEqual(result.account, 'Assets:Savings:Web:AliPay')

    def test_slot_not_configured(self):
        result = resolve_slot(self.user.id, PlatformSlot.ALIPAY_FUND)

        self.assertEqual(result.status, ResolutionStatus.UNMATCHED)
        self.assertEqual(result.reason, 'slot_not_configured')

    def test_slot_without_binding(self):
        self._external(slot=PlatformSlot.ALIPAY_BALANCE)

        result = resolve_slot(self.user.id, PlatformSlot.ALIPAY_BALANCE)

        self.assertEqual(result.status, ResolutionStatus.UNMATCHED)
        self.assertEqual(result.reason, 'account_not_bound')
        self.assertIsNotNone(result.external_account)

    def test_slot_disabled_ledger_account(self):
        account = self._account('Assets:Savings:Web:AliPay', enable=False)
        external = self._external(slot=PlatformSlot.ALIPAY_BALANCE)
        self._bind(external, account)

        result = resolve_slot(self.user.id, PlatformSlot.ALIPAY_BALANCE)

        self.assertEqual(result.status, ResolutionStatus.DISABLED)
        self.assertEqual(result.reason, 'account_disabled')

    def test_card_with_tail_matched(self):
        account = self._account('Liabilities:CreditCard:Bank:CITIC:C6428')
        external = self._external(
            institution=self.citic, kind=InstrumentKind.CREDIT_CARD, tail='6428'
        )
        self._bind(external, account)

        result = resolve_card(self.user.id, 'CITIC', InstrumentKind.CREDIT_CARD, '6428')

        self.assertEqual(result.status, ResolutionStatus.MATCHED)
        self.assertEqual(result.account, 'Liabilities:CreditCard:Bank:CITIC:C6428')

    def test_card_without_tail_single_candidate(self):
        account = self._account('Assets:Savings:Bank:NBCB:C8837')
        external = self._external(
            institution=self.citic, kind=InstrumentKind.DEBIT_CARD, tail='8837'
        )
        self._bind(external, account)

        result = resolve_card(self.user.id, 'CITIC', InstrumentKind.DEBIT_CARD)

        self.assertEqual(result.status, ResolutionStatus.MATCHED)
        self.assertEqual(result.account, 'Assets:Savings:Bank:NBCB:C8837')

    def test_card_without_tail_multiple_candidates_is_ambiguous(self):
        account = self._account('Assets:Savings:Bank:NBCB:C8837')
        other_account = self._account('Assets:Savings:Bank:NBCB:C1234')
        first = self._external(institution=self.citic, kind=InstrumentKind.DEBIT_CARD, tail='8837')
        second = self._external(institution=self.citic, kind=InstrumentKind.DEBIT_CARD, tail='1234')
        self._bind(first, account)
        self._bind(second, other_account)

        result = resolve_card(self.user.id, 'CITIC', InstrumentKind.DEBIT_CARD)

        self.assertEqual(result.status, ResolutionStatus.AMBIGUOUS)
        self.assertEqual(len(result.candidates), 2)
        self.assertIsNone(result.account)

    def test_unknown_institution(self):
        result = resolve_card(self.user.id, 'NOT_A_BANK', InstrumentKind.DEBIT_CARD, '1234')

        self.assertEqual(result.status, ResolutionStatus.UNMATCHED)
        self.assertEqual(result.reason, 'unknown_institution')

    def test_alias_matched(self):
        account = self._account('Assets:Savings:Bank:NBCB:C8837')
        external = self._external(institution=self.citic, kind=InstrumentKind.DEBIT_CARD, tail='8837')
        self._bind(external, account)
        ExternalAccountAlias.objects.create(owner=self.user, external_account=external, text='8837')

        result = resolve_alias(self.user.id, '8837')

        self.assertEqual(result.status, ResolutionStatus.MATCHED)
        self.assertEqual(result.account, 'Assets:Savings:Bank:NBCB:C8837')

    def test_alias_not_found(self):
        result = resolve_alias(self.user.id, '9999')

        self.assertEqual(result.status, ResolutionStatus.UNMATCHED)
        self.assertEqual(result.reason, 'alias_not_found')

    def test_alias_to_disabled_external_account(self):
        external = self._external(enable=False)
        ExternalAccountAlias.objects.create(owner=self.user, external_account=external, text='零钱')

        result = resolve_alias(self.user.id, '零钱')

        self.assertEqual(result.status, ResolutionStatus.DISABLED)
        self.assertEqual(result.reason, 'external_account_disabled')

    def test_unsupported_requirement(self):
        with self.assertRaises(TypeError):
            resolve(self.user.id, object())
