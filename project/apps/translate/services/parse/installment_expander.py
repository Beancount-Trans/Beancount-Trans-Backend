"""将信用卡分期账单行展开为 purchase + N 期负债条目。"""
from __future__ import annotations

import calendar
import copy
import re
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, List, Optional, Set

from project.apps.translate.views.AliPay import alipay_is_refund_row, alipay_parent_uuid

PAYABLES_ACCOUNT = "Liabilities:Payables"
INSTALLMENT_COUNT_RE = re.compile(r"(\d+)\s*期")
SUCCESS_STATUSES = frozenset({"交易成功", "支付成功"})


def parse_installment_count(payment_method: str) -> Optional[int]:
    if not payment_method or "分期" not in payment_method:
        return None
    match = INSTALLMENT_COUNT_RE.search(payment_method)
    if not match:
        return None
    count = int(match.group(1))
    return count if count >= 2 else None


def collect_refund_parent_uuids(bill_data: List[Dict]) -> Set[str]:
    parents: Set[str] = set()
    for row in bill_data:
        if alipay_is_refund_row(row):
            parent = alipay_parent_uuid(row)
            if parent:
                parents.add(parent)
    return parents


def should_expand(row: Dict, refund_parent_uuids: Set[str]) -> bool:
    if alipay_is_refund_row(row):
        return False
    if row.get("transaction_status") not in SUCCESS_STATUSES:
        return False
    if not parse_installment_count(row.get("payment_method") or ""):
        return False
    uid = (row.get("uuid") or "").strip()
    if uid and uid in refund_parent_uuids:
        return False
    return True


def split_installment_amounts(total: Decimal, n: int) -> List[Decimal]:
    if n <= 0:
        raise ValueError("n must be positive")
    per = (total / n).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    amounts = [per] * (n - 1)
    amounts.append(total - sum(amounts))
    return amounts


def add_months(base_date: datetime, months: int) -> datetime:
    year = base_date.year
    month = base_date.month + months
    day = base_date.day
    while month > 12:
        month -= 12
        year += 1
    while month < 1:
        month += 12
        year -= 1
    max_day = calendar.monthrange(year, month)[1]
    day = min(day, max_day)
    return base_date.replace(year=year, month=month, day=day)


def _format_amount(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}"


def expand_installment_entries(parsed_entry: Dict) -> List[Dict]:
    row = parsed_entry.get("_original_row") or {}
    count = parse_installment_count(row.get("payment_method") or "")
    if not count:
        return [parsed_entry]

    total = Decimal(str(parsed_entry["amount"]))
    original_expense = parsed_entry["expense"]
    credit_card = parsed_entry["account"]
    original_row_copy = copy.deepcopy(row)

    tx_time = row.get("transaction_time") or f"{parsed_entry['date']} {parsed_entry['time']}"
    base_dt = datetime.strptime(tx_time, "%Y-%m-%d %H:%M:%S")
    period_amounts = split_installment_amounts(total, count)

    purchase = copy.deepcopy(parsed_entry)
    purchase["expense"] = original_expense
    purchase["account"] = PAYABLES_ACCOUNT
    purchase["amount"] = _format_amount(total)
    purchase["installment_role"] = "purchase"
    purchase["installment_period"] = 0
    purchase["_original_row"] = original_row_copy

    entries: List[Dict] = [purchase]

    for period_idx, period_amount in enumerate(period_amounts):
        inst = copy.deepcopy(parsed_entry)
        inst_date = add_months(base_dt, period_idx)
        inst["date"] = inst_date.strftime("%Y-%m-%d")
        inst["balance_date"] = (inst_date + timedelta(days=1)).strftime("%Y-%m-%d")
        inst["expense"] = PAYABLES_ACCOUNT
        inst["account"] = credit_card
        inst["amount"] = _format_amount(period_amount)
        # 折扣/补贴只影响原单，每期还款是确定的负债冲销，不应再补收入行
        inst["discount"] = False
        inst["installment_role"] = "installment"
        inst["installment_period"] = period_idx
        inst["selected_expense_key"] = None
        inst["expense_candidates_with_score"] = []
        inst["_original_row"] = original_row_copy
        entries.append(inst)

    return entries


def expand_parsed_entry(
    parsed_entry: Dict,
    refund_parent_uuids: Optional[Set[str]] = None,
) -> List[Dict]:
    row = parsed_entry.get("_original_row") or {}
    parents = refund_parent_uuids if refund_parent_uuids is not None else set()
    if not should_expand(row, parents):
        return [parsed_entry]
    return expand_installment_entries(parsed_entry)


def pick_reparse_slice(
    entries: List[Dict],
    installment_role: Optional[str] = None,
    installment_period: Optional[int] = None,
) -> Dict:
    if len(entries) == 1:
        return entries[0]
    if installment_role:
        for entry in entries:
            if entry.get("installment_role") != installment_role:
                continue
            if installment_role == "installment" and installment_period is not None:
                if entry.get("installment_period") == installment_period:
                    return entry
            elif installment_role == "purchase":
                return entry
    for entry in entries:
        if entry.get("installment_role") == "purchase":
            return entry
    return entries[0]


def resolve_reparse_entry(
    parsed_entry: Dict,
    original_row: Dict,
    installment_role: Optional[str] = None,
    installment_period: Optional[int] = None,
    refund_parent_uuids: Optional[Set[str]] = None,
) -> Dict:
    parsed_entry["_original_row"] = original_row
    expanded = expand_parsed_entry(parsed_entry, refund_parent_uuids or set())
    return pick_reparse_slice(expanded, installment_role, installment_period)
