import os

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project.settings.test')
django.setup()

from django.test import TestCase

from project.apps.external_accounts.hints import (
    extract_tail,
    infer_external_account_hint,
    infer_hint_from_account_path,
)
from project.apps.external_accounts.models import Institution, InstitutionAlias, InstrumentKind, PlatformSlot


class HintExtractionTest(TestCase):
    def setUp(self):
        definitions = [
            ('BOC', '中国银行', ['中国银行', '中行']),
            ('ICBC', '中国工商银行', ['中国工商银行', '工商银行', '工行']),
            ('CITIC', '中信银行', ['中信银行', '中信']),
            ('CMB', '招商银行', ['招商银行', '招行']),
            ('ALIPAY', '支付宝', ['支付宝']),
            ('WECHAT', '微信', ['微信', '微信支付']),
        ]
        for code, name, aliases in definitions:
            institution = Institution.objects.create(code=code, name=name, is_platform=code in ('ALIPAY', 'WECHAT'))
            for text in aliases:
                InstitutionAlias.objects.create(institution=institution, text=text)

    def test_bank_debit_card(self):
        hint = infer_external_account_hint('中国工商银行储蓄卡(1234)', bill='ICBC_Debit')

        self.assertEqual(hint.institution_code, 'ICBC')
        self.assertEqual(hint.kind, InstrumentKind.DEBIT_CARD)
        self.assertEqual(hint.tail, '1234')
        self.assertEqual(hint.slot, '')

    def test_bank_credit_card(self):
        hint = infer_external_account_hint('招商银行信用卡(6428)', bill='CMB_Credit')

        self.assertEqual(hint.institution_code, 'CMB')
        self.assertEqual(hint.kind, InstrumentKind.CREDIT_CARD)
        self.assertEqual(hint.tail, '6428')

    def test_installment_suffix_does_not_change_identity(self):
        hint = infer_external_account_hint('中信银行信用卡分期(6428) 3期', bill='CMB_Credit')

        self.assertEqual(hint.institution_code, 'CITIC')
        self.assertEqual(hint.kind, InstrumentKind.CREDIT_CARD)
        self.assertEqual(hint.tail, '6428')

    def test_fullwidth_parentheses_tail(self):
        self.assertEqual(extract_tail('中信银行信用卡（6428）'), '6428')

    def test_alipay_fund_slot(self):
        hint = infer_external_account_hint('余额宝-自动转入', bill='alipay')

        self.assertEqual(hint.institution_code, 'ALIPAY')
        self.assertEqual(hint.kind, InstrumentKind.FUND)
        self.assertEqual(hint.slot, PlatformSlot.ALIPAY_FUND)
        self.assertEqual(hint.tail, '')

    def test_alipay_huabei_slot(self):
        hint = infer_external_account_hint('花呗主动还款-2022年09月账单', bill='alipay')

        self.assertEqual(hint.institution_code, 'ALIPAY')
        self.assertEqual(hint.kind, InstrumentKind.CREDIT_LINE)
        self.assertEqual(hint.slot, PlatformSlot.ALIPAY_HUABEI)

    def test_alipay_balance_does_not_match_fund(self):
        hint = infer_external_account_hint('账户余额', bill='alipay')

        self.assertEqual(hint.slot, PlatformSlot.ALIPAY_BALANCE)
        self.assertEqual(hint.kind, InstrumentKind.WALLET)

    def test_wechat_wallet_and_fund(self):
        wallet = infer_external_account_hint('零钱提现', bill='wechat')
        fund = infer_external_account_hint('零钱通转出', bill='wechat')

        self.assertEqual(wallet.slot, PlatformSlot.WECHAT_PAY)
        self.assertEqual(wallet.kind, InstrumentKind.WALLET)
        self.assertEqual(fund.slot, PlatformSlot.WECHAT_FUND)
        self.assertEqual(fund.kind, InstrumentKind.FUND)

    def test_unknown_bank_card_keeps_institution_empty(self):
        hint = infer_external_account_hint('某某银行储蓄卡(9999)', bill='alipay')

        self.assertEqual(hint.institution_code, '')
        self.assertEqual(hint.kind, InstrumentKind.DEBIT_CARD)
        self.assertEqual(hint.tail, '9999')

    def test_unknown_plain_text_is_empty(self):
        hint = infer_external_account_hint('一笔普通消费', bill='')

        self.assertTrue(hint.is_empty)

    def test_hint_from_debit_card_path(self):
        hint = infer_hint_from_account_path('Assets:Savings:Bank:BOC:C0814')

        self.assertEqual(hint.institution_code, 'BOC')
        self.assertEqual(hint.kind, InstrumentKind.DEBIT_CARD)
        self.assertEqual(hint.tail, '0814')

    def test_hint_from_credit_card_path(self):
        hint = infer_hint_from_account_path('Liabilities:CreditCard:Bank:CITIC:C6428')

        self.assertEqual(hint.institution_code, 'CITIC')
        self.assertEqual(hint.kind, InstrumentKind.CREDIT_CARD)
        self.assertEqual(hint.tail, '6428')

    def test_hint_from_web_path(self):
        hint = infer_hint_from_account_path('Assets:Savings:Web:AliFund')

        self.assertEqual(hint.institution_code, 'ALIPAY')
        self.assertEqual(hint.kind, InstrumentKind.FUND)
        self.assertEqual(hint.slot, PlatformSlot.ALIPAY_FUND)

    def test_hint_from_unknown_path_is_empty(self):
        self.assertTrue(infer_hint_from_account_path('Assets:Savings:Recharge:HaLuo').is_empty)
