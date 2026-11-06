"""Git 同步时从账本 open 回写平台账户币种

账本托管的账户在平台上不允许改币种，因此同步必须把用户区 open 声明的币种
回写到平台；平台区（trans/）的 open 由平台自己写入，不参与回写。
所有测试将 settings.ASSETS_BASE_PATH 指向 tmp_path，避免污染真实 Assets 目录。
"""

from unittest.mock import patch

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model

from project.apps.account.account_reconcile import sync_declared_currencies
from project.apps.account.models import Account
from project.apps.git_repository.models import GitRepository
from project.apps.git_repository.services import PlatformGitService

User = get_user_model()

pytestmark = pytest.mark.django_db


def _create_user(username: str):
    return User.objects.create_user(username=username, password='x')


def _create_git_repo(user, repo_name: str):
    return GitRepository.objects.create(
        owner=user,
        repo_name=repo_name,
        remote_ssh_url='git@github.com:org/repo.git',
        deploy_key_private=(
            '-----BEGIN PRIVATE KEY-----\nMII\n-----END PRIVATE KEY-----\n'
        ),
        deploy_key_public='ssh-rsa AAAAB3',
        setup_mode='link',
        provider='github',
    )


def _prepare(
    monkeypatch,
    tmp_path,
    username: str,
    repo_name: str,
    *,
    user_ledger: str = '',
    trans_ledger: str = '',
):
    monkeypatch.setattr(settings, 'ASSETS_BASE_PATH', str(tmp_path))
    user = _create_user(username)
    git_repo = _create_git_repo(user, repo_name)

    root = tmp_path / repo_name
    (root / 'trans').mkdir(parents=True)
    (root / 'account').mkdir()
    (root / 'main.bean').write_text(
        'include "trans/main.bean"\ninclude "account/assets.bean"\n',
        encoding='utf-8',
    )
    (root / 'trans' / 'main.bean').write_text(trans_ledger, encoding='utf-8')
    (root / 'account' / 'assets.bean').write_text(user_ledger, encoding='utf-8')
    (root / '.git').mkdir()
    (root / '.git' / 'HEAD').write_text('ref: refs/heads/main', encoding='utf-8')

    svc = PlatformGitService()
    svc.assets_base_path = tmp_path
    return user, git_repo, svc


def _platform_account(user, account_path: str, currency: str = '') -> Account:
    """创建平台账户；跳过账本写入，避免测试账本被平台改动"""
    account = Account(
        account=account_path, owner=user, enable=True, currency=currency
    )
    account._skip_ledger_sync = True
    account.save()
    return account


@patch('project.apps.git_repository.services.PlatformGitService._pull_repository')
def test_sync_updates_currency_from_user_region_open(
    mock_pull, monkeypatch, tmp_path
):
    user, _git_repo, svc = _prepare(
        monkeypatch,
        tmp_path,
        'currency_sync_user',
        'currency-sync-assets',
        user_ledger='2000-01-01 open Assets:Wallet COIN\n',
    )
    _platform_account(user, 'Assets:Wallet', 'CNY')

    result = svc.sync_repository(user)

    assert result['status'] == 'success'
    assert result['updated_account_currencies'] == [
        {'account': 'Assets:Wallet', 'old': 'CNY', 'new': 'COIN'}
    ]
    account = Account.objects.get(owner=user, account='Assets:Wallet')
    assert account.currency == 'COIN'


@patch('project.apps.git_repository.services.PlatformGitService._pull_repository')
def test_sync_clears_currency_when_open_declares_none(
    mock_pull, monkeypatch, tmp_path
):
    user, _git_repo, svc = _prepare(
        monkeypatch,
        tmp_path,
        'currency_clear_user',
        'currency-clear-assets',
        user_ledger='2000-01-01 open Assets:Wallet\n',
    )
    _platform_account(user, 'Assets:Wallet', 'CNY')

    result = svc.sync_repository(user)

    assert result['status'] == 'success'
    assert result['updated_account_currencies'] == [
        {'account': 'Assets:Wallet', 'old': 'CNY', 'new': ''}
    ]
    account = Account.objects.get(owner=user, account='Assets:Wallet')
    assert account.currency == ''


@patch('project.apps.git_repository.services.PlatformGitService._pull_repository')
def test_sync_ignores_platform_region_open(mock_pull, monkeypatch, tmp_path):
    user, _git_repo, svc = _prepare(
        monkeypatch,
        tmp_path,
        'currency_trans_user',
        'currency-trans-assets',
        trans_ledger='2000-01-01 open Assets:Wallet COIN\n',
    )
    _platform_account(user, 'Assets:Wallet', 'CNY')

    result = svc.sync_repository(user)

    assert result['status'] == 'success'
    assert result['updated_account_currencies'] == []
    account = Account.objects.get(owner=user, account='Assets:Wallet')
    assert account.currency == 'CNY'


@patch('project.apps.git_repository.services.PlatformGitService._pull_repository')
def test_sync_skips_accounts_without_currency_support(
    mock_pull, monkeypatch, tmp_path
):
    user, _git_repo, svc = _prepare(
        monkeypatch,
        tmp_path,
        'currency_expense_user',
        'currency-expense-assets',
        user_ledger='2000-01-01 open Expenses:Food CNY\n',
    )
    _platform_account(user, 'Expenses:Food')

    result = svc.sync_repository(user)

    assert result['status'] == 'success'
    assert result['updated_account_currencies'] == []
    account = Account.objects.get(owner=user, account='Expenses:Food')
    assert account.currency == ''


@patch('project.apps.git_repository.services.PlatformGitService._pull_repository')
def test_sync_imports_currency_with_new_account(mock_pull, monkeypatch, tmp_path):
    user, git_repo, svc = _prepare(
        monkeypatch,
        tmp_path,
        'currency_import_user',
        'currency-import-assets',
        user_ledger=(
            '2000-01-01 open Assets:Wallet COIN,CNY\n'
            '2000-01-01 open Expenses:Food CNY\n'
        ),
    )
    git_repo.auto_import_ledger_accounts = True
    git_repo.save()

    result = svc.sync_repository(user)

    assert result['status'] == 'success'
    assert set(result['imported_accounts']) == {'Assets:Wallet', 'Expenses:Food'}
    assert (
        Account.objects.get(owner=user, account='Assets:Wallet').currency
        == 'COIN,CNY'
    )
    assert Account.objects.get(owner=user, account='Expenses:Food').currency == ''


@patch('project.apps.git_repository.services.PlatformGitService._pull_repository')
def test_sync_continues_when_currency_sync_fails(
    mock_pull, monkeypatch, tmp_path
):
    user, _git_repo, svc = _prepare(
        monkeypatch,
        tmp_path,
        'currency_fail_user',
        'currency-fail-assets',
        user_ledger='2000-01-01 open Assets:Wallet COIN\n',
    )
    _platform_account(user, 'Assets:Wallet', 'CNY')

    with patch(
        'project.apps.account.account_reconcile.sync_declared_currencies',
        side_effect=RuntimeError('boom'),
    ):
        result = svc.sync_repository(user)

    assert result['status'] == 'success'
    assert '同步账本账户币种失败' in result['ledger_warning']


def test_sync_declared_currencies_requires_git_user(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'ASSETS_BASE_PATH', str(tmp_path))
    user = _create_user('currency_non_git_user')

    assert sync_declared_currencies(user) == {'updated': []}
