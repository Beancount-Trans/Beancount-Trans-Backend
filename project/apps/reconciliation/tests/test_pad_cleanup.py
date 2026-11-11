"""
对账 pad 生命周期测试

覆盖 ReconciliationCommentService：
- cleanup_unused_pads：真实交易使余额落入断言容差内（完全一致或相差 0.01）时，
  注释 reconciliation.bean 中的 pad；相差超过容差（0.02）时 pad 仍被使用，不注释；
  无 reconciliation.bean 或 pad 位于其它文件时不处理；重复调用幂等。
- repair_broken_balances：balance 断言被更早日期的后补交易打破时，自动补 pad；
  分摊账户优先复用当次对账写入的 pad 账户；只处理 reconciliation.bean 中的断言。
"""
from datetime import date
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from beancount import loader

from project.apps.account.models import Account
from project.apps.reconciliation.models import ScheduledTask
from project.apps.reconciliation.services.reconciliation_comment_service import (
    ReconciliationCommentService,
)

User = get_user_model()

MAIN_BEAN = (
    'option "operating_currency" "CNY"\n'
    'plugin "beancount.plugins.auto_accounts"\n'
    'include "trans/main.bean"\n'
)

PAD_LINE = '2026-10-07 pad Assets:Savings:Web:AliFund Equity:Opening-Balances'
BALANCE_LINE = '2026-10-08 balance Assets:Savings:Web:AliFund 205.76 CNY'
RECONCILIATION = f'{PAD_LINE}\n{BALANCE_LINE}\n'


def _collect_transaction(amount: str) -> str:
    return (
        '2026-10-06 * "Beancount-Trans" "解析写入"\n'
        f'    Assets:Savings:Web:AliFund {amount} CNY\n'
        f'    Income:Other -{amount} CNY\n'
    )


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


def _has_unused_pad_error(main_path: Path) -> bool:
    _entries, errors, _options = loader.load_file(str(main_path))
    return any(getattr(e, 'message', None) == 'Unused Pad entry' for e in errors)


@pytest.fixture
def build_ledger(tmp_path, monkeypatch):
    """把 ASSETS_BASE_PATH 指向临时目录，返回构建用户账本的工厂函数。"""
    base = tmp_path / 'Assets'
    base.mkdir()
    monkeypatch.setattr(settings, 'ASSETS_BASE_PATH', str(base))

    def build(username, *, reconciliation=None, collect='', extra_files=None):
        user_dir = base / username

        includes = ['include "collect.bean"']
        if reconciliation is not None:
            includes.append('include "reconciliation.bean"')
        for name in (extra_files or {}):
            includes.append(f'include "{name}"')

        _write(user_dir / 'main.bean', MAIN_BEAN)
        _write(user_dir / 'trans/main.bean', '\n'.join(includes) + '\n')

        collect_content = '; header\n\n' + collect if collect else '; header\n'
        _write(user_dir / 'trans/collect.bean', collect_content)

        if reconciliation is not None:
            _write(user_dir / 'trans/reconciliation.bean', reconciliation)

        for name, text in (extra_files or {}).items():
            _write(user_dir / 'trans' / name, text)

        return user_dir

    return build


def test_exact_match_comments_pad(build_ledger):
    """余额与断言完全一致：pad 变为无用，被注释；balance 断言保留。"""
    username = 'cleanupuser'
    user_dir = build_ledger(
        username,
        reconciliation=RECONCILIATION,
        collect=_collect_transaction('205.76'),
    )

    assert _has_unused_pad_error(user_dir / 'main.bean')

    assert ReconciliationCommentService.cleanup_unused_pads(username) == 1

    content = (user_dir / 'trans/reconciliation.bean').read_text(encoding='utf-8')
    assert f'; {PAD_LINE}' in content
    assert BALANCE_LINE in content

    # 清理后不再有无用 pad 错误
    assert not _has_unused_pad_error(user_dir / 'main.bean')

    # 幂等：再次调用不再注释任何行
    assert ReconciliationCommentService.cleanup_unused_pads(username) == 0


def test_within_tolerance_comments_pad(build_ledger):
    """余额与断言相差 0.01（容差内）：同样被识别并注释。"""
    username = 'cleanupuser'
    user_dir = build_ledger(
        username,
        reconciliation=RECONCILIATION,
        collect=_collect_transaction('205.75'),
    )

    assert _has_unused_pad_error(user_dir / 'main.bean')
    assert ReconciliationCommentService.cleanup_unused_pads(username) == 1

    content = (user_dir / 'trans/reconciliation.bean').read_text(encoding='utf-8')
    assert f'; {PAD_LINE}' in content
    assert BALANCE_LINE in content


def test_beyond_tolerance_keeps_pad(build_ledger):
    """余额与断言相差 0.02（超容差）：pad 仍被使用，不注释。"""
    username = 'cleanupuser'
    user_dir = build_ledger(
        username,
        reconciliation=RECONCILIATION,
        collect=_collect_transaction('205.74'),
    )

    # pad 被实际使用，不存在无用 pad 错误
    assert not _has_unused_pad_error(user_dir / 'main.bean')
    assert ReconciliationCommentService.cleanup_unused_pads(username) == 0

    content = (user_dir / 'trans/reconciliation.bean').read_text(encoding='utf-8')
    assert PAD_LINE in content
    assert not content.lstrip().startswith(';')


def test_no_reconciliation_bean_returns_zero(build_ledger):
    """无 reconciliation.bean：直接返回 0。"""
    username = 'cleanupuser'
    build_ledger(
        username,
        reconciliation=None,
        collect=_collect_transaction('205.76'),
    )

    assert ReconciliationCommentService.cleanup_unused_pads(username) == 0


def test_pad_in_other_file_not_touched(build_ledger):
    """无用 pad 位于非 reconciliation.bean 文件：不受影响。"""
    username = 'cleanupuser'
    other_filename = 'other.bean'
    user_dir = build_ledger(
        username,
        reconciliation=None,
        collect=_collect_transaction('205.76'),
        extra_files={other_filename: RECONCILIATION},
    )

    # 其它文件中确实存在被判定为无用的 pad
    assert _has_unused_pad_error(user_dir / 'main.bean')

    # 只处理 reconciliation.bean，故不注释任何行
    assert ReconciliationCommentService.cleanup_unused_pads(username) == 0

    other_content = (user_dir / 'trans' / other_filename).read_text(encoding='utf-8')
    assert PAD_LINE in other_content


BACKFILL_SPEND = (
    '2026-10-06 * "Beancount-Trans" "解析写入"\n'
    '    Assets:Savings:Web:AliFund -300.00 CNY\n'
    '    Expenses:Shopping 300.00 CNY\n'
)


def _has_broken_balance(main_path: Path) -> bool:
    _entries, errors, _options = loader.load_file(str(main_path))
    return any(
        str(getattr(e, 'message', '')).startswith('Balance failed for') for e in errors
    )


@pytest.mark.django_db
def test_repair_adds_pad_for_backfilled_transaction(build_ledger):
    """断言被更早日期的后补交易打破：自动补 pad 后账本恢复无错，且重复调用幂等。"""
    username = 'repairuser'
    user = User.objects.create_user(username=username, password='testpass123')
    Account.objects.create(account='Assets:Savings:Web:AliFund', owner=user)

    user_dir = build_ledger(
        username,
        reconciliation=f'{BALANCE_LINE}\n',
        collect=BACKFILL_SPEND,
    )
    assert _has_broken_balance(user_dir / 'main.bean')

    assert ReconciliationCommentService.repair_broken_balances(user) == 1

    content = (user_dir / 'trans/reconciliation.bean').read_text(encoding='utf-8')
    assert '2026-10-07 pad Assets:Savings:Web:AliFund Equity:Opening-Balances' in content
    assert not _has_broken_balance(user_dir / 'main.bean')

    # 幂等：断言已修复，不再补 pad
    assert ReconciliationCommentService.repair_broken_balances(user) == 0


@pytest.mark.django_db
def test_repair_reuses_stored_pad_account(build_ledger):
    """分摊账户优先复用当次对账写入的 pad 账户。"""
    username = 'repairuser2'
    user = User.objects.create_user(username=username, password='testpass123')
    account = Account.objects.create(account='Assets:Savings:Web:AliFund', owner=user)
    ScheduledTask.objects.create(
        task_type='reconciliation',
        content_type=ContentType.objects.get_for_model(Account),
        object_id=account.id,
        scheduled_date=date(2026, 10, 7),
        completed_date=date(2026, 10, 7),
        as_of_date=date(2026, 10, 7),
        status='completed',
        reconciliation_entries=[
            {
                'type': 'Pad',
                'date': '2026-10-07',
                'account': 'Assets:Savings:Web:AliFund',
                'source_account': 'Equity:Adjustments',
            }
        ],
    )

    user_dir = build_ledger(
        username,
        reconciliation=f'{BALANCE_LINE}\n',
        collect=BACKFILL_SPEND,
    )

    assert ReconciliationCommentService.repair_broken_balances(user) == 1

    content = (user_dir / 'trans/reconciliation.bean').read_text(encoding='utf-8')
    assert '2026-10-07 pad Assets:Savings:Web:AliFund Equity:Adjustments' in content
    assert not _has_broken_balance(user_dir / 'main.bean')


MULTI_RECONCILIATION = (
    '2026-10-08 balance Assets:Savings:Web:AliFund 205.76 CNY\n'
    '2026-11-08 balance Assets:Savings:Web:AliFund 500.00 CNY\n'
)
MULTI_COLLECT = (
    '2026-10-05 * "Beancount-Trans" "解析写入"\n'
    '    Assets:Savings:Web:AliFund -100.00 CNY\n'
    '    Expenses:Shopping 100.00 CNY\n'
    '\n'
    '2026-10-06 * "Beancount-Trans" "解析写入"\n'
    '    Assets:Savings:Web:AliFund 205.76 CNY\n'
    '    Income:Other -205.76 CNY\n'
    '\n'
    '2026-11-06 * "Beancount-Trans" "解析写入"\n'
    '    Assets:Savings:Web:AliFund 100.00 CNY\n'
    '    Income:Other -100.00 CNY\n'
)


@pytest.mark.django_db
def test_repair_fixes_multiple_broken_assertions(build_ledger):
    """同一账户多条断言被打断时，逐条补 pad 直到全部通过。"""
    username = 'repairuser4'
    user = User.objects.create_user(username=username, password='testpass123')
    Account.objects.create(account='Assets:Savings:Web:AliFund', owner=user)

    user_dir = build_ledger(
        username,
        reconciliation=MULTI_RECONCILIATION,
        collect=MULTI_COLLECT,
    )
    assert _has_broken_balance(user_dir / 'main.bean')

    assert ReconciliationCommentService.repair_broken_balances(user) == 2
    assert not _has_broken_balance(user_dir / 'main.bean')

    content = (user_dir / 'trans/reconciliation.bean').read_text(encoding='utf-8')
    assert '2026-10-07 pad Assets:Savings:Web:AliFund Equity:Opening-Balances' in content
    assert '2026-11-07 pad Assets:Savings:Web:AliFund Equity:Opening-Balances' in content


@pytest.mark.django_db
def test_repair_ignores_balance_in_other_file(build_ledger):
    """只处理 reconciliation.bean 中的断言，其它文件里的失败断言不补 pad。"""
    username = 'repairuser3'
    user = User.objects.create_user(username=username, password='testpass123')
    Account.objects.create(account='Assets:Savings:Web:AliFund', owner=user)

    other = '2026-10-08 balance Assets:Savings:Web:WechatPay 100.00 CNY\n'
    user_dir = build_ledger(
        username,
        reconciliation=RECONCILIATION,
        collect=_collect_transaction('205.76'),
        extra_files={'other.bean': other},
    )
    assert _has_broken_balance(user_dir / 'main.bean')

    assert ReconciliationCommentService.repair_broken_balances(user) == 0

    content = (user_dir / 'trans/reconciliation.bean').read_text(encoding='utf-8')
    assert PAD_LINE in content
