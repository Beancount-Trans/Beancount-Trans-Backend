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
    InstitutionAlias,
    InstrumentKind,
    PlatformSlot,
)
from project.apps.maps.models import Template, TemplateItem
from project.apps.translate.services.account_resolution import resolve_account_from_text


class AccountResolutionTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='owner', password='pass')
        self.boc = Institution.objects.create(code='BOC', name='中国银行')
        InstitutionAlias.objects.create(institution=self.boc, text='中国银行')

    def _account(self, path):
        return Account.objects.create(account=path, owner=self.user)

    def _bound_card(self, institution, kind, tail, account, alias_text):
        external = ExternalAccount.objects.create(
            owner=self.user, institution=institution, kind=kind, tail=tail
        )
        ExternalAccountBinding.objects.create(
            owner=self.user, external_account=external, account=account
        )
        ExternalAccountAlias.objects.create(
            owner=self.user, external_account=external, text=alias_text
        )
        return external

    def test_card_text_resolves_by_structured_identity(self):
        account = self._account('Assets:Savings:Bank:BOC:C0814')
        self._bound_card(self.boc, InstrumentKind.DEBIT_CARD, '0814', account, '0814')

        result = resolve_account_from_text(self.user.id, '中国银行储蓄卡(0814)', bill='BOC_Debit')

        self.assertTrue(result.ok)
        self.assertEqual(result.account, 'Assets:Savings:Bank:BOC:C0814')

    def test_tail_less_text_falls_back_to_alias_containment(self):
        institution = Institution.objects.create(code='NBCB', name='宁波银行')
        account = self._account('Assets:Savings:Bank:NBCB:C8837')
        self._bound_card(
            institution,
            InstrumentKind.DEBIT_CARD,
            '8837',
            account,
            '宁波银行储蓄卡(8837)',
        )

        result = resolve_account_from_text(self.user.id, '宁波银行储蓄卡', bill='alipay')

        self.assertTrue(result.ok)
        self.assertEqual(result.account, 'Assets:Savings:Bank:NBCB:C8837')

    def test_tail_less_text_with_two_cards_is_ambiguous(self):
        institution = Institution.objects.create(code='NBCB', name='宁波银行')
        first = self._account('Assets:Savings:Bank:NBCB:C8837')
        second = self._account('Assets:Savings:Bank:NBCB:C1234')
        self._bound_card(
            institution, InstrumentKind.DEBIT_CARD, '8837', first, '宁波银行储蓄卡(8837)'
        )
        self._bound_card(
            institution, InstrumentKind.DEBIT_CARD, '1234', second, '宁波银行储蓄卡(1234)'
        )

        result = resolve_account_from_text(self.user.id, '宁波银行储蓄卡', bill='alipay')

        self.assertFalse(result.ok)
        self.assertEqual(result.status, 'ambiguous')

    def _template_with_slot(self):
        template = Template.objects.create(
            name='官方资产映射', type='assets', is_official=True, owner=self.user
        )
        TemplateItem.objects.create(
            template=template,
            key='余额宝',
            account='Assets:Savings:Web:AliFund',
            institution_code='ALIPAY',
            external_kind='fund',
            slot=PlatformSlot.ALIPAY_FUND,
            display_name='支付宝余额宝',
        )

    def test_template_slot_is_used_when_account_exists(self):
        self._template_with_slot()
        self._account('Assets:Savings:Web:AliFund')

        result = resolve_account_from_text(self.user.id, '余额宝-自动转入', bill='alipay')

        self.assertTrue(result.ok)
        self.assertEqual(result.account, 'Assets:Savings:Web:AliFund')
        self.assertEqual(result.reason, 'template')

    def test_template_slot_does_not_invent_missing_account(self):
        self._template_with_slot()

        result = resolve_account_from_text(self.user.id, '余额宝-自动转入', bill='alipay')

        self.assertFalse(result.ok)

    def test_template_slot_used_for_anonymous(self):
        self._template_with_slot()

        result = resolve_account_from_text(None, '余额宝-自动转入', bill='alipay')

        self.assertTrue(result.ok)
        self.assertEqual(result.account, 'Assets:Savings:Web:AliFund')

    def test_unknown_text_is_unmatched(self):
        result = resolve_account_from_text(self.user.id, '一笔普通消费', bill='')

        self.assertFalse(result.ok)
        self.assertIn(result.reason, ('no_external_account', 'alias_not_found'))
