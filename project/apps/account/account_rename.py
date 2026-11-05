"""非 Git 用户的账户改名

平台只改写自己可管理的账本文件（``trans/`` 与非 Git 的历史月度文件），
不触碰 ``main.bean`` / ``account/**``。流程：

1. 校验新名称、检查用户区是否存在引用；
2. 快照 → 改写平台区账户引用 → 整本校验；
3. 校验通过后在同一事务内更新账户及其子账户；
4. 任一步失败都恢复文件快照。

Git 用户不允许在平台直接改名：其账本以远端为准，应先在账本中改名，
同步后再通过反向对账确认。
"""
import logging
import os
import re
from collections import Counter, namedtuple

from django.core.exceptions import ValidationError
from django.db import transaction

from project.apps.account.ledger_dedupe import collect_account_errors
from project.apps.account.ledger_sync import (
    _is_git_user,
    _is_platform_managed,
    _iter_bean_file_paths,
    _rel_path,
    _split_inline_comment,
)
from project.apps.account.models import (
    Account,
    is_root_account_path,
    is_valid_beancount_account_path,
)
from project.utils.file import BeanFileManager

logger = logging.getLogger(__name__)

RenameResult = namedtuple(
    'RenameResult',
    ['old_account', 'new_account', 'changed_paths', 'renamed_accounts'],
)

def _validate_new_name(account, new_name):
    if not new_name:
        raise ValidationError("新的账户路径不能为空")

    if is_root_account_path(new_name):
        raise ValidationError(
            "Beancount 不允许只含根账户的路径，账户至少需要一级子账户"
        )

    if not is_valid_beancount_account_path(new_name):
        raise ValidationError(
            "账户路径必须由根账户和至少一级子账户组成，"
            "用冒号分隔，且只能包含字母、数字和连字符"
        )

    if new_name == account.account:
        raise ValidationError("新的账户路径与当前一致")

    if new_name.startswith(account.account + ':'):
        raise ValidationError("不能把账户改名到自己的子账户路径下")

    if Account.objects.filter(owner=account.owner, account=new_name).exclude(
        pk=account.pk
    ).exists():
        raise ValidationError("该账户路径已存在，请勿重复添加")


def _account_ref_pattern(account_name):
    """账户名 token 前缀正则：可匹配同名账户及其子账户前缀"""
    return re.compile(r'(?<![\w:-])' + re.escape(account_name) + r'(?![\w-])')


def _rewrite_text(text, pattern, new_account):
    """按行改写账户引用，跳过行尾注释与双引号内文本"""
    lines = []
    for line in text.splitlines():
        code, comment = _split_inline_comment(line)
        parts = code.split('"')
        for index in range(0, len(parts), 2):
            parts[index] = pattern.sub(new_account, parts[index])
        lines.append('"'.join(parts) + comment)

    new_text = '\n'.join(lines)
    if text.endswith('\n'):
        new_text += '\n'
    return new_text


def _write_atomic(path, text):
    directory = os.path.dirname(os.path.abspath(path))
    tmp_path = os.path.join(directory, f'.{os.path.basename(path)}.rename.tmp')
    with open(tmp_path, 'w', encoding='utf-8') as f:
        f.write(text)
    os.replace(tmp_path, path)


def _restore_snapshots(snapshots):
    for path, text in snapshots.items():
        try:
            _write_atomic(path, text)
        except OSError as e:  # pragma: no cover - 恢复失败只能记录
            logger.error("恢复账本文件失败 %s: %s", path, e)


def _error_counter(user):
    main_path = BeanFileManager.get_main_bean_path(user)
    return Counter(
        error['message'] for error in collect_account_errors(main_path)
    )


def rename_account(account, new_name) -> RenameResult:
    """改名并同步平台区账本；仅适用于非 Git 用户"""
    if _is_git_user(account.owner):
        raise ValidationError(
            "Git 用户的账本以远端仓库为准，请在账本中完成改名并同步，"
            "再在平台中确认账户对账结果"
        )

    _validate_new_name(account, new_name)

    old_name = account.account
    pattern = _account_ref_pattern(old_name)

    platform_files = []
    blocked_paths = []
    for file_path, root in _iter_bean_file_paths(account.owner):
        rel_path = _rel_path(root, file_path)
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                text = f.read()
        except OSError as e:
            logger.warning("读取账本文件失败，跳过 %s: %s", file_path, e)
            continue

        if not _is_platform_managed(account.owner, file_path):
            if pattern.search(text):
                blocked_paths.append(rel_path)
            continue

        platform_files.append((file_path, rel_path, text))

    if blocked_paths:
        raise ValidationError(
            "账户引用位于用户区文件 "
            f"{', '.join(blocked_paths)}，平台不能安全改写；"
            "请先在账本中手动完成改名"
        )

    before_errors = _error_counter(account.owner)

    snapshots = {}
    changed_paths = []
    for file_path, rel_path, text in platform_files:
        new_text = _rewrite_text(text, pattern, new_name)
        if new_text == text:
            continue
        snapshots[file_path] = text
        _write_atomic(file_path, new_text)
        changed_paths.append(rel_path)

    after_errors = _error_counter(account.owner)
    added = after_errors - before_errors
    if added:
        _restore_snapshots(snapshots)
        details = '；'.join(f'{message} ×{count}' for message, count in added.items())
        raise ValidationError(f"改名后账本出现新的账户错误，已回滚：{details}")

    descendants = list(
        Account.objects.filter(
            owner=account.owner, account__startswith=old_name + ':'
        ).order_by('account')
    )
    rename_pairs = [
        (child.account, new_name + child.account[len(old_name):])
        for child in descendants
    ]

    existing_names = set(
        Account.objects.filter(
            owner=account.owner,
            account__in=[new_child for _old_child, new_child in rename_pairs],
        ).values_list('account', flat=True)
    )
    if existing_names:
        _restore_snapshots(snapshots)
        raise ValidationError(
            f"子账户改名后路径冲突：{', '.join(sorted(existing_names))}"
        )

    try:
        with transaction.atomic():
            account._skip_children_rename = True
            account.account = new_name
            account.save()
            for child, (_old_child, new_child) in zip(descendants, rename_pairs):
                Account.objects.filter(pk=child.pk).update(account=new_child)
    except Exception:
        _restore_snapshots(snapshots)
        raise

    renamed_accounts = [(old_name, new_name)] + rename_pairs
    return RenameResult(
        old_name,
        new_name,
        changed_paths,
        renamed_accounts,
    )
