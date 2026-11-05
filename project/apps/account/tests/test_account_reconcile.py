"""账本 → 平台账户反向对账测试"""
import json
from io import StringIO
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from rest_framework.test import APIClient

from project.apps.account.account_reconcile import apply_pairings, diff_ledger_accounts
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
def user(assets_dir):
    return User.objects.create_user(username='reconcileuser', password='testpass123')


@pytest.fixture
def git_user(assets_dir):
    user = User.objects.create_user(username='reconcilegit', password='testpass123')
    GitRepository.objects.create(
        owner=user,
        repo_name='reconcilegit-assets',
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


def _create_account(user, name):
    account = Account(account=name, owner=user)
    account._skip_ledger_sync = True
    account.save()
    return account


def _write_scope(user):
    _write(user, 'main.bean', 'include "scope.bean"\n')
    _write(
        user,
        'scope.bean',
        '2000-01-01 open Assets:Bank CNY\n'
        '2000-01-01 open Assets:New CNY\n',
    )


def test_diff_reports_new_and_missing(user):
    _create_account(user, 'Assets:Bank')
    _create_account(user, 'Assets:Old')
    _write_scope(user)

    result = diff_ledger_accounts(user)

    assert result['ledger_available'] is True
    assert result['ledger_only'] == ['Assets:New']
    assert result['db_only'] == ['Assets:Old']
    assert result['matched'] == ['Assets:Bank']


def test_parent_accounts_are_not_reported_as_missing(user):
    _create_account(user, 'Assets:Bank')
    _create_account(user, 'Assets:Old:Sub')
    _write_scope(user)

    result = diff_ledger_accounts(user)

    assert 'Assets:Old' not in result['db_only']
    assert 'Assets:Old:Sub' in result['db_only']


def test_missing_main_bean_marks_ledger_unavailable(user):
    _create_account(user, 'Assets:Bank')
    Path(BeanFileManager.get_main_bean_path(user)).unlink()

    result = diff_ledger_accounts(user)

    assert result['ledger_available'] is False
    assert result['ledger_only'] == []
    assert result['db_only'] == []
    assert result['matched'] == []


def test_command_outputs_json(user):
    _create_account(user, 'Assets:Bank')
    _write_scope(user)

    out = StringIO()
    call_command(
        'reconcile_ledger_accounts', '--user', user.username, '--json', stdout=out
    )

    report = json.loads(out.getvalue())
    assert report[0]['username'] == user.username
    assert report[0]['ledger_only'] == ['Assets:New']


def _write_simple_ledger(user, content):
    _write(user, 'main.bean', 'include "collect.bean"\n')
    _write(user, 'collect.bean', content)


def test_apply_pairings_updates_platform_accounts(git_user):
    _create_account(git_user, 'Assets:Old')
    _write_simple_ledger(git_user, '2000-01-01 open Assets:New CNY\n')

    applied = apply_pairings(
        git_user,
        [{'old_account': 'Assets:Old', 'new_account': 'Assets:New'}],
    )

    assert applied[0]['new_account'] == 'Assets:New'
    assert Account.objects.filter(owner=git_user, account='Assets:New').exists()
    assert not Account.objects.filter(owner=git_user, account='Assets:Old').exists()


def test_apply_pairings_rejects_remaining_old_reference(git_user):
    _create_account(git_user, 'Assets:Old')
    _write_simple_ledger(
        git_user,
        '2000-01-01 open Assets:New CNY\n'
        '2000-01-01 open Expenses:Food CNY\n'
        '\n'
        '2025-01-01 * "旧引用"\n'
        '  Assets:Old  -10.00 CNY\n'
        '  Expenses:Food  10.00 CNY\n',
    )

    with pytest.raises(ValidationError):
        apply_pairings(
            git_user,
            [{'old_account': 'Assets:Old', 'new_account': 'Assets:New'}],
        )

    assert Account.objects.filter(owner=git_user, account='Assets:Old').exists()


def test_apply_pairings_rejects_non_git_user(user):
    _create_account(user, 'Assets:Old')

    with pytest.raises(ValidationError):
        apply_pairings(
            user,
            [{'old_account': 'Assets:Old', 'new_account': 'Assets:New'}],
        )


def test_reconcile_api(git_user):
    _create_account(git_user, 'Assets:Old')
    _write_simple_ledger(git_user, '2000-01-01 open Assets:New CNY\n')

    client = APIClient()
    client.force_authenticate(user=git_user)

    response = client.get('/api/account/reconcile/')
    assert response.status_code == 200, response.data
    assert response.data['ledger_only'] == ['Assets:New']
    assert response.data['db_only'] == ['Assets:Old']

    response = client.post(
        '/api/account/reconcile/apply/',
        {'pairs': [{'old_account': 'Assets:Old', 'new_account': 'Assets:New'}]},
        format='json',
    )
    assert response.status_code == 200, response.data
    assert Account.objects.filter(owner=git_user, account='Assets:New').exists()
