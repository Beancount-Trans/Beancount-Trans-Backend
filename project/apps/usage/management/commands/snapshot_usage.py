from django.core.management.base import BaseCommand

from project.apps.usage.tasks import snapshot_usage_task


class Command(BaseCommand):
    help = '采集存储类用量快照（等价于每日定时任务），可指定日期补跑'

    def add_arguments(self, parser):
        parser.add_argument(
            '--period',
            default=None,
            help='统计日期 YYYY-MM-DD，默认今天；重复执行覆盖同一日快照',
        )

    def handle(self, *args, **options):
        result = snapshot_usage_task(period=options['period'])
        self.stdout.write(self.style.SUCCESS(f'快照完成: {result}'))
