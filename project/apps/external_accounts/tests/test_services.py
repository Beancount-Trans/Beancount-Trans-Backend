import os

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project.settings.test')
django.setup()

from django.test import TestCase

from project.apps.external_accounts.models import Institution, InstitutionAlias
from project.apps.external_accounts.services import match_institution, normalize_text


class MatchInstitutionTest(TestCase):
    def setUp(self):
        self.boc = self._institution('BOC', '中国银行', ['中国银行', '中行'])
        self.icbc = self._institution('ICBC', '中国工商银行', ['中国工商银行', '工商银行', '工行'])
        self.citic = self._institution('CITIC', '中信银行', ['中信银行', '中信'])
        self.nbcb = self._institution('NBCB', '宁波银行', ['宁波银行'])
        self.alipay = self._institution('ALIPAY', '支付宝', ['支付宝', 'alipay'], is_platform=True)

    def _institution(self, code, name, aliases, is_platform=False):
        institution = Institution.objects.create(code=code, name=name, is_platform=is_platform)
        for text in aliases:
            InstitutionAlias.objects.create(institution=institution, text=text)
        return institution

    def test_exact_alias(self):
        self.assertEqual(match_institution('中信银行'), self.citic)

    def test_longest_alias_wins(self):
        self.assertEqual(match_institution('中信银行信用卡(6428)'), self.citic)

    def test_does_not_confuse_boc_with_icbc(self):
        self.assertEqual(match_institution('中国银行储蓄卡(0814)'), self.boc)
        self.assertEqual(match_institution('中国工商银行储蓄卡(1234)'), self.icbc)

    def test_short_alias_matches(self):
        self.assertEqual(match_institution('工行储蓄卡(1234)'), self.icbc)

    def test_platform_account(self):
        self.assertEqual(match_institution('支付宝余额'), self.alipay)

    def test_fullwidth_parentheses_are_normalized(self):
        self.assertEqual(normalize_text('宁波银行储蓄卡（8837）'), '宁波银行储蓄卡(8837)')
        self.assertEqual(match_institution('宁波银行储蓄卡（8837）'), self.nbcb)

    def test_unknown_returns_none(self):
        self.assertIsNone(match_institution('不存在的银行'))
        self.assertIsNone(match_institution(''))
