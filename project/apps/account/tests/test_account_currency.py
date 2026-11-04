"""账户币种校验、账本重写与 Git 同步测试"""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from project.apps.account.currency import (
    find_currency_conflicts,
    normalize_currency_codes,
    validate_currency_codes,
)
from project.apps.account.ledger_sync import LedgerSyncResult, sync_open
from project.apps.account.models import Account
from project.apps.account.serializers import AccountSerializer
from project.apps.git_repository.models import GitRepository
from project.apps.git_repository.services import GitServiceException, PlatformGitService
from project.utils.file import BeanFileManager

User = get_user_model()

pytestmark = pytest.mark.django_db

ACCOUNT_NAME = 'Assets:Savings:Bank:Test'


@pytest.fixture
def assets_dir(tmp_path, monkeypatch):
    base = tmp_path / 'Assets'
    base.mkdir()
    monkeypatch.setattr(settings, 'ASSETS_BASE_PATH', str(base))
    return base


@pytest.fixture
def user(assets_dir):
    return User.objects.create_user(username='currencyuser', password='testpass123')


@pytest.fixture
def git_user(user):
    GitRepository.objects.create(
        owner=user,
        repo_name='currencyuser-assets',
        remote_ssh_url='git@example.com:org/repo.git',
        deploy_key_private='-----BEGIN PRIVATE KEY-----\nMII\n-----END PRIVATE KEY-----\n',
        deploy_key_public='ssh-rsa AAAA',
    )
    return user


def _write_ledger(user, relative_path, content):
    path = Path(BeanFileManager.get_user_assets_path(user)) / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')
    return path


def _read_ledger(user, relative_path):
    return (Path(BeanFileManager.get_user_assets_path(user)) / relative_path).read_text(
        encoding='utf-8'
    )


def _request(user):
    return SimpleNamespace(user=user)


class TestCurrencyCodes:
    def test_normalize(self):
        assert normalize_currency_codes(' cny, COIN，cny ') == 'CNY,COIN'
        assert normalize_currency_codes(None) == ''

    def test_invalid_code_rejected(self):
        with pytest.raises(ValidationError):
            validate_currency_codes('CNY$')

    def test_non_balance_sheet_account_rejected(self, user):
        with pytest.raises(ValidationError):
            Account.objects.create(
                account='Expenses:Food', owner=user, currency='CNY'
            )


class TestAccountCurrencySave:
    def test_new_account_writes_currency_in_open(self, user):
        Account.objects.create(account=ACCOUNT_NAME, owner=user, currency='cny, coin')

        text = _read_ledger(user, 'trans/collect.bean')
        assert f'2000-01-01 open {ACCOUNT_NAME} CNY,COIN' in text

    def test_change_currency_rewrites_open_in_place(self, user):
        account = Account.objects.create(account=ACCOUNT_NAME, owner=user, currency='CNY')
        account.currency = 'USD'
        account.save()

        text = _read_ledger(user, 'trans/collect.bean')
        assert text.count(f'open {ACCOUNT_NAME}') == 1
        assert f'2000-01-01 open {ACCOUNT_NAME} USD' in text

    def test_clear_currency_removes_constraint(self, user):
        account = Account.objects.create(account=ACCOUNT_NAME, owner=user, currency='CNY')
        account.currency = ''
        account.save()

        text = _read_ledger(user, 'trans/collect.bean')
        assert f'2000-01-01 open {ACCOUNT_NAME}\n' in text
        assert f'open {ACCOUNT_NAME} CNY' not in text

    def test_rewrite_preserves_booking_and_comment(self, user):
        _write_ledger(
            user,
            'account/assets.bean',
            f'2000-01-01 open {ACCOUNT_NAME} CNY "STRICT"  ; 测试注释\n',
        )
        account = Account.objects.create(account=ACCOUNT_NAME, owner=user, currency='CNY')
        account.currency = 'USD'
        account.save()

        text = _read_ledger(user, 'account/assets.bean')
        assert f'2000-01-01 open {ACCOUNT_NAME} USD "STRICT"  ; 测试注释' in text

    def test_rewrite_open_in_monthly_file(self, user):
        _write_ledger(
            user,
            '2024/01.bean',
            f'2000-01-01 open {ACCOUNT_NAME} CNY\n'
            '\n'
            '2024-01-02 * "测试"\n'
            f'  {ACCOUNT_NAME}  -10.00 CNY\n'
            '  Expenses:Food  10.00 CNY\n',
        )
        account = Account.objects.create(account=ACCOUNT_NAME, owner=user, currency='CNY')
        account.currency = 'USD'
        account.save()

        text = _read_ledger(user, '2024/01.bean')
        assert f'2000-01-01 open {ACCOUNT_NAME} USD' in text

    def test_unchanged_currency_does_not_rewrite(self, user):
        account = Account.objects.create(account=ACCOUNT_NAME, owner=user, currency='CNY')
        path = Path(BeanFileManager.get_collect_bean_path(user))
        before = path.read_text(encoding='utf-8')

        account.currency = 'CNY'
        account.save()

        assert path.read_text(encoding='utf-8') == before


class TestLedgerValidity:
    def test_generated_ledger_with_currency_passes_loader(self, user):
        beancount_loader = pytest.importorskip('beancount.loader')

        account = Account.objects.create(
            account=ACCOUNT_NAME, owner=user, currency='CNY,COIN'
        )
        account.currency = 'CNY'
        account.save()

        main_path = Path(BeanFileManager.get_main_bean_path(user))
        main_path.write_text(
            'plugin "beancount.plugins.nounused"\n\n'
            'include "trans/main.bean"\n',
            encoding='utf-8',
        )

        _entries, errors, _options = beancount_loader.load_file(str(main_path))
        messages = [str(error) for error in errors]
        assert not any('Unused account' in m for m in messages), messages
        assert not any('Duplicate open' in m for m in messages), messages
        assert not any('Invalid currency' in m for m in messages), messages
        assert errors == [], messages


class TestCurrencyConflict:
    def test_conflicting_posting_blocks_serializer_update(self, user):
        pytest.importorskip('beancount.loader')
        _write_ledger(
            user,
            'main.bean',
            f'2000-01-01 open {ACCOUNT_NAME}\n'
            '2000-01-01 open Expenses:Food\n'
            '\n'
            '2025-01-01 * "测试"\n'
            f'  {ACCOUNT_NAME}  10.00 USD\n'
            '  Expenses:Food  -10.00 USD\n',
        )
        account = Account(account=ACCOUNT_NAME, owner=user, currency='')
        account._skip_ledger_sync = True
        account.save()

        serializer = AccountSerializer(
            instance=account,
            data={'currency': 'CNY'},
            partial=True,
            context={'request': _request(user)},
        )
        assert not serializer.is_valid()
        assert 'currency' in serializer.errors

        account.refresh_from_db()
        assert account.currency == ''

    def test_superset_currency_allowed(self, user):
        pytest.importorskip('beancount.loader')
        _write_ledger(
            user,
            'main.bean',
            f'2000-01-01 open {ACCOUNT_NAME}\n'
            '2000-01-01 open Expenses:Food\n'
            '\n'
            '2025-01-01 * "测试"\n'
            f'  {ACCOUNT_NAME}  10.00 CNY\n'
            '  Expenses:Food  -10.00 CNY\n',
        )
        account = Account(account=ACCOUNT_NAME, owner=user, currency='')
        account._skip_ledger_sync = True
        account.save()
        # 重新读取，去掉创建时用于跳过账本同步的临时标记
        account = Account.objects.get(pk=account.pk)

        serializer = AccountSerializer(
            instance=account,
            data={'currency': 'CNY,COIN'},
            partial=True,
            context={'request': _request(user)},
        )
        assert serializer.is_valid(), serializer.errors
        serializer.save()

        text = _read_ledger(user, 'trans/collect.bean')
        assert f'open {ACCOUNT_NAME} CNY,COIN' in text or f'open {ACCOUNT_NAME} CNY,COIN' in _read_ledger(
            user, 'main.bean'
        )

    def test_find_conflicts_returns_used_currency(self, user):
        pytest.importorskip('beancount.loader')
        _write_ledger(
            user,
            'main.bean',
            f'2000-01-01 open {ACCOUNT_NAME}\n'
            '2000-01-01 open Expenses:Food\n'
            '\n'
            '2025-01-01 * "测试"\n'
            f'  {ACCOUNT_NAME}  10.00 USD\n'
            '  Expenses:Food  -10.00 USD\n',
        )
        assert find_currency_conflicts(user, ACCOUNT_NAME, ['CNY']) == ['USD']


class TestRecreateRemovesClose:
    def test_close_in_monthly_file_returns_changed_path(self, user):
        _write_ledger(
            user,
            '2024/01.bean',
            f'2000-01-01 open {ACCOUNT_NAME}\n'
            '\n'
            '2025-01-01 close ' + ACCOUNT_NAME + '\n',
        )
        account = Account(account=ACCOUNT_NAME, owner=user, enable=True, currency='')

        result = sync_open(account)

        assert '2024/01.bean' in result.paths


class TestGitSync:
    def test_trans_only_paths_do_not_push(self, git_user):
        account = Account(account=ACCOUNT_NAME, owner=git_user)

        with patch.object(PlatformGitService, 'push_ledger') as mock_push:
            account._apply_ledger_git_sync(
                LedgerSyncResult('3000-01-01 open Assets:X', ['trans/collect.bean']),
                'chore(account): 测试',
            )

        mock_push.assert_not_called()

    def test_tracked_path_pushes_with_custom_message(self, git_user):
        account = Account(account=ACCOUNT_NAME, owner=git_user)

        with patch.object(
            PlatformGitService, 'push_ledger',
            return_value={'status': 'success', 'message': 'ok', 'files': []},
        ) as mock_push:
            account._apply_ledger_git_sync(
                LedgerSyncResult('2000-01-01 open Assets:X USD', ['2024/01.bean']),
                'chore(account): 更新账户币种',
            )

        mock_push.assert_called_once()
        kwargs = mock_push.call_args.kwargs
        assert kwargs['paths'] == ['2024/01.bean']
        assert kwargs['commit_message'] == 'chore(account): 更新账户币种'

    def test_push_failure_records_warning_and_sync_error(self, git_user):
        account = Account(account=ACCOUNT_NAME, owner=git_user)

        with patch.object(
            PlatformGitService, 'push_ledger',
            side_effect=GitServiceException('推送失败: boom'),
        ):
            account._apply_ledger_git_sync(
                LedgerSyncResult('2000-01-01 open Assets:X USD', ['2024/01.bean']),
                'chore(account): 更新账户币种',
            )

        assert account.ledger_sync_warning
        git_repo = git_user.git_repo
        git_repo.refresh_from_db()
        assert git_repo.sync_status == 'failed'
        assert '推送失败' in git_repo.sync_error
