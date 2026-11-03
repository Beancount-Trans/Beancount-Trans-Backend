"""MCP Tools：把平台已具备的账本只读能力暴露给 MCP 客户端。

工具实现全部复用 assistant 应用下的服务层，不重复实现 BQL 校验与账本解析。
"""
from __future__ import annotations

import logging
from datetime import date

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, Field

from project.apps.assistant.services.bql_validator import BQLValidationError
from project.apps.assistant.services.journal_query import (
    DEFAULT_PAGE_SIZE,
    JournalQueryError,
    JournalQueryService,
    resolve_range,
)
from project.apps.assistant.services.ledger_query import LedgerNotFoundError, LedgerQueryService
from project.apps.assistant.services.schema_provider import get_ledger_context

from .identity import (
    IdentityError,
    get_current_user,
    touch_current_personal_access_token,
)
from .ledger_files import LedgerFileError, read_ledger_file

logger = logging.getLogger(__name__)

GET_LEDGER_CONTEXT_DESCRIPTION = (
    '获取用户账本上下文：平台账户/标签目录（含描述）、'
    '账本实际账户、默认货币、BQL 语法说明与查询示例。'
    '执行 BQL 查询前应先调用本工具了解可用的账户与标签。'
)

RUN_BQL_DESCRIPTION = (
    '执行只读 BQL 查询并返回表格结果。必须 SELECT 开头；'
    '分析/余额/合计/对比类问题必须用 sum(units(position)) 与 GROUP BY 聚合，禁止拉明细后心算；'
    '用户说的类别名称先对照平台账户/标签目录映射到 account ~ / \'标签路径\' IN tags；'
    '账户用 account ~ 正则；时间用 year/month 或 date 范围；'
    '金额过滤用 number > N，禁止 units(position) > N；'
    '类目总额用前缀 account ~ 的 sum，子科目拆分用 GROUP BY（父/子账户金额独立）；'
    '结果截断时改用 GROUP BY 重查；'
    '解读 Income/Liabilities 结果时注意复式记账符号，向用户展示收入/欠款用绝对值。'
)


class BqlQueryResult(BaseModel):
    """BQL 查询结果。"""

    bql: str = Field(description='实际执行的 BQL 语句')
    result_text: str = Field(description='表格形式的查询结果')
    row_count: int = Field(description='符合条件的总行数（未截断前的行数）')
    truncated: bool = Field(description='结果是否因超出上限被截断')


GET_JOURNAL_DESCRIPTION = (
    '按日期区间读取结构化日记账（交易与分录），按日期倒序分页返回，只读。'
    'start/end 为 YYYY-MM-DD，省略时默认最近 30 天；区间最长 366 天。'
    'limit 默认 100、最大 500，offset 为交易偏移量；用 total/has_more/next_offset 翻页。'
    '每笔交易含 id、date、payee、narration、tags 与 postings'
    '（account 完整路径、description 平台目录描述、number 十进制字符串、currency）。'
    'number 取自 units(position)，不含成本/价格信息。'
    '需要逐笔明细或面板展示时用本工具；分类汇总、余额、环比仍用 run_bql。'
    'Income/Liabilities 分录可能为负数，向用户展示时取绝对值并说明口径。'
)


class JournalPosting(BaseModel):
    """日记账中的一条分录。"""

    account: str = Field(description='完整账户路径')
    description: str = Field(description='平台账户目录中匹配到的最长前缀描述；没有则为空串')
    number: str = Field(description='分录数量（十进制字符串，保留原始精度）')
    currency: str = Field(description='币种')


class JournalEntry(BaseModel):
    """日记账中的一笔交易。"""

    id: str = Field(description='交易在账本内的稳定 id（同笔交易的分录共享）')
    date: str = Field(description='交易日期 YYYY-MM-DD')
    payee: str = Field(description='交易对方；没有则为空串')
    narration: str = Field(description='交易备注；没有则为空串')
    tags: list[str] = Field(description='交易标签（完整路径），已排序')
    postings: list[JournalPosting] = Field(description='分录，保持账本内顺序')


class JournalResult(BaseModel):
    """一页结构化日记账。"""

    start: str = Field(description='实际查询的起始日期')
    end: str = Field(description='实际查询的结束日期')
    total: int = Field(description='区间内交易总数（分页前）')
    offset: int = Field(description='本次返回的起始交易偏移量')
    limit: int = Field(description='本次请求的页大小')
    has_more: bool = Field(description='是否还有下一页')
    next_offset: int | None = Field(default=None, description='下一页的 offset；没有更多时为 null')
    entries: list[JournalEntry] = Field(description='本页交易，按日期倒序')


READ_LEDGER_FILE_DESCRIPTION = (
    '读取当前用户账本目录内的单个 .bean 文件原文。'
    'path 为相对账本根目录的路径（如 main.bean、2026/01/xxx.bean），'
    '用于查看账户结构、include 关系与原始交易记录；'
    '需要统计或筛选时请改用 run_bql，不要靠读取文件人工汇总。'
)


class LedgerFileContent(BaseModel):
    """账本文件内容。"""

    path: str = Field(description='相对账本根目录的路径')
    size_bytes: int = Field(description='文件大小（字节）')
    content: str = Field(description='文件原文')


def _current_user():
    try:
        return get_current_user()
    except IdentityError as exc:
        raise ToolError(str(exc)) from exc


def _parse_day(value: str, field_name: str) -> date | None:
    """解析可选的 YYYY-MM-DD 参数，空串表示未提供。"""
    text = (value or '').strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ToolError(f'参数错误：{field_name} 必须是 YYYY-MM-DD 格式') from exc


def register(server: MCPServer) -> None:
    """把账本查询相关工具注册到 MCP 服务。"""

    @server.tool(name='get_ledger_context', description=GET_LEDGER_CONTEXT_DESCRIPTION)
    def get_ledger_context_tool() -> str:
        context = get_ledger_context(_current_user())
        touch_current_personal_access_token()
        return context

    @server.tool(name='run_bql', description=RUN_BQL_DESCRIPTION)
    def run_bql_tool(query: str) -> BqlQueryResult:
        service = LedgerQueryService(_current_user())
        try:
            result = service.execute(query)
        except BQLValidationError as exc:
            raise ToolError(f'BQL 校验失败：{exc}') from exc
        except LedgerNotFoundError as exc:
            raise ToolError(f'账本不可用：{exc}') from exc
        except ValueError as exc:
            raise ToolError(f'BQL 执行失败：{exc}') from exc
        touch_current_personal_access_token()
        return BqlQueryResult(
            bql=result.bql,
            result_text=result.result_text,
            row_count=result.row_count,
            truncated=result.truncated,
        )

    @server.tool(name='get_journal', description=GET_JOURNAL_DESCRIPTION)
    def get_journal_tool(
        start: str = '',
        end: str = '',
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> JournalResult:
        user = _current_user()
        start_day, end_day = resolve_range(
            _parse_day(start, 'start'),
            _parse_day(end, 'end'),
        )
        try:
            page = JournalQueryService(user).page(
                start=start_day,
                end=end_day,
                limit=limit,
                offset=offset,
            )
        except LedgerNotFoundError as exc:
            raise ToolError(f'账本不可用：{exc}') from exc
        except JournalQueryError as exc:
            raise ToolError(f'日记账查询失败：{exc}') from exc
        touch_current_personal_access_token()
        return JournalResult(
            start=page.start.isoformat(),
            end=page.end.isoformat(),
            total=page.total,
            offset=page.offset,
            limit=page.limit,
            has_more=page.has_more,
            next_offset=page.next_offset,
            entries=[
                JournalEntry(
                    id=entry.id,
                    date=entry.date.isoformat(),
                    payee=entry.payee,
                    narration=entry.narration,
                    tags=list(entry.tags),
                    postings=[
                        JournalPosting(
                            account=posting.account,
                            description=posting.description,
                            number=posting.number,
                            currency=posting.currency,
                        )
                        for posting in entry.postings
                    ],
                )
                for entry in page.entries
            ],
        )

    @server.tool(name='read_ledger_file', description=READ_LEDGER_FILE_DESCRIPTION)
    def read_ledger_file_tool(path: str) -> LedgerFileContent:
        try:
            ledger_file = read_ledger_file(_current_user(), path)
        except LedgerFileError as exc:
            raise ToolError(str(exc)) from exc
        touch_current_personal_access_token()
        return LedgerFileContent(
            path=ledger_file.path,
            size_bytes=ledger_file.size_bytes,
            content=ledger_file.content,
        )
