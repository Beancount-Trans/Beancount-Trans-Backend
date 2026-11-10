from dataclasses import dataclass, field
from enum import StrEnum
from typing import Optional, Sequence

from project.apps.external_accounts.models import (
    ExternalAccount,
    ExternalAccountAlias,
    ExternalAccountBinding,
    Institution,
    InstrumentKind,
)


class ResolutionStatus(StrEnum):
    MATCHED = 'matched'
    UNMATCHED = 'unmatched'
    AMBIGUOUS = 'ambiguous'
    DISABLED = 'disabled'


@dataclass(frozen=True)
class Resolution:
    status: ResolutionStatus
    account: Optional[str] = None
    external_account: Optional[ExternalAccount] = None
    candidates: Sequence[ExternalAccount] = field(default_factory=tuple)
    reason: str = ''

    @property
    def ok(self) -> bool:
        return self.status == ResolutionStatus.MATCHED


@dataclass(frozen=True)
class SlotRequirement:
    """要求解析到某个平台固定槽位。"""

    slot: str


@dataclass(frozen=True)
class CardRequirement:
    """要求解析到某机构的某类卡。

    ``tail`` 为空表示账单未提供卡号；``kind`` 为空表示账单未写明卡类型，
    此时必须提供 ``tail``，否则候选过宽。
    """

    institution: str | Institution
    kind: InstrumentKind | str = ''
    tail: str = ''


@dataclass(frozen=True)
class AliasRequirement:
    """要求按账单写法查外部账户。"""

    text: str


def _bound_result(owner_id: int, external_account: ExternalAccount) -> Resolution:
    binding = (
        ExternalAccountBinding.objects.select_related('account')
        .filter(owner_id=owner_id, external_account=external_account)
        .first()
    )
    if binding is None:
        return Resolution(
            ResolutionStatus.UNMATCHED,
            external_account=external_account,
            reason='account_not_bound',
        )
    account = binding.account
    if not account.enable:
        return Resolution(
            ResolutionStatus.DISABLED,
            account=account.account,
            external_account=external_account,
            reason='account_disabled',
        )
    return Resolution(
        ResolutionStatus.MATCHED,
        account=account.account,
        external_account=external_account,
    )


def _institution_of(requirement: CardRequirement) -> Optional[Institution]:
    if isinstance(requirement.institution, Institution):
        return requirement.institution
    return Institution.objects.filter(code=requirement.institution, enable=True).first()


def resolve(owner_id: int, requirement) -> Resolution:
    """把结构化需求解析为账本账户路径。

    不猜：0 个候选返回 unmatched，多个候选返回 ambiguous。
    """
    if isinstance(requirement, SlotRequirement):
        external_account = (
            ExternalAccount.objects.filter(
                owner_id=owner_id,
                slot=requirement.slot,
                enable=True,
            )
            .order_by('id')
            .first()
        )
        if external_account is None:
            return Resolution(ResolutionStatus.UNMATCHED, reason='slot_not_configured')
        return _bound_result(owner_id, external_account)

    if isinstance(requirement, CardRequirement):
        institution = _institution_of(requirement)
        if institution is None:
            return Resolution(ResolutionStatus.UNMATCHED, reason='unknown_institution')
        if not requirement.kind and not requirement.tail:
            return Resolution(ResolutionStatus.UNMATCHED, reason='card_identity_incomplete')

        queryset = ExternalAccount.objects.filter(
            owner_id=owner_id,
            institution=institution,
            enable=True,
        )
        if requirement.kind:
            queryset = queryset.filter(kind=requirement.kind)
        if requirement.tail:
            queryset = queryset.filter(tail=requirement.tail)

        candidates = list(queryset.order_by('id'))
        if not candidates:
            return Resolution(ResolutionStatus.UNMATCHED, reason='external_account_not_found')
        if len(candidates) > 1:
            return Resolution(
                ResolutionStatus.AMBIGUOUS,
                candidates=tuple(candidates),
                reason='multiple_candidates',
            )
        return _bound_result(owner_id, candidates[0])

    if isinstance(requirement, AliasRequirement):
        alias = (
            ExternalAccountAlias.objects.select_related('external_account__institution')
            .filter(owner_id=owner_id, text=requirement.text)
            .first()
        )
        if alias is None:
            return Resolution(ResolutionStatus.UNMATCHED, reason='alias_not_found')
        if not alias.external_account.enable:
            return Resolution(ResolutionStatus.DISABLED, reason='external_account_disabled')
        return _bound_result(owner_id, alias.external_account)

    raise TypeError(f"不支持的解析需求: {type(requirement)!r}")


def resolve_slot(owner_id: int, slot: str) -> Resolution:
    return resolve(owner_id, SlotRequirement(slot=slot))


def resolve_card(
    owner_id: int,
    institution: str | Institution,
    kind: InstrumentKind | str = '',
    tail: str = '',
) -> Resolution:
    return resolve(owner_id, CardRequirement(institution=institution, kind=kind, tail=tail))


def resolve_alias(owner_id: int, text: str) -> Resolution:
    return resolve(owner_id, AliasRequirement(text=text))
