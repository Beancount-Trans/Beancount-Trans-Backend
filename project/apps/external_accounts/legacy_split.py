from dataclasses import dataclass, field
from typing import List

from project.apps.external_accounts.hints import slot_product
from project.apps.external_accounts.models import (
    ExternalAccount,
    ExternalAccountAlias,
    ExternalAccountBinding,
    Institution,
    InstrumentKind,
    PlatformSlot,
)

LEGACY_ALIPAY_ACCOUNT = 'Liabilities:CreditCard:Web:AliPay'

PLATFORM_SPLITS = {
    '花呗': (
        PlatformSlot.ALIPAY_HUABEI,
        InstrumentKind.CREDIT_LINE,
        'Liabilities:CreditCard:Web:AliPay:Huabei',
        '支付宝花呗',
    ),
    '借呗': (
        PlatformSlot.ALIPAY_JIEBEI,
        InstrumentKind.LOAN,
        'Liabilities:CreditCard:Web:AliPay:Jiebei',
        '支付宝借呗',
    ),
    '备用金': (
        PlatformSlot.ALIPAY_BEIYONGJIN,
        InstrumentKind.LOAN,
        'Liabilities:CreditCard:Web:AliPay:Beiyongjin',
        '支付宝备用金',
    ),
}

CITIC_OLD_TAIL = '7929'
CITIC_NEW_TAIL = '6428'
CITIC_ACCOUNT = 'Liabilities:CreditCard:Bank:CITIC:C6428'

# 数据迁移用历史模型建账户，不会触发 Account.save 的账本 open；
# 部署后需用 sync_external_accounts --open-accounts 为这些账户补写 open。
SPLIT_ACCOUNT_PATHS = tuple(item[2] for item in PLATFORM_SPLITS.values()) + (CITIC_ACCOUNT,)


@dataclass
class SplitStats:
    created_accounts: int = 0
    repointed_mappings: int = 0
    bound: int = 0
    conflicts: List[str] = field(default_factory=list)


def _ensure_account(Account, owner_id, path, parent=None):
    account = Account.objects.filter(owner_id=owner_id, account=path).first()
    if account is not None:
        return account, False
    account = Account.objects.create(
        owner_id=owner_id,
        account=path,
        parent=parent,
        enable=True,
    )
    return account, True


def _ensure_external(owner_id, institution, kind, tail, slot, display_name):
    product = slot_product(slot) if slot else ''
    return ExternalAccount.objects.get_or_create(
        owner_id=owner_id,
        institution=institution,
        kind=kind,
        tail=tail,
        product=product,
        defaults={'display_name': display_name, 'slot': slot},
    )


def _ensure_binding(owner_id, external, account, stats: SplitStats):
    account_pk = account.pk
    binding = ExternalAccountBinding.objects.filter(external_account=external).first()
    if binding is not None:
        if binding.account_id != account_pk:
            ExternalAccountBinding.objects.filter(pk=binding.pk).update(account_id=account_pk)
            stats.bound += 1
        return
    if ExternalAccountBinding.objects.filter(account_id=account_pk).exists():
        stats.conflicts.append(f"账本账户 {account.account} 已被其他外部账户占用")
        return
    ExternalAccountBinding.objects.create(
        owner_id=owner_id,
        external_account=external,
        account_id=account_pk,
    )
    stats.bound += 1


def split_legacy_platform_accounts(owner_id: int, assets_model=None, account_model=None) -> SplitStats:
    """把花呗/借呗/备用金从旧聚合账户拆成独立账户，并收敛中信旧卡号。

    幂等：重复执行不会重复创建账户或绑定。
    只迁移指向旧聚合账户的映射，不覆盖用户自建账户。
    """
    if assets_model is None or account_model is None:
        from project.apps.account.models import Account as live_account_model
        from project.apps.maps.models import Assets as live_assets_model

        assets_model = assets_model or live_assets_model
        account_model = account_model or live_account_model

    stats = SplitStats()
    alipay = Institution.objects.filter(code='ALIPAY').first()
    legacy_account = account_model.objects.filter(
        owner_id=owner_id, account=LEGACY_ALIPAY_ACCOUNT
    ).first()

    if alipay is not None:
        for key, (slot, kind, path, display_name) in PLATFORM_SPLITS.items():
            mapping = (
                assets_model.objects.filter(owner_id=owner_id, key=key)
                .select_related('assets')
                .first()
            )
            if mapping is None:
                continue

            target_account, account_created = _ensure_account(
                account_model, owner_id, path, parent=legacy_account
            )
            if account_created:
                stats.created_accounts += 1

            if mapping.assets_id and mapping.assets.account == LEGACY_ALIPAY_ACCOUNT:
                mapping.assets = target_account
                mapping.save(update_fields=['assets', 'modified'])
                stats.repointed_mappings += 1

            external, _ = _ensure_external(
                owner_id, alipay, kind, '', slot, display_name
            )
            ExternalAccountAlias.objects.get_or_create(
                owner_id=owner_id,
                text=key,
                defaults={
                    'external_account': external,
                    'source': ExternalAccountAlias.SOURCE_TEMPLATE,
                },
            )
            _ensure_binding(owner_id, external, target_account, stats)

    citic = Institution.objects.filter(code='CITIC').first()
    if citic is not None:
        target_account, account_created = _ensure_account(
            account_model, owner_id, CITIC_ACCOUNT
        )
        if account_created:
            stats.created_accounts += 1

        old_mapping = (
            assets_model.objects.filter(owner_id=owner_id, key=CITIC_OLD_TAIL)
            .select_related('assets')
            .first()
        )
        if old_mapping is not None and old_mapping.assets_id != target_account.pk:
            old_mapping.assets = target_account
            old_mapping.save(update_fields=['assets', 'modified'])
            stats.repointed_mappings += 1

        external, _ = _ensure_external(
            owner_id,
            citic,
            InstrumentKind.CREDIT_CARD,
            CITIC_NEW_TAIL,
            '',
            '',
        )
        ExternalAccountAlias.objects.get_or_create(
            owner_id=owner_id,
            text=CITIC_OLD_TAIL,
            defaults={
                'external_account': external,
                'source': ExternalAccountAlias.SOURCE_BILL,
            },
        )
        _ensure_binding(owner_id, external, target_account, stats)
        _merge_legacy_card_external(owner_id, citic, external)

    return stats


def _merge_legacy_card_external(owner_id, institution, current):
    """把旧卡号身份并入当前卡号身份。

    换卡前回填可能已按旧尾号建过一个外部账户；它代表同一张卡，
    因此把别名挪到当前身份上，并在旧身份无绑定时删除。
    """
    legacy = ExternalAccount.objects.filter(
        owner_id=owner_id,
        institution=institution,
        kind=InstrumentKind.CREDIT_CARD,
        tail=CITIC_OLD_TAIL,
    ).first()
    if legacy is None or legacy.pk == current.pk:
        return

    ExternalAccountAlias.objects.filter(external_account=legacy).update(
        external_account=current
    )
    if not ExternalAccountBinding.objects.filter(external_account=legacy).exists():
        legacy.delete()
