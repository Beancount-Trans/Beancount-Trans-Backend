"""同步后自动导入账本新增账户

覆盖开关默认开启、关闭后跳过导入、开启后只导入显式 open 的账户，以及设置接口。
所有测试将 settings.ASSETS_BASE_PATH 指向 tmp_path，避免污染真实 Assets 目录。
"""

from unittest.mock import patch

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from project.apps.account.account_reconcile import import_declared_accounts
from project.apps.account.models import Account
from project.apps.git_repository.models import GitRepository
from project.apps.git_repository.services import PlatformGitService

User = get_user_model()

pytestmark = pytest.mark.django_db


def _create_user(username: str):
    return User.objects.create_user(username=username, password='x')


def _create_git_repo(user, repo_name: str, **overrides):
    payload = {
        'owner': user,
        'repo_name': repo_name,
        'remote_ssh_url': 'git@github.com:org/repo.git',
        'deploy_key_private': '-----BEGIN PRIVATE KEY-----\nMII\n-----END PRIVATE KEY-----\n',
        'deploy_key_public': 'ssh-rsa AAAAB3',
        'setup_mode': 'link',
        'provider': 'github',
    }
    payload.update(overrides)
    return GitRepository.objects.create(**payload)


def _prepare(monkeypatch, tmp_path, username: str, repo_name: str, ledger: str):
    monkeypatch.setattr(settings, 'ASSETS_BASE_PATH', str(tmp_path))
    user = _create_user(username)
    git_repo = _create_git_repo(user, repo_name)

    user_assets_path = tmp_path / repo_name
    (user_assets_path / 'trans').mkdir(parents=True)
    (user_assets_path / 'trans' / 'main.bean').write_text(ledger, encoding='utf-8')
    (user_assets_path / 'main.bean').write_text(
        'include "trans/main.bean"\n', encoding='utf-8'
    )
    (user_assets_path / '.git').mkdir()
    (user_assets_path / '.git' / 'HEAD').write_text(
        'ref: refs/heads/main', encoding='utf-8'
    )

    svc = PlatformGitService()
    svc.assets_base_path = tmp_path
    return user, git_repo, svc, user_assets_path


LEDGER = '2000-01-01 open Assets:Wallet CNY\n'


@patch('project.apps.git_repository.services.PlatformGitService._pull_repository')
def test_sync_skips_import_when_disabled(mock_pull, monkeypatch, tmp_path):
    user, git_repo, svc, _path = _prepare(
        monkeypatch, tmp_path, 'auto_off_user', 'auto-off-assets', LEDGER
    )
    git_repo.auto_import_ledger_accounts = False
    git_repo.save()

    result = svc.sync_repository(user)

    assert result['status'] == 'success'
    assert result['imported_accounts'] == []
    assert not Account.objects.filter(owner=user, account='Assets:Wallet').exists()


def test_auto_import_enabled_by_default():
    user = _create_user('auto_default_user')
    git_repo = _create_git_repo(user, 'auto-default-assets')

    assert git_repo.auto_import_ledger_accounts is True


@patch('project.apps.git_repository.services.PlatformGitService._pull_repository')
def test_sync_imports_opened_accounts_when_enabled(mock_pull, monkeypatch, tmp_path):
    user, git_repo, svc, _path = _prepare(
        monkeypatch,
        tmp_path,
        'auto_on_user',
        'auto-on-assets',
        LEDGER + '2000-01-01 open Expenses:Food CNY\n',
    )
    git_repo.auto_import_ledger_accounts = True
    git_repo.save()

    result = svc.sync_repository(user)

    assert result['status'] == 'success'
    assert set(result['imported_accounts']) == {'Assets:Wallet', 'Expenses:Food'}
    assert Account.objects.filter(owner=user, account='Assets:Wallet').exists()
    assert Account.objects.filter(owner=user, account='Expenses:Food').exists()

    # 再次同步不重复导入
    result = svc.sync_repository(user)
    assert result['imported_accounts'] == []
    assert Account.objects.filter(owner=user, account='Assets:Wallet').count() == 1


@patch('project.apps.git_repository.services.PlatformGitService._pull_repository')
def test_sync_continues_when_import_fails(mock_pull, monkeypatch, tmp_path):
    user, git_repo, svc, _path = _prepare(
        monkeypatch, tmp_path, 'auto_fail_user', 'auto-fail-assets', LEDGER
    )
    git_repo.auto_import_ledger_accounts = True
    git_repo.save()

    with patch(
        'project.apps.account.account_reconcile.import_declared_accounts',
        side_effect=RuntimeError('boom'),
    ):
        result = svc.sync_repository(user)

    assert result['status'] == 'success'
    assert '自动导入账本账户失败' in result['ledger_warning']


def test_import_declared_accounts_does_not_duplicate_existing(monkeypatch, tmp_path):
    user, _git_repo, _svc, _path = _prepare(
        monkeypatch,
        tmp_path,
        'auto_skip_user',
        'auto-skip-assets',
        '2000-01-01 open Assets:Wallet CNY\n'
        '2000-01-01 open Assets:Parent CNY\n',
    )
    # 平台已有 Assets:Parent 作为子账户的父记录，账本也声明 open，不应重复创建
    parent = Account(account='Assets:Parent:Child', owner=user, enable=True)
    parent._skip_ledger_sync = True
    parent.save()

    result = import_declared_accounts(user)

    assert result['created'] == ['Assets:Wallet']
    assert result['skipped'] == []
    assert result['errors'] == []
    assert Account.objects.filter(owner=user, account='Assets:Parent').count() == 1


def test_import_declared_accounts_requires_git_user(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'ASSETS_BASE_PATH', str(tmp_path))
    user = _create_user('auto_non_git_user')

    result = import_declared_accounts(user)

    assert result == {'created': [], 'skipped': [], 'errors': []}


def test_settings_api_toggles_auto_import(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, 'ASSETS_BASE_PATH', str(tmp_path))
    user = _create_user('auto_settings_user')
    git_repo = _create_git_repo(user, 'auto-settings-assets')

    client = APIClient()
    client.force_authenticate(user=user)

    response = client.patch(
        reverse('git-repository-update-settings'),
        {'auto_import_ledger_accounts': True},
        format='json',
    )

    assert response.status_code == 200, response.data
    assert response.data['auto_import_ledger_accounts'] is True
    git_repo.refresh_from_db()
    assert git_repo.auto_import_ledger_accounts is True


def test_settings_api_requires_git_repository():
    user = _create_user('auto_settings_404_user')
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.patch(
        reverse('git-repository-update-settings'),
        {'auto_import_ledger_accounts': True},
        format='json',
    )

    assert response.status_code == 404
