"""用量计量服务：写入原始事实与聚合查询。

设计约束：
- 计量失败绝不阻断业务：所有写入函数内部吞异常，只记日志。
- 表里不出现金额、单价、余额，货币只存在于 Cloud 侧。
- 事件是"累加量"，快照是"时点量"，两者不要混用。
"""
import logging
from datetime import date, datetime
from uuid import uuid4

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Count, Sum
from django.utils import timezone

from .models import Dimension, OwnerType, UsageEvent, UsageSnapshot

logger = logging.getLogger(__name__)

# 各维度默认单位
DIMENSION_UNITS = {
    Dimension.PARSE: 'count',
    Dimension.BQL: 'count',
    Dimension.LLM_TOKENS: 'tokens',
    Dimension.STORAGE_FILE: 'B',
    Dimension.STORAGE_REPO: 'B',
}

SNAPSHOT_DIMENSIONS = (Dimension.STORAGE_FILE, Dimension.STORAGE_REPO)


def metering_enabled() -> bool:
    return bool(getattr(settings, 'USAGE_METERING_ENABLED', True))


def record_event(
    *,
    event_key: str,
    dimension: str,
    quantity: int,
    unit: str = '',
    source_type: str,
    owner_type: str = OwnerType.USER,
    owner_id: int | None = None,
    source_id: str = '',
    status: str = 'ok',
    occurred_at: datetime | None = None,
    meta: dict | None = None,
) -> UsageEvent | None:
    """写入一条用量事件；重复 event_key 视为幂等，返回 None。"""
    if not metering_enabled():
        return None
    try:
        # 用 savepoint 包住：幂等冲突回滚到此处，不污染外层事务
        with transaction.atomic():
            return UsageEvent.objects.create(
                event_key=event_key,
                owner_type=owner_type,
                owner_id=owner_id,
                dimension=dimension,
                quantity=max(int(quantity), 0),
                unit=unit or DIMENSION_UNITS.get(dimension, 'count'),
                source_type=source_type,
                source_id=str(source_id or ''),
                status=status,
                occurred_at=occurred_at or timezone.now(),
                meta=meta or {},
            )
    except IntegrityError:
        # 幂等键冲突：同一次业务动作被重复记录，忽略
        return None
    except Exception:
        logger.exception('写入用量事件失败: dimension=%s key=%s', dimension, event_key)
        return None


def record_parse(*, task_id: str, user_id: int | None, status: str, entries: int = 0, error: str = ''):
    """记录一次解析任务（成功与失败都记）。"""
    return record_event(
        event_key=f'parse:{task_id}',
        dimension=Dimension.PARSE,
        quantity=1,
        source_type='parse_job',
        owner_type=OwnerType.USER if user_id else OwnerType.PLATFORM,
        owner_id=user_id,
        source_id=str(task_id or ''),
        status=status,
        meta={'entries': int(entries or 0), 'error': (error or '')[:500]},
    )


def record_bql(
    *,
    user_id: int | None,
    ok: bool,
    row_count: int = 0,
    error: str = '',
    source_type: str = 'assistant_bql',
):
    """记录一次 BQL 调用尝试（含失败与被拒）。"""
    return record_event(
        event_key=f'bql:{uuid4().hex}',
        dimension=Dimension.BQL,
        quantity=1,
        source_type=source_type,
        owner_type=OwnerType.USER if user_id else OwnerType.PLATFORM,
        owner_id=user_id,
        status='ok' if ok else 'failed',
        meta={'row_count': int(row_count or 0), 'error': (error or '')[:500]},
    )


def record_llm_tokens(
    *,
    user_id: int | None,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    model: str = '',
    byok: bool = False,
    source_type: str = 'assistant_turn',
    status: str = 'ok',
    reason: str = '',
):
    """记录一轮 LLM 调用的 token 用量。

    include_usage 未生效时上游传 0 并给 status='failed'、reason='no_usage'，
    这样"拿不到用量"本身会被统计出来，而不是静默消失。
    """
    total = int(prompt_tokens or 0) + int(completion_tokens or 0)
    meta = {
        'prompt': int(prompt_tokens or 0),
        'completion': int(completion_tokens or 0),
        'model': model,
        'byok': bool(byok),
    }
    if reason:
        meta['reason'] = reason
    return record_event(
        event_key=f'llm:{uuid4().hex}',
        dimension=Dimension.LLM_TOKENS,
        quantity=total,
        source_type=source_type,
        owner_type=OwnerType.USER if user_id else OwnerType.PLATFORM,
        owner_id=user_id,
        status=status,
        meta=meta,
    )


def record_snapshot(
    *,
    dimension: str,
    amount: int,
    owner_id: int | None,
    period: date,
    owner_type: str = OwnerType.USER,
    unit: str = 'B',
    measured_at: datetime | None = None,
) -> UsageSnapshot | None:
    """按时点覆盖写入快照，同一 (主体, 维度, 日期) 只保留一条。"""
    if not metering_enabled():
        return None
    try:
        with transaction.atomic():
            snapshot, _ = UsageSnapshot.objects.update_or_create(
                owner_type=owner_type,
                owner_id=owner_id,
                dimension=dimension,
                period=period,
                defaults={
                    'amount': max(int(amount), 0),
                    'unit': unit,
                    'measured_at': measured_at or timezone.now(),
                },
            )
        return snapshot
    except Exception:
        logger.exception('写入用量快照失败: dimension=%s owner=%s', dimension, owner_id)
        return None


def aggregate(*, owner_id: int, dimension: str, start: datetime, end: datetime, owner_type: str = OwnerType.USER) -> dict:
    """聚合某主体在时间区间内的用量。

    事件类维度累加 quantity；快照类维度取区间内最新一条。
    """
    if dimension in SNAPSHOT_DIMENSIONS:
        snapshot = (
            UsageSnapshot.objects
            .filter(
                owner_type=owner_type,
                owner_id=owner_id,
                dimension=dimension,
                period__gte=start.date(),
                period__lte=end.date(),
            )
            .order_by('-period')
            .first()
        )
        return {
            'dimension': dimension,
            'quantity': snapshot.amount if snapshot else 0,
            'unit': snapshot.unit if snapshot else DIMENSION_UNITS.get(dimension, 'B'),
            'events': 1 if snapshot else 0,
            'source': 'snapshot',
        }

    result = (
        UsageEvent.objects
        .filter(
            owner_type=owner_type,
            owner_id=owner_id,
            dimension=dimension,
            occurred_at__gte=start,
            occurred_at__lte=end,
        )
        .aggregate(total=Sum('quantity'), events=Count('id'))
    )
    return {
        'dimension': dimension,
        'quantity': result['total'] or 0,
        'unit': DIMENSION_UNITS.get(dimension, 'count'),
        'events': result['events'] or 0,
        'source': 'event',
    }
