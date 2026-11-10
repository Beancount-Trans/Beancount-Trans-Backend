# project/apps/translate/services/asset_provisioning.py
"""解析成功后自动补齐资产账户与资产映射。

付款方式能推断出「机构 + 资金性质（+ 尾号 / 平台槽位）」时，自动创建：

- 账本账户（``Account``）
- 外部账户身份、账单写法别名与绑定（``external_accounts``）
- 资产映射（``Assets``），关键字为账单里的付款方式；用户仍处于官方模板模式时不写映射

推断不出唯一身份（无机构、无尾号、无槽位）时不做猜测，保持原解析结果。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Iterable, Optional

from project.apps.account.models import Account
from project.apps.external_accounts.hints import (
    infer_external_account_hint,
    slot_product,
)
from project.apps.external_accounts.models import (
    CARD_KINDS,
    ExternalAccount,
    ExternalAccountAlias,
    ExternalAccountBinding,
    Institution,
    InstrumentKind,
    PlatformSlot,
)
from project.apps.maps.models import Assets, Expense, Income
from project.apps.translate.utils import ASSETS_OTHER, BILL_ALI

logger = logging.getLogger(__name__)

# ``maps.Assets.key`` 的字段长度；超长的付款方式仍会创建账户与别名，只是不建映射。
ASSETS_MAPPING_KEY_MAX_LENGTH = 16

SLOT_ACCOUNT_PATHS = {
    PlatformSlot.ALIPAY_BALANCE: 'Assets:Savings:Web:AliPay',
    PlatformSlot.ALIPAY_FUND: 'Assets:Savings:Web:AliFund',
    PlatformSlot.ALIPAY_HUABEI: 'Liabilities:CreditCard:Web:AliPay:Huabei',
    PlatformSlot.ALIPAY_JIEBEI: 'Liabilities:CreditCard:Web:AliPay:Jiebei',
    PlatformSlot.ALIPAY_BEIYONGJIN: 'Liabilities:CreditCard:Web:AliPay:Beiyongjin',
    PlatformSlot.WECHAT_PAY: 'Assets:Savings:Web:WechatPay',
    PlatformSlot.WECHAT_FUND: 'Assets:Savings:Web:WechatFund',
}


@dataclass(frozen=True)
class ProvisionResult:
    account: str
    account_created: bool


def payment_key(bill: str, row: Dict) -> str:
    """账单行 → 资产解析使用的付款方式关键字（与解析入口保持一致）。"""
    text = (row.get('payment_method') or '').strip()
    if bill == BILL_ALI and '&' in text:
        text = text.split('&')[0]
    return text


def _account_path_for(kind: str, hint) -> str:
    """把结构化线索映射成账本账户路径；线索不足时返回空串。"""
    if hint.slot:
        return SLOT_ACCOUNT_PATHS.get(hint.slot, '')
    if not kind or kind not in CARD_KINDS or not hint.institution_code or not hint.tail:
        return ''
    if kind == InstrumentKind.CREDIT_CARD:
        return f'Liabilities:CreditCard:Bank:{hint.institution_code}:C{hint.tail}'
    return f'Assets:Savings:Bank:{hint.institution_code}:C{hint.tail}'


def _resolve_card_kind(owner_id: int, institution: Institution, tail: str, hinted_kind: str) -> str:
    """确定卡类型：账单写明就用；没写时复用已有身份，未知则按储蓄卡兜底。"""
    if hinted_kind:
        return hinted_kind
    kinds = set(
        ExternalAccount.objects.filter(
            owner_id=owner_id, institution=institution, tail=tail
        ).values_list('kind', flat=True)
    )
    if len(kinds) == 1:
        return kinds.pop()
    if kinds:
        # 同一机构同一尾号同时存在储蓄卡与信用卡，无法判断
        return ''
    # 微信/支付宝常省略「储蓄卡」，缺省按借记卡落账，用户可在账户管理里改
    return InstrumentKind.DEBIT_CARD


def _ensure_account(owner_id: int, path: str) -> tuple[Optional[Account], bool]:
    account = Account.objects.filter(owner_id=owner_id, account=path).first()
    if account is not None:
        # 用户主动停用的账户不重新启用，也不接管
        return (account, False) if account.enable else (None, False)
    try:
        return Account.objects.create(owner_id=owner_id, account=path, enable=True), True
    except Exception:
        logger.warning('自动创建资产账户失败: owner=%s path=%s', owner_id, path, exc_info=True)
        return None, False


def _has_own_mappings(owner_id: int) -> bool:
    """用户是否已有自己的映射数据。

    ``MappingDataProvider`` 以「三类映射全空」判定使用官方模板；只补资产映射
    会把用户从模板模式切到空用户表，导致支出/收入映射突然失效。
    """
    return (
        Expense.objects.filter(owner_id=owner_id).exists()
        or Assets.objects.filter(owner_id=owner_id).exists()
        or Income.objects.filter(owner_id=owner_id).exists()
    )


def _ensure_asset_mapping(owner_id: int, key: str, account: Account) -> bool:
    if not key or len(key) > ASSETS_MAPPING_KEY_MAX_LENGTH:
        return False
    if not _has_own_mappings(owner_id):
        # 模板模式下外部账户身份已足够解析，不建映射以免打断官方模板
        return False
    mapping = Assets.objects.filter(owner_id=owner_id, key=key).first()
    if mapping is None:
        Assets.objects.create(owner_id=owner_id, key=key, assets=account, enable=True)
        return True
    if mapping.assets_id is None:
        mapping.assets = account
        mapping.save(update_fields=['assets', 'modified'])
        return True
    return False


def _ensure_external_account(owner_id: int, hint, institution: Institution, kind: str):
    if hint.slot:
        existing = ExternalAccount.objects.filter(owner_id=owner_id, slot=hint.slot).first()
        if existing is not None:
            return existing
    product = slot_product(hint.slot)
    display_name = PlatformSlot(hint.slot).label if hint.slot else ''
    return ExternalAccount.objects.get_or_create(
        owner_id=owner_id,
        institution=institution,
        kind=kind,
        tail=hint.tail,
        product=product,
        defaults={
            'display_name': display_name,
            'slot': hint.slot,
            'enable': True,
        },
    )[0]


def _ensure_alias(owner_id: int, text: str, external: ExternalAccount) -> ExternalAccount:
    """写入账单写法别名；已指向其他外部账户时尊重既有别名。"""
    if not text or text == '/':
        return external
    alias, _ = ExternalAccountAlias.objects.get_or_create(
        owner_id=owner_id,
        text=text,
        defaults={
            'external_account': external,
            'source': ExternalAccountAlias.SOURCE_BILL,
        },
    )
    return alias.external_account


def provision_asset_account(owner_id: int, text: str, bill: str = '') -> Optional[ProvisionResult]:
    """按付款方式补齐资产账户、外部账户身份与资产映射。

    返回可用的账本账户路径；线索不足或账户已被停用时返回 ``None``。
    银行卡未写卡类型时，优先复用同机构同尾号的已有身份，否则按储蓄卡兜底。
    """
    text = (text or '').strip()
    if not text:
        return None

    hint = infer_external_account_hint(text, bill=bill)
    institution = Institution.objects.filter(code=hint.institution_code, enable=True).first()
    if institution is None:
        return None

    kind = hint.kind
    if not kind and not hint.slot and hint.tail:
        kind = _resolve_card_kind(owner_id, institution, hint.tail, kind)
    path = _account_path_for(kind, hint)
    if not path:
        return None

    try:
        external = _ensure_external_account(owner_id, hint, institution, kind)
        external = _ensure_alias(owner_id, hint.raw or text, external)
    except Exception:
        logger.warning('自动创建外部账户失败: owner=%s text=%s', owner_id, text, exc_info=True)
        return None

    binding = (
        ExternalAccountBinding.objects.select_related('account')
        .filter(owner_id=owner_id, external_account=external)
        .first()
    )
    if binding is not None:
        if not binding.account.enable:
            return None
        account, account_created = binding.account, False
    else:
        account, account_created = _ensure_account(owner_id, path)
        if account is None:
            return None
        if not ExternalAccountBinding.objects.filter(account=account).exists():
            try:
                ExternalAccountBinding.objects.create(
                    owner_id=owner_id, external_account=external, account=account
                )
            except Exception:
                logger.warning(
                    '自动绑定外部账户失败: owner=%s text=%s account=%s',
                    owner_id, text, account.account, exc_info=True,
                )

    try:
        _ensure_asset_mapping(owner_id, text, account)
    except Exception:
        logger.warning('自动创建资产映射失败: owner=%s text=%s', owner_id, text, exc_info=True)

    return ProvisionResult(account=account.account, account_created=account_created)


def provision_missing_asset_accounts(
    parsed_data: Iterable[Dict],
    bill_type: str,
    owner_id: Optional[int],
    enabled: bool = True,
) -> int:
    """把解析结果中落到 ``Assets:Other`` 的资产腿替换为自动补齐的账户。

    只处理「支出 / 收入」行：这类行的资产腿来自付款方式；``/`` 与「不计收支」
    行的资产腿是转账对端，付款方式并不指向它，不能据此猜测。
    """
    if not enabled or not owner_id or not parsed_data:
        return 0

    resolved: Dict[str, Optional[ProvisionResult]] = {}
    changed = 0
    for entry in parsed_data:
        if entry.get('account') != ASSETS_OTHER:
            continue
        row = entry.get('_original_row') or {}
        if (row.get('transaction_type') or '') not in ('支出', '收入'):
            continue

        key = payment_key(bill_type, row)
        if not key:
            continue
        if key not in resolved:
            resolved[key] = provision_asset_account(owner_id, key, bill=bill_type)
        result = resolved[key]
        if result is None:
            continue

        entry['account'] = result.account
        changed += 1

    if changed:
        logger.info('解析成功后自动补齐资产账户 %s 笔（bill=%s, owner=%s）', changed, bill_type, owner_id)
    return changed
