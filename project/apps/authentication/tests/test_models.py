import pytest
from unittest.mock import patch
from django.contrib.auth.models import User
from django.core.cache import cache
from project.apps.authentication.models import UserProfile


@pytest.mark.django_db
class TestUserProfile:
    """UserProfile 模型方法测试"""

    def setup_method(self):
        """设置测试环境"""
        cache.clear()
        self.user = User.objects.create_user(
            username='testuser',
            password='TestPass123!',
            email='test@example.com'
        )

    def test_is_phone_verified_true(self):
        """测试手机号已验证"""
        self.user.profile.phone_number = '+8613800138100'
        self.user.profile.phone_verified = True
        self.user.profile.save()

        assert self.user.profile.is_phone_verified() is True

    def test_is_phone_verified_false(self):
        """测试手机号未验证"""
        # 没有手机号
        assert self.user.profile.is_phone_verified() is False

        # 有手机号但未验证
        self.user.profile.phone_number = '+8613800138101'
        self.user.profile.phone_verified = False
        self.user.profile.save()

        assert self.user.profile.is_phone_verified() is False

    def test_has_2fa_enabled_true(self):
        """测试已启用2FA"""
        from django_otp.plugins.otp_totp.models import TOTPDevice

        device = TOTPDevice.objects.create(
            user=self.user,
            name='default',
            confirmed=True,
            key='JBSWY3DPEHPK3PXP'
        )
        self.user.profile.totp_enabled = True
        self.user.profile.totp_device_id = device.id
        self.user.profile.save()

        assert self.user.profile.has_2fa_enabled() is True

    def test_has_2fa_enabled_false(self):
        """测试未启用2FA"""
        assert self.user.profile.has_2fa_enabled() is False

    def test_generate_sms_code(self):
        """测试生成短信验证码（6位数字）"""
        code = UserProfile.generate_sms_code()

        assert len(code) == 6
        assert code.isdigit()

    def test_get_sms_cache_key(self):
        """测试获取短信验证码缓存键"""
        phone_number = '+8613800138102'
        self.user.profile.phone_number = phone_number
        self.user.profile.save()

        key = self.user.profile.get_sms_cache_key()
        assert key == f'sms:code:{phone_number}'

        # 使用参数
        key2 = self.user.profile.get_sms_cache_key('+8613800138103')
        assert key2 == 'sms:code:+8613800138103'

    def test_can_send_sms_allowed(self):
        """测试可以发送短信"""
        phone_number = '+8613800138104'
        self.user.profile.phone_number = phone_number
        self.user.profile.save()

        # 没有重发限制
        assert self.user.profile.can_send_sms(phone_number) is True

    def test_can_send_sms_rate_limited(self):
        """测试发送频率限制"""
        phone_number = '+8613800138105'
        self.user.profile.phone_number = phone_number
        self.user.profile.save()

        # 设置重发限制
        resend_key = self.user.profile.get_sms_resend_cache_key(phone_number)
        cache.set(resend_key, '1', 60)

        assert self.user.profile.can_send_sms(phone_number) is False

    def test_store_sms_code(self):
        """测试存储短信验证码"""
        phone_number = '+8613800138106'
        code = '123456'

        result = self.user.profile.store_sms_code(code, phone_number)

        assert result is True
        # 验证验证码已存储
        cache_key = self.user.profile.get_sms_cache_key(phone_number)
        stored_code = cache.get(cache_key)
        assert stored_code == code

    def test_verify_sms_code_success(self):
        """测试验证短信验证码成功"""
        phone_number = '+8613800138107'
        code = '123456'

        self.user.profile.store_sms_code(code, phone_number)

        result = self.user.profile.verify_sms_code(code, phone_number)

        assert result is True
        # 验证验证码已被删除（使用后销毁）
        cache_key = self.user.profile.get_sms_cache_key(phone_number)
        assert cache.get(cache_key) is None

    def test_verify_sms_code_wrong(self):
        """测试验证码错误"""
        phone_number = '+8613800138108'
        code = '123456'

        self.user.profile.store_sms_code(code, phone_number)

        result = self.user.profile.verify_sms_code('654321', phone_number)

        assert result is False

    def test_verify_sms_code_expired(self):
        """测试验证码过期"""
        phone_number = '+8613800138109'
        code = '123456'

        # 存储验证码然后删除（模拟过期）
        self.user.profile.store_sms_code(code, phone_number)
        cache_key = self.user.profile.get_sms_cache_key(phone_number)
        cache.delete(cache_key)

        result = self.user.profile.verify_sms_code(code, phone_number)

        assert result is False

    def test_send_sms_code_success(self):
        """测试发送短信验证码成功"""
        from unittest.mock import patch, MagicMock

        phone_number = '+8613800138110'

        # Mock AliyunSMSService类，让它的实例返回成功的send_code
        # 注意：send_sms_code方法内部导入的是sms模块，所以需要mock sms模块中的类
        with patch('project.apps.authentication.sms.AliyunSMSService') as mock_sms_class:
            mock_service = MagicMock()
            mock_service.send_code.return_value = True
            mock_sms_class.return_value = mock_service

            result = self.user.profile.send_sms_code(phone_number)

            assert result is True
            mock_service.send_code.assert_called_once()

    def test_send_sms_code_rate_limited(self):
        """测试发送短信频率限制"""
        phone_number = '+8613800138111'

        # 设置重发限制
        resend_key = self.user.profile.get_sms_resend_cache_key(phone_number)
        cache.set(resend_key, '1', 60)

        with pytest.raises(ValueError) as exc_info:
            self.user.profile.send_sms_code(phone_number)

        assert '发送过于频繁' in str(exc_info.value)

