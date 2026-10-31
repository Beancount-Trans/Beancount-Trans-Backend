"""用量快照定时任务：采集"时点量"类维度（存储体积）。

事件类维度（parse / bql / llm.tokens）由业务埋点实时写入，不走本任务。
"""
import logging
from datetime import date, timedelta

from celery import shared_task
from django.conf import settings
from django.db.models import Sum
from django.utils import timezone

from .models import Dimension, OwnerType, UsageSnapshot
from .services import record_snapshot

logger = logging.getLogger(__name__)

# 回溯窗口：只对近期有快照的主体补 0，避免全表扫描
_LOOKBACK_DAYS = 30


@shared_task
def snapshot_usage_task(period: str | None = None) -> dict:
    """按日采集存储类用量快照，重复执行覆盖而非累加。"""
    if not getattr(settings, 'USAGE_METERING_ENABLED', True):
        return {'status': 'disabled'}

    target = date.fromisoformat(period) if period else timezone.localdate()
    measured_at = timezone.now()

    file_owners = _snapshot_file_storage(target, measured_at)
    repo_owners = _snapshot_repo_storage(target, measured_at)
    return {
        'status': 'ok',
        'period': target.isoformat(),
        'storage.file': file_owners,
        'storage.repo': repo_owners,
    }


def _recent_owners(dimension: str, period: date) -> set[int]:
    """近期已有快照的主体：即使今天没数据也要写 0，否则会残留旧值。"""
    return set(
        UsageSnapshot.objects
        .filter(
            owner_type=OwnerType.USER,
            dimension=dimension,
            period__gte=period - timedelta(days=_LOOKBACK_DAYS),
        )
        .exclude(owner_id__isnull=True)
        .values_list('owner_id', flat=True)
    )


def _write_owners(dimension: str, totals: dict[int, int], period: date, measured_at) -> int:
    owners = set(totals) | _recent_owners(dimension, period)
    for owner_id in owners:
        record_snapshot(
            dimension=dimension,
            amount=totals.get(owner_id, 0),
            owner_id=owner_id,
            period=period,
            measured_at=measured_at,
        )
    return len(owners)


def _snapshot_file_storage(period: date, measured_at) -> int:
    from project.apps.file_manager.models import File

    rows = (
        File.objects
        .values('owner_id')
        .annotate(total=Sum('size'))
    )
    totals = {
        row['owner_id']: int(row['total'] or 0)
        for row in rows
        if row['owner_id'] is not None
    }
    return _write_owners(Dimension.STORAGE_FILE, totals, period, measured_at)


def _snapshot_repo_storage(period: date, measured_at) -> int:
    from project.apps.git_repository.clients import GiteaAPIClient
    from project.apps.git_repository.models import GitRepository

    if not getattr(settings, 'GITEA_ADMIN_TOKEN', ''):
        logger.info('GITEA_ADMIN_TOKEN 为空，跳过 Git 仓库体积采集')
        return 0

    try:
        client = GiteaAPIClient()
    except Exception:
        logger.exception('初始化 Gitea 客户端失败，跳过 Git 仓库体积采集')
        return 0

    repos = (
        GitRepository.objects
        .filter(provider='gitea_hosted')
        .exclude(gitea_repo_id__isnull=True)
        .only('id', 'owner_id', 'repo_name')
    )
    totals: dict[int, int] = {}
    for repo in repos:
        try:
            totals[repo.owner_id] = int(client.get_repository_size(repo.repo_name) or 0)
        except Exception:
            logger.warning('获取仓库体积失败，跳过: repo=%s', repo.repo_name, exc_info=True)
    return _write_owners(Dimension.STORAGE_REPO, totals, period, measured_at)
