"""清理平台账本文件（trans/collect.bean）中重复的 ``open`` 指令

背景：用户注册时平台会按官方账户模板向 ``trans/collect.bean`` 写入全部账户的
``open``；若用户随后启用 Git 并使用模板仓库，模板的 ``account/*.bean`` 里往往
已有同账户的 ``open``，两者同时生效即触发 Beancount 的
``Duplicate open directive``。

本模块只做一件事：把「平台文件里与其它已生效账本文件重复的 open」删掉，
并修正因此可能落在 open 之前的占位 note 日期。不改用户/模板文件，不改 open
的日期、币种与注释。

写回后会重新加载账本，只比对「账户类错误」的增量；若错误变多则还原文件。
"""
import logging
import os
import re
from collections import Counter, namedtuple
from datetime import date

from project.utils.file import BeanFileManager

logger = logging.getLogger(__name__)

DedupeResult = namedtuple(
    'DedupeResult',
    ['changed', 'removed_opens', 'rewritten_notes', 'residual_errors'],
)

_INCLUDE_RE = re.compile(r'^\s*include\s+"([^"]+)"')
_OPEN_RE = re.compile(r'^\s*(\d{4}-\d{2}-\d{2})\s+open\s+([^\s;]+)(.*)$')
_NOTE_RE = re.compile(r'^(\s*)(\d{4}-\d{2}-\d{2})\s+note\s+([^\s;]+)\s+"([^"]*)"(.*)$')

# 只关心账户声明/引用类错误，用户历史数据错误（Balance、不平交易等）不参与比对
ACCOUNT_ERROR_MARKERS = (
    'Duplicate open directive',
    'Duplicate close directive',
    'Invalid reference to unknown account',
    'Invalid reference to inactive account',
    'Invalid currency ',
    'Unopened account ',
)


def resolve_effective_files(main_bean_path):
    """从 main.bean 递归解析 include，返回实际生效的 .bean 绝对路径集合

    - include 路径按 Beancount 语义相对于「当前文件所在目录」解析；
    - 缺失文件忽略（Beancount 自身会报错，这里不重复处理）；
    - 带环检测；
    - main.bean 不存在或解析不到任何 include 时返回 None，
      调用方按「全部 .bean」兜底，避免遗留重复。
    """
    if not main_bean_path or not os.path.isfile(main_bean_path):
        return None

    main_abs = os.path.abspath(main_bean_path)
    effective = set()
    seen = set()
    stack = [main_abs]
    found_include = False

    while stack:
        path = stack.pop()
        if path in seen:
            continue
        seen.add(path)

        try:
            with open(path, 'r', encoding='utf-8') as f:
                text = f.read()
        except OSError as e:
            logger.warning('解析 include 时读取失败，跳过 %s: %s', path, e)
            continue

        effective.add(path)

        for line in text.splitlines():
            match = _INCLUDE_RE.match(line)
            if not match:
                continue
            found_include = True
            include_path = match.group(1)
            resolved = os.path.abspath(
                os.path.join(os.path.dirname(path), include_path)
            )
            if os.path.isfile(resolved):
                stack.append(resolved)

    if not found_include:
        return None
    return effective


def _iter_bean_files(root):
    """遍历 root 下全部 .bean 文件（绝对路径）"""
    if not os.path.isdir(root):
        return
    for dirpath, _dirnames, filenames in os.walk(root):
        for filename in sorted(filenames):
            if filename.endswith('.bean'):
                yield os.path.abspath(os.path.join(dirpath, filename))


def _collect_external_opens(user, platform_file):
    """收集平台文件之外、账本实际生效文件中的 open（账户 → 最早日期）"""
    assets_root = BeanFileManager.get_user_assets_path(user)
    main_path = BeanFileManager.get_main_bean_path(user)

    effective = resolve_effective_files(main_path)
    candidates = effective if effective is not None else set(_iter_bean_files(assets_root))

    platform_abs = os.path.abspath(platform_file)
    external = {}

    for path in candidates:
        if os.path.abspath(path) == platform_abs:
            continue
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
        except OSError as e:
            logger.warning('读取账本文件失败，跳过 %s: %s', path, e)
            continue

        for line in content.splitlines():
            match = _OPEN_RE.match(line)
            if not match:
                continue
            account = match.group(2)
            try:
                open_date = date.fromisoformat(match.group(1))
            except ValueError:
                continue
            current = external.get(account)
            if current is None or open_date < current:
                external[account] = open_date

    return external


def collect_account_errors(main_bean_path):
    """加载账本，返回账户声明/引用类错误（含文件与行号）"""
    if not main_bean_path or not os.path.isfile(main_bean_path):
        return []

    try:
        from beancount import loader
    except Exception as e:  # pragma: no cover - 依赖缺失时放行
        logger.warning('beancount 不可用，跳过账本校验: %s', e)
        return []

    try:
        _entries, errors, _options = loader.load_file(main_bean_path)
    except Exception as e:  # pragma: no cover - 加载异常不应阻断业务
        logger.warning('加载账本失败，跳过校验: %s', e)
        return []

    result = []
    for error in errors:
        message = str(getattr(error, 'message', '') or '')
        if not any(marker in message for marker in ACCOUNT_ERROR_MARKERS):
            continue
        source = getattr(error, 'source', None)
        result.append({
            'message': message,
            'file': getattr(source, 'filename', None) if source else None,
            'line': getattr(source, 'lineno', None) if source else None,
        })
    return result


def _error_counter(errors):
    return Counter(error['message'] for error in errors)


def _write_atomic(path, text):
    """写临时文件后原子替换，避免半截内容"""
    directory = os.path.dirname(os.path.abspath(path))
    tmp_path = os.path.join(directory, f'.{os.path.basename(path)}.dedupe.tmp')
    with open(tmp_path, 'w', encoding='utf-8') as f:
        f.write(text)
    os.replace(tmp_path, path)


def _dedupe_platform_text(text, external, placeholder_note):
    """对平台文件内容做去重，返回 (新内容, 删除的 open 行, 改写的 note 行, 是否变化)"""
    trailing_newline = text.endswith('\n')
    lines = text.splitlines()
    kept = []
    removed_opens = []
    rewritten_notes = []
    seen_accounts = set()
    removed_accounts = set()
    rewritten_accounts = set()

    for line in lines:
        open_match = _OPEN_RE.match(line)
        if open_match:
            account = open_match.group(2)
            if account in external or account in seen_accounts:
                removed_opens.append(line.strip())
                removed_accounts.add(account)
                continue
            seen_accounts.add(account)
            kept.append(line)
            continue

        note_match = _NOTE_RE.match(line)
        if (
            note_match
            and note_match.group(3) in removed_accounts
            and note_match.group(4) == placeholder_note
            and note_match.group(3) not in rewritten_accounts
        ):
            account = note_match.group(3)
            external_date = external.get(account)
            if external_date is not None:
                try:
                    note_date = date.fromisoformat(note_match.group(2))
                except ValueError:
                    kept.append(line)
                    continue
                if note_date < external_date:
                    new_line = (
                        f'{note_match.group(1)}{external_date.isoformat()} '
                        f'note {account} "{note_match.group(4)}"{note_match.group(5)}'
                    )
                    rewritten_notes.append(new_line.strip())
                    rewritten_accounts.add(account)
                    kept.append(new_line)
                    continue

        kept.append(line)

    changed = bool(removed_opens or rewritten_notes)
    if not changed:
        return text, removed_opens, rewritten_notes, False

    new_text = '\n'.join(kept)
    if trailing_newline:
        new_text += '\n'
    return new_text, removed_opens, rewritten_notes, True


def dedupe_platform_opens(user, *, dry_run=False):
    """删除平台文件中与其它生效文件重复的 open，并修正占位 note 日期

    Args:
        user: 用户对象（决定账本目录）
        dry_run: 为 True 时只计算改动，不写盘、不校验

    Returns:
        DedupeResult: changed / removed_opens / rewritten_notes / residual_errors
    """
    from project.apps.account.ledger_sync import PLACEHOLDER_NOTE

    platform_file = BeanFileManager.get_collect_bean_path(user)
    if not os.path.isfile(platform_file):
        return DedupeResult(False, [], [], [])

    try:
        with open(platform_file, 'r', encoding='utf-8') as f:
            original_text = f.read()
    except OSError as e:
        logger.warning('读取平台账本文件失败，跳过去重 %s: %s', platform_file, e)
        return DedupeResult(False, [], [], [])

    external = _collect_external_opens(user, platform_file)
    new_text, removed_opens, rewritten_notes, changed = _dedupe_platform_text(
        original_text, external, PLACEHOLDER_NOTE
    )

    if not changed:
        return DedupeResult(False, [], [], [])

    if dry_run:
        return DedupeResult(True, removed_opens, rewritten_notes, [])

    main_bean_path = BeanFileManager.get_main_bean_path(user)
    baseline = _error_counter(collect_account_errors(main_bean_path))

    _write_atomic(platform_file, new_text)

    post_errors = collect_account_errors(main_bean_path)
    residual_counter = _error_counter(post_errors) - baseline
    if residual_counter:
        residual_errors = [
            error for error in post_errors if residual_counter.get(error['message'])
        ]
        _write_atomic(platform_file, original_text)
        logger.error(
            '平台账本去重后出现新的账户类错误，已还原 %s: %s',
            platform_file,
            '; '.join(sorted(residual_counter)),
        )
        return DedupeResult(False, removed_opens, rewritten_notes, residual_errors)

    logger.info(
        '平台账本去重完成 %s: 删除 open %d 条，改写 note %d 条',
        platform_file,
        len(removed_opens),
        len(rewritten_notes),
    )
    return DedupeResult(True, removed_opens, rewritten_notes, [])
