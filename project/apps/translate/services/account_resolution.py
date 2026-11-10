from typing import Optional

from project.apps.external_accounts.hints import infer_external_account_hint, normalize_text
from project.apps.external_accounts.models import (
    CARD_KINDS,
    ExternalAccountAlias,
    ExternalAccountBinding,
)
from project.apps.external_accounts.resolver import (
    Resolution,
    ResolutionStatus,
    resolve_alias,
    resolve_card,
    resolve_slot,
)


def _unmatched(reason: str) -> Resolution:
    return Resolution(ResolutionStatus.UNMATCHED, reason=reason)


def _account_exists(owner_id, path: str) -> bool:
    if not owner_id:
        return True
    from project.apps.account.models import Account

    return Account.objects.filter(owner_id=owner_id, account=path, enable=True).exists()


def resolve_template_slot(slot: str, owner_id=None) -> Optional[str]:
    """模板模式（匿名或尚未生成外部账户）下按槽位取账户路径。"""
    from project.apps.maps.models import TemplateItem

    item = (
        TemplateItem.objects.filter(
            template__type='assets',
            template__is_official=True,
            slot=slot,
        )
        .exclude(account__isnull=True)
        .exclude(account='')
        .order_by('id')
        .first()
    )
    if item is None or not _account_exists(owner_id, item.account):
        return None
    return item.account


def resolve_template_card(institution_code: str, kind: str, tail: str, owner_id=None) -> Optional[str]:
    """模板模式下按机构、卡类型与尾号取账户路径。

    ``kind`` 为空表示账单未写明卡类型，此时仅按尾号匹配，且要求唯一。
    """
    from project.apps.maps.models import TemplateItem

    if not kind and not tail:
        return None

    items = (
        TemplateItem.objects.filter(
            template__type='assets',
            template__is_official=True,
            institution_code=institution_code,
        )
        .exclude(account__isnull=True)
        .exclude(account='')
    )
    if kind:
        items = items.filter(external_kind=kind)
    if tail:
        items = items.filter(tail=tail)
    accounts = list(items.values_list('account', flat=True).distinct())
    if len(accounts) != 1:
        return None
    return accounts[0] if _account_exists(owner_id, accounts[0]) else None


def resolve_template_key(text: str, owner_id=None) -> Optional[str]:
    from project.apps.maps.models import TemplateItem

    item = (
        TemplateItem.objects.filter(
            template__type='assets',
            template__is_official=True,
            key=text,
        )
        .exclude(account__isnull=True)
        .exclude(account='')
        .order_by('id')
        .first()
    )
    if item is None or not _account_exists(owner_id, item.account):
        return None
    return item.account


def resolve_alias_by_containment(owner_id, text: str) -> Resolution:
    """机构未知时，用别名做包含匹配。

    账单只给到银行名（如提现的「宁波银行储蓄卡」），别名往往带尾号。
    只有唯一命中才返回，多命中视为歧义，不猜。
    """
    normalized = normalize_text(text)
    if len(normalized) < 2:
        return _unmatched('text_too_short')

    matched = []
    for alias in ExternalAccountAlias.objects.select_related('external_account').filter(
        owner_id=owner_id
    ):
        alias_text = normalize_text(alias.text)
        if not alias_text:
            continue
        if alias_text in normalized or normalized in alias_text:
            if not alias.external_account.enable:
                continue
            matched.append(alias.external_account)

    unique = {external.pk: external for external in matched}
    if not unique:
        return _unmatched('alias_not_found')
    if len(unique) > 1:
        return Resolution(
            ResolutionStatus.AMBIGUOUS,
            candidates=tuple(unique.values()),
            reason='multiple_candidates',
        )

    external = next(iter(unique.values()))
    binding = (
        ExternalAccountBinding.objects.select_related('account')
        .filter(external_account=external)
        .first()
    )
    if binding is None:
        return _unmatched('account_not_bound')
    if not binding.account.enable:
        return Resolution(
            ResolutionStatus.DISABLED,
            account=binding.account.account,
            external_account=external,
            reason='account_disabled',
        )
    return Resolution(
        ResolutionStatus.MATCHED,
        account=binding.account.account,
        external_account=external,
    )


def resolve_account_from_text(owner_id, text: str, bill: str = '') -> Resolution:
    """把账单文本解析成账本账户路径。

    顺序：外部账户（槽位 / 卡 / 别名）→ 官方模板（槽位 / 卡 / key）→ 未命中。
    """
    hint = infer_external_account_hint(text or '', bill=bill)

    if hint.slot:
        result = resolve_slot(owner_id, hint.slot)
        if result.ok:
            return result
        template_account = resolve_template_slot(hint.slot, owner_id)
        if template_account:
            return Resolution(ResolutionStatus.MATCHED, account=template_account, reason='template')

    if hint.institution_code and (hint.kind in CARD_KINDS or hint.tail):
        result = resolve_card(owner_id, hint.institution_code, hint.kind, hint.tail)
        if result.ok:
            return result
        template_account = resolve_template_card(
            hint.institution_code, hint.kind, hint.tail, owner_id
        )
        if template_account:
            return Resolution(ResolutionStatus.MATCHED, account=template_account, reason='template')

    if hint.raw:
        result = resolve_alias(owner_id, hint.raw)
        if result.ok:
            return result
        template_account = resolve_template_key(hint.raw, owner_id)
        if template_account:
            return Resolution(ResolutionStatus.MATCHED, account=template_account, reason='template')
        if not hint.institution_code:
            return resolve_alias_by_containment(owner_id, hint.raw)

    return _unmatched('no_external_account')


def resolve_payment_account(
    owner_id,
    text: str,
    bill: str = '',
    fallback: str = '',
    mappings=None,
) -> str:
    """付款方式文本 → 账本账户路径。

    结构化身份优先；解析不到时回退到用户自建的资产映射 key（``mappings`` 为已加载的
    ``Assets`` 列表），最后返回 ``fallback``。
    """
    text = (text or '').strip()
    if not text:
        return fallback

    result = resolve_account_from_text(owner_id, text, bill=bill)
    if result.ok:
        return result.account

    for mapping in mappings or ():
        if mapping.key == text and mapping.assets:
            return mapping.assets.account
    return fallback
