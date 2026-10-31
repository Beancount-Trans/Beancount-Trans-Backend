from datetime import datetime, time

from django.core.management.base import BaseCommand
from django.utils import timezone

from project.apps.usage.models import Dimension
from project.apps.usage.services import aggregate


class Command(BaseCommand):
    help = '按主体与时间区间汇总用量（运维排查用，不是对外接口）'

    def add_arguments(self, parser):
        parser.add_argument('--owner-id', type=int, required=True, help='计费主体 ID（用户 ID）')
        parser.add_argument('--start', default=None, help='起始日期 YYYY-MM-DD，默认本月 1 日')
        parser.add_argument('--end', default=None, help='结束日期 YYYY-MM-DD，默认今天')
        parser.add_argument(
            '--dimension',
            action='append',
            default=None,
            help=f'计量维度，可重复；默认全部（{", ".join(Dimension.values)}）',
        )

    def handle(self, *args, **options):
        today = timezone.localdate()
        start_date = self._parse_date(options['start']) or today.replace(day=1)
        end_date = self._parse_date(options['end']) or today
        dimensions = options['dimension'] or list(Dimension.values)

        start = self._aware(start_date, time.min)
        end = self._aware(end_date, time.max)

        self.stdout.write(f'主体 {options["owner_id"]} | {start_date} ~ {end_date}')
        self.stdout.write('')
        self.stdout.write('| 维度 | 用量 | 单位 | 事件数 | 来源 |')
        self.stdout.write('| :--- | ---: | :--- | ---: | :--- |')
        for dimension in dimensions:
            item = aggregate(
                owner_id=options['owner_id'],
                dimension=dimension,
                start=start,
                end=end,
            )
            self.stdout.write(
                f'| {item["dimension"]} | {item["quantity"]} | {item["unit"]} '
                f'| {item["events"]} | {item["source"]} |'
            )

    @staticmethod
    def _parse_date(value):
        return datetime.strptime(value, '%Y-%m-%d').date() if value else None

    @staticmethod
    def _aware(day, at):
        value = datetime.combine(day, at)
        return timezone.make_aware(value) if timezone.is_naive(value) else value
