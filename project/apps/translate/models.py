# project/apps/translate/models.py
from django.core.validators import RegexValidator
from django.db import models
from django.contrib.auth.models import User
from project.apps.file_manager.models import File


class FormatConfig(models.Model):
    flag = models.CharField(max_length=1, default='*')
    show_note = models.BooleanField(default=True)
    show_tag = models.BooleanField(default=True)
    show_time = models.BooleanField(default=True)
    show_uuid = models.BooleanField(default=True)
    show_status = models.BooleanField(default=True)
    show_discount = models.BooleanField(default=True)
    income_template = models.CharField(max_length=50,default='Income:Transfer:Organizational',null=True, blank=True)
    commission_template = models.CharField(max_length=50,default='Expenses:Finance:Commission', null=True, blank=True)
    reconciliation_fallback_account = models.CharField(
        max_length=128,
        default='Equity:Adjustments',
        null=True,
        blank=True,
        help_text="无法匹配资产账户时的默认兜底账户，也用于非首次对账差额分配"
    )
    owner = models.ForeignKey(User, related_name='format', on_delete=models.CASCADE)
    currency = models.CharField(
        max_length=24,
        default='CNY',
        validators=[
            RegexValidator(
                regex=r'^[A-Z][A-Z0-9\'._-]{0,22}([A-Z0-9])?$',
                message='货币必须以大写字母开头，以大写字母/数字结尾，并且只能包含 [A-Z0-9\'._-]'
            )
        ]
    )
    ai_model = models.CharField(max_length=16, default='BERT', null=False, help_text="AI模型")
    deepseek_apikey = models.CharField(max_length=128, null=True, blank=True, help_text="DeepSeek API密钥")
    assistant_base_url = models.CharField(
        max_length=256, null=True, blank=True, help_text="账本助手 API 接口地址"
    )
    assistant_api_key = models.CharField(
        max_length=256, null=True, blank=True, help_text="账本助手 API 密钥"
    )
    assistant_model = models.CharField(
        max_length=64, null=True, blank=True, help_text="账本助手模型名称"
    )
    parsing_mode_preference = models.CharField(
        max_length=16,
        default='review',
        choices=[
            ('review', '审核模式'),
            ('direct_write', '直接写入模式')
        ],
        help_text="解析模式偏好：审核模式需要用户审核后再写入，直接写入模式解析后立即写入"
    )

    class Meta:
        verbose_name = "格式化输出"
        verbose_name_plural = verbose_name
        # 确保每个用户只有一个配置
        constraints = [
            models.UniqueConstraint(
                fields=['owner'],
                name='unique_user_config'
            )
        ]

    def clean(self):
        """自动转换格式：确保首字母大写，其他字母转为大写，符号和数字不变"""
        if self.currency:
            # 首字母大写
            first_char = self.currency[0].upper() if self.currency else ''
            remaining = ''.join([c.upper() if c.isalpha() else c for c in self.currency[1:]])
            self.currency = first_char + remaining
        super().clean()

    @classmethod
    def get_user_config(cls, user):
        """获取或创建用户配置（带默认值），未登录时使用用户1的配置"""
        # 处理未登录用户
        if user is None or not user.is_authenticated:
            # 获取默认用户（需确保用户ID=1存在）
            from django.contrib.auth import get_user_model
            User = get_user_model()

            try:
                user = User.objects.get(id=1)
            except User.DoesNotExist:
                raise ValueError("默认用户（ID=1）不存在，请先创建该用户")

        # 获取或创建配置
        return cls.objects.get_or_create(
            owner=user,
            defaults={
                'flag': '*',
                'show_note': True,
                'show_tag': True,
                'show_time': True,
                'show_uuid': True,
                'show_status': True,
                'show_discount': True,
                'income_template': 'Income:Transfer:Organizational',
                'commission_template': 'Expenses:Finance:Commission',
                'reconciliation_fallback_account': 'Equity:Adjustments',
                'currency': 'CNY',
                'ai_model': 'BERT',
                'parsing_mode_preference': 'review'
            }
        )[0]  # 始终返回配置实例


class ParseFile(models.Model):
    file = models.OneToOneField(
        File,
        on_delete=models.CASCADE,
        primary_key=True,
    )
    status = models.CharField(
        max_length=20, default='unprocessed', choices=[
            ('unprocessed', '未解析'),
            ('pending', '待解析'),
            ('processing', '解析中'),
            ('pending_review', '待审核'),
            ('parsed', '已解析'),
            ('failed', '解析失败'),
            ('cancelled', '取消解析')
        ]
    )
    error_message = models.TextField(null=True, blank=True)
