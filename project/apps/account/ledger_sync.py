"""账户生命周期同步到账本（trans/collect.bean）

平台新增 / 启用 / 关闭 / 删除 / 修改币种时，向用户账本追加或重写 Beancount 指令：

- 新增账户：``open``（统一使用早期基准日 2000-01-01，带账户币种）+ 当天一条 ``note`` 占位。
  ``note`` 用于消除 ``beancount.plugins.nounused`` 插件的 ``Unused account`` 报错
  （该插件文档给出的官方消音方式），同时承载真实的创建/启用时间。
- 启用 / 关闭：追加带日期的状态 ``note``。平台的开关是可逆的，而 Beancount 的
  ``open`` / ``close`` 不可逆，因此不用 ``close`` 表达可逆状态。
- 删除账户：追加 ``close``（不可逆操作才写 close）。
- 修改币种：``open`` 对同一账户只允许出现一次，因此**就地重写**已有 ``open`` 行的
  币种列表（保留 open 日期、booking 方式与行尾注释），不追加重复 ``open``。
- 重新创建同名账户时：移除账本中该账户已有的 ``close``，否则无法再次 open
  （Beancount 同一账户只允许一次 open / close）。

写入前会扫描整本账本做去重；状态 ``note`` 与账本中最近一条状态记录相同则跳过。
本模块只负责文件读写，不负责 Git 提交；需要提交的路径通过 ``LedgerSyncResult.paths``
返回给上层处理。
"""
import logging
import os
import re
from collections import namedtuple
from datetime import date

from project.utils.file import BeanFileManager

logger = logging.getLogger(__name__)

PLACEHOLDER_NOTE = "平台创建账户"
STATE_NOTE_ENABLED = "平台启用账户"
STATE_NOTE_DISABLED = "平台关闭账户"
STATE_NOTES = (STATE_NOTE_ENABLED, STATE_NOTE_DISABLED)
# 平台账户统一的开户基准日：早于任何可能的交易，避免回溯交易触发 inactive account
OPEN_BASE_DATE = date(2000, 1, 1)
# 早期版本用 note 记录删除，重建同名账户时顺带清理
LEGACY_NOTE_DELETED = "平台删除账户"

COLLECT_REL_PATH = 'trans/collect.bean'

LedgerSyncResult = namedtuple(
    'LedgerSyncResult', ['text', 'paths', 'warnings'], defaults=((),)
)


def _is_git_user(user) -> bool:
    """是否启用了 Git（有无 GitRepository 记录）"""
    return hasattr(user, 'git_repo')


def _is_platform_managed(user, file_path) -> bool:
    """文件是否属于平台可写区

    - ``trans/`` 始终由平台独占写入；
    - 非 Git 用户的历史月度文件（``{year}/**``）也由平台生成，视为平台区；
    - Git 用户的 ``main.bean``、``account/**``、``{year}/**`` 属于用户区，平台不改写。
    """
    root = os.path.abspath(BeanFileManager.get_user_assets_path(user))
    abs_path = os.path.abspath(file_path)
    try:
        rel_path = os.path.relpath(abs_path, root).replace(os.sep, '/')
    except ValueError:
        return False

    if rel_path.startswith('../') or rel_path == '..':
        return False
    if rel_path == 'trans' or rel_path.startswith('trans/'):
        return True
    if not _is_git_user(user) and re.match(r'^\d{4}/', rel_path):
        return True
    return False

_DATE_PREFIX_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})(?:\s|$)')
_OPEN_CLOSE_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})\s+(open|close)\s+(\S+)')
_NOTE_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})\s+note\s+(\S+)\s+"([^"]*)"')
_CURRENCY_LIST_RE = re.compile(r"^[A-Z][A-Z0-9'._-]*(?:,[A-Z][A-Z0-9'._-]*)*$")


def _iter_bean_file_paths(user):
    """遍历用户账本下所有 .bean 文件

    Yields:
        Tuple[str, str]: (文件绝对路径, 用户账本根目录)
    """
    root = BeanFileManager.get_user_assets_path(user)
    if not os.path.isdir(root):
        return

    for dirpath, _dirnames, filenames in os.walk(root):
        for filename in sorted(filenames):
            if filename.endswith('.bean'):
                yield os.path.join(dirpath, filename), root


def _rel_path(root, file_path):
    """文件绝对路径 → 相对用户仓库根的 POSIX 路径"""
    return os.path.relpath(file_path, root).replace(os.sep, '/')


def _iter_ledger_blocks(user):
    """遍历用户账本下所有 .bean 条目

    Yields:
        Tuple[str, str]: (块首行日期 "YYYY-MM-DD", 块文本)
    """
    for file_path, _root in _iter_bean_file_paths(user):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except OSError as e:
            logger.warning(f"读取账本文件失败，跳过 {file_path}: {e}")
            continue

        for _month, block in BeanFileManager.split_entries(content):
            first_line = block.lstrip().splitlines()[0] if block.strip() else ''
            match = _DATE_PREFIX_RE.match(first_line)
            if match:
                yield match.group(1), block


def _account_reference_re(account_name):
    """构造带边界的账户引用正则，避免 Assets:A 命中 Assets:AB"""
    return re.compile(r'(?<![\w:-])' + re.escape(account_name) + r'(?![\w:-])')


def scan_account_state(user, account_name) -> dict:
    """扫描账本中该账户的状态

    Returns:
        dict: {
            'open_date': date | None,          # 首次 open 的日期
            'close_date': date | None,         # 最近一次 close 的日期
            'earliest_reference': date | None, # 最早被交易/其它指令引用的日期
            'last_state_note': str | None,     # 最近一条平台状态 note 的文本
        }
    """
    state = {
        'open_date': None,
        'close_date': None,
        'earliest_reference': None,
        'last_state_note': None,
    }
    reference_re = _account_reference_re(account_name)
    last_note_key = None

    for order, (block_date, block) in enumerate(_iter_ledger_blocks(user)):
        first_line = block.strip().splitlines()[0]
        directive_match = _OPEN_CLOSE_RE.match(first_line)

        if directive_match:
            directive_date, directive_type, directive_account = directive_match.groups()
            if directive_account != account_name:
                continue
            parsed_date = date.fromisoformat(directive_date)
            if directive_type == 'open':
                if state['open_date'] is None or parsed_date < state['open_date']:
                    state['open_date'] = parsed_date
            elif state['close_date'] is None or parsed_date > state['close_date']:
                state['close_date'] = parsed_date
            continue

        note_match = _NOTE_RE.match(first_line)
        if note_match and note_match.group(2) == account_name:
            if note_match.group(3) in STATE_NOTES:
                # 取日期最晚的一条；同日期取文件中靠后的一条
                note_key = (block_date, order)
                if last_note_key is None or note_key > last_note_key:
                    last_note_key = note_key
                    state['last_state_note'] = note_match.group(3)
                continue

        if reference_re.search(block):
            parsed_date = date.fromisoformat(block_date)
            if state['earliest_reference'] is None or parsed_date < state['earliest_reference']:
                state['earliest_reference'] = parsed_date

    return state


def _resolve_open_date(state) -> date:
    """open 日期取固定基准日，若账本存在更早的引用则取更早者

    这样回溯上传早期账单时不会触发 Beancount 的 inactive account 错误。
    """
    earliest_reference = state.get('earliest_reference')
    if earliest_reference and earliest_reference < OPEN_BASE_DATE:
        return earliest_reference
    return OPEN_BASE_DATE


def _currency_suffix(currency) -> str:
    """open 行尾的币种列表（空币种不写约束）"""
    return f" {currency}" if currency else ""


def _write(user, lines) -> str:
    text = "\n".join(lines)
    BeanFileManager.append_to_collect_bean(user, [text])
    return text


def _write_result(user, lines) -> LedgerSyncResult:
    text = _write(user, lines)
    return LedgerSyncResult(text, [COLLECT_REL_PATH])


def _remove_close_entries(user, account_name):
    """移除平台区中该账户的 close（以及早期版本的删除状态 note）

    重新创建同名账户时，账本里残存的 close 会让账户永久处于关闭状态
    （Beancount 同一账户只允许 open / close 各一次），因此需要移除。
    只处理平台区文件；用户区的 close 保持不动并返回提示，交由用户自行处理。

    Returns:
        Tuple[List[str], List[str], List[str]]:
            (被移除的行, 发生改写的文件相对路径, 因位于用户区而跳过的文件)
    """
    escaped_account = re.escape(account_name)
    close_re = re.compile(rf'^\d{{4}}-\d{{2}}-\d{{2}}\s+close\s+{escaped_account}\s*$')
    legacy_note_re = re.compile(
        rf'^\d{{4}}-\d{{2}}-\d{{2}}\s+note\s+{escaped_account}\s+"{re.escape(LEGACY_NOTE_DELETED)}"\s*$'
    )

    removed = []
    changed_paths = []
    skipped_paths = []

    for file_path, root in _iter_bean_file_paths(user):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.read().splitlines()
        except OSError as e:
            logger.warning(f"读取账本文件失败，跳过 {file_path}: {e}")
            continue

        kept, file_removed = [], []
        for line in lines:
            if close_re.match(line) or legacy_note_re.match(line):
                file_removed.append(line)
            else:
                kept.append(line)

        if not file_removed:
            continue

        rel_path = _rel_path(root, file_path)
        if not _is_platform_managed(user, file_path):
            skipped_paths.append(rel_path)
            continue

        text = re.sub(r'\n{3,}', '\n\n', "\n".join(kept)).rstrip('\n') + '\n'
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(text)
        removed.extend(file_removed)
        changed_paths.append(rel_path)

    return removed, changed_paths, skipped_paths


def _split_inline_comment(text):
    """按引号感知的方式拆出行尾注释，返回 (代码部分, ;注释部分)"""
    in_quotes = False
    for index, char in enumerate(text):
        if char == '"':
            in_quotes = not in_quotes
        elif char == ';' and not in_quotes:
            return text[:index], text[index:]
    return text, ''


def _format_open_tail(tokens, comment) -> str:
    """把 open 账户名之后的 token 与注释拼回行尾"""
    core = ' '.join(tokens)
    tail = f' {core}' if core else ''
    if comment:
        tail = f'{tail}  {comment}' if tail else f'  {comment}'
    return tail


def _rewrite_open_line(line, account_name, currency) -> str:
    """改写单行 open 的币种列表，保留日期、booking 与注释"""
    match = re.match(
        rf'^(?P<prefix>\d{{4}}-\d{{2}}-\d{{2}}\s+open\s+{re.escape(account_name)})(?P<tail>(?:\s.*)?)$',
        line,
    )
    if not match:
        return line

    prefix = match.group('prefix')
    tail = match.group('tail') or ''
    code, comment = _split_inline_comment(tail)
    tokens = code.split()

    if currency:
        if tokens and _CURRENCY_LIST_RE.match(tokens[0]):
            new_tokens = [currency] + tokens[1:]
        else:
            new_tokens = [currency] + tokens
    else:
        if not tokens or not _CURRENCY_LIST_RE.match(tokens[0]):
            return line
        new_tokens = tokens[1:]

    new_tail = _format_open_tail(new_tokens, comment)
    if new_tail == tail:
        return line
    return prefix + new_tail


def _rewrite_open_currency(user, account_name, currency):
    """就地重写平台区中该账户 open 行的币种列表

    只处理平台区文件；用户区的 open 保持不动并返回提示，交由用户自行调整。

    Returns:
        Tuple[List[str], List[str], List[str]]:
            (改写后的行, 发生改写的文件相对路径, 因位于用户区而跳过的文件)
    """
    changed_lines = []
    changed_paths = []
    skipped_paths = []

    for file_path, root in _iter_bean_file_paths(user):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                text = f.read()
        except OSError as e:
            logger.warning(f"读取账本文件失败，跳过 {file_path}: {e}")
            continue

        if not text:
            continue

        file_changed = False
        file_changed_lines = []
        new_lines = []
        for line in text.splitlines():
            new_line = _rewrite_open_line(line, account_name, currency)
            if new_line != line:
                file_changed = True
                file_changed_lines.append(new_line)
            new_lines.append(new_line)

        if not file_changed:
            continue

        rel_path = _rel_path(root, file_path)
        if not _is_platform_managed(user, file_path):
            skipped_paths.append(rel_path)
            continue

        new_text = '\n'.join(new_lines)
        if text.endswith('\n'):
            new_text += '\n'
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(new_text)
        changed_lines.extend(file_changed_lines)
        changed_paths.append(rel_path)

    return changed_lines, changed_paths, skipped_paths


def sync_open(account) -> LedgerSyncResult:
    """把新账户的 open 写入账本

    账本中已存在该账户的 open（例如手工维护的 account/*.bean）时跳过 open 写入；
    若存在 close（删除后重建同名账户），先移除 close 恢复账户可用；
    若账户配置了币种，则把已有 open 的币种列表对齐到平台配置。
    """
    state = scan_account_state(account.owner, account.account)
    if state['open_date'] is not None:
        removed, close_paths, close_skipped = _remove_close_entries(
            account.owner, account.account
        )
        lines = list(removed)
        paths = list(close_paths)
        warnings = []

        if close_skipped:
            warnings.append(
                f"账户 {account.account} 的 close 位于用户区文件 "
                f"{', '.join(close_skipped)}，平台未改动，请在账本中自行处理"
            )

        # 账本已有 open 时保留原行，仅按平台配置对齐币种
        if account.currency:
            rewritten, rewrite_paths, rewrite_skipped = _rewrite_open_currency(
                account.owner, account.account, account.currency
            )
            lines.extend(rewritten)
            paths.extend(rewrite_paths)
            if rewrite_skipped:
                warnings.append(
                    f"账户 {account.account} 的 open 位于用户区文件 "
                    f"{', '.join(rewrite_skipped)}，平台未修改币种，请在账本中自行调整"
                )

        # 重建的账户默认启用：若账本最后状态是「关闭」，补一条「启用」让状态链连贯
        if account.enable and state['last_state_note'] == STATE_NOTE_DISABLED:
            record_date = max(date.today(), state['open_date'])
            note_line = f'{record_date} note {account.account} "{STATE_NOTE_ENABLED}"'
            _write(account.owner, [note_line])
            lines.append(note_line)
            paths.append(COLLECT_REL_PATH)

        unique_paths = list(dict.fromkeys(paths))
        return LedgerSyncResult(
            "\n".join(lines) if lines else None, unique_paths, warnings
        )

    open_date = _resolve_open_date(state)
    record_date = max(date.today(), open_date)
    return _write_result(account.owner, [
        f"{open_date} open {account.account}{_currency_suffix(account.currency)}",
        f'{record_date} note {account.account} "{PLACEHOLDER_NOTE}"',
    ])


def sync_state(account, state_text) -> LedgerSyncResult:
    """把可逆状态（启用/关闭）以 note 形式写入账本

    与账本中最近一条状态记录相同时跳过，因此同一天内反复切换只会记录最终状态；
    若账本中从未 open 过该账户，则先补一条 open（带币种）。
    """
    state = scan_account_state(account.owner, account.account)
    if state['last_state_note'] == state_text:
        return LedgerSyncResult(None, [])

    lines = []
    open_date = state['open_date']
    if open_date is None:
        open_date = _resolve_open_date(state)
        lines.append(f"{open_date} open {account.account}{_currency_suffix(account.currency)}")

    record_date = max(date.today(), open_date)
    lines.append(f'{record_date} note {account.account} "{state_text}"')
    return _write_result(account.owner, lines)


def sync_close(account) -> LedgerSyncResult:
    """删除账户时把 close 写入账本

    账本中已存在 close 时跳过；若账本中从未 open 过该账户，则先补一条 open（带币种）。
    """
    state = scan_account_state(account.owner, account.account)
    if state['close_date'] is not None:
        return LedgerSyncResult(None, [])

    lines = []
    open_date = state['open_date']
    if open_date is None:
        open_date = _resolve_open_date(state)
        lines.append(f"{open_date} open {account.account}{_currency_suffix(account.currency)}")

    close_date = max(date.today(), open_date)
    lines.append(f"{close_date} close {account.account}")
    return _write_result(account.owner, lines)


def sync_currency(account) -> LedgerSyncResult:
    """币种变更时同步账本

    - 账本已有 open：就地重写币种列表；
    - 账本没有 open：补写 open（带币种）+ 占位 note，避免 nounused 报 Unused account。
    """
    state = scan_account_state(account.owner, account.account)
    if state['open_date'] is None:
        return sync_open(account)

    rewritten, paths, skipped = _rewrite_open_currency(
        account.owner, account.account, account.currency
    )
    text = "\n".join(rewritten) if rewritten else None
    warnings = []
    if skipped:
        warnings.append(
            f"账户 {account.account} 的 open 位于用户区文件 "
            f"{', '.join(skipped)}，平台未修改币种，请在账本中自行调整"
        )
    return LedgerSyncResult(text, paths, warnings)
