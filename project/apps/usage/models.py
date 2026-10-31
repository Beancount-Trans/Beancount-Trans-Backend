from django.db import models

from project.models import BaseModel


class Dimension(models.TextChoices):
    """计量维度。

    维度名一经上线即为契约（Cloud 侧按同名取值），不要随意改名。
    """

    PARSE = 'parse.count', '解析次数'
    LLM_TOKENS = 'llm.tokens', 'LLM Token'
    BQL = 'bql.call', 'BQL 调用'
    STORAGE_FILE = 'storage.file', '文件存储'
    STORAGE_REPO = 'storage.repo', 'Git 仓库体积'


class OwnerType(models.TextChoices):
    """计费主体类型。family 后续随家庭账本引入。"""

    USER = 'user', '用户'
    PLATFORM = 'platform', '平台'


class UsageEvent(BaseModel):
    """用量事件：只追加的原始事实，不出现金额、单价、余额。

    非事件型维度（存储体积）走 UsageSnapshot，不要写进本表，
    否则同一天的存储量会被重复累加。
    """

    event_key = models.CharField(
        max_length=64,
        unique=True,
        verbose_name='幂等键',
        help_text='重复写入时据此去重，如 parse:<task_id>',
    )
    owner_type = models.CharField(
        max_length=16,
        choices=OwnerType.choices,
        default=OwnerType.USER,
        verbose_name='计费主体类型',
        help_text='user / platform',
    )
    owner_id = models.BigIntegerField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name='计费主体 ID',
        help_text='平台主体为空',
    )
    anon_key = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        verbose_name='匿名标识',
        help_text='HMAC(IP+UA)，不存原始 IP',
    )
    dimension = models.CharField(
        max_length=32,
        choices=Dimension.choices,
        db_index=True,
        verbose_name='计量维度',
    )
    quantity = models.BigIntegerField(
        verbose_name='数量',
        help_text='原始计量值，不折算货币',
    )
    unit = models.CharField(
        max_length=8,
        verbose_name='单位',
        help_text='count / tokens / B',
    )
    source_type = models.CharField(
        max_length=32,
        verbose_name='来源类型',
        help_text='parse_job / assistant_turn / mcp_request',
    )
    source_id = models.CharField(
        max_length=64,
        blank=True,
        default='',
        verbose_name='来源对象 ID',
    )
    status = models.CharField(
        max_length=8,
        default='ok',
        verbose_name='状态',
        help_text='ok / failed；失败也计数',
    )
    occurred_at = models.DateTimeField(
        db_index=True,
        verbose_name='业务时间',
        help_text='月结按此切分，区别于入库时间 created',
    )
    meta = models.JSONField(
        default=dict,
        blank=True,
        verbose_name='扩展信息',
        help_text='模型名、耗时、条目数、失败原因等',
    )

    class Meta:
        verbose_name = '用量事件'
        verbose_name_plural = verbose_name
        ordering = ['-occurred_at']
        indexes = [
            models.Index(
                fields=['owner_type', 'owner_id', 'occurred_at'],
                name='usage_owner_time_idx',
            ),
            models.Index(
                fields=['dimension', 'occurred_at'],
                name='usage_dim_time_idx',
            ),
        ]

    def __str__(self):
        return f'{self.dimension} {self.quantity}{self.unit} @ {self.occurred_at:%Y-%m-%d %H:%M}'


class UsageSnapshot(BaseModel):
    """用量快照：时点量（存储体积等），按日覆盖写。

    同一 (主体, 维度, 日期) 只保留一条，重复采集合覆盖而非累加。
    """

    owner_type = models.CharField(
        max_length=16,
        choices=OwnerType.choices,
        default=OwnerType.USER,
        verbose_name='计费主体类型',
    )
    owner_id = models.BigIntegerField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name='计费主体 ID',
    )
    dimension = models.CharField(
        max_length=32,
        choices=Dimension.choices,
        db_index=True,
        verbose_name='计量维度',
    )
    amount = models.BigIntegerField(
        verbose_name='数量',
        help_text='当前占用，单位见 unit',
    )
    unit = models.CharField(
        max_length=8,
        verbose_name='单位',
    )
    period = models.DateField(
        db_index=True,
        verbose_name='统计日期',
    )
    measured_at = models.DateTimeField(
        verbose_name='采集时间',
    )

    class Meta:
        verbose_name = '用量快照'
        verbose_name_plural = verbose_name
        ordering = ['-period']
        unique_together = (('owner_type', 'owner_id', 'dimension', 'period'),)

    def __str__(self):
        return f'{self.dimension} {self.amount}{self.unit} @ {self.period}'
