import logging
from rest_framework import serializers
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from phonenumber_field.serializerfields import PhoneNumberField
from project.apps.authentication.models import PersonalAccessToken, UserProfile
from project.apps.authentication.utils import validate_username_format

logger = logging.getLogger(__name__)


class PhoneSendCodeSerializer(serializers.Serializer):
    """发送验证码序列化器"""
    phone_number = PhoneNumberField(
        required=True,
        help_text='手机号，格式如 +8613800138000 或 13800138000'
    )

    def validate_phone_number(self, value):
        """验证手机号格式"""
        if not value:
            raise serializers.ValidationError("手机号不能为空")
        return value


class PhoneLoginByCodeSerializer(serializers.Serializer):
    """验证码登录序列化器"""
    phone_number = PhoneNumberField(
        required=True,
        help_text='手机号'
    )
    code = serializers.CharField(
        required=True,
        min_length=6,
        max_length=6,
        help_text='6位数字验证码'
    )

    def validate_code(self, value):
        """验证验证码格式"""
        if not value.isdigit():
            raise serializers.ValidationError("验证码必须是6位数字")
        return value


class PhoneLoginByPasswordSerializer(serializers.Serializer):
    """密码登录序列化器"""
    phone_number = PhoneNumberField(
        required=True,
        help_text='手机号'
    )
    password = serializers.CharField(
        required=True,
        write_only=True,
        help_text='密码'
    )
    totp_code = serializers.CharField(
        required=False,
        allow_blank=True,
        min_length=6,
        max_length=6,
        help_text='TOTP验证码（可选，如果用户启用了TOTP则必填）'
    )

    def validate_totp_code(self, value):
        """验证TOTP码格式"""
        if value and not value.isdigit():
            raise serializers.ValidationError("TOTP验证码必须是6位数字")
        return value


class PersonalAccessTokenSerializer(serializers.ModelSerializer):
    """个人访问令牌输出序列化器（不含明文令牌）"""
    class Meta:
        model = PersonalAccessToken
        fields = ['id', 'name', 'prefix', 'scopes', 'expires_at', 'last_used_at', 'revoked_at', 'created']
        read_only_fields = fields


class PersonalAccessTokenCreateSerializer(serializers.Serializer):
    """个人访问令牌创建序列化器"""
    name = serializers.CharField(max_length=64, help_text='用途说明，如 Claude Code')
    expires_in_days = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=1,
        max_value=3650,
        help_text='有效天数，留空表示长期有效',
    )

    def validate_name(self, value):
        name = (value or '').strip()
        if not name:
            raise serializers.ValidationError('名称不能为空')
        return name


class PersonalAccessTokenUpdateSerializer(serializers.Serializer):
    """个人访问令牌更新序列化器（目前仅支持修改用途说明）"""
    name = serializers.CharField(max_length=64, help_text='用途说明，如 Claude Code')

    def validate_name(self, value):
        name = (value or '').strip()
        if not name:
            raise serializers.ValidationError('名称不能为空')
        return name


class PhoneRegisterSerializer(serializers.Serializer):
    """手机号注册序列化器（用户名和密码自动生成）"""
    phone_number = PhoneNumberField(
        required=True,
        help_text='手机号'
    )
    code = serializers.CharField(
        required=True,
        min_length=6,
        max_length=6,
        help_text='6位数字验证码'
    )
    email = serializers.EmailField(
        required=False,
        allow_blank=True,
        help_text='邮箱（可选）'
    )

    def validate_phone_number(self, value):
        """验证手机号是否已被注册"""
        if UserProfile.objects.filter(phone_number=value).exists():
            raise serializers.ValidationError("该手机号已被注册")
        return value

    def validate_code(self, value):
        """验证验证码格式"""
        if not value.isdigit():
            raise serializers.ValidationError("验证码必须是6位数字")
        return value


class PhoneBindingSerializer(serializers.Serializer):
    """绑定手机号序列化器"""
    phone_number = PhoneNumberField(
        required=True,
        help_text='手机号'
    )
    code = serializers.CharField(
        required=True,
        min_length=6,
        max_length=6,
        help_text='6位数字验证码'
    )

    def validate_phone_number(self, value):
        """验证手机号是否已被其他用户绑定"""
        # 检查是否已被其他用户绑定
        existing_profile = UserProfile.objects.filter(phone_number=value).first()
        if existing_profile:
            # 获取当前用户
            request = self.context.get('request')
            if request and request.user.is_authenticated:
                if existing_profile.user != request.user:
                    raise serializers.ValidationError("该手机号已被其他用户绑定")
        return value

    def validate_code(self, value):
        """验证验证码格式"""
        if not value.isdigit():
            raise serializers.ValidationError("验证码必须是6位数字")
        return value


class SocialAccountSerializer(serializers.Serializer):
    """社交账号序列化器"""
    provider = serializers.CharField(read_only=True, help_text='提供商')
    uid = serializers.CharField(read_only=True, help_text='第三方用户ID')
    extra_data = serializers.JSONField(read_only=True, help_text='额外数据')
    date_joined = serializers.DateTimeField(read_only=True, help_text='绑定时间')


class UserProfileSerializer(serializers.ModelSerializer):
    """用户完整信息序列化器"""
    username = serializers.CharField(source='user.username', read_only=True)
    email = serializers.EmailField(source='user.email', read_only=True)
    date_joined = serializers.DateTimeField(source='user.date_joined', read_only=True)
    last_login = serializers.DateTimeField(source='user.last_login', read_only=True)

    class Meta:
        model = UserProfile
        fields = [
            'username', 
            'email', 
            'phone_number', 
            'phone_verified',
            'date_joined',
            'last_login',
            'created',
            'modified'
        ]
        read_only_fields = ['phone_verified', 'created', 'modified']


class UserUpdateSerializer(serializers.Serializer):
    """用户信息更新序列化器"""
    username = serializers.CharField(
        required=False,
        min_length=3,
        max_length=150,
        help_text='用户名'
    )
    email = serializers.EmailField(
        required=False,
        help_text='邮箱'
    )

    def validate_username(self, value):
        """验证用户名是否已被其他用户使用，并检查格式"""
        request = self.context.get('request')
        user = request.user if request and request.user.is_authenticated else None
        
        # 检查是否是11位数字（手机号格式）
        is_phone_format = value.isdigit() and len(value) == 11
        
        if is_phone_format:
            # 如果用户名是手机号格式，检查是否是用户自己的手机号
            if user:
                try:
                    profile = user.profile
                    if profile.phone_number:
                        # 提取用户绑定的手机号的本地号码部分
                        from project.apps.authentication.utils import extract_local_phone_number
                        user_phone_local = extract_local_phone_number(profile.phone_number)
                        
                        # 如果用户名与用户绑定的手机号一致，允许使用
                        if value == user_phone_local:
                            # 允许使用自己的手机号作为用户名
                            # 验证唯一性（排除自己）
                            if User.objects.filter(username=value).exclude(id=user.id).exists():
                                raise serializers.ValidationError("该用户名已被其他用户使用")
                            return value
                        else:
                            raise serializers.ValidationError("如果用户名设置为手机号，则只能使用自己绑定的手机号")
                    else:
                        raise serializers.ValidationError("您尚未绑定手机号，无法使用手机号作为用户名")
                except UserProfile.DoesNotExist:
                    raise serializers.ValidationError("您尚未绑定手机号，无法使用手机号作为用户名")
            else:
                raise serializers.ValidationError("请先登录")
        
        # 非手机号格式，使用常规验证
        is_valid, error_message = validate_username_format(value)
        if not is_valid:
            raise serializers.ValidationError(error_message)

        # 验证唯一性
        if user:
            if User.objects.filter(username=value).exclude(id=user.id).exists():
                raise serializers.ValidationError("该用户名已被其他用户使用")
        
        return value

    def validate_email(self, value):
        """验证邮箱是否已被其他用户使用"""
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            if User.objects.filter(email=value).exclude(id=request.user.id).exists():
                raise serializers.ValidationError("该邮箱已被其他用户使用")
        return value


class TOTPEnableSerializer(serializers.Serializer):
    """启用TOTP序列化器"""
    code = serializers.CharField(
        required=True,
        min_length=6,
        max_length=6,
        help_text='6位TOTP验证码'
    )

    def validate_code(self, value):
        """验证验证码格式"""
        if not value.isdigit():
            raise serializers.ValidationError("验证码必须是6位数字")
        return value


class TOTPDisableSerializer(serializers.Serializer):
    """禁用TOTP序列化器"""
    code = serializers.CharField(
        required=True,
        min_length=6,
        max_length=6,
        help_text='6位TOTP验证码（用于确认禁用）'
    )

    def validate_code(self, value):
        """验证验证码格式"""
        if not value.isdigit():
            raise serializers.ValidationError("验证码必须是6位数字")
        return value


class UsernameLoginByPasswordSerializer(serializers.Serializer):
    """用户名/邮箱+密码登录序列化器"""
    username = serializers.CharField(
        required=True,
        help_text='用户名或邮箱'
    )
    password = serializers.CharField(
        required=True,
        write_only=True,
        help_text='密码'
    )
    totp_code = serializers.CharField(
        required=False,
        allow_blank=True,
        min_length=6,
        max_length=6,
        help_text='TOTP验证码（可选，如果用户启用了TOTP则必填）'
    )

    def validate_totp_code(self, value):
        """验证TOTP码格式"""
        if value and not value.isdigit():
            raise serializers.ValidationError("TOTP验证码必须是6位数字")
        return value

