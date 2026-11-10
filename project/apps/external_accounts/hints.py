import re
from dataclasses import dataclass
from typing import Optional

from project.apps.external_accounts.models import CARD_KINDS, InstrumentKind, PlatformSlot
from project.apps.external_accounts.services import match_institution, normalize_text

TAIL_PATTERN = re.compile(r'\((\d{4})\)')

BILL_INSTITUTION = {
    'alipay': 'ALIPAY',
    'wechat': 'WECHAT',
    'CMB_Credit': 'CMB',
    'BOC_Debit': 'BOC',
    'ICBC_Debit': 'ICBC',
    'CCB_Debit': 'CCB',
}

BILL_CARD_KIND = {
    'CMB_Credit': InstrumentKind.CREDIT_CARD,
    'BOC_Debit': InstrumentKind.DEBIT_CARD,
    'ICBC_Debit': InstrumentKind.DEBIT_CARD,
    'CCB_Debit': InstrumentKind.DEBIT_CARD,
}

# 顺序敏感：余额宝要先于余额，零钱通要先于零钱
SLOT_RULES = (
    ('余额宝', PlatformSlot.ALIPAY_FUND),
    ('花呗', PlatformSlot.ALIPAY_HUABEI),
    ('借呗', PlatformSlot.ALIPAY_JIEBEI),
    ('备用金', PlatformSlot.ALIPAY_BEIYONGJIN),
    ('零钱通', PlatformSlot.WECHAT_FUND),
    ('零钱', PlatformSlot.WECHAT_PAY),
    ('余额', PlatformSlot.ALIPAY_BALANCE),
)

SLOT_KIND = {
    PlatformSlot.ALIPAY_BALANCE: InstrumentKind.WALLET,
    PlatformSlot.ALIPAY_FUND: InstrumentKind.FUND,
    PlatformSlot.ALIPAY_HUABEI: InstrumentKind.CREDIT_LINE,
    PlatformSlot.ALIPAY_JIEBEI: InstrumentKind.LOAN,
    PlatformSlot.ALIPAY_BEIYONGJIN: InstrumentKind.LOAN,
    PlatformSlot.WECHAT_PAY: InstrumentKind.WALLET,
    PlatformSlot.WECHAT_FUND: InstrumentKind.FUND,
}

SLOT_PRODUCT = {
    PlatformSlot.ALIPAY_BALANCE: 'balance',
    PlatformSlot.ALIPAY_FUND: 'fund',
    PlatformSlot.ALIPAY_HUABEI: 'huabei',
    PlatformSlot.ALIPAY_JIEBEI: 'jiebei',
    PlatformSlot.ALIPAY_BEIYONGJIN: 'beiyongjin',
    PlatformSlot.WECHAT_PAY: 'wallet',
    PlatformSlot.WECHAT_FUND: 'fund',
}


def slot_product(slot: str) -> str:
    return SLOT_PRODUCT.get(slot, '')


@dataclass(frozen=True)
class ExternalAccountHint:
    """行级结构化线索：从账单文本推出的外部账户身份要素。

    只描述"这笔账单指向哪个外部账户"，不做账户绑定。
    """

    institution_code: str = ''
    kind: str = ''
    tail: str = ''
    slot: str = ''
    product: str = ''
    raw: str = ''

    @property
    def is_empty(self) -> bool:
        return not any((self.institution_code, self.kind, self.tail, self.slot))

    def as_label(self) -> str:
        parts = [self.institution_code, self.kind, self.tail or '-', self.slot or '-']
        return '/'.join(parts)


def extract_tail(text: str) -> str:
    match = TAIL_PATTERN.search(normalize_text(text))
    return match.group(1) if match else ''


def infer_slot(text: str, institution_code: str = '') -> str:
    if institution_code not in ('ALIPAY', 'WECHAT'):
        return ''
    normalized = normalize_text(text)
    for keyword, slot in SLOT_RULES:
        if keyword in normalized:
            return slot
    return ''


def infer_card_kind_from_text(text: str) -> str:
    normalized = normalize_text(text)
    if '信用卡' in normalized:
        return InstrumentKind.CREDIT_CARD
    if '储蓄卡' in normalized or '借记卡' in normalized:
        return InstrumentKind.DEBIT_CARD
    return ''


def infer_kind(text: str, bill: str = '', slot: str = '') -> str:
    if slot:
        return SLOT_KIND.get(slot, '')
    card_kind = infer_card_kind_from_text(text)
    if card_kind:
        return card_kind
    return BILL_CARD_KIND.get(bill, '')


def infer_external_account_hint(
    text: str,
    bill: str = '',
    default_institution: Optional[str] = None,
) -> ExternalAccountHint:
    """从账单文本与来源推断外部账户线索。

    institution 优先按机构别名识别；文本明确是银行卡但机构识别不到时，
    保留空机构而不是退回账单来源，避免把未知银行卡误判为平台账户。
    """
    normalized = normalize_text(text)
    card_kind = infer_card_kind_from_text(normalized)
    institution = match_institution(normalized)

    if institution is not None:
        institution_code = institution.code
    elif card_kind:
        institution_code = ''
    else:
        institution_code = default_institution or BILL_INSTITUTION.get(bill, '')

    slot = infer_slot(normalized, institution_code)
    kind = infer_kind(normalized, bill, slot)
    # 尾号只在「卡类型已知」或「机构是银行」时保留：平台槽位反而更精确
    is_bank = institution is not None and not institution.is_platform
    tail = extract_tail(normalized)
    if slot or not tail or not (card_kind or kind in CARD_KINDS or is_bank):
        tail = ''

    return ExternalAccountHint(
        institution_code=institution_code,
        kind=kind,
        tail=tail,
        slot=slot,
        raw=normalized,
    )


CARD_PATH_KINDS = (
    ('Assets:Savings:Bank', InstrumentKind.DEBIT_CARD),
    ('Liabilities:CreditCard:Bank', InstrumentKind.CREDIT_CARD),
)

WEB_PATH_HINTS = {
    'Assets:Savings:Web:AliPay': ('ALIPAY', InstrumentKind.WALLET, PlatformSlot.ALIPAY_BALANCE, '支付宝余额'),
    'Assets:Savings:Web:AliFund': ('ALIPAY', InstrumentKind.FUND, PlatformSlot.ALIPAY_FUND, '支付宝余额宝'),
    'Assets:Savings:Web:WechatPay': ('WECHAT', InstrumentKind.WALLET, PlatformSlot.WECHAT_PAY, '微信零钱'),
    'Assets:Savings:Web:WechatFund': ('WECHAT', InstrumentKind.FUND, PlatformSlot.WECHAT_FUND, '微信零钱通'),
    'Liabilities:CreditCard:Web:AliPay': ('ALIPAY', InstrumentKind.CREDIT_LINE, PlatformSlot.ALIPAY_HUABEI, '支付宝花呗'),
    'Liabilities:CreditCard:Web:AliPay:Huabei': ('ALIPAY', InstrumentKind.CREDIT_LINE, PlatformSlot.ALIPAY_HUABEI, '支付宝花呗'),
    'Liabilities:CreditCard:Web:AliPay:Jiebei': ('ALIPAY', InstrumentKind.LOAN, PlatformSlot.ALIPAY_JIEBEI, '支付宝借呗'),
    'Liabilities:CreditCard:Web:AliPay:Beiyongjin': ('ALIPAY', InstrumentKind.LOAN, PlatformSlot.ALIPAY_BEIYONGJIN, '支付宝备用金'),
}

CARD_PATH_PATTERN = re.compile(r':(?P<code>[A-Z]+):C(?P<tail>\d{4})$')


def infer_hint_from_account_path(path: str) -> ExternalAccountHint:
    """从账本账户路径推断外部身份，供模板与存量数据回填使用。"""
    if not path:
        return ExternalAccountHint()

    web = WEB_PATH_HINTS.get(path)
    if web:
        institution_code, kind, slot, display_name = web
        return ExternalAccountHint(
            institution_code=institution_code,
            kind=kind,
            slot=slot,
            product=slot_product(slot),
            raw=display_name,
        )

    for prefix, kind in CARD_PATH_KINDS:
        if not path.startswith(prefix):
            continue
        match = CARD_PATH_PATTERN.search(path)
        if match:
            return ExternalAccountHint(
                institution_code=match.group('code'),
                kind=kind,
                tail=match.group('tail'),
            )
        return ExternalAccountHint()

    return ExternalAccountHint()
