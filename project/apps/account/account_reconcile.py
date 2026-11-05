"""账本 → 平台账户的反向对账

差集报告默认不改数据库：
- ``ledger_only``：账本声明了 open，但平台完全没有该路径（含平台自动补的父账户记录）；
- ``db_only``：平台有叶子账户，但账本没有 open，可能被改名或删除。

Git 用户改名依赖这一步先给出候选，再由用户确认。
``import_declared_accounts`` 供「自动导入账本新增账户」开关调用，只新增平台账户。
"""
import os

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count

from project.apps.account.ledger_refs import declared_accounts, referenced_accounts
from project.apps.account.ledger_sync import _is_git_user
from project.apps.account.models import Account, is_valid_beancount_account_path
from project.utils.file import BeanFileManager


def diff_ledger_accounts(user) -> dict:
    """对比账本 open 账户与平台账户

    ``ledger_only`` 用平台全部账户路径判定：平台会为子账户自动补父账户记录，
    只看叶子会把「账本也 open 了父账户」误报成平台缺失。

    Returns:
        dict: {
            'ledger_available': bool,
            'ledger_only': list[str],
            'db_only': list[str],
            'matched': list[str],
        }
    """
    main_path = BeanFileManager.get_main_bean_path(user)
    ledger_available = os.path.isfile(main_path)
    if not ledger_available:
        return {
            'ledger_available': False,
            'ledger_only': [],
            'db_only': [],
            'matched': [],
        }

    ledger_accounts = declared_accounts(user)

    db_accounts = set(
        Account.objects.filter(owner=user).values_list('account', flat=True)
    )
    db_leaf_accounts = set(
        Account.objects.filter(owner=user)
        .annotate(child_count=Count('children'))
        .filter(child_count=0)
        .values_list('account', flat=True)
    )

    return {
        'ledger_available': ledger_available,
        'ledger_only': sorted(ledger_accounts - db_accounts),
        'db_only': sorted(db_leaf_accounts - ledger_accounts),
        'matched': sorted(ledger_accounts & db_accounts),
    }


def import_declared_accounts(user) -> dict:
    """把账本中显式 open、平台缺失的账户导入平台（Git 用户专用）

    只新增平台账户：不写账本（账本已有 open），不推断币种，默认启用。
    同名账户已存在（如作为父账户）或路径不合法时跳过，不中断其余账户导入。

    Returns:
        dict: {'created': list[str], 'skipped': list[str], 'errors': list[str]}
    """
    empty = {'created': [], 'skipped': [], 'errors': []}
    if not _is_git_user(user):
        return empty

    diff = diff_ledger_accounts(user)
    if not diff['ledger_available']:
        return empty

    created, skipped, errors = [], [], []
    for name in diff['ledger_only']:
        if not is_valid_beancount_account_path(name):
            skipped.append(name)
            continue
        if Account.objects.filter(owner=user, account=name).exists():
            skipped.append(name)
            continue

        account = Account(account=name, owner=user, enable=True)
        account._skip_ledger_sync = True
        try:
            account.save()
        except (ValidationError, IntegrityError) as e:
            errors.append(f"{name}: {e}")
            continue
        created.append(name)

    return {'created': created, 'skipped': skipped, 'errors': errors}


def apply_pairings(user, pairs):
    """按用户确认的旧 → 新配对更新平台账户（Git 用户）

    用户已在账本中完成改名，这里只同步数据库，不写账本。
    """
    if not _is_git_user(user):
        raise ValidationError("非 Git 用户请直接在平台改名")

    ledger_accounts = declared_accounts(user)
    referenced = referenced_accounts(user)
    applied = []

    with transaction.atomic():
        for pair in pairs:
            old_name = pair['old_account']
            new_name = pair['new_account']

            account = Account.objects.filter(owner=user, account=old_name).first()
            if account is None:
                raise ValidationError(f"平台不存在账户 {old_name}")
            if new_name == old_name:
                raise ValidationError(f"新旧账户相同: {old_name}")

            if Account.objects.filter(owner=user, account=new_name).exclude(
                pk=account.pk
            ).exists():
                raise ValidationError(f"账户已存在: {new_name}")

            has_children = account.children.exists()
            if not has_children and new_name not in ledger_accounts:
                raise ValidationError(f"账本中未找到 {new_name} 的 open")

            remaining = sorted(
                name
                for name in referenced
                if name == old_name or name.startswith(old_name + ':')
            )
            if remaining:
                raise ValidationError(
                    f"账本中仍存在旧账户引用: {', '.join(remaining)}"
                )

            descendants = list(
                Account.objects.filter(
                    owner=user, account__startswith=old_name + ':'
                ).order_by('account')
            )
            rename_pairs = [
                (child.account, new_name + child.account[len(old_name):])
                for child in descendants
            ]
            conflicts = set(
                Account.objects.filter(
                    owner=user,
                    account__in=[new_child for _old, new_child in rename_pairs],
                ).values_list('account', flat=True)
            )
            if conflicts:
                raise ValidationError(
                    f"子账户改名后路径冲突: {', '.join(sorted(conflicts))}"
                )

            account._skip_children_rename = True
            account.account = new_name
            account.save()
            for child, (_old_child, new_child) in zip(descendants, rename_pairs):
                Account.objects.filter(pk=child.pk).update(account=new_child)

            applied.append({
                'old_account': old_name,
                'new_account': new_name,
                'renamed_accounts': [(old_name, new_name)] + rename_pairs,
            })

    return applied
