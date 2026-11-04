"""账户生命周期同步到账本（trans/collect.bean）

平台新增 / 启用 / 关闭 / 删除账户时，向用户账本追加 Beancount 指令：

- 新增账户：``open``（统一使用早期基准日 2000-01-01）+ 当天一条 ``note`` 占位。
  ``note`` 用于消除 ``beancount.plugins.nounused`` 插件的 ``Unused account`` 报错
  （该插件文档给出的官方消音方式），同时承载真实的创建/启用时间。
- 启用 / 关闭：追加带日期的状态 ``note``。平台的开关是可逆的，而 Beancount 的
  ``open`` / ``close`` 不可逆，因此不用 ``close`` 表达可逆状态。
- 删除账户：追加 ``close``（不可逆操作才写 close）。
- 重新创建同名账户时：移除账本中该账户已有的 ``close``，否则无法再次 open
  （Beancount 同一账户只允许一次 open / close）。

写入前会扫描整本账本做去重；状态 ``note`` 与账本中最近一条状态记录相同则跳过。
本模块只依赖文件系统工具，不依赖数据库。
"""
import logging
import os
import re
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

_DATE_PREFIX_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})(?:\s|$)')
_OPEN_CLOSE_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})\s+(open|close)\s+(\S+)')
_NOTE_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})\s+note\s+(\S+)\s+"([^"]*)"')


def _iter_ledger_blocks(user):
    """遍历用户账本下所有 .bean 条目

    Yields:
        Tuple[str, str]: (块首行日期 "YYYY-MM-DD", 块文本)
    """
    root = BeanFileManager.get_user_assets_path(user)
    if not os.path.isdir(root):
        return

    for dirpath, _dirnames, filenames in os.walk(root):
        for filename in sorted(filenames):
            if not filename.endswith('.bean'):
                continue
            file_path = os.path.join(dirpath, filename)
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


def _write(user, lines) -> str:
    text = "\n".join(lines)
    BeanFileManager.append_to_collect_bean(user, [text])
    return text


def _remove_close_entries(user, account_name) -> list:
    """移除账本中该账户的 close（以及早期版本的删除状态 note）

    重新创建同名账户时，账本里残存的 close 会让账户永久处于关闭状态
    （Beancount 同一账户只允许 open / close 各一次），因此需要移除。
    覆盖用户账本目录下所有 `.bean`（含已归入月度的账本），仅删除单行 close 条目。

    Returns:
        List[str]: 被移除的行
    """
    escaped_account = re.escape(account_name)
    close_re = re.compile(rf'^\d{{4}}-\d{{2}}-\d{{2}}\s+close\s+{escaped_account}\s*$')
    legacy_note_re = re.compile(
        rf'^\d{{4}}-\d{{2}}-\d{{2}}\s+note\s+{escaped_account}\s+"{re.escape(LEGACY_NOTE_DELETED)}"\s*$'
    )

    root = BeanFileManager.get_user_assets_path(user)
    if not os.path.isdir(root):
        return []

    removed = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for filename in sorted(filenames):
            if not filename.endswith('.bean'):
                continue
            file_path = os.path.join(dirpath, filename)
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

            text = re.sub(r'\n{3,}', '\n\n', "\n".join(kept)).rstrip('\n') + '\n'
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(text)
            removed.extend(file_removed)

    return removed


def sync_open(account) -> str | None:
    """把新账户的 open 写入账本

    账本中已存在该账户的 open（例如手工维护的 account/*.bean）时跳过；
    若存在 close（删除后重建同名账户），先移除 close 恢复账户可用。
    """
    state = scan_account_state(account.owner, account.account)
    if state['open_date'] is not None:
        removed = _remove_close_entries(account.owner, account.account)
        # 重建的账户默认启用：若账本最后状态是「关闭」，补一条「启用」让状态链连贯
        if account.enable and state['last_state_note'] == STATE_NOTE_DISABLED:
            record_date = max(date.today(), state['open_date'])
            note_line = f'{record_date} note {account.account} "{STATE_NOTE_ENABLED}"'
            _write(account.owner, [note_line])
            removed.append(note_line)
        return "\n".join(removed) if removed else None

    open_date = _resolve_open_date(state)
    record_date = max(date.today(), open_date)
    return _write(account.owner, [
        f"{open_date} open {account.account}",
        f'{record_date} note {account.account} "{PLACEHOLDER_NOTE}"',
    ])


def sync_state(account, state_text) -> str | None:
    """把可逆状态（启用/关闭）以 note 形式写入账本

    与账本中最近一条状态记录相同时跳过，因此同一天内反复切换只会记录最终状态；
    若账本中从未 open 过该账户，则先补一条 open。
    """
    state = scan_account_state(account.owner, account.account)
    if state['last_state_note'] == state_text:
        return None

    lines = []
    open_date = state['open_date']
    if open_date is None:
        open_date = _resolve_open_date(state)
        lines.append(f"{open_date} open {account.account}")

    record_date = max(date.today(), open_date)
    lines.append(f'{record_date} note {account.account} "{state_text}"')
    return _write(account.owner, lines)


def sync_close(account) -> str | None:
    """删除账户时把 close 写入账本

    账本中已存在 close 时跳过；若账本中从未 open 过该账户，则先补一条 open。
    """
    state = scan_account_state(account.owner, account.account)
    if state['close_date'] is not None:
        return None

    lines = []
    open_date = state['open_date']
    if open_date is None:
        open_date = _resolve_open_date(state)
        lines.append(f"{open_date} open {account.account}")

    close_date = max(date.today(), open_date)
    lines.append(f"{close_date} close {account.account}")
    return _write(account.owner, lines)
