"""账户引用索引测试"""
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model

from project.apps.account.ledger_refs import (
    account_reference_count,
    build_account_index,
    declared_accounts,
    referenced_accounts,
)
from project.utils.file import BeanFileManager

User = get_user_model()

pytestmark = pytest.mark.django_db


@pytest.fixture
def assets_dir(tmp_path, monkeypatch):
    base = tmp_path / 'Assets'
    base.mkdir()
    monkeypatch.setattr(settings, 'ASSETS_BASE_PATH', str(base))
    return base


@pytest.fixture
def user(assets_dir):
    return User.objects.create_user(username='refsuser', password='testpass123')


def _write(user, relative_path, content):
    path = Path(BeanFileManager.get_user_assets_path(user)) / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')
    return path


def _build_ledger(user):
    _write(user, 'main.bean', 'include "trans/main.bean"\ninclude "account/assets.bean"\n')
    _write(user, 'trans/main.bean', 'include "collect.bean"\ninclude "bills/x.bean"\n')
    _write(
        user,
        'trans/collect.bean',
        '2000-01-01 open Assets:Bank CNY\n'
        '2000-01-01 open Expenses:Food\n'
        '2000-01-01 open Liabilities:Card\n'
        '\n'
        '2025-01-01 * "午餐"\n'
        '  Assets:Bank  -10.00 CNY\n'
        '  Expenses:Food  10.00 CNY\n'
        '\n'
        '2025-01-02 balance Liabilities:Card -100.00 CNY\n'
        '2025-01-03 note Assets:Bank "测试"\n',
    )
    _write(
        user,
        'trans/bills/x.bean',
        '2025-02-01 * "账单"\n'
        '  Assets:Bank  -5.00 CNY\n'
        '  Expenses:Food  5.00 CNY\n',
    )
    _write(user, 'account/assets.bean', '2000-01-01 open Assets:Template\n')
    _write(user, 'unused/y.bean', '2000-01-01 open Assets:Unused\n')


def test_index_only_contains_effective_files(user):
    _build_ledger(user)

    index = build_account_index(user)

    assert set(index) == {
        'Assets:Bank',
        'Expenses:Food',
        'Liabilities:Card',
        'Assets:Template',
    }
    files = {location['file'] for locations in index.values() for location in locations}
    assert 'unused/y.bean' not in files
    assert 'trans/bills/x.bean' in files
    assert 'account/assets.bean' in files


def test_declared_accounts_are_open_only(user):
    _build_ledger(user)

    assert declared_accounts(user) == {
        'Assets:Bank',
        'Expenses:Food',
        'Liabilities:Card',
        'Assets:Template',
    }


def test_reference_count_covers_transactions_and_directives(user):
    _build_ledger(user)

    counts = account_reference_count(user)

    # open + 交易 + note
    assert counts['Assets:Bank'] == 4
    # open + 两笔交易
    assert counts['Expenses:Food'] == 3
    # open + balance
    assert counts['Liabilities:Card'] == 2


def test_missing_main_bean_returns_empty(user):
    Path(BeanFileManager.get_main_bean_path(user)).unlink()

    assert build_account_index(user) == {}
    assert referenced_accounts(user) == set()
    assert declared_accounts(user) == set()
