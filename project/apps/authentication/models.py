import hashlib
import hmac
import random
import logging
import secrets
from django.db import models
from django.contrib.auth.models import User
from django.core.cache import cache
from django.conf import settings
from django.utils import timezone
from phonenumber_field.modelfields import PhoneNumberField
from project.models import BaseModel

logger = logging.getLogger(__name__)


class UserProfile(BaseModel):
    """用户扩展信息模型"""
    user = models.OneToOneField(
        User, 
        on_delete=models.CASCADE, 
        related_name='profile',
        verbose_name='用户'
    )
    phone_number = PhoneNumberField(
        unique=True, 
        null=True, 
        blank=True,
        verbose_name='手机号',
        help_text='E164格式的手机号，如+8613800138000'
    )
    phone_verified = models.BooleanField(
        default=False,
        verbose_name='手机号已验证',
        help_text='手机号是否通过验证码验证'
    )
    # 2FA相关字段
    totp_enabled = models.BooleanField(
        default=False,
        verbose_name='启用TOTP双因素认证',
        help_text='是否启用基于时间的一次性密码（TOTP）双因素认证'
    )
    sms_2fa_enabled = models.BooleanField(
        default=False,
        verbose_name='启用SMS双因素认证',
        help_text='是否启用基于短信的双因素认证'
    )
    totp_device_id = models.IntegerField(
        null=True,
        blank=True,
        verbose_name='TOTP设备ID',
        help_text='关联的TOTP设备ID（django-otp）'
    )

    class Meta:
        db_table = 'user_profile'
        verbose_name = '用户扩展信息'
        verbose_name_plural = verbose_name

    def __str__(self):
        return f"{self.user.username} - {self.phone_number or '未绑定手机号'}"

    def has_2fa_enabled(self):
        """检查是否启用了任何2FA方式"""
        return self.totp_enabled

    def is_phone_verified(self):
        """检查手机号是否已验证"""
        return bool(self.phone_number and self.phone_verified)

    @staticmethod
    def generate_sms_code():
        """生成6位数字验证码"""
        return ''.join([str(random.randint(0, 9)) for _ in range(6)])

    def get_sms_cache_key(self, phone_number=None):
        """获取短信验证码的缓存键"""
        phone = phone_number or self.phone_number
        if phone:
            return f"sms:code:{phone}"
        return None

    def get_sms_resend_cache_key(self, phone_number=None):
        """获取短信重发限制的缓存键"""
        phone = phone_number or self.phone_number
        if phone:
            return f"sms:resend:{phone}"
        return None

    def can_send_sms(self, phone_number=None):
        """检查是否可以发送短信（防止频繁发送）"""
        cache_key = self.get_sms_resend_cache_key(phone_number)
        if not cache_key:
            return False
        return not cache.get(cache_key)

    def store_sms_code(self, code, phone_number=None):
        """存储短信验证码到缓存"""
        cache_key = self.get_sms_cache_key(phone_number)
        if not cache_key:
            return False

        # 存储验证码，设置过期时间
        expire_seconds = getattr(settings, 'SMS_CODE_EXPIRE_SECONDS', 300)
        cache.set(cache_key, code, expire_seconds)

        # 设置重发限制
        resend_interval = getattr(settings, 'SMS_CODE_RESEND_INTERVAL', 60)
        resend_cache_key = self.get_sms_resend_cache_key(phone_number)
        cache.set(resend_cache_key, '1', resend_interval)

        logger.info(f"验证码已存储: {cache_key}, 过期时间: {expire_seconds}秒")
        return True

    def verify_sms_code(self, code, phone_number=None):
        """验证短信验证码"""
        cache_key = self.get_sms_cache_key(phone_number)
        if not cache_key:
            return False

        stored_code = cache.get(cache_key)
        if not stored_code:
            logger.warning(f"验证码不存在或已过期: {cache_key}")
            return False

        if str(stored_code) == str(code):
            # 验证成功，删除缓存的验证码
            cache.delete(cache_key)
            logger.info(f"验证码验证成功: {cache_key}")
            return True

        logger.warning(f"验证码不匹配: {cache_key}")
        return False

    def send_sms_code(self, phone_number=None):
        """发送短信验证码"""
        from project.apps.authentication.sms import AliyunSMSService

        phone = phone_number or self.phone_number
        if not phone:
            raise ValueError("手机号不能为空")

        # 检查是否可以发送
        if not self.can_send_sms(phone):
            resend_interval = getattr(settings, 'SMS_CODE_RESEND_INTERVAL', 60)
            raise ValueError(f"发送过于频繁，请{resend_interval}秒后再试")

        # 生成验证码
        code = self.generate_sms_code()

        # 存储验证码
        self.store_sms_code(code, phone)

        # 发送短信
        sms_service = AliyunSMSService()
        result = sms_service.send_code(str(phone), code)

        if not result:
            # 发送失败，清除缓存
            cache_key = self.get_sms_cache_key(phone)
            cache.delete(cache_key)
            raise Exception("短信发送失败")

        return True


class PersonalAccessToken(BaseModel):
    """长期只读访问令牌（供 MCP 客户端等外部程序使用）。

    明文令牌只在创建时返回一次，库内仅保存前缀与 SHA-256 摘要。
    """

    TOKEN_PREFIX = 'bct_'
    PREFIX_LENGTH = 8
    DEFAULT_SCOPES = 'ledger:read'
    LAST_USED_THROTTLE_SECONDS = 60

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='personal_access_tokens',
        verbose_name='用户',
    )
    name = models.CharField(
        max_length=64,
        verbose_name='名称',
        help_text='便于用户识别的用途说明，如 Claude Code',
    )
    prefix = models.CharField(
        max_length=16,
        unique=True,
        verbose_name='令牌前缀',
        help_text='明文前缀，用于按前缀定位令牌记录',
    )
    token_hash = models.CharField(
        max_length=64,
        unique=True,
        verbose_name='令牌摘要',
        help_text='SHA-256 摘要，明文不落库',
    )
    scopes = models.CharField(
        max_length=200,
        default=DEFAULT_SCOPES,
        verbose_name='权限范围',
        help_text='空格分隔的 scope 列表',
    )
    expires_at = models.DateTimeField(null=True, blank=True, verbose_name='过期时间')
    last_used_at = models.DateTimeField(null=True, blank=True, verbose_name='最后使用时间')
    revoked_at = models.DateTimeField(null=True, blank=True, verbose_name='撤销时间')

    class Meta:
        db_table = 'personal_access_token'
        verbose_name = '个人访问令牌'
        verbose_name_plural = verbose_name
        ordering = ['-created']

    def __str__(self):
        return f'{self.user.username} - {self.name} ({self.prefix}…)'

    @staticmethod
    def hash_token(raw_token: str) -> str:
        return hashlib.sha256(raw_token.encode('utf-8')).hexdigest()

    @classmethod
    def issue(cls, user: User, name: str, *, expires_at=None, scopes: str | None = None):
        """创建令牌，返回 (记录, 明文令牌)。明文只在此处返回一次。"""
        prefix = secrets.token_hex(cls.PREFIX_LENGTH // 2)
        raw_token = f'{cls.TOKEN_PREFIX}{prefix}{secrets.token_urlsafe(32)}'
        token = cls.objects.create(
            user=user,
            name=name,
            prefix=prefix,
            token_hash=cls.hash_token(raw_token),
            scopes=scopes or cls.DEFAULT_SCOPES,
            expires_at=expires_at,
        )
        return token, raw_token

    @property
    def scope_list(self) -> list[str]:
        return [scope for scope in self.scopes.split() if scope]

    def is_usable(self) -> bool:
        if self.revoked_at is not None:
            return False
        if self.expires_at is not None and self.expires_at <= timezone.now():
            return False
        return True

    def matches(self, raw_token: str) -> bool:
        return hmac.compare_digest(self.token_hash, self.hash_token(raw_token))

    def touch(self, *, min_interval_seconds: int | None = None) -> None:
        """记录一次实际使用（默认按间隔节流）。

        调用方必须在真正读取了该令牌所授权的用户数据后调用；
        令牌鉴权、绑定、仅纳入请求等动作不应调用。
        """
        interval = self.LAST_USED_THROTTLE_SECONDS if min_interval_seconds is None else min_interval_seconds
        now = timezone.now()
        if self.last_used_at and (now - self.last_used_at).total_seconds() < interval:
            return
        type(self).objects.filter(pk=self.pk).update(last_used_at=now)
        self.last_used_at = now

    @classmethod
    def authenticate(cls, raw_token: str) -> 'PersonalAccessToken | None':
        """校验明文令牌，返回可用令牌记录；失败返回 None。"""
        if not raw_token or not raw_token.startswith(cls.TOKEN_PREFIX):
            return None
        start = len(cls.TOKEN_PREFIX)
        prefix = raw_token[start:start + cls.PREFIX_LENGTH]
        if len(prefix) < cls.PREFIX_LENGTH:
            return None
        token = cls.objects.select_related('user').filter(prefix=prefix).first()
        if token is None or not token.matches(raw_token) or not token.is_usable():
            return None
        if not token.user.is_active:
            return None
        return token

