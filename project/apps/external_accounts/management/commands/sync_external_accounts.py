from django.core.management.base import BaseCommand

from project.apps.external_accounts.legacy_split import SPLIT_ACCOUNT_PATHS
from project.apps.external_accounts.legacy_sync import sync_owner


class Command(BaseCommand):
    help = '把既有资产映射回填为外部账户、别名与账本账户绑定'

    def add_arguments(self, parser):
        parser.add_argument('--owner', type=int, help='只处理指定用户 ID')
        parser.add_argument('--all', action='store_true', help='处理所有拥有资产映射的用户')
        parser.add_argument(
            '--open-accounts',
            action='store_true',
            help='为平台拆分产生的账户补写账本 open（数据迁移不触发 Account.save）',
        )

    def handle(self, *args, **options):
        from project.apps.maps.models import Assets

        owner_ids = []
        if options['owner']:
            owner_ids = [options['owner']]
        elif options['all']:
            owner_ids = list(
                Assets.objects.values_list('owner_id', flat=True).distinct().order_by('owner_id')
            )
        else:
            self.stderr.write('需要指定 --owner 或 --all')
            return

        for owner_id in owner_ids:
            stats = sync_owner(owner_id)
            self.stdout.write(
                f"owner={owner_id} created={stats.created} bound={stats.bound} "
                f"aliases={stats.aliases} skipped={stats.skipped} conflicts={len(stats.conflicts)}"
            )
            for conflict in stats.conflicts:
                self.stdout.write(f"  - {conflict}")

            if options['open_accounts']:
                self._open_accounts(owner_id)

    def _open_accounts(self, owner_id):
        from project.apps.account.ledger_sync import sync_open
        from project.apps.account.models import Account

        accounts = Account.objects.filter(
            owner_id=owner_id, account__in=SPLIT_ACCOUNT_PATHS, enable=True
        )
        for account in accounts:
            try:
                result = sync_open(account)
            except Exception as e:
                self.stderr.write(f"owner={owner_id} {account.account} 补写 open 失败: {e}")
                continue
            if result.text:
                self.stdout.write(f"owner={owner_id} {account.account} 已补写 open")
