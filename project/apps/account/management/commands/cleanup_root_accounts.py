"""清理不合法的根账户

Beancount 的账户路径至少需要「根账户 + 一级子账户」，``Assets``、``Equity``
这类只含根账户的路径会触发 ``Invalid token``。

本命令用于修复存量数据：
1. 删除平台区账本文件中的根账户指令（open / close / note / balance / pad / document）；
2. 解除子账户对根账户的 parent 引用，并删除根账户数据库记录。

只处理平台区文件；用户区文件中的根账户指令保持不动并输出提示。
"""
import os
import re
from collections import Counter

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from project.apps.account.ledger_dedupe import collect_account_errors
from project.apps.account.ledger_sync import (
    _is_platform_managed,
    _iter_bean_file_paths,
    _rel_path,
)
from project.apps.account.models import ROOT_ACCOUNTS, Account
from project.utils.file import BeanFileManager

User = get_user_model()

ROOT_DIRECTIVE_RE = re.compile(
    r'^\s*\d{4}-\d{2}-\d{2}\s+'
    r'(open|close|note|balance|pad|document)\s+'
    r'(Assets|Liabilities|Equity|Income|Expenses)(?=\s|$)'
)
ROOT_POSTING_RE = re.compile(
    r'^\s+(Assets|Liabilities|Equity|Income|Expenses)(?=\s|$)'
)


def _write_atomic(path, text):
    directory = os.path.dirname(os.path.abspath(path))
    tmp_path = os.path.join(directory, f'.{os.path.basename(path)}.root.tmp')
    with open(tmp_path, 'w', encoding='utf-8') as f:
        f.write(text)
    os.replace(tmp_path, path)


def _restore(snapshots):
    for path, text in snapshots.items():
        try:
            _write_atomic(path, text)
        except OSError:
            pass


def _error_counter(user):
    main_path = BeanFileManager.get_main_bean_path(user)
    return Counter(error['message'] for error in collect_account_errors(main_path))


def _clean_files(user, dry_run):
    changed_paths = []
    skipped_paths = []
    residual = []
    snapshots = {}

    for file_path, root in _iter_bean_file_paths(user):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                text = f.read()
        except OSError:
            continue

        kept = []
        removed = 0
        for line in text.splitlines():
            if ROOT_DIRECTIVE_RE.match(line):
                removed += 1
                continue
            if ROOT_POSTING_RE.match(line):
                residual.append(line.strip())
            kept.append(line)

        if not removed:
            continue

        rel_path = _rel_path(root, file_path)
        if not _is_platform_managed(user, file_path):
            skipped_paths.append(rel_path)
            continue

        new_text = '\n'.join(kept)
        if text.endswith('\n'):
            new_text += '\n'
        snapshots[file_path] = text
        if not dry_run:
            _write_atomic(file_path, new_text)
        changed_paths.append(rel_path)

    return changed_paths, skipped_paths, residual, snapshots


def _clean_database(user, dry_run):
    roots = list(Account.objects.filter(owner=user, account__in=ROOT_ACCOUNTS))
    if not roots or dry_run:
        return len(roots)

    with transaction.atomic():
        for root in roots:
            Account.objects.filter(owner=user, parent=root).update(parent=None)
            root.delete()
    return len(roots)


def cleanup_user(user, dry_run=False):
    before = _error_counter(user)
    changed, skipped, residual, snapshots = _clean_files(user, dry_run)

    if not dry_run and changed:
        after = _error_counter(user)
        added = after - before
        if added:
            _restore(snapshots)
            details = '；'.join(f'{msg} ×{count}' for msg, count in added.items())
            raise CommandError(f'清理后账本出现新的账户错误，已回滚：{details}')

    deleted_roots = _clean_database(user, dry_run)
    return {
        'changed_files': changed,
        'skipped_files': skipped,
        'residual_postings': residual,
        'deleted_root_accounts': deleted_roots,
    }


class Command(BaseCommand):
    help = '删除不合法的根账户（DB）并清理平台区账本中的根账户指令'

    def add_arguments(self, parser):
        parser.add_argument('--user', help='只处理指定用户名')
        parser.add_argument('--all-users', action='store_true', help='处理全部用户')
        parser.add_argument('--dry-run', action='store_true', help='只输出计划，不修改')

    def handle(self, *args, **options):
        username = options.get('user')
        all_users = options.get('all_users')

        if not username and not all_users:
            raise CommandError('必须指定 --user <username> 或 --all-users')
        if username and all_users:
            raise CommandError('--user 与 --all-users 只能二选一')

        if username:
            try:
                users = [User.objects.get(username=username)]
            except User.DoesNotExist:
                raise CommandError(f'用户不存在: {username}')
        else:
            users = User.objects.filter(is_active=True).order_by('username')

        for user in users:
            result = cleanup_user(user, dry_run=options.get('dry_run', False))
            self.stdout.write(f'用户 {user.username}:')
            self.stdout.write(
                f"  清理文件: {', '.join(result['changed_files']) or '-'}"
            )
            self.stdout.write(
                f"  用户区跳过: {', '.join(result['skipped_files']) or '-'}"
            )
            self.stdout.write(
                f"  根账户数据库记录: {result['deleted_root_accounts']}"
            )
            if result['residual_postings']:
                self.stdout.write(
                    '  仍在交易中使用根账户（需手工处理）: '
                    + '; '.join(result['residual_postings'])
                )
