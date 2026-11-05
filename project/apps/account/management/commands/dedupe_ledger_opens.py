# project/apps/account/management/commands/dedupe_ledger_opens.py
"""清理平台账本文件（trans/collect.bean）中重复的 open

用于修复存量用户：注册时平台写入的 open 与之后引入的模板 ``account/*.bean``
重复，导致 Beancount 报 ``Duplicate open directive``。

用法:
    python manage.py dedupe_ledger_opens --all-users --dry-run
    python manage.py dedupe_ledger_opens --user 13362459561
    python manage.py dedupe_ledger_opens --all-users --report-only
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from project.apps.account.ledger_dedupe import (
    collect_account_errors,
    dedupe_platform_opens,
)
from project.utils.file import BeanFileManager

User = get_user_model()


class Command(BaseCommand):
    help = '删除 trans/collect.bean 中与其它生效账本文件重复的 open'

    def add_arguments(self, parser):
        parser.add_argument(
            '--all-users',
            action='store_true',
            help='处理所有用户',
        )
        parser.add_argument(
            '--user',
            type=str,
            help='仅处理指定用户（用户名）',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='只打印将删除的 open 与需改写的 note，不写盘',
        )
        parser.add_argument(
            '--report-only',
            action='store_true',
            help='只输出账户类错误（文件:行号），不改文件',
        )

    def handle(self, *args, **options):
        all_users = options['all_users']
        username = options.get('user')
        dry_run = options['dry_run']
        report_only = options['report_only']

        if bool(all_users) == bool(username):
            self.stdout.write(self.style.ERROR('请且仅指定 --all-users 或 --user 之一'))
            return

        if all_users:
            users = User.objects.all().order_by('id')
        else:
            try:
                users = [User.objects.get(username=username)]
            except User.DoesNotExist:
                self.stdout.write(self.style.ERROR(f'用户 {username} 不存在'))
                return

        if dry_run:
            self.stdout.write(self.style.WARNING('=== 模拟运行（不会修改文件）==='))

        total_removed = 0
        total_rewritten = 0
        total_residual = 0
        total_errors = 0
        processed = 0

        for user in users:
            assets_path = BeanFileManager.get_user_assets_path(user)
            main_path = BeanFileManager.get_main_bean_path(user)

            if report_only:
                errors = collect_account_errors(main_path)
                processed += 1
                if not errors:
                    self.stdout.write(f'用户 {user.username}: 无账户类错误')
                    continue
                total_errors += len(errors)
                self.stdout.write(
                    self.style.WARNING(
                        f'用户 {user.username}: 账户类错误 {len(errors)} 个'
                    )
                )
                for error in errors:
                    location = error.get('file') or main_path
                    line = error.get('line')
                    suffix = f':{line}' if line else ''
                    self.stdout.write(f'  - {location}{suffix} {error["message"]}')
                continue

            result = dedupe_platform_opens(user, dry_run=dry_run)
            processed += 1
            total_removed += len(result.removed_opens)
            total_rewritten += len(result.rewritten_notes)
            total_residual += len(result.residual_errors)

            if not result.changed and not result.residual_errors:
                self.stdout.write(f'用户 {user.username}: 无需处理（{assets_path}）')
                continue

            self.stdout.write(
                self.style.SUCCESS(
                    f'用户 {user.username}: 删除 open {len(result.removed_opens)} 条，'
                    f'改写 note {len(result.rewritten_notes)} 条'
                )
            )
            for line in result.removed_opens:
                self.stdout.write(f'  - 删除: {line}')
            for line in result.rewritten_notes:
                self.stdout.write(f'  - 改写: {line}')
            for error in result.residual_errors:
                location = error.get('file') or main_path
                line = error.get('line')
                suffix = f':{line}' if line else ''
                self.stdout.write(
                    self.style.ERROR(f'  ! {location}{suffix} {error["message"]}')
                )

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('=== 完成 ==='))
        self.stdout.write(f'处理用户数: {processed}')
        if report_only:
            self.stdout.write(f'账户类错误: {total_errors}')
        else:
            self.stdout.write(f'删除 open: {total_removed}')
            self.stdout.write(f'改写 note: {total_rewritten}')
            self.stdout.write(f'残留账户类错误: {total_residual}')
