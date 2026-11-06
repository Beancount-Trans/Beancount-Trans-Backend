"""Git 用户账户托管态：平台区可改，账本托管需回到账本"""
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from project.apps.account.custody import is_ledger_custodied
from project.apps.account.models import Account
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
def git_user(assets_dir):
    user = User.objects.create_user(username='custodyuser', password='testpass123')
    GitRepository.objects.create(
        owner=user,
        repo_name='custodyuser-assets',
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


def _create_platform_account(user, name, currency=''):
    account = Account(account=name, owner=user, currency=currency)
    account.save()
    return account


def _create_ledger_account(user, name, currency=''):
    _write(user, 'main.bean', 'include "account/assets.bean"\n')
    _write(
        user,
        'account/assets.bean',
        f'2000-01-01 open {name} {currency}\n' if currency
        else f'2000-01-01 open {name}\n',
    )
    account = Account(account=name, owner=user, currency=currency)
    account._skip_ledger_sync = True
    account.save()
    return account


def test_platform_only_account_is_not_ledger_custodied(git_user):
    account = _create_platform_account(git_user, 'Assets:Wallet')

    assert 'open Assets:Wallet' in _read(git_user, 'trans/collect.bean')
    assert is_ledger_custodied(git_user, account) is False


def test_user_area_open_marks_account_as_ledger_custodied(git_user):
    account = _create_ledger_account(git_user, 'Assets:Wallet', 'CNY')

    assert is_ledger_custodied(git_user, account) is True


def test_ledger_custodied_child_marks_parent(git_user):
    _create_ledger_account(git_user, 'Assets:Bank:CMB', 'CNY')
    parent = Account.objects.get(owner=git_user, account='Assets:Bank')

    assert is_ledger_custodied(git_user, parent) is True


def test_currency_change_allowed_for_platform_account(git_user):
    account = _create_platform_account(git_user, 'Assets:Wallet')

    account.currency = 'CNY'
    account.save()

    account.refresh_from_db()
    assert account.currency == 'CNY'
    assert 'open Assets:Wallet CNY' in _read(git_user, 'trans/collect.bean')


def test_currency_change_rejected_for_ledger_account(git_user):
    account = _create_ledger_account(git_user, 'Assets:Wallet')

    account.currency = 'CNY'
    with pytest.raises(ValidationError):
        account.save()

    account.refresh_from_db()
    assert account.currency == ''


def test_delete_rejected_for_ledger_account(git_user):
    account = _create_ledger_account(git_user, 'Assets:Wallet')

    with pytest.raises(ValidationError):
        account.delete_with_migration()

    assert Account.objects.filter(pk=account.pk).exists()


def test_delete_allowed_for_platform_account(git_user):
    account = _create_platform_account(git_user, 'Assets:Wallet')

    account.delete_with_migration()

    assert not Account.objects.filter(pk=account.pk).exists()
    assert 'close Assets:Wallet' in _read(git_user, 'trans/collect.bean')
