"""非 Git 账户改名测试"""
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from rest_framework import serializers

from project.apps.account.account_rename import rename_account
from project.apps.account.models import Account
from project.apps.account.serializers import AccountSerializer
from project.apps.git_repository.models import GitRepository
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
    return User.objects.create_user(username='renameuser', password='testpass123')


@pytest.fixture
def git_user(assets_dir):
    user = User.objects.create_user(username='renamegituser', password='testpass123')
    GitRepository.objects.create(
        owner=user,
        repo_name='renamegituser-assets',
        remote_ssh_url='git@example.com:org/repo.git',
        deploy_key_private='-----BEGIN PRIVATE KEY-----\nMII\n-----END PRIVATE KEY-----\n',
        deploy_key_public='ssh-rsa AAAA',
    )
    return user


def _write(user, relative_path, content):
    path = Path(BeanFileManager.get_user_assets_path(user)) / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')
    return path


def _read(user, relative_path):
    return (
        Path(BeanFileManager.get_user_assets_path(user)) / relative_path
    ).read_text(encoding='utf-8')


def _create_account(user, name):
    account = Account(account=name, owner=user)
    account._skip_ledger_sync = True
    account.save()
    return account


def _write_ledger(user, account_name):
    _write(user, 'main.bean', 'include "trans/collect.bean"\n')
    _write(
        user,
        'trans/collect.bean',
        f'2000-01-01 open {account_name} CNY\n'
        '2000-01-01 open Expenses:Food CNY\n'
        '\n'
        '2025-01-01 * "测试"\n'
        f'  {account_name}  -10.00 CNY\n'
        '  Expenses:Food  10.00 CNY\n'
        '\n'
        f'2025-01-02 note {account_name} "备注"\n',
    )


def test_rename_rewrites_platform_ledger(user):
    _write_ledger(user, 'Assets:Old')
    account = _create_account(user, 'Assets:Old')

    result = rename_account(account, 'Assets:New')

    assert result.old_account == 'Assets:Old'
    assert result.new_account == 'Assets:New'
    assert 'trans/collect.bean' in result.changed_paths

    text = _read(user, 'trans/collect.bean')
    assert 'Assets:Old' not in text
    assert '2000-01-01 open Assets:New CNY' in text
    assert 'note Assets:New "备注"' in text
    assert '  Assets:New  -10.00 CNY' in text

    account.refresh_from_db()
    assert account.account == 'Assets:New'


def test_rename_updates_children(user):
    _write_ledger(user, 'Assets:Old:Sub')
    child = _create_account(user, 'Assets:Old:Sub')
    parent = child.parent

    result = rename_account(parent, 'Assets:New')

    child.refresh_from_db()
    assert child.account == 'Assets:New:Sub'
    assert ('Assets:Old:Sub', 'Assets:New:Sub') in result.renamed_accounts
    assert 'Assets:Old' not in _read(user, 'trans/collect.bean')


def test_git_user_platform_account_can_rename(git_user):
    """Git 用户只在平台区有 open 时，允许平台改名。"""
    _write(git_user, 'main.bean', 'include "trans/collect.bean"\n')
    _write(git_user, 'trans/collect.bean', '2000-01-01 open Assets:Old CNY\n')
    account = _create_account(git_user, 'Assets:Old')

    rename_account(account, 'Assets:New')

    account.refresh_from_db()
    assert account.account == 'Assets:New'
    assert 'Assets:New' in _read(git_user, 'trans/collect.bean')


def test_git_user_ledger_account_cannot_rename(git_user):
    """Git 用户账本已有 open 时，改名需回到账本。"""
    _write(git_user, 'main.bean', 'include "account/assets.bean"\n')
    _write(git_user, 'account/assets.bean', '2000-01-01 open Assets:Old CNY\n')
    account = _create_account(git_user, 'Assets:Old')

    with pytest.raises(ValidationError):
        rename_account(account, 'Assets:New')

    account.refresh_from_db()
    assert account.account == 'Assets:Old'


def test_user_area_reference_blocks_rename(user):
    _write_ledger(user, 'Assets:Old')
    _write(user, 'account/assets.bean', '2000-01-01 open Assets:Old CNY\n')
    account = _create_account(user, 'Assets:Old')

    with pytest.raises(ValidationError):
        rename_account(account, 'Assets:New')

    account.refresh_from_db()
    assert account.account == 'Assets:Old'
    assert 'Assets:Old' in _read(user, 'account/assets.bean')


def test_serializer_update_triggers_rename(user):
    _write_ledger(user, 'Assets:Old')
    account = _create_account(user, 'Assets:Old')

    serializer = AccountSerializer(
        instance=account,
        data={'account': 'Assets:New'},
        partial=True,
        context={'request': SimpleNamespace(user=user)},
    )
    assert serializer.is_valid(), serializer.errors
    serializer.save()

    account.refresh_from_db()
    assert account.account == 'Assets:New'
    assert 'Assets:New' in _read(user, 'trans/collect.bean')


def test_serializer_rejects_ledger_custodied_rename(git_user):
    _write(git_user, 'main.bean', 'include "account/assets.bean"\n')
    _write(git_user, 'account/assets.bean', '2000-01-01 open Assets:Old CNY\n')
    account = _create_account(git_user, 'Assets:Old')

    serializer = AccountSerializer(
        instance=account,
        data={'account': 'Assets:New'},
        partial=True,
        context={'request': SimpleNamespace(user=git_user)},
    )
    assert serializer.is_valid(), serializer.errors

    with pytest.raises(serializers.ValidationError):
        serializer.save()

    account.refresh_from_db()
    assert account.account == 'Assets:Old'
