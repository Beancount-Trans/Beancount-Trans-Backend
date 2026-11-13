"""条目审核关注度判定。

审核页默认只展示「需关注」条目：账户落到兜底账户，或结构无法解析的交易。
其余条目视为自动通过，仅在「全部」视图展示，确认写入时仍会一并写入账本。
"""
import re
from typing import Any, Dict, List, Optional, Tuple

ASSET_FALLBACK = 'Assets:Other'
CATEGORY_FALLBACKS = ('Expenses:Other', 'Income:Other')
FALLBACK_PREFIXES = (ASSET_FALLBACK,) + CATEGORY_FALLBACKS

POSTING_ACCOUNT_RE = re.compile(
    r'^\s+[!*]?\s*((?:Assets|Expenses|Income|Liabilities|Equity)(?::[^\s]+)*)(?=\s|$)'
)
HEADER_LINE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}\s')

REASON_ASSET_FALLBACK = 'asset_fallback'
REASON_CATEGORY_FALLBACK = 'category_fallback'
REASON_BALANCE_FALLBACK = 'balance_fallback'
REASON_UNPARSABLE = 'unparsable'


def entry_text(entry: Dict[str, Any]) -> str:
    """取条目的编辑后文本，缺省回退到原始格式化文本。"""
    text = entry.get('edited_formatted') or entry.get('formatted') or ''
    return str(text).replace('\r\n', '\n')


def extract_posting_accounts(text: str) -> List[str]:
    """按行提取过账账户，忽略元数据行与交易头行。"""
    accounts: List[str] = []
    for line in text.split('\n'):
        match = POSTING_ACCOUNT_RE.match(line)
        if match:
            accounts.append(match.group(1))
    return accounts


def _is_under(account: str, base: str) -> bool:
    """账户是否等于 base 或位于其子层级，避免 Assets:Otherwise 被误判。"""
    return bool(base) and (account == base or account.startswith(base + ':'))


def classify_entry(
    entry: Dict[str, Any],
    fallback_account: Optional[str] = None,
) -> Tuple[bool, Optional[str], Optional[str]]:
    """判定条目是否需要人工审核。

    Returns:
        (needs_review, reason, matched_account)
    """
    text = entry_text(entry)
    if not text.strip():
        return True, REASON_UNPARSABLE, None

    header_count = sum(1 for line in text.split('\n') if HEADER_LINE_RE.match(line))
    if header_count != 1:
        return True, REASON_UNPARSABLE, None

    accounts = extract_posting_accounts(text)
    if len(accounts) < 2:
        return True, REASON_UNPARSABLE, None

    for account in accounts:
        if _is_under(account, ASSET_FALLBACK):
            return True, REASON_ASSET_FALLBACK, account
        if any(_is_under(account, base) for base in CATEGORY_FALLBACKS):
            return True, REASON_CATEGORY_FALLBACK, account
        if fallback_account and _is_under(account, fallback_account):
            return True, REASON_BALANCE_FALLBACK, account

    return False, None, None


def annotate_entry(
    entry: Dict[str, Any],
    fallback_account: Optional[str] = None,
) -> Dict[str, Any]:
    """返回带关注度字段的条目副本，不修改原缓存对象。"""
    annotated = dict(entry)
    needs_review, reason, account = classify_entry(entry, fallback_account)
    annotated['needs_review'] = needs_review
    annotated['review_reason'] = reason
    annotated['review_account'] = account
    return annotated


def annotate_entries(
    entries: Optional[List[Dict[str, Any]]],
    fallback_account: Optional[str] = None,
) -> List[Dict[str, Any]]:
    return [annotate_entry(entry, fallback_account) for entry in entries or []]


def count_needs_review(
    entries: Optional[List[Dict[str, Any]]],
    fallback_account: Optional[str] = None,
) -> int:
    return sum(
        1
        for entry in entries or []
        if classify_entry(entry, fallback_account)[0]
    )
