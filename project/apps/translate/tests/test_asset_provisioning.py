"""解析成功后自动补齐资产账户与资产映射。"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

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
from project.apps.maps.models import Assets, Expense
from project.apps.translate.services.asset_provisioning import (
    provision_asset_account,
    provision_missing_asset_accounts,
)
from project.apps.translate.services.steps import ParseStep
from project.apps.translate.utils import ASSETS_OTHER


def _seed_institutions():
    cmb = Institution.objects.create(code='CMB', name='招商银行')
    InstitutionAlias.objects.create(institution=cmb, text='招商银行')
    psbc = Institution.objects.create(code='PSBC', name='中国邮政储蓄银行')
    InstitutionAlias.objects.create(institution=psbc, text='中国邮政储蓄银行')
    Institution.objects.create(code='ALIPAY', name='支付宝', is_platform=True)


@pytest.fixture
def institutions(db):
    _seed_institutions()


@pytest.fixture
def own_mappings(user):
    """用户已有自己的映射数据（非官方模板模式）。"""
    Expense.objects.create(owner=user, key='其他', enable=True)
    return user


@pytest.mark.django_db
class TestProvisionAssetAccount:
    def test_creates_account_external_identity_and_mapping(self, user, institutions, own_mappings):
        text = '招商银行储蓄卡(8837)'

        result = provision_asset_account(user.id, text, bill='alipay')

        assert result is not None
        assert result.account == 'Assets:Savings:Bank:CMB:C8837'
        assert result.account_created is True

        account = Account.objects.get(owner=user, account=result.account)
        # 父账户由 Account.save 自动补齐
        assert Account.objects.filter(owner=user, account='Assets:Savings:Bank:CMB').exists()

        mapping = Assets.objects.get(owner=user, key=text)
        assert mapping.assets_id == account.id
        assert mapping.enable is True

        external = ExternalAccount.objects.get(
            owner=user, kind=InstrumentKind.DEBIT_CARD, tail='8837'
        )
        assert external.slot == ''
        assert ExternalAccountAlias.objects.get(owner=user, text=text).external_account_id == external.id
        assert ExternalAccountBinding.objects.get(external_account=external).account_id == account.id

    def test_is_idempotent(self, user, institutions, own_mappings):
        text = '招商银行储蓄卡(8837)'

        first = provision_asset_account(user.id, text, bill='alipay')
        second = provision_asset_account(user.id, text, bill='alipay')

        assert first.account == second.account
        assert second.account_created is False
        assert Account.objects.filter(owner=user, account=first.account).count() == 1
        assert Assets.objects.filter(owner=user, key=text).count() == 1
        assert ExternalAccount.objects.filter(owner=user, tail='8837').count() == 1

    def test_skips_card_without_tail(self, user, institutions):
        result = provision_asset_account(user.id, '招商银行储蓄卡', bill='alipay')

        assert result is None
        assert not Account.objects.filter(owner=user).exists()
        assert not Assets.objects.filter(owner=user).exists()

    def test_provisions_platform_slot(self, user, institutions, own_mappings):
        result = provision_asset_account(user.id, '余额', bill='alipay')

        assert result is not None
        assert result.account == 'Assets:Savings:Web:AliPay'
        external = ExternalAccount.objects.get(owner=user, slot=PlatformSlot.ALIPAY_BALANCE)
        assert external.kind == InstrumentKind.WALLET
        assert external.product == 'balance'
        assert Assets.objects.get(owner=user, key='余额').assets.account == result.account

    def test_long_key_still_creates_account_without_mapping(self, user, institutions, own_mappings):
        text = '中国邮政储蓄银行储蓄卡(1234)'
        assert len(text) > 16

        result = provision_asset_account(user.id, text, bill='alipay')

        assert result is not None
        assert Account.objects.filter(owner=user, account=result.account).exists()
        assert not Assets.objects.filter(owner=user, key=text).exists()
        assert ExternalAccountAlias.objects.filter(owner=user, text=text).exists()

    def test_template_mode_keeps_official_templates(self, user, institutions):
        # 用户没有任何自有映射：只写外部账户身份，不建 Assets 映射
        result = provision_asset_account(user.id, '招商银行储蓄卡(8837)', bill='alipay')

        assert result is not None
        assert Account.objects.filter(owner=user, account=result.account).exists()
        assert ExternalAccountBinding.objects.filter(owner=user, account__account=result.account).exists()
        assert not Assets.objects.filter(owner=user).exists()


def _alipay_row(**overrides):
    row = {
        'transaction_time': '2026-04-01 10:00:00',
        'transaction_category': '日用百货',
        'counterparty': '测试商家',
        'commodity': '测试商品',
        'transaction_type': '支出',
        'amount': 10.0,
        'payment_method': '招商银行储蓄卡(8837)',
        'transaction_status': '交易成功',
        'notes': '/',
        'bill_identifier': 'alipay',
        'uuid': 'ORDER-1',
    }
    row.update(overrides)
    return row


def _parsed(account=ASSETS_OTHER):
    return {
        'uuid': 'ORDER-1',
        'amount': '10.00',
        'expense': 'Expenses:Shopping',
        'account': account,
        'note': '测试商品',
        'links': [],
    }


@pytest.mark.django_db
class TestParseStepProvisioning:
    def test_unmatched_asset_leg_is_replaced(self, user, institutions):
        context = {
            'owner_id': user.id,
            'user': user,
            'config': SimpleNamespace(auto_provision_assets=True, ai_model='None'),
            'prefilter_bill': [_alipay_row()],
            'bill_type': 'alipay',
            'args': {'write': False},
            'parsed_data': [],
        }
        with patch(
            'project.apps.translate.services.steps.build_ledger_index_for_user',
            return_value={},
        ), patch(
            'project.apps.translate.services.steps.single_parse_transaction',
            side_effect=lambda row, *_a, **_k: _parsed(),
        ):
            result = ParseStep().execute(context)

        assert result['parsed_data'][0]['account'] == 'Assets:Savings:Bank:CMB:C8837'

    def test_disabled_flag_keeps_assets_other(self, user, institutions):
        context = {
            'owner_id': user.id,
            'user': user,
            'config': SimpleNamespace(auto_provision_assets=False, ai_model='None'),
            'prefilter_bill': [_alipay_row()],
            'bill_type': 'alipay',
            'args': {'write': False},
            'parsed_data': [],
        }
        with patch(
            'project.apps.translate.services.steps.build_ledger_index_for_user',
            return_value={},
        ), patch(
            'project.apps.translate.services.steps.single_parse_transaction',
            side_effect=lambda row, *_a, **_k: _parsed(),
        ):
            result = ParseStep().execute(context)

        assert result['parsed_data'][0]['account'] == ASSETS_OTHER
        assert not Account.objects.filter(owner=user).exists()

    def test_balance_rows_are_not_guessed(self, user, institutions):
        changed = provision_missing_asset_accounts(
            [{'_original_row': _alipay_row(transaction_type='/'), 'account': ASSETS_OTHER}],
            'alipay',
            user.id,
        )

        assert changed == 0
        assert not Account.objects.filter(owner=user).exists()
