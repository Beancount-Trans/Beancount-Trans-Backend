"""账户同步账本状态功能测试

覆盖：
- 新建账户 → trans/collect.bean 追加 open（2000-01-01）+ 当天 note 占位
- 自动创建的父账户不写入账本
- 账本已有手工 open → 跳过、不重复
- 启用 / 关闭 → 追加状态 note（可反复切换，不写 close）
- 同日多次开关 → 相邻重复状态去重
- 删除账户 → 追加 close（从未 open 则补 open）
- 删除后又新建同名账户 → 移除账本中已有的 close
- 账本存在更早引用时 → open 仍为固定基准日
- 生成后的账本通过 beancount.loader 加载，启用 nounused 插件无报错

所有测试将 settings.ASSETS_BASE_PATH 指向 tmp_path，避免污染真实 Assets 目录。
"""
from datetime import date, timedelta
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model

from project.apps.account.ledger_sync import (
    LEGACY_NOTE_DELETED,
    OPEN_BASE_DATE,
    PLACEHOLDER_NOTE,
    STATE_NOTE_DISABLED,
    STATE_NOTE_ENABLED,
)
from project.apps.account.models import Account
from project.utils.file import BeanFileManager

User = get_user_model()

pytestmark = pytest.mark.django_db

ACCOUNT_NAME = 'Assets:Savings:Recharge:LiuXian'


@pytest.fixture
def assets_dir(tmp_path, monkeypatch):
    """把 ASSETS_BASE_PATH 指向临时目录，隔离文件系统。"""
    base = tmp_path / 'Assets'
    base.mkdir()
    monkeypatch.setattr(settings, 'ASSETS_BASE_PATH', str(base))
    return base


@pytest.fixture
def user(assets_dir):
    return User.objects.create_user(username='ledgeruser', password='testpass123')


def _collect_text(user):
    return Path(BeanFileManager.get_collect_bean_path(user)).read_text(encoding='utf-8')


def _write_ledger_file(user, relative_path, content):
    path = Path(BeanFileManager.get_user_assets_path(user)) / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')
    return path


def test_new_account_writes_open_and_note(user):
    Account.objects.create(account=ACCOUNT_NAME, owner=user)

    text = _collect_text(user)
    assert f"{OPEN_BASE_DATE} open {ACCOUNT_NAME}" in text
    assert f'{date.today()} note {ACCOUNT_NAME} "{PLACEHOLDER_NOTE}"' in text
    # collect.bean 已被 trans/main.bean include
    main_text = Path(BeanFileManager.get_trans_main_bean_path(user)).read_text(encoding='utf-8')
    assert 'include "collect.bean"' in main_text


def test_parent_accounts_are_not_written(user):
    Account.objects.create(account=ACCOUNT_NAME, owner=user)

    text = _collect_text(user)
    assert 'Assets:Savings:Recharge ' not in text
    assert 'Assets:Savings:Recharge\n' not in text
    assert 'Assets:Savings ' not in text
    assert 'Assets ' not in text
    # 父账户仍真实存在
    assert Account.objects.filter(owner=user, account='Assets:Savings:Recharge').exists()


def test_existing_manual_open_is_not_duplicated(user):
    _write_ledger_file(
        user,
        'account/assets.bean',
        f"2026-01-01 open {ACCOUNT_NAME}\n",
    )

    Account.objects.create(account=ACCOUNT_NAME, owner=user)

    collect_path = Path(BeanFileManager.get_collect_bean_path(user))
    if collect_path.exists():
        assert 'open ' + ACCOUNT_NAME not in collect_path.read_text(encoding='utf-8')


def test_disable_and_enable_write_state_notes(user):
    account = Account.objects.create(account=ACCOUNT_NAME, owner=user)

    account.enable = False
    account.save()
    text = _collect_text(user)
    assert text.count(f'note {ACCOUNT_NAME} "{STATE_NOTE_DISABLED}"') == 1

    # 重复关闭不重复写
    account.enable = False
    account.save()
    assert _collect_text(user).count(f'note {ACCOUNT_NAME} "{STATE_NOTE_DISABLED}"') == 1

    account.enable = True
    account.save()
    text = _collect_text(user)
    assert text.count(f'note {ACCOUNT_NAME} "{STATE_NOTE_ENABLED}"') == 1
    # 启用不写第二份 open
    assert text.count(f"open {ACCOUNT_NAME}") == 1
    assert f"close {ACCOUNT_NAME}" not in text


def test_same_day_toggles_keep_final_state(user):
    account = Account.objects.create(account=ACCOUNT_NAME, owner=user)

    for enable in (False, True, False, True):
        account.enable = enable
        account.save()

    text = _collect_text(user)
    assert text.count(f'note {ACCOUNT_NAME} "{STATE_NOTE_DISABLED}"') == 2
    assert text.count(f'note {ACCOUNT_NAME} "{STATE_NOTE_ENABLED}"') == 2
    assert text.count(f"open {ACCOUNT_NAME}") == 1
    assert f"close {ACCOUNT_NAME}" not in text


def test_delete_writes_close_and_adds_open_if_missing(user):
    account = Account(account=ACCOUNT_NAME, owner=user, enable=True)
    account._skip_ledger_sync = True
    account.save()

    account.delete_with_migration()

    text = _collect_text(user)
    assert text.count(f"open {ACCOUNT_NAME}") == 1
    assert text.count(f"close {ACCOUNT_NAME}") == 1
    assert f'note {ACCOUNT_NAME} "{LEGACY_NOTE_DELETED}"' not in text


def test_recreate_after_delete_removes_close(user):
    account = Account.objects.create(account=ACCOUNT_NAME, owner=user)
    account.delete_with_migration()

    Account.objects.create(account=ACCOUNT_NAME, owner=user)

    text = _collect_text(user)
    assert text.count(f"open {ACCOUNT_NAME}") == 1
    assert f"close {ACCOUNT_NAME}" not in text


def test_recreate_removes_legacy_close(user):
    today = date.today()
    _write_ledger_file(
        user,
        'trans/collect.bean',
        f"{today} open {ACCOUNT_NAME}\n"
        f'{today} note {ACCOUNT_NAME} "{PLACEHOLDER_NOTE}"\n'
        f"\n"
        f"{today} close {ACCOUNT_NAME}\n",
    )

    Account.objects.create(account=ACCOUNT_NAME, owner=user)

    text = _collect_text(user)
    assert text.count(f"open {ACCOUNT_NAME}") == 1
    assert f"close {ACCOUNT_NAME}" not in text


def test_recreate_removes_legacy_close_from_earlier_date(user):
    yesterday = date.today() - timedelta(days=1)
    _write_ledger_file(
        user,
        'trans/collect.bean',
        f"{yesterday} open {ACCOUNT_NAME}\n"
        f'{yesterday} note {ACCOUNT_NAME} "{PLACEHOLDER_NOTE}"\n'
        f"\n"
        f"{yesterday} close {ACCOUNT_NAME}\n",
    )

    Account.objects.create(account=ACCOUNT_NAME, owner=user)

    text = _collect_text(user)
    assert text.count(f"open {ACCOUNT_NAME}") == 1
    assert f"close {ACCOUNT_NAME}" not in text


def test_recreate_removes_close_from_monthly_file(user):
    today = date.today()
    _write_ledger_file(
        user,
        f'{today.year}/{today.month:02d}.bean',
        f"{today} open {ACCOUNT_NAME}\n"
        f"\n"
        f"{today} close {ACCOUNT_NAME}\n",
    )

    Account.objects.create(account=ACCOUNT_NAME, owner=user)

    text = (
        Path(BeanFileManager.get_user_assets_path(user))
        / f'{today.year}/{today.month:02d}.bean'
    ).read_text(encoding='utf-8')
    assert f"close {ACCOUNT_NAME}" not in text
    assert f"open {ACCOUNT_NAME}" in text


def test_recreate_after_disabled_delete_writes_enable_note(user):
    account = Account.objects.create(account=ACCOUNT_NAME, owner=user)
    account.enable = False
    account.save()
    account.delete_with_migration()

    Account.objects.create(account=ACCOUNT_NAME, owner=user)

    text = _collect_text(user)
    assert f"close {ACCOUNT_NAME}" not in text
    assert text.count(f'note {ACCOUNT_NAME} "{STATE_NOTE_DISABLED}"') == 1
    assert text.count(f'note {ACCOUNT_NAME} "{STATE_NOTE_ENABLED}"') == 1
    # 最后一条状态记录应为「启用」
    assert text.rindex(f'"{STATE_NOTE_ENABLED}"') > text.rindex(f'"{STATE_NOTE_DISABLED}"')


def test_open_date_uses_fixed_base_date(user):
    _write_ledger_file(
        user,
        'trans/old_bill.bean',
        f'2025-01-01 * "旧交易"\n'
        f'  {ACCOUNT_NAME}  10.00 CNY\n'
        f'  Income:Other\n'
        f'\n',
    )

    Account.objects.create(account=ACCOUNT_NAME, owner=user)

    text = _collect_text(user)
    assert f"{OPEN_BASE_DATE} open {ACCOUNT_NAME}" in text


def test_generated_ledger_passes_beancount_nounused(user):
    beancount_loader = pytest.importorskip('beancount.loader')

    account = Account.objects.create(account=ACCOUNT_NAME, owner=user)
    # 反复启用/关闭后仍应生成合法账本
    for enable in (False, True, False):
        account.enable = enable
        account.save()

    # 同一天删除后又新建同名账户
    account.delete_with_migration()
    Account.objects.create(account=ACCOUNT_NAME, owner=user)

    assets_root = Path(BeanFileManager.get_user_assets_path(user))
    main_path = assets_root / 'main.bean'
    main_path.write_text(
        'plugin "beancount.plugins.nounused"\n\n'
        'include "trans/collect.bean"\n',
        encoding='utf-8',
    )

    _entries, errors, _options = beancount_loader.load_file(str(main_path))
    messages = [str(error) for error in errors]
    assert not any('Unused account' in m for m in messages), messages
    assert not any('Duplicate open' in m for m in messages), messages
    assert not any('inactive account' in m for m in messages), messages
    assert errors == [], messages


def test_state_note_not_before_open_date(user):
    _write_ledger_file(
        user,
        'account/assets.bean',
        f"2099-01-01 open {ACCOUNT_NAME}\n",
    )

    account = Account.objects.create(account=ACCOUNT_NAME, owner=user)
    account.enable = False
    account.save()

    text = _collect_text(user)
    note_line = [
        line for line in text.splitlines()
        if line.endswith(f'note {ACCOUNT_NAME} "{STATE_NOTE_DISABLED}"')
    ][0]
    assert date.fromisoformat(note_line.split()[0]) >= date(2099, 1, 1)
