"""账户币种的解析、校验与账本冲突扫描

- 币种以英文逗号分隔存储（如 ``CNY,COIN``），与 Beancount ``open`` 指令一致。
- 仅资产 / 负债账户允许设置币种，其余根账户必须留空。
- 修改币种前扫描账本，确保该账户没有使用新币种列表之外的交易或余额断言，
  否则 Beancount 加载会报错。
"""
import logging
import os
import re
from typing import List

from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)

# 仅这两类账户允许设置币种
CURRENCY_ACCOUNT_ROOTS = ('Assets', 'Liabilities')

# 与 project/apps/translate/models.py 中 FormatConfig.currency 保持一致
CURRENCY_CODE_RE = re.compile(r"^[A-Z][A-Z0-9'._-]{0,22}([A-Z0-9])?$")

_SEPARATOR_RE = re.compile(r'[,，\s]+')


def normalize_currency_codes(raw) -> str:
    """把用户输入规范为 ``CNY,COIN`` 形式

    - 支持中文逗号、空格分隔
    - 统一大写
    - 去空、按首次出现顺序去重
    """
    if raw is None:
        return ''
    parts = [part.strip().upper() for part in _SEPARATOR_RE.split(str(raw).strip())]
    seen = set()
    normalized = []
    for part in parts:
        if not part or part in seen:
            continue
        seen.add(part)
        normalized.append(part)
    return ','.join(normalized)


def validate_currency_codes(raw) -> str:
    """规范化并校验币种格式，非法时抛 ValidationError"""
    currency = normalize_currency_codes(raw)
    if not currency:
        return ''
    for code in currency.split(','):
        if not CURRENCY_CODE_RE.match(code):
            raise ValidationError(f"币种格式无效：{code}")
    return currency


def is_currency_account(account_path: str) -> bool:
    """账户根类型是否允许设置币种"""
    root = (account_path or '').split(':')[0]
    return root in CURRENCY_ACCOUNT_ROOTS


def find_currency_conflicts(user, account_path: str, allowed_codes: List[str]) -> List[str]:
    """扫描账本，返回该账户已使用但不在 allowed_codes 中的币种

    以用户 ``main.bean`` 可加载到的条目为准；账本不存在或加载异常时返回空列表，
    避免币种功能阻断其它正常操作。
    """
    if not allowed_codes:
        return []

    # 延迟导入，避免 account 模型加载时引入文件处理与 beancount 依赖
    from project.utils.file import BeanFileManager

    try:
        main_bean_path = BeanFileManager.get_main_bean_path(user)
    except Exception as exc:  # pragma: no cover - 防御性分支
        logger.warning("获取账本入口失败，跳过币种冲突扫描: %s", exc)
        return []

    if not os.path.exists(main_bean_path):
        return []

    try:
        from beancount import loader
        from beancount.core.data import Balance, Transaction
    except Exception as exc:  # pragma: no cover - 依赖缺失时放行
        logger.warning("beancount 不可用，跳过币种冲突扫描: %s", exc)
        return []

    try:
        entries, errors, _options = loader.load_file(main_bean_path)
    except Exception as exc:
        logger.warning("加载账本失败，跳过币种冲突扫描: %s", exc)
        return []

    if errors:
        logger.warning("账本加载存在 %d 个错误，币种冲突扫描仅覆盖已解析条目", len(errors))

    used = set()
    for entry in entries:
        if isinstance(entry, Transaction):
            for posting in entry.postings:
                units = getattr(posting, 'units', None)
                if posting.account == account_path and units is not None:
                    used.add(units.currency)
        elif isinstance(entry, Balance):
            if entry.account == account_path and entry.amount is not None:
                used.add(entry.amount.currency)

    return sorted(used - set(allowed_codes))


def validate_account_currency(
    account_path: str,
    raw_currency,
    user=None,
    scan_conflicts: bool = False,
) -> str:
    """账户币种的统一校验入口

    Args:
        account_path: 最终账户路径（更新时可能是新路径）
        raw_currency: 用户输入或数据库中的币种
        user: 用于扫描账本的用户；为空时跳过冲突扫描
        scan_conflicts: 是否执行账本冲突扫描

    Returns:
        str: 规范化后的币种

    Raises:
        ValidationError: 根账户不支持币种、格式非法或存在冲突币种
    """
    currency = validate_currency_codes(raw_currency)
    if not currency:
        return ''

    if not is_currency_account(account_path):
        raise ValidationError("仅资产与负债账户支持设置币种")

    allowed_codes = currency.split(',')
    if scan_conflicts and user is not None:
        conflicts = find_currency_conflicts(user, account_path, allowed_codes)
        if conflicts:
            raise ValidationError(
                f"账本中该账户已使用币种 {','.join(conflicts)}，"
                f"无法限制为 {currency}；请先在账本中处理这些交易"
            )

    return currency
