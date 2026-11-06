"""账本 → 平台账户核对报告命令

用法：
    python manage.py reconcile_ledger_accounts --user <username>
    python manage.py reconcile_ledger_accounts --all-users
    python manage.py reconcile_ledger_accounts --all-users --json

只输出差集，不修改数据库。
"""
import json

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from project.apps.account.account_reconcile import diff_ledger_accounts

User = get_user_model()


class Command(BaseCommand):
    help = '对比账本 open 账户与平台账户，输出新增/缺失候选（不修改数据）'

    def add_arguments(self, parser):
        parser.add_argument('--user', help='只处理指定用户名')
        parser.add_argument('--all-users', action='store_true', help='处理全部用户')
        parser.add_argument('--json', action='store_true', help='以 JSON 输出')

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

        reports = []
        for user in users:
            result = diff_ledger_accounts(user)
            report = {'username': user.username, **result}
            reports.append(report)

            if options.get('json'):
                continue

            self.stdout.write(f'用户 {user.username}:')
            if not result['ledger_available']:
                self.stdout.write('  账本不可用（main.bean 不存在），跳过对比')
                continue
            self.stdout.write(f"  账本有、平台无: {', '.join(result['ledger_only']) or '-'}")
            self.stdout.write(f"  平台有、账本无: {', '.join(result['db_only']) or '-'}")
            self.stdout.write(f"  两边一致: {len(result['matched'])} 个")

        if options.get('json'):
            self.stdout.write(json.dumps(reports, ensure_ascii=False, indent=2))
