from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models

from project.apps.account.models import Account
from project.models import BaseModel


class InstrumentKind(models.TextChoices):
    """外部账户的资金性质。

    只按资金性质分类，不按具体产品细分；新产品通过 product 字段扩展。
    """

    DEBIT_CARD = 'debit_card', '储蓄卡'
    CREDIT_CARD = 'credit_card', '信用卡'
    WALLET = 'wallet', '钱包'
    FUND = 'fund', '理财'
    CREDIT_LINE = 'credit_line', '信用支付'
    LOAN = 'loan', '借款'


CARD_KINDS = (InstrumentKind.DEBIT_CARD, InstrumentKind.CREDIT_CARD)


class PlatformSlot(models.TextChoices):
    """平台内部固定槽位，由开发者维护。

    解析代码用它定位账单不携带对方信息时的那一腿账户，用户不可编辑。
    """

    ALIPAY_BALANCE = 'alipay_balance', '支付宝余额'
    ALIPAY_FUND = 'alipay_fund', '支付宝余额宝'
    ALIPAY_HUABEI = 'alipay_huabei', '支付宝花呗'
    ALIPAY_JIEBEI = 'alipay_jiebei', '支付宝借呗'
    ALIPAY_BEIYONGJIN = 'alipay_beiyongjin', '支付宝备用金'
    WECHAT_PAY = 'wechat_pay', '微信零钱'
    WECHAT_FUND = 'wechat_fund', '微信零钱通'


class Institution(BaseModel):
    """机构注册表：银行与平台。开发者维护，用户不直接编辑。"""

    code = models.CharField(max_length=32, unique=True, help_text="机构编码，如 CITIC、ALIPAY")
    name = models.CharField(max_length=64, help_text="机构规范全称，如 中信银行")
    is_platform = models.BooleanField(default=False, help_text="是否为支付平台")
    enable = models.BooleanField(default=True, help_text="启用状态")

    class Meta:
        ordering = ['code']
        verbose_name = '机构'
        verbose_name_plural = verbose_name

    def __str__(self):
        return self.name


class InstitutionAlias(BaseModel):
    """机构别名：把账单里的各种写法归一到同一个机构。"""

    institution = models.ForeignKey(
        Institution, related_name='alias_items', on_delete=models.CASCADE, help_text="所属机构"
    )
    text = models.CharField(max_length=64, unique=True, help_text="账单中可能出现的机构写法")

    class Meta:
        ordering = ['text']
        verbose_name = '机构别名'
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"{self.text} → {self.institution.name}"


class ExternalAccount(BaseModel):
    """外部账户身份。

    身份由 (owner, institution, kind, tail, product) 决定，不依赖用户填写的名字。
    """

    owner = models.ForeignKey(
        User, related_name='external_accounts', on_delete=models.CASCADE, db_index=True, help_text="属主"
    )
    institution = models.ForeignKey(
        Institution, related_name='accounts', on_delete=models.PROTECT, help_text="所属机构"
    )
    kind = models.CharField(max_length=16, choices=InstrumentKind.choices, help_text="资金性质")
    tail = models.CharField(max_length=4, blank=True, default='', help_text="卡号后四位，仅银行卡使用")
    product = models.CharField(max_length=32, blank=True, default='', help_text="平台产品标识，如 huabei")
    display_name = models.CharField(max_length=64, blank=True, default='', help_text="展示名，留空时由身份字段生成")
    slot = models.CharField(
        max_length=32,
        choices=PlatformSlot.choices,
        blank=True,
        default='',
        help_text="平台固定槽位，由开发者维护；同一属主下非空值唯一",
    )
    enable = models.BooleanField(default=True, help_text="启用状态")

    class Meta:
        ordering = ['institution__code', 'kind', 'tail', 'product']
        verbose_name = '外部账户'
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(
                fields=['owner', 'institution', 'kind', 'tail', 'product'],
                name='uniq_external_account_identity',
            ),
            models.UniqueConstraint(
                fields=['owner', 'slot'],
                condition=~models.Q(slot=''),
                name='uniq_external_account_slot',
            ),
        ]

    def __str__(self):
        return self.canonical_name

    @property
    def is_card(self) -> bool:
        return self.kind in CARD_KINDS

    @property
    def canonical_name(self) -> str:
        """规范外部名。

        银行卡由机构名 + 卡类型 + 尾号生成，用户无需掌握写法；
        平台账户优先使用显式展示名。
        """
        if self.display_name:
            return self.display_name
        if self.is_card and self.tail:
            return f"{self.institution.name}{self.get_kind_display()}({self.tail})"
        return f"{self.institution.name}{self.get_kind_display()}"

    def clean(self):
        if self.is_card:
            if not self.tail:
                raise ValidationError("银行卡必须提供尾号")
            if not (len(self.tail) == 4 and self.tail.isdigit()):
                raise ValidationError("尾号必须是 4 位数字")
        elif self.tail:
            raise ValidationError("非银行卡不能设置尾号")


class ExternalAccountAlias(BaseModel):
    """账单写法到外部账户的别名。

    同一属主下，一个写法只能指向一个外部账户。
    """

    SOURCE_TEMPLATE = 'template'
    SOURCE_BILL = 'bill'
    SOURCE_USER = 'user'
    SOURCE_CHOICES = [
        (SOURCE_TEMPLATE, '模板'),
        (SOURCE_BILL, '账单学习'),
        (SOURCE_USER, '用户补充'),
    ]

    owner = models.ForeignKey(
        User, related_name='external_account_aliases', on_delete=models.CASCADE, db_index=True, help_text="属主"
    )
    external_account = models.ForeignKey(
        ExternalAccount, related_name='aliases', on_delete=models.CASCADE, help_text="外部账户"
    )
    text = models.CharField(max_length=64, help_text="账单中出现的写法")
    source = models.CharField(max_length=16, choices=SOURCE_CHOICES, default=SOURCE_USER, help_text="来源")

    class Meta:
        ordering = ['text']
        verbose_name = '外部账户别名'
        verbose_name_plural = verbose_name
        constraints = [
            models.UniqueConstraint(fields=['owner', 'text'], name='uniq_external_alias_text_per_owner'),
        ]

    def __str__(self):
        return self.text


class ExternalAccountBinding(BaseModel):
    """外部账户与账本账户的绑定，一对一。"""

    owner = models.ForeignKey(
        User, related_name='external_account_bindings', on_delete=models.CASCADE, db_index=True, help_text="属主"
    )
    external_account = models.OneToOneField(
        ExternalAccount, related_name='binding', on_delete=models.CASCADE, help_text="外部账户"
    )
    account = models.OneToOneField(
        Account, related_name='external_binding', on_delete=models.PROTECT, help_text="账本账户"
    )

    class Meta:
        verbose_name = '外部账户绑定'
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"{self.external_account.canonical_name} → {self.account.account}"
