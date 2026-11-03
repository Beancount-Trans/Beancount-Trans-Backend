"""结构化日记账查询：按日期区间返回交易及其分录。

与 `run_bql` 的区别：这里不渲染文本、不做账户路径富化，直接返回面板/助手可直接
消费的结构化数据，并在 Python 侧按交易分组、分页，因此不受 BQL 单次行数上限影响。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from django.contrib.auth.models import User

from project.apps.usage.services import record_bql

from .ledger_query import LedgerNotFoundError, LedgerQueryService
from .metadata_catalog import build_path_to_description_map

#: 单次查询允许的最大自然日跨度，避免一次扫描整本账。
MAX_RANGE_DAYS = 366
#: 单页最大交易数。
MAX_PAGE_SIZE = 500
#: 默认每页交易数。
DEFAULT_PAGE_SIZE = 100


class JournalQueryError(ValueError):
    """日记账参数或查询错误，可直接展示给调用方。"""


@dataclass(frozen=True)
class JournalPosting:
    """一条分录。"""

    account: str
    description: str
    number: str
    currency: str


@dataclass(frozen=True)
class JournalEntry:
    """一笔交易。"""

    id: str
    date: date
    payee: str
    narration: str
    tags: tuple[str, ...]
    postings: tuple[JournalPosting, ...]


@dataclass(frozen=True)
class JournalPage:
    """一页日记账。"""

    start: date
    end: date
    total: int
    offset: int
    limit: int
    entries: tuple[JournalEntry, ...]

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.entries) < self.total

    @property
    def next_offset(self) -> int | None:
        return self.offset + len(self.entries) if self.has_more else None


def resolve_range(start: date | None, end: date | None) -> tuple[date, date]:
    """补全查询区间：缺 end 取今天，缺 start 取 end 往前 30 天。"""
    from .reference_date import get_reference_date

    if end is None:
        end = get_reference_date()
    if start is None:
        start = end - timedelta(days=29)
    return start, end


def _longest_prefix_description(account: str, path_map: dict[str, str]) -> str:
    """取账户路径上最长的目录描述；子账户继承父账户描述。"""
    best_path = ''
    best_description = ''
    for path, description in path_map.items():
        if account == path or account.startswith(f'{path}:'):
            if len(path) > len(best_path):
                best_path, best_description = path, description
    return best_description


def _validate(*, start: date, end: date, limit: int, offset: int) -> None:
    if start > end:
        raise JournalQueryError('start 不能晚于 end')
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise JournalQueryError(f'查询区间不能超过 {MAX_RANGE_DAYS} 天')
    if not 1 <= limit <= MAX_PAGE_SIZE:
        raise JournalQueryError(f'limit 必须在 1 到 {MAX_PAGE_SIZE} 之间')
    if offset < 0:
        raise JournalQueryError('offset 不能为负数')


def _group_entries(rows: list[tuple], path_map: dict[str, str]) -> list[JournalEntry]:
    """把按 (date DESC, id) 排序的分录行合并成交易，保持账本内分录顺序。"""
    entries: list[dict] = []
    current_id: str | None = None
    for row in rows:
        entry_id, entry_date, payee, narration, tags, account, units = row
        if units is None:
            continue
        if entry_id != current_id:
            current_id = entry_id
            entries.append(
                {
                    'id': entry_id,
                    'date': entry_date,
                    'payee': payee or '',
                    'narration': narration or '',
                    'tags': tuple(sorted(tags or ())),
                    'postings': [],
                }
            )
        entries[-1]['postings'].append(
            JournalPosting(
                account=account,
                description=_longest_prefix_description(account, path_map),
                number=str(units.number),
                currency=units.currency,
            )
        )
    return [
        JournalEntry(
            id=entry['id'],
            date=entry['date'],
            payee=entry['payee'],
            narration=entry['narration'],
            tags=entry['tags'],
            postings=tuple(entry['postings']),
        )
        for entry in entries
    ]


class JournalQueryService:
    """按日期区间读取结构化日记账。

    分页在分组之后进行：必须先拿到区间内全部交易才能确定交易边界，因此 limit
    只裁剪返回结果，不减少账本扫描量。
    """

    def __init__(self, user: User):
        self.user = user
        self.ledger = LedgerQueryService(user)

    def page(
        self,
        *,
        start: date,
        end: date,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> JournalPage:
        _validate(start=start, end=end, limit=limit, offset=offset)
        query = (
            'SELECT id, date, payee, narration, tags, account, units(position) '
            f'WHERE date >= {start.isoformat()} AND date <= {end.isoformat()} '
            'ORDER BY date DESC, id'
        )
        try:
            rows = self.ledger.fetch_rows(query)
        except LedgerNotFoundError as exc:
            record_bql(
                user_id=getattr(self.user, 'id', None),
                ok=False,
                error=str(exc),
                source_type='journal_read',
            )
            raise
        except Exception as exc:
            record_bql(
                user_id=getattr(self.user, 'id', None),
                ok=False,
                error=f'{type(exc).__name__}: {exc}',
                source_type='journal_read',
            )
            raise JournalQueryError(str(exc)) from exc

        entries = _group_entries(rows, build_path_to_description_map(self.user))
        record_bql(
            user_id=getattr(self.user, 'id', None),
            ok=True,
            row_count=len(rows),
            source_type='journal_read',
        )
        total = len(entries)
        page_entries = tuple(entries[offset: offset + limit])
        return JournalPage(
            start=start,
            end=end,
            total=total,
            offset=offset,
            limit=limit,
            entries=page_entries,
        )
