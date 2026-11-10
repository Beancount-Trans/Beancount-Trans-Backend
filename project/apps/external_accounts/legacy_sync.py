from dataclasses import dataclass, field
from typing import List

from project.apps.external_accounts.hints import (
    infer_external_account_hint,
    infer_hint_from_account_path,
    slot_product,
)
from project.apps.external_accounts.models import (
    CARD_KINDS,
    ExternalAccount,
    ExternalAccountAlias,
    ExternalAccountBinding,
    Institution,
    PlatformSlot,
)


@dataclass
class SyncStats:
    created: int = 0
    bound: int = 0
    aliases: int = 0
    skipped: int = 0
    conflicts: List[str] = field(default_factory=list)


def _display_name(slot: str) -> str:
    if not slot:
        return ''
    return PlatformSlot(slot).label


def sync_owner(owner_id: int, assets_model=None) -> SyncStats:
    """把既有资产映射转换为外部账户身份。

    幂等：重复执行不会产生重复外部账户、别名或绑定。
    账户已被其他外部身份占用时不抢占，记入 conflicts。

    assets_model 可传入历史模型，供删除 Assets.full 的数据迁移调用。
    """
    if assets_model is None:
        from project.apps.maps.models import Assets as assets_model

    stats = SyncStats()
    mappings = (
        assets_model.objects.filter(owner_id=owner_id, assets__isnull=False)
        .select_related('assets')
        .order_by('id')
    )

    for mapping in mappings:
        text = getattr(mapping, 'full', '') or mapping.key
        hint = infer_external_account_hint(text, bill='')
        if not hint.institution_code or not hint.kind:
            hint = infer_hint_from_account_path(mapping.assets.account)
        if not hint.institution_code or not hint.kind:
            stats.skipped += 1
            continue

        institution = Institution.objects.filter(code=hint.institution_code).first()
        if institution is None:
            stats.skipped += 1
            continue

        product = slot_product(hint.slot)
        if hint.kind in CARD_KINDS:
            display_name = ''
        else:
            display_name = _display_name(hint.slot) or hint.raw
        external, created = ExternalAccount.objects.get_or_create(
            owner_id=owner_id,
            institution=institution,
            kind=hint.kind,
            tail=hint.tail,
            product=product,
            defaults={'display_name': display_name, 'slot': hint.slot},
        )
        if created:
            stats.created += 1

        for alias_text in {getattr(mapping, 'full', ''), mapping.key}:
            if not alias_text or alias_text == '/':
                continue
            alias, alias_created = ExternalAccountAlias.objects.get_or_create(
                owner_id=owner_id,
                text=alias_text,
                defaults={
                    'external_account': external,
                    'source': ExternalAccountAlias.SOURCE_TEMPLATE,
                },
            )
            if alias_created:
                stats.aliases += 1
            elif alias.external_account_id != external.pk:
                stats.conflicts.append(f"别名「{alias_text}」已指向其他外部账户")

        binding = ExternalAccountBinding.objects.filter(external_account=external).first()
        if binding is not None:
            if binding.account_id != mapping.assets_id:
                stats.conflicts.append(
                    f"外部账户「{external.canonical_name}」已绑定到 {binding.account.account}"
                )
            continue

        # 说明：mapping.assets 在数据迁移里是历史模型实例，不能直接传给当前模型的 FK 查询
        binding = ExternalAccountBinding.objects.filter(account_id=mapping.assets_id).first()
        if binding is not None:
            stats.conflicts.append(
                f"账本账户 {mapping.assets.account} 已被「{binding.external_account.canonical_name}」占用"
            )
            continue

        ExternalAccountBinding.objects.create(
            owner_id=owner_id,
            external_account=external,
            account_id=mapping.assets_id,
        )
        stats.bound += 1

    return stats
