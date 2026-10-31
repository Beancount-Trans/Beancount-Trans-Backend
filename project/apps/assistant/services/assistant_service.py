"""LLM 编排：DeepSeek function calling + BQL 工具。"""
import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from django.conf import settings
from django.contrib.auth.models import User
from openai import BadRequestError, OpenAI

from project.apps.assistant.models import SharedLedgerBinding

from .api_key_resolver import LlmProvider, resolve_llm_provider
from .thinking_params import build_completion_extras, is_thinking_enabled
from .bql_reference import build_bql_capability_reference
from .bql_validator import BQLValidationError
from .dsml_tool_parser import extract_dsml_tool_calls, strip_dsml_markup
from .ledger_query import LedgerNotFoundError, LedgerQueryService
from .reference_date import build_reference_date_context, get_reference_date
from .reply_number_guard import (
    GUARD_DISCLAIMER,
    GUARD_RETRY_MESSAGE,
    apply_guard_disclaimer,
    validate_reply_numbers,
)
from .insight_mode import INSIGHT_MODE_BLOCK, detect_insight_mode, get_last_user_message
from .plain_language_mode import (
    build_plain_language_prompt_block,
    detect_plain_language_mode,
)
from .fava_url import query_record_fava_fields
from .schema_provider import (
    build_bql_examples,
    build_insight_bql_examples,
    build_ledger_options,
    build_shared_ledger_prompt_block,
    get_ledger_context,
    ledger_keys_for,
)
from .shared_ledger import touch_usage

logger = logging.getLogger(__name__)


def _record_llm_usage(
    user,
    usage,
    model: str,
    provider: LlmProvider,
    *,
    status: str = 'ok',
    reason: str = '',
) -> None:
    """记录一轮 LLM 调用的 token 用量（延迟导入，内部已吞异常）。"""
    from project.apps.usage.services import record_llm_tokens

    prompt_tokens = int(getattr(usage, 'prompt_tokens', 0) or 0) if usage else 0
    completion_tokens = int(getattr(usage, 'completion_tokens', 0) or 0) if usage else 0
    record_llm_tokens(
        user_id=getattr(user, 'id', None),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        model=model,
        byok=getattr(provider, 'source', '') == 'user',
        status=status,
        reason=reason or ('' if usage else 'no_usage'),
    )

PROVIDER_NOT_CONFIGURED_MESSAGE = (
    '尚未配置助手模型，请在「输出配置」的账本助手中填写接口与密钥（Ollama 可省略密钥）。'
)


SYSTEM_PROMPT_TEMPLATE = """你是 Beancount-Trans 的个人账本助手。你只能基于工具返回的真实数据回答用户问题。

{reference_date_context}

规则：
1. 涉及**新的**金额、余额、占比、合计、排名、对比时（含「分析 / 详细 / 结构 / 明细」），应调用 run_bql 获取数据；若对话历史中已附带查询结果，可直接引用，禁止编造，禁止对历史表格心算合计；不得仅凭 get_ledger_context 中的账户名作答。
2. 禁止心算：不得对 BQL 返回的多行明细或账户列表手动加减乘除；多账户合计、余额、总额必须用 sum(units(position)) 与 GROUP BY（或单次 sum）由查询引擎计算。
3. 应收款、资产、负债、收入等：先对照平台账户目录映射 account ~ 正则，再用 GROUP BY account 查各户余额；需要总额时用 sum(units(position))。
4. 账户层级：GROUP BY 的父账户行仅含直接 posting；类目总额用 sum + account ~ '^前缀'（含子孙），勿把父账户行当总额；无 posting 的账户不会出现在结果中。
5. 不确定账户名称时，先调用 get_ledger_context 了解平台账户/标签目录、账本账户列表和 BQL 语法。
6. 用户提及支出/收入类别（如「餐饮」「交通」）时，先对照「平台账户目录」描述匹配，再写 account ~ 正则（子科目路径以目录为准，如 ^Expenses:子科目）。
7. 涉及消费性质（必要/非必要、线上/线下等）时，对照「平台标签目录」，BQL 用 '完整标签路径' IN tags 筛选。
8. 展示结果时：有平台描述则写「描述（账户路径）」；无描述则用账户路径；表格汇总同样遵循。
9. 生成 BQL 时严格遵守「BQL 能力说明」与下方示例；账户用 account ~ 正则；金额过滤用 number 列，禁止 units(position) > N。
10. 结构/明细分析：先用聚合查询（GROUP BY account 或 payee）定位重点，发现重点后应做第二条追溯或明细查询；若结果提示「已截断」，必须改用 GROUP BY 聚合重查，禁止对截断样本求和。
11. 涉及「本月」「上月」「最近」等时间时，以上述基准日期为准构造 BQL 日期条件；查询资产、负债、应收、累计收支等**无时间范围**的余额/累计口径时，必须加 date <= 基准日期（今天），排除日期在未来的预记账条目（如提前记录的到账工资）；仅当用户明确询问未来/计划条目时才查询未来日期。
12. 用中文简洁回答，优先使用 Markdown 结构化展示，标明货币单位；若查无数据，明确说明。
13. 余额查询若返回账户名但 sum 列为空白或 0.00，表示余额为 0，应直接告知用户，不要因「看不到数字」而反复换语法重查。
14. 同一问题最多调用 run_bql {max_bql_runs} 次（系统硬限制）；若仍无满意结果，请根据已有查询结果作答，不要无限重试。
15. 记账只能通过 record_transaction 工具（只生成待审核条目，不直接写账本）；除此之外禁止任何写操作，包括改动账本文件、删除或修改已有条目、执行 BQL 写语句。用户追问、解释上一轮结论、澄清问法或说明能力边界时，可直接回答，不必调用工具；与账本无关的闲聊不要展开。历史中已出现过的 BQL 勿原样重跑，除非用户要求刷新或时间范围变化。
16. 自然语言记账规则：仅在用户明确表达「记一笔 / 花了 / 收到 / 转账」等记账意图时调用 record_transaction，纯查询/分析类提问不要调用；账户不确定时先调用 get_ledger_context 核对平台账户目录，金额或日期不确定时先向用户确认，禁止编造金额、账户与日期；相对日期按上述基准日期换算为 YYYY-MM-DD；调用成功后必须在回复中告知用户「已生成 N 条待审核条目，需在「条目审核」中确认后写入 collect.bean」。
17. 使用 Markdown 格式化回答：金额与关键数字用 **粗体**；多项对比用 Markdown 表格；列举用有序/无序列表；不要输出原始 HTML。
18. 调用工具前，用一两句话简要说明你的分析思路（会展示在「思考过程」中）；最终回答中不要重复这段思路。
19. 复式记账符号：Income 累计为负表示收入，向用户展示时用绝对值并标明为收入，勿将负号误解为亏损；Income 为正表示冲销。Expenses 为正表示支出。Liabilities 累计为负表示欠款，展示时可取绝对值。展示时数字须来自 BQL 结果（可取绝对值），禁止心算。
20. 禁止在回复正文中输出 DSML、XML 或任何工具调用原始标记；需要查询时必须通过工具接口调用；查无数据时直接说明，不要重复输出查询语法。
21. 结论中引用具体账目时，须写清**日期**、**收款人/叙述**、**金额**（均来自 BQL 结果行，禁止心算或编造）。
22. 不要在回复正文中输出 Fava 或平台 URL、uuid、查询链接；界面会在结论后自动附加「来源」链接。

{bql_capability_reference}

{bql_examples}"""


def get_max_bql_runs() -> int:
    return int(getattr(settings, 'ASSISTANT_MAX_BQL_RUNS', 5))


def get_max_tool_rounds() -> int:
    return int(getattr(settings, 'ASSISTANT_MAX_TOOL_ROUNDS', 8))


def build_system_prompt(
    reference_date: date | None = None,
    *,
    insight_mode: bool = False,
    shared_ledger_block: str = '',
    plain_language_mode: bool = False,
    shared_plain_conditional: bool = False,
) -> str:
    ref = reference_date or get_reference_date()
    bql_examples = build_bql_examples(ref)
    if insight_mode:
        bql_examples = f'{bql_examples}\n\n{build_insight_bql_examples(ref)}'
    prompt = SYSTEM_PROMPT_TEMPLATE.format(
        reference_date_context=build_reference_date_context(ref),
        bql_capability_reference=build_bql_capability_reference(
            insight_mode=insight_mode, reference_date=ref
        ),
        bql_examples=bql_examples,
        max_bql_runs=get_max_bql_runs(),
    )
    if insight_mode:
        prompt = f'{prompt}\n\n{INSIGHT_MODE_BLOCK}'
    if shared_ledger_block:
        prompt = f'{prompt}\n\n{shared_ledger_block}'
    if plain_language_mode:
        prompt = f'{prompt}\n\n{build_plain_language_prompt_block(forced=True)}'
    elif shared_plain_conditional:
        prompt = f'{prompt}\n\n{build_plain_language_prompt_block(forced=False)}'
    return prompt


def build_tools(
    *,
    insight_mode: bool = False,
    ledger_options: list[dict] | None = None,
    self_ledger_available: bool = True,
) -> list[dict[str, Any]]:
    has_shared = bool([o for o in (ledger_options or []) if o.get('key') != 'self'])
    run_bql_description = (
        '执行只读 BQL 查询并返回表格结果。必须 SELECT 开头；'
        '分析/余额/合计/对比类问题必须用 sum(units(position)) 与 GROUP BY 聚合，禁止拉明细后心算；'
        '用户说的类别名称先对照平台账户/标签目录映射到 account ~ / \'标签路径\' IN tags；'
        '账户用 account ~ 正则；时间用 year/month 或 date 范围；'
        '金额过滤用 number > N，禁止 units(position) > N；'
        '类目总额用前缀 account ~ 的 sum，子科目拆分用 GROUP BY（父/子账户金额独立）；'
        '结果截断时改用 GROUP BY 重查；遵守 system prompt 中的 BQL 能力说明与示例；'
        '解读 Income/Liabilities 结果时注意复式记账符号，向用户展示收入/欠款用绝对值。'
    )
    if insight_mode:
        run_bql_description += (
            ' 洞察模式：优先跨期 GROUP BY year, month；'
            '允许 FROM entries 查 meta/Balance/Pad；'
            'links/tags 用 IN 语法；'
            '发现异常线索后必须追溯历史（同 payee 跨月、同 link、同 tag 等）。'
        )
    ledger_property: dict[str, Any] | None = None
    if has_shared:
        ledger_keys: list[str] = []
        for entry in (ledger_options or []):
            if entry.get('key') == 'self':
                continue
            ledger_keys.extend(entry.get('keys') or [entry.get('key')])
        ledger_keys_str = '、'.join(str(key) for key in ledger_keys)
        if self_ledger_available:
            ledger_description = (
                '账本标识：self=我的账本，其他值为共享账本的可用标识（别名或来源用户名）；'
                f'缺省 self。可选值：{ledger_keys_str}'
            )
        else:
            ledger_description = (
                '账本标识：本人账本尚未创建；必须显式指定下面的共享账本标识。'
                f'可选值：{ledger_keys_str}'
            )
        ledger_property = {
            'type': 'string',
            'description': ledger_description,
        }
        run_bql_description += (
            ' 查询顺序：默认先查 self；仅当本人账本不存在、或对 self 的查询返回空结果/无相关记录时，'
            '才改用相关的共享账本再查一次（用户点名可用标识、或要求跨账本对比/合计时可直接查共享账本）。'
            '跨账本合计仅在用户明确要求时进行，需说明合计口径、标注每项数字的来源账本，'
            '并提示两账本间往来/转账可能被重复计入；共享账本只读。'
        )
    max_bookkeeping_entries = int(
        getattr(settings, 'COPILOT_BOOKKEEPING_MAX_ENTRIES', 10)
    )
    record_transaction_description = (
        '把用户自然语言描述的交易记为「待审核条目」（不直接写入账本，'
        '审核通过后才写入 collect.bean）。'
        '仅在用户明确表达「记一笔 / 花了 / 收到 / 转账」等记账意图时调用；'
        '分析、查询、闲聊不要调用。'
        '账户必须来自平台账户目录（不确定时先调用 get_ledger_context 核对）；'
        '禁止编造金额、账户与日期。'
        '相对日期（今天/昨天）需按 system prompt 中的基准日期换算为 YYYY-MM-DD。'
        f'一次可提交多笔交易（最多 {max_bookkeeping_entries} 条）。'
        '币种缺省使用用户配置币种，当前仅支持默认币种。'
        '调用成功后必须在回复中告知用户：已生成 N 条待审核条目，'
        '需在「条目审核」中确认后写入 collect.bean。'
    )
    return [
        {
            'type': 'function',
            'function': {
                'name': 'get_ledger_context',
                'description': (
                    '获取用户账本上下文：平台账户/标签目录（含描述）、'
                    '账本实际账户、默认货币、BQL 语法说明与查询示例'
                ),
                'parameters': {
                    'type': 'object',
                    'properties': (
                        {'ledger': ledger_property} if ledger_property else {}
                    ),
                    'required': [],
                },
            },
        },
        {
            'type': 'function',
            'function': {
                'name': 'run_bql',
                'description': run_bql_description,
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'query': {
                            'type': 'string',
                            'description': 'BQL SELECT 查询语句，参考示例写法',
                        },
                        **({'ledger': ledger_property} if ledger_property else {}),
                    },
                    'required': ['query'],
                },
            },
        },
        {
            'type': 'function',
            'function': {
                'name': 'record_transaction',
                'description': record_transaction_description,
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'entries': {
                            'type': 'array',
                            'description': (
                                f'待记账交易列表，1~{max_bookkeeping_entries} 条'
                            ),
                            'items': {
                                'type': 'object',
                                'properties': {
                                    'type': {
                                        'type': 'string',
                                        'enum': ['expense', 'income', 'transfer'],
                                        'description': (
                                            'expense=支出，income=收入，'
                                            'transfer=账户间转账'
                                        ),
                                    },
                                    'date': {
                                        'type': 'string',
                                        'description': '交易日期 YYYY-MM-DD',
                                    },
                                    'amount': {
                                        'type': 'number',
                                        'description': '正数金额',
                                    },
                                    'narration': {
                                        'type': 'string',
                                        'description': '交易说明，如「午餐」「打车」',
                                    },
                                    'payee': {
                                        'type': 'string',
                                        'description': '交易对方，可选，缺省用 narration',
                                    },
                                    'account': {
                                        'type': 'string',
                                        'description': (
                                            'expense/income：损益账户'
                                            '（Expenses:/Income:）'
                                        ),
                                    },
                                    'payment_account': {
                                        'type': 'string',
                                        'description': 'expense/income：支付或收款账户',
                                    },
                                    'from_account': {
                                        'type': 'string',
                                        'description': 'transfer：转出账户（Assets:）',
                                    },
                                    'to_account': {
                                        'type': 'string',
                                        'description': 'transfer：转入账户（Assets:）',
                                    },
                                    'tags': {
                                        'type': 'array',
                                        'items': {'type': 'string'},
                                        'description': (
                                            '标签完整路径，可带或不带 # 前缀'
                                        ),
                                    },
                                    'currency': {
                                        'type': 'string',
                                        'description': '币种，缺省用用户配置币种',
                                    },
                                },
                                'required': ['type', 'date', 'amount', 'narration'],
                            },
                        }
                    },
                    'required': ['entries'],
                },
            },
        },
    ]


_BOOKKEEPING_TYPE_LABELS = {'expense': '支出', 'income': '收入', 'transfer': '转账'}


def format_bookkeeping_result(result: dict[str, Any]) -> str:
    """把 CopilotBookkeepingService.create_entries 返回值转成可读中文文本。"""
    created = result.get('created') or []
    duplicates = result.get('duplicates') or []
    errors = result.get('errors') or []
    pending_total = result.get('pending_total') or 0
    lines: list[str] = []

    if created:
        lines.append(f'已生成 {len(created)} 条待审核条目：')
        for index, item in enumerate(created, start=1):
            type_label = _BOOKKEEPING_TYPE_LABELS.get(
                item.get('type'), item.get('type') or ''
            )
            account = item.get('account') or ''
            counterparty = item.get('counterparty_account') or ''
            route = f'{account} ← {counterparty}'
            if item.get('type') == 'transfer':
                route = f'{account} → {counterparty}'
            lines.append(
                f'{index}. {item.get("date", "")} {type_label} '
                f'{item.get("amount", "")} {item.get("currency", "")} '
                f'{item.get("narration", "")}（{route}）'
            )

    if duplicates:
        lines.append(f'疑似重复，已跳过 {len(duplicates)} 条：')
        for index, item in enumerate(duplicates, start=1):
            lines.append(
                f'{index}. {item.get("date", "")} {item.get("amount", "")} '
                f'{item.get("narration", "")}：'
                f'疑似重复，已跳过（{item.get("reason", "与已有条目重复")}）'
            )

    if errors:
        lines.append(f'校验失败 {len(errors)} 条：')
        for item in errors:
            index = item.get('index')
            prefix = f'第 {index} 条' if index else '整体'
            lines.append(f'{prefix}：{item.get("error", "未知错误")}')

    if not result.get('ok') and not created:
        reason = '没有条目被写入，请核对账户、金额、日期或币种后重试。'
        if not errors and not duplicates:
            reason = '记账未成功：没有可处理的条目。'
        lines.append(reason)

    lines.append(
        f'待审核条目共 {pending_total} 条，'
        '请在「条目审核」中确认后写入 collect.bean。'
    )
    return '\n'.join(lines)


def format_sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@dataclass
class QueryRecord:
    bql: str
    result_preview: str
    fava_path: str = ''
    report: dict[str, Any] | None = None
    ledger: str = ''


def query_record_to_dict(record: QueryRecord) -> dict[str, Any]:
    payload: dict[str, Any] = {
        'bql': record.bql,
        'result_preview': record.result_preview,
    }
    if record.fava_path:
        payload['fava_path'] = record.fava_path
    if record.report:
        payload['report'] = record.report
    if record.ledger:
        payload['ledger'] = record.ledger
    return payload


def query_records_from_dicts(records: list[dict[str, Any]]) -> list[QueryRecord]:
    result: list[QueryRecord] = []
    for record in records:
        if not record.get('bql') or not record.get('result_preview'):
            continue
        result.append(QueryRecord(
            bql=record['bql'],
            result_preview=record['result_preview'],
            fava_path=record.get('fava_path') or '',
            report=record.get('report'),
            ledger=record.get('ledger') or '',
        ))
    return result


@dataclass
class AssistantReply:
    reply: str
    queries: list[QueryRecord] = field(default_factory=list)
    thinking: str = ''
    reasoning: str = ''


@dataclass
class StreamEvent:
    event: str
    data: dict[str, Any]


@dataclass
class _AccumulatedToolCall:
    id: str = ''
    name: str = ''
    arguments: str = ''


@dataclass
class _StreamRoundResult:
    content_parts: list[str]
    tool_calls: list[_AccumulatedToolCall]
    reasoning_parts: list[str] = field(default_factory=list)


class AssistantService:
    MAX_MESSAGES = 20

    def __init__(
        self,
        user: User,
        reference_date: date | None = None,
        *,
        deep_think: bool = False,
        shared_ledgers: list[dict] | None = None,
    ):
        self.user = user
        self.reference_date = reference_date or get_reference_date()
        self.ledger_query = LedgerQueryService(user)
        self.ledger_options = build_ledger_options(shared_ledgers or [])
        # 共享账本来源用户名：提问可能直接点名对方用户名，用于判定简明表达。
        self.shared_owner_usernames = [
            item['owner'].username for item in (shared_ledgers or [])
        ]
        self.ledger_queries: dict[str, LedgerQueryService] = {'self': self.ledger_query}
        self.ledger_bindings: dict[str, SharedLedgerBinding] = {}
        binding_ids = [
            item['binding_id']
            for item in (shared_ledgers or [])
            if item.get('binding_id') is not None
        ]
        binding_by_id: dict[int, SharedLedgerBinding] = {}
        if binding_ids:
            bindings = (
                SharedLedgerBinding.objects
                .filter(id__in=binding_ids, recipient=user)
                .select_related('token')
            )
            binding_by_id = {binding.id: binding for binding in bindings}
        for item in (shared_ledgers or []):
            shared_service = LedgerQueryService(item['owner'])
            binding = binding_by_id.get(item.get('binding_id'))
            for key in ledger_keys_for(item):
                self.ledger_queries.setdefault(key, shared_service)
                if binding is not None:
                    self.ledger_bindings.setdefault(key, binding)
        self.has_any_ledger = any(
            svc.ledger_exists() for svc in self.ledger_queries.values()
        )
        self.deep_think = deep_think
        self.provider = resolve_llm_provider(user)
        self.model = self.provider.model
        self.thinking_enabled = is_thinking_enabled(self.provider, deep_think=deep_think)
        self.max_bql_runs = get_max_bql_runs()
        self.max_tool_rounds = get_max_tool_rounds()
        # 简明表达模式：回答来自共享账本数据时置真，由 _iter_chat_events 计算；
        # 该模式下不下发 BQL 查询记录给客户端（界面不展示查询详情）。
        self.plain_language_mode = False
        # 本轮是否启用洞察模式 / 是否调用了记账工具，用于后台统计应答模式。
        self.insight_mode = False
        self.bookkeeping_used = False

    def _build_client(self, provider: LlmProvider) -> OpenAI:
        import httpx

        return OpenAI(
            api_key=provider.api_key,
            base_url=provider.base_url,
            timeout=httpx.Timeout(10.0, read=120.0),
        )

    def _create_stream(self, client: OpenAI, kwargs: dict[str, Any]):
        """发起流式请求；提供方不支持 stream_options 时去掉该参数重试一次。"""
        try:
            return client.chat.completions.create(**kwargs)
        except (TypeError, BadRequestError):
            if 'stream_options' not in kwargs:
                raise
            logger.warning('提供方不支持 stream_options，本次调用不计 token 用量')
            retry_kwargs = {key: value for key, value in kwargs.items() if key != 'stream_options'}
            return client.chat.completions.create(**retry_kwargs)

    def _self_ledger_missing_message(self, ledger: str) -> str | None:
        """选中 self 但本人账本不存在、且存在其他可选账本时，返回可读提示。"""
        if ledger != 'self' or self.ledger_query.ledger_exists():
            return None
        other_keys = [key for key in self.ledger_queries if key != 'self']
        if not other_keys:
            return None
        return (
            '本人账本尚未创建，无法查询「我的账本」；'
            f'请用 ledger 指定共享账本：{"、".join(other_keys)}'
        )

    def _dispatch_tool(self, name: str, arguments: dict[str, Any], queries: list[QueryRecord]) -> str:
        if name == 'get_ledger_context':
            ledger = arguments.get('ledger') or 'self'
            service = self.ledger_queries.get(ledger)
            if service is None:
                return (
                    f'账本标识无效: {ledger}；'
                    f'可用账本: {", ".join(self.ledger_queries)}'
                )
            guard_message = self._self_ledger_missing_message(ledger)
            if guard_message is not None:
                return guard_message
            context = get_ledger_context(service.user, reference_date=self.reference_date)
            binding = self.ledger_bindings.get(ledger)
            if binding is not None and service.ledger_exists():
                touch_usage(binding)
            return context

        if name == 'run_bql':
            ledger = arguments.get('ledger') or 'self'
            service = self.ledger_queries.get(ledger)
            if service is None:
                return (
                    f'账本标识无效: {ledger}；'
                    f'可用账本: {", ".join(self.ledger_queries)}'
                )
            guard_message = self._self_ledger_missing_message(ledger)
            if guard_message is not None:
                return guard_message
            if len(queries) >= self.max_bql_runs:
                return (
                    f'已达本问题 BQL 查询上限（{self.max_bql_runs} 次），请根据已有结果作答。'
                )
            query = arguments.get('query', '')
            try:
                result = service.execute(query)
                binding = self.ledger_bindings.get(ledger)
                if binding is not None:
                    touch_usage(binding)
                if ledger == 'self':
                    fava_fields = query_record_fava_fields(self.user, result.bql)
                    queries.append(QueryRecord(
                        bql=result.bql,
                        result_preview=result.result_text,
                        fava_path=fava_fields.get('fava_path', ''),
                        report=fava_fields.get('report'),
                        ledger='self',
                    ))
                    if result.row_count == 0 and len(self.ledger_queries) > 1:
                        return (
                            result.result_text
                            + '\n（本人账本无结果；如需，可用 ledger=<共享账本可用标识> 再查一次）'
                        )
                else:
                    queries.append(QueryRecord(
                        bql=result.bql,
                        result_preview=result.result_text,
                        ledger=ledger,
                    ))
                return result.result_text
            except BQLValidationError as exc:
                return str(exc)
            except ValueError as exc:
                return str(exc)
            except Exception as exc:
                return f'查询失败: {exc}'

        if name == 'record_transaction':
            entries = arguments.get('entries')
            if not isinstance(entries, list) or not entries:
                return '记账失败: entries 必须是非空数组，请提供至少一笔交易。'
            try:
                # 延迟导入，避免 assistant 与 translate 模块级循环依赖
                from project.apps.translate.services.copilot_bookkeeping_service import (
                    CopilotBookkeepingService,
                )

                result = CopilotBookkeepingService.create_entries(self.user, entries)
                if not isinstance(result, dict):
                    return '记账失败: 记账服务返回了异常结果。'
                self.bookkeeping_used = True
                text = format_bookkeeping_result(result)
                requested_ledger = arguments.get('ledger')
                if requested_ledger not in (None, '', 'self'):
                    text += '\n（注意：记账仅支持写入本人账本，已忽略 ledger 参数。）'
                return text
            except Exception as exc:
                logger.exception('Copilot 记账工具执行失败')
                return f'记账失败: {exc}'

        return f'未知工具: {name}'

    def _resolve_dsml_tool_calls(self, round_result: _StreamRoundResult) -> bool:
        """将 content 中的 DSML 工具调用标记兜底解析为原生 tool_calls。"""
        if round_result.tool_calls:
            return False
        content = ''.join(round_result.content_parts)
        dsml_calls = extract_dsml_tool_calls(content)
        if not dsml_calls:
            return False
        round_result.tool_calls = [
            _AccumulatedToolCall(id=call.id, name=call.name, arguments=call.arguments)
            for call in dsml_calls
        ]
        round_result.content_parts = []
        return True

    def _finalize_reply(
        self,
        reply_text: str,
        queries: list[QueryRecord],
        show_bql: bool,
        *,
        reasoning: str = '',
        thinking: str = '',
    ) -> AssistantReply:
        reply_text = strip_dsml_markup(reply_text)
        if not reply_text:
            reply_text = '抱歉，我暂时无法回答这个问题，请尝试换个问法。'

        if show_bql and queries:
            bql_section = '\n\n'.join(
                f'```bql\n{q.bql}\n```\n{q.result_preview}' for q in queries
            )
            reply_text = f'{reply_text}\n\n---\n查询详情:\n{bql_section}'

        return AssistantReply(
            reply=reply_text,
            queries=queries,
            thinking=thinking,
            reasoning=reasoning,
        )

    def _client_visible_queries(self, queries: list[QueryRecord]) -> list[QueryRecord]:
        """下发给客户端的查询记录。

        共享账本不向使用者披露数据来源与查询过程：简明模式（含来自共享账本的回答）
        不展示任何查询记录，其余情况下也一律剔除共享账本记录，只保留本人账本记录。
        与 ``shared_ledger.filter_client_visible_queries`` 的持久化读取口径保持一致。
        """
        if 'plain' in self._recorded_modes(queries):
            return []
        return [q for q in queries if (q.ledger or 'self') == 'self']

    def _plain_reply(self, queries: list[QueryRecord]) -> bool:
        """本条回复是否属于简明表达（含数据取自共享账本的情况）。"""
        return self.plain_language_mode or any(
            (q.ledger or 'self') != 'self' for q in queries
        )

    def _recorded_modes(self, queries: list[QueryRecord]) -> list[str]:
        """本条回复使用的模式标签（供后台统计）。"""
        modes: list[str] = []
        if self.insight_mode:
            modes.append('insight')
        if self._plain_reply(queries):
            modes.append('plain')
        if self.bookkeeping_used:
            modes.append('bookkeeping')
        if not modes:
            modes.append('normal')
        return modes

    def _done_event_data(self, reply: AssistantReply) -> dict[str, Any]:
        return {
            'reply': reply.reply,
            'queries': [
                query_record_to_dict(q)
                for q in self._client_visible_queries(reply.queries)
            ],
            'thinking': reply.thinking,
            'reasoning': reply.reasoning,
            'model': self.model,
            'modes': self._recorded_modes(reply.queries),
        }

    def _build_thinking_reply(
        self,
        reply_text: str,
        queries: list[QueryRecord],
        show_bql: bool,
        api_reasoning_parts: list[str],
        planning_parts: list[str],
    ) -> AssistantReply:
        reasoning_text = self._merged_reasoning_text(api_reasoning_parts, planning_parts)
        return self._finalize_reply(
            reply_text,
            queries,
            show_bql,
            reasoning=reasoning_text,
            thinking=reasoning_text,
        )

    def _run_llm_round(
        self,
        client: OpenAI,
        llm_messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
        stream_text: bool = False,
        planning_mode: bool = False,
    ) -> Iterator[StreamEvent | _StreamRoundResult]:
        kwargs: dict[str, Any] = {
            'model': self.model,
            'messages': llm_messages,
            'stream': True,
            # 要求流式响应在末尾 chunk 返回 token 用量，用于计量
            'stream_options': {'include_usage': True},
            **build_completion_extras(self.provider, deep_think=self.deep_think),
        }
        if not self.thinking_enabled:
            kwargs['temperature'] = 0.1
        if tools is not None:
            kwargs['tools'] = tools
        if tool_choice is not None:
            kwargs['tool_choice'] = tool_choice

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls_map: dict[int, _AccumulatedToolCall] = {}

        stream = self._create_stream(client, kwargs)

        usage = None
        try:
            for chunk in stream:
                chunk_usage = getattr(chunk, 'usage', None)
                if chunk_usage is not None:
                    usage = chunk_usage
                # include_usage 的收尾 chunk 没有 choices，直接取会 IndexError
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta.tool_calls:
                    for tool_call in delta.tool_calls:
                        entry = tool_calls_map.setdefault(tool_call.index, _AccumulatedToolCall())
                        if tool_call.id:
                            entry.id = tool_call.id
                        if tool_call.function.name:
                            entry.name = tool_call.function.name
                        if tool_call.function.arguments:
                            entry.arguments += tool_call.function.arguments
                reasoning_content = getattr(delta, 'reasoning_content', None)
                if reasoning_content:
                    reasoning_parts.append(reasoning_content)
                    if stream_text:
                        yield StreamEvent('reasoning_delta', {
                            'content': reasoning_content,
                            'source': 'api',
                        })
                if delta.content:
                    content_parts.append(delta.content)
                    if stream_text:
                        if planning_mode:
                            yield StreamEvent('reasoning_delta', {
                                'content': delta.content,
                                'source': 'planning',
                            })
                        elif not tool_calls_map:
                            yield StreamEvent('delta', {'content': delta.content})
        except Exception:
            _record_llm_usage(
                self.user, None, self.model, self.provider,
                status='failed', reason='stream_error',
            )
            raise

        _record_llm_usage(self.user, usage, self.model, self.provider)

        yield _StreamRoundResult(
            content_parts=content_parts,
            tool_calls=[tool_calls_map[i] for i in sorted(tool_calls_map)],
            reasoning_parts=reasoning_parts,
        )

    def _assistant_message_from_tool_calls(
        self,
        tool_calls: list[_AccumulatedToolCall],
        *,
        reasoning_content: str = '',
        content: str = '',
    ) -> dict[str, Any]:
        message: dict[str, Any] = {
            'role': 'assistant',
            'content': content or None,
            'tool_calls': [
                {
                    'id': tc.id,
                    'type': 'function',
                    'function': {'name': tc.name, 'arguments': tc.arguments},
                }
                for tc in tool_calls
            ],
        }
        if self.thinking_enabled:
            message['reasoning_content'] = reasoning_content
        return message

    def _validation_queries(
        self,
        current_queries: list[QueryRecord],
        prior_queries: list[dict[str, Any]] | None = None,
    ) -> list[QueryRecord]:
        return query_records_from_dicts(prior_queries or []) + list(current_queries)

    def _iter_force_final_reply(
        self,
        client: OpenAI,
        llm_messages: list[dict[str, Any]],
        queries: list[QueryRecord],
        show_bql: bool,
        api_reasoning_parts: list[str],
        planning_parts: list[str],
        prior_queries: list[dict[str, Any]] | None = None,
    ) -> Iterator[StreamEvent]:
        synthesis_messages = [
            *llm_messages,
            {
                'role': 'user',
                'content': (
                    '已达到工具调用次数上限。请仅根据上文工具已返回的查询结果，'
                    '用中文直接回答用户最初的问题；若数据不足请说明，不要编造数字。'
                ),
            },
        ]
        yield StreamEvent('status', {'phase': 'writing'})
        content_parts: list[str] = []
        for item in self._run_llm_round(client, synthesis_messages, stream_text=True):
            if isinstance(item, StreamEvent):
                if item.event == 'reasoning_delta':
                    source = item.data.get('source', 'api')
                    if source == 'planning':
                        planning_parts.append(item.data['content'])
                    else:
                        api_reasoning_parts.append(item.data['content'])
                yield item
            else:
                content_parts = item.content_parts

        reply_text = ''.join(content_parts).strip()
        final = self._build_thinking_reply(
            reply_text,
            queries,
            show_bql,
            api_reasoning_parts,
            planning_parts,
        )
        yield from self._yield_validated_final(
            client,
            llm_messages,
            final,
            show_bql,
            api_reasoning_parts,
            planning_parts,
            prior_queries=prior_queries,
        )

    def _merged_reasoning_text(
        self,
        api_reasoning_parts: list[str],
        planning_parts: list[str],
    ) -> str:
        api_text = ''.join(api_reasoning_parts).strip()
        planning_text = ''.join(planning_parts).strip()
        if api_text and planning_text:
            return f'{api_text}\n\n{planning_text}'
        return api_text or planning_text

    def _yield_validated_final(
        self,
        client: OpenAI,
        llm_messages: list[dict[str, Any]],
        final: AssistantReply,
        show_bql: bool,
        api_reasoning_parts: list[str],
        planning_parts: list[str],
        prior_queries: list[dict[str, Any]] | None = None,
    ) -> Iterator[StreamEvent]:
        validation_queries = self._validation_queries(final.queries, prior_queries)
        validation = validate_reply_numbers(final.reply, validation_queries)
        if validation.ok:
            yield StreamEvent('done', self._done_event_data(final))
            return

        synthesis_messages = [
                *llm_messages,
                {'role': 'assistant', 'content': final.reply},
                {'role': 'user', 'content': GUARD_RETRY_MESSAGE},
        ]
        yield StreamEvent('status', {'phase': 'writing'})
        content_parts: list[str] = []
        for item in self._run_llm_round(client, synthesis_messages, stream_text=True):
            if isinstance(item, StreamEvent):
                yield item
            else:
                content_parts = item.content_parts

        reply_text = ''.join(content_parts).strip()
        final = self._build_thinking_reply(
            reply_text,
            final.queries,
            show_bql,
            api_reasoning_parts,
            planning_parts,
        )
        validation = validate_reply_numbers(final.reply, validation_queries)

        if not validation.ok:
            base_reply = final.reply.split(GUARD_DISCLAIMER.strip())[0].rstrip()
            # 简明表达模式不下发查询详情，附加「以 BQL 结果为准」的提示会自相矛盾。
            if not self._plain_reply(validation_queries):
                base_reply = apply_guard_disclaimer(base_reply)
            final = self._finalize_reply(
                base_reply,
                final.queries,
                show_bql,
                reasoning=final.reasoning,
                thinking=final.thinking,
            )

        yield StreamEvent('done', self._done_event_data(final))

    def _iter_chat_events(
        self,
        messages: list[dict[str, str]],
        show_bql: bool = False,
        prior_queries: list[dict[str, Any]] | None = None,
    ) -> Iterator[StreamEvent]:
        provider = self.provider
        if not provider.configured:
            raise ValueError(PROVIDER_NOT_CONFIGURED_MESSAGE)

        if not self.has_any_ledger:
            raise LedgerNotFoundError('尚未创建任何可访问的账本，请先上传并解析账单。')

        if len(messages) > self.MAX_MESSAGES:
            messages = messages[-self.MAX_MESSAGES:]

        client = self._build_client(provider)
        queries: list[QueryRecord] = []
        last_user_message = get_last_user_message(messages)
        insight_mode = detect_insight_mode(last_user_message)
        self.insight_mode = insight_mode
        self.bookkeeping_used = False
        self_ledger_available = self.ledger_query.ledger_exists()
        shared_ledger_keys = [
            key
            for option in self.ledger_options
            if option.get('key') != 'self'
            for key in (option.get('keys') or [option.get('key')])
        ]
        shared_ledger_keys.extend(self.shared_owner_usernames)
        plain_language_mode = detect_plain_language_mode(
            self_ledger_available=self_ledger_available,
            last_user_message=last_user_message,
            shared_ledger_keys=shared_ledger_keys,
        )
        self.plain_language_mode = plain_language_mode
        tools = build_tools(
            insight_mode=insight_mode,
            ledger_options=self.ledger_options,
            self_ledger_available=self_ledger_available,
        )
        llm_messages: list[dict[str, Any]] = [
            {
                'role': 'system',
                'content': build_system_prompt(
                    self.reference_date,
                    insight_mode=insight_mode,
                    shared_ledger_block=build_shared_ledger_prompt_block(
                        self.ledger_options,
                        self_ledger_available=self_ledger_available,
                    ),
                    plain_language_mode=plain_language_mode,
                    shared_plain_conditional=bool(shared_ledger_keys)
                    and not plain_language_mode,
                ),
            },
            *messages,
        ]

        yield StreamEvent('status', {'phase': 'thinking'})
        tool_round = 0
        api_reasoning_parts: list[str] = []
        planning_parts: list[str] = []

        while True:
            round_result: _StreamRoundResult | None = None
            writing_status_sent = False
            planning_len_before = len(planning_parts)
            for item in self._run_llm_round(
                client,
                llm_messages,
                tools=tools,
                tool_choice='auto',
                stream_text=True,
                planning_mode=True,
            ):
                if isinstance(item, StreamEvent):
                    if item.event == 'reasoning_delta':
                        source = item.data.get('source', 'api')
                        if source == 'planning':
                            planning_parts.append(item.data['content'])
                        else:
                            api_reasoning_parts.append(item.data['content'])
                    if item.event == 'delta' and not writing_status_sent:
                        yield StreamEvent('status', {'phase': 'writing'})
                        writing_status_sent = True
                    yield item
                else:
                    round_result = item

            if round_result is None:
                raise RuntimeError('LLM 轮次未返回结果')

            if self._resolve_dsml_tool_calls(round_result):
                planning_parts[planning_len_before:] = []

            if round_result.tool_calls:
                tool_round += 1
                if tool_round > self.max_tool_rounds:
                    yield from self._iter_force_final_reply(
                        client,
                        llm_messages,
                        queries,
                        show_bql,
                        api_reasoning_parts,
                        planning_parts,
                        prior_queries=prior_queries,
                    )
                    return

                yield StreamEvent('status', {'phase': 'querying'})
                llm_messages.append(self._assistant_message_from_tool_calls(
                    round_result.tool_calls,
                    reasoning_content=''.join(round_result.reasoning_parts),
                    content=''.join(round_result.content_parts),
                ))

                for tool_call in round_result.tool_calls:
                    fn_name = tool_call.name
                    try:
                        fn_args = json.loads(tool_call.arguments or '{}')
                    except json.JSONDecodeError:
                        fn_args = {}

                    tool_start: dict[str, Any] = {'name': fn_name}
                    if fn_name == 'run_bql':
                        tool_start['query'] = fn_args.get('query', '')
                    yield StreamEvent('tool_start', tool_start)

                    queries_before = len(queries)
                    tool_result = self._dispatch_tool(fn_name, fn_args, queries)

                    tool_end: dict[str, Any] = {'name': fn_name}
                    if (
                        fn_name == 'run_bql'
                        and len(queries) > queries_before
                        and self._client_visible_queries(queries)
                    ):
                        tool_end.update(query_record_to_dict(queries[-1]))
                    yield StreamEvent('tool_end', tool_end)

                    llm_messages.append({
                        'role': 'tool',
                        'tool_call_id': tool_call.id,
                        'content': tool_result,
                    })
                continue

            planning_parts[planning_len_before:] = []
            reasoning_text = self._merged_reasoning_text(api_reasoning_parts, planning_parts)
            if reasoning_text.strip():
                yield StreamEvent('thinking_set', {
                    'content': reasoning_text,
                    'reasoning': reasoning_text,
                })

            yield StreamEvent('status', {'phase': 'writing'})
            for piece in round_result.content_parts:
                yield StreamEvent('delta', {'content': piece})

            reply_text = ''.join(round_result.content_parts).strip()
            final = self._build_thinking_reply(
                reply_text,
                queries,
                show_bql,
                api_reasoning_parts,
                planning_parts,
            )
            yield from self._yield_validated_final(
                client,
                llm_messages,
                final,
                show_bql,
                api_reasoning_parts,
                planning_parts,
                prior_queries=prior_queries,
            )
            return

    def chat(self, messages: list[dict[str, str]], show_bql: bool = False) -> AssistantReply:
        result: AssistantReply | None = None
        for event in self._iter_chat_events(messages, show_bql=show_bql):
            if event.event == 'done':
                result = AssistantReply(
                    reply=event.data['reply'],
                    queries=query_records_from_dicts(event.data['queries']),
                    thinking=event.data.get('thinking', ''),
                    reasoning=event.data.get('reasoning', ''),
                )
        if result is None:
            raise RuntimeError('对话未产生完成事件')
        return result

    def chat_stream(
        self,
        messages: list[dict[str, str]],
        show_bql: bool = False,
    ) -> Iterator[str]:
        try:
            for event in self._iter_chat_events(messages, show_bql=show_bql):
                yield format_sse(event.event, event.data)
        except (ValueError, LedgerNotFoundError):
            raise
        except Exception as exc:
            logger.exception('AI 助手流式调用失败')
            yield format_sse('error', {'detail': f'AI 助手暂时不可用: {exc}'})
