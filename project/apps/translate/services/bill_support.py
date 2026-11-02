"""按账单类型汇总真实解析成功记录，供解析首页展示可用性。"""
import logging
from datetime import date, datetime
from typing import Optional

from django.utils import timezone

from project.apps.translate.models import BillParseStat
from project.apps.translate.utils import (
    BILL_ALI,
    BILL_BOC_DEBIT,
    BILL_CCB_DEBIT,
    BILL_CMB_CREDIT,
    BILL_ICBC_DEBIT,
    BILL_WECHAT,
)

logger = logging.getLogger(__name__)

BILL_SUPPORT_TYPES = (
    BILL_WECHAT,
    BILL_ALI,
    BILL_ICBC_DEBIT,
    BILL_CCB_DEBIT,
    BILL_BOC_DEBIT,
    BILL_CMB_CREDIT,
)


def _parse_date(value) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.strip()[:10], '%Y-%m-%d').date()
    except ValueError:
        return None


def extract_latest_bill_date(context: dict) -> Optional[date]:
    """取账单原始交易日期最大值；不取分期展开后的未来日期。"""
    try:
        raw_dates = [
            _parse_date((row or {}).get('transaction_time'))
            for row in (context.get('initialized_bill') or [])
        ]
        valid_raw = [item for item in raw_dates if item]
        if valid_raw:
            return max(valid_raw)

        parsed_dates = [
            _parse_date((entry or {}).get('date'))
            for entry in (context.get('parsed_data') or [])
        ]
        valid_parsed = [item for item in parsed_dates if item]
        return max(valid_parsed) if valid_parsed else None
    except Exception:
        logger.exception('提取账单最新交易日期失败')
        return None


def record_parse_success(bill_type, latest_bill_date: Optional[date] = None) -> None:
    """记录一次成功解析；统计失败不影响解析主流程。"""
    if bill_type not in BILL_SUPPORT_TYPES:
        return
    try:
        stat, created = BillParseStat.objects.get_or_create(
            bill_type=bill_type,
            defaults={
                'last_success_at': timezone.now(),
                'latest_bill_date': latest_bill_date,
            },
        )
        if created:
            return
        stat.last_success_at = timezone.now()
        if latest_bill_date and (
            stat.latest_bill_date is None or latest_bill_date > stat.latest_bill_date
        ):
            stat.latest_bill_date = latest_bill_date
        stat.save(update_fields=['last_success_at', 'latest_bill_date', 'updated_at'])
    except Exception:
        logger.exception('记录账单解析成功状态失败: bill_type=%s', bill_type)


def build_bill_support_snapshot() -> list:
    """按固定顺序返回全部支持账单类型，无记录的类型补 null。"""
    stats = {item.bill_type: item for item in BillParseStat.objects.all()}
    snapshot = []
    for bill_type in BILL_SUPPORT_TYPES:
        stat = stats.get(bill_type)
        snapshot.append({
            'bill_type': bill_type,
            'last_success_at': stat.last_success_at.isoformat() if stat else None,
            'latest_bill_date': (
                stat.latest_bill_date.isoformat()
                if stat and stat.latest_bill_date
                else None
            ),
        })
    return snapshot
