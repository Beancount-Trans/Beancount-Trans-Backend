"""生效账本的账户引用索引

只读取 include 图内实际生效的账本文件，用 beancount 解析结果建立
「账户 → 引用位置」索引，供反向对账与改名使用。

与 ``ledger_dedupe.resolve_effective_files`` 的区别：
- 本模块面向账户引用（交易 posting 与各类指令的 account 字段）；
- 位置信息取条目自身的 ``filename`` / ``lineno``，用于定位改写范围。
"""
import os
from collections import defaultdict

from beancount import loader
from beancount.core import data

from project.apps.account.ledger_dedupe import resolve_effective_files
from project.utils.file import BeanFileManager


def _rel_path(user, filename):
    """绝对路径 → 相对用户账本根的 POSIX 路径"""
    root = os.path.abspath(BeanFileManager.get_user_assets_path(user))
    abs_path = os.path.abspath(filename)
    try:
        rel_path = os.path.relpath(abs_path, root).replace(os.sep, '/')
    except ValueError:
        return abs_path
    return rel_path


def _effective_files(user):
    """返回 include 图内的生效文件；主账本不可解析时返回 None"""
    main_path = BeanFileManager.get_main_bean_path(user)
    return resolve_effective_files(main_path)


def build_account_index(user):
    """建立账户引用索引

    Returns:
        dict[str, list[dict]]: 账户 → [{'file': 相对路径, 'line': 行号, 'kind': 指令类型}]
        无法解析主账本时返回空字典（调用方不应据此判定账本无账户）。
    """
    main_path = BeanFileManager.get_main_bean_path(user)
    if not os.path.isfile(main_path):
        return {}

    entries, _errors, _options = loader.load_file(main_path)
    index = defaultdict(list)

    for entry in entries:
        filename = (entry.meta or {}).get('filename')
        lineno = (entry.meta or {}).get('lineno')
        if not filename:
            continue
        location = {
            'file': _rel_path(user, filename),
            'line': lineno,
            'kind': type(entry).__name__,
        }

        if isinstance(entry, data.Transaction):
            for posting in entry.postings:
                if posting.account:
                    index[posting.account].append(dict(location))
            continue

        account = getattr(entry, 'account', None)
        if account:
            index[account].append(dict(location))

        source_account = getattr(entry, 'source_account', None)
        if source_account:
            index[source_account].append(dict(location))

    return dict(index)


def referenced_accounts(user):
    """账本中实际引用到的账户集合（含交易与指令）"""
    return set(build_account_index(user))


def declared_state(user):
    """账本中的 (已 open 账户, 已 close 账户) 集合，一次解析同时返回"""
    main_path = BeanFileManager.get_main_bean_path(user)
    if not os.path.isfile(main_path):
        return set(), set()

    entries, _errors, _options = loader.load_file(main_path)
    opens = {
        entry.account
        for entry in entries
        if isinstance(entry, data.Open) and entry.account
    }
    closes = {
        entry.account
        for entry in entries
        if isinstance(entry, data.Close) and entry.account
    }
    return opens, closes


def declared_currencies(user):
    """账本 open 声明的账户币种

    Returns:
        dict[str, dict]: 账户 → {'currencies': list[str], 'file': 相对路径}
        未声明币种时 ``currencies`` 为空列表；主账本缺失时返回空字典。
    """
    main_path = BeanFileManager.get_main_bean_path(user)
    if not os.path.isfile(main_path):
        return {}

    entries, _errors, _options = loader.load_file(main_path)
    result = {}
    for entry in entries:
        if not isinstance(entry, data.Open) or not entry.account:
            continue
        filename = (entry.meta or {}).get('filename')
        result[entry.account] = {
            'currencies': list(entry.currencies or []),
            'file': _rel_path(user, filename) if filename else None,
        }
    return result


def declared_accounts(user):
    """账本中声明过 open 的账户集合"""
    return declared_state(user)[0]


def closed_accounts(user):
    """账本中声明过 close 的账户集合"""
    return declared_state(user)[1]


def account_reference_count(user):
    """每个账户的引用次数（用于改名影响清单）"""
    return {
        account: len(locations)
        for account, locations in build_account_index(user).items()
    }
