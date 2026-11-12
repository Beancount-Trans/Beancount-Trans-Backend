import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from rest_framework.test import APIClient
from allauth.socialaccount.models import SocialAccount, SocialApp
from django.contrib.sites.models import Site
from project.apps.authentication.models import UserProfile


@pytest.mark.django_db
class TestAccountBinding:
    """账号绑定测试"""

    def setup_method(self):
        """设置测试环境"""
        self.client = APIClient()
        cache.clear()

        # 创建测试用户
        self.user = User.objects.create_user(
            username='testuser',
            password='TestPass123!',
            email='test@example.com'
        )

    def test_get_bindings(self):
        """测试获取绑定信息"""
        self.client.force_authenticate(user=self.user)

        response = self.client.get('/api/auth/bindings/')

        assert response.status_code == 200
        assert response.data['username'] == 'testuser'
        assert response.data['email'] == 'test@example.com'
        assert response.data['phone_number'] is None
        assert response.data['social_accounts'] == []

    def test_bind_phone(self):
        """测试绑定手机号"""
        self.client.force_authenticate(user=self.user)

        phone_number = '+8613800138006'

        # 存储验证码
        from project.apps.authentication.models import UserProfile
        temp_profile = UserProfile(phone_number=phone_number)
        temp_profile.store_sms_code('123456', phone_number)

        # 绑定手机号
        response = self.client.post('/api/auth/bindings/bind-phone/', {
            'phone_number': phone_number,
            'code': '123456'
        })

        assert response.status_code == 200
        assert '手机号绑定成功' in response.data['message']

        # 验证绑定
        self.user.refresh_from_db()
        assert str(self.user.profile.phone_number) == phone_number
        assert self.user.profile.phone_verified is True

    def test_bind_phone_already_bound(self):
        """测试绑定已被占用的手机号"""
        # 创建另一个用户并绑定手机号
        other_user = User.objects.create_user(username='otheruser', password='Test123!')
        other_user.profile.phone_number = '+8613800138007'
        other_user.profile.save()

        # 当前用户尝试绑定相同手机号
        self.client.force_authenticate(user=self.user)

        from project.apps.authentication.models import UserProfile
        temp_profile = UserProfile(phone_number='+8613800138007')
        temp_profile.store_sms_code('123456', '+8613800138007')

        response = self.client.post('/api/auth/bindings/bind-phone/', {
            'phone_number': '+8613800138007',
            'code': '123456'
        })

        assert response.status_code == 400
        assert '已被其他用户绑定' in str(response.data)

    def test_unbind_phone(self):
        """测试解绑手机号"""
        # 设置用户有密码（至少保留一种登录方式）
        self.user.set_password('TestPass123!')
        self.user.save()

        # 绑定手机号
        self.user.profile.phone_number = '+8613800138008'
        self.user.profile.save()

        self.client.force_authenticate(user=self.user)

        # 解绑手机号
        response = self.client.delete('/api/auth/bindings/unbind-phone/')

        assert response.status_code == 200
        assert '手机号解绑成功' in response.data['message']

        # 验证解绑
        self.user.refresh_from_db()
        assert self.user.profile.phone_number is None

    def test_unbind_last_login_method(self):
        """测试不能解绑最后一种登录方式"""
        # 仅绑定手机号，没有密码和社交账号
        self.user.set_unusable_password()
        self.user.save()
        self.user.profile.phone_number = '+8613800138009'
        self.user.profile.save()

        self.client.force_authenticate(user=self.user)

        # 尝试解绑手机号
        response = self.client.delete('/api/auth/bindings/unbind-phone/')

        assert response.status_code == 400
        assert '至少保留一种登录方式' in response.data['error']

    def test_get_user_profile(self):
        """测试获取用户信息"""
        self.client.force_authenticate(user=self.user)

        response = self.client.get('/api/auth/profile/me/')

        assert response.status_code == 200
        assert response.data['username'] == 'testuser'
        assert response.data['email'] == 'test@example.com'

    def test_update_user_profile(self):
        """测试更新用户信息"""
        self.client.force_authenticate(user=self.user)

        response = self.client.patch('/api/auth/profile/update_me/', {
            'email': 'newemail@example.com'
        })

        assert response.status_code == 200

        # 验证更新
        self.user.refresh_from_db()
        assert self.user.email == 'newemail@example.com'

    def test_unbind_social_success(self):
        """测试解绑社交账号成功"""
        from allauth.socialaccount.models import SocialAccount, SocialApp
        from django.contrib.sites.models import Site

        self.client.force_authenticate(user=self.user)

        # 创建社交账号
        site = Site.objects.get_or_create(domain='example.com', name='example.com')[0]
        app = SocialApp.objects.create(
            provider='github',
            name='GitHub',
            client_id='test_client_id',
            secret='test_secret'
        )
        app.sites.add(site)

        social_account = SocialAccount.objects.create(
            user=self.user,
            provider='github',
            uid='12345',
            extra_data={'login': 'testuser'}
        )

        # 确保用户有其他登录方式
        self.user.set_password('TestPass123!')
        self.user.save()

        response = self.client.delete(f'/api/auth/bindings/unbind-social/github/')

        assert response.status_code == 200
        assert '账号解绑成功' in response.data['message']

        # 验证解绑
        assert not SocialAccount.objects.filter(id=social_account.id).exists()

    def test_unbind_social_not_found(self):
        """测试社交账号不存在"""
        self.client.force_authenticate(user=self.user)

        response = self.client.delete('/api/auth/bindings/unbind-social/github/')

        assert response.status_code == 404
        assert '未找到' in response.data['error']

    def test_unbind_social_last_login_method(self):
        """测试不能解绑最后一种登录方式"""
        from allauth.socialaccount.models import SocialAccount, SocialApp
        from django.contrib.sites.models import Site

        # 用户只有社交账号，没有密码和手机号
        self.user.set_unusable_password()
        self.user.profile.phone_number = None
        self.user.save()

        site = Site.objects.get_or_create(domain='example.com', name='example.com')[0]
        app = SocialApp.objects.create(
            provider='github',
            name='GitHub',
            client_id='test_client_id',
            secret='test_secret'
        )
        app.sites.add(site)

        SocialAccount.objects.create(
            user=self.user,
            provider='github',
            uid='12345',
            extra_data={'login': 'testuser'}
        )

        self.client.force_authenticate(user=self.user)

        response = self.client.delete('/api/auth/bindings/unbind-social/github/')

        assert response.status_code == 400
        assert '至少保留一种登录方式' in response.data['error']

    def test_unbind_social_with_multiple_social_accounts(self):
        """测试多个社交账号时的解绑"""
        from allauth.socialaccount.models import SocialAccount, SocialApp
        from django.contrib.sites.models import Site

        self.client.force_authenticate(user=self.user)

        # 确保用户有密码
        self.user.set_password('TestPass123!')
        self.user.save()

        site = Site.objects.get_or_create(domain='example.com', name='example.com')[0]

        # 创建多个社交账号
        app1 = SocialApp.objects.create(
            provider='github',
            name='GitHub',
            client_id='test_client_id_1',
            secret='test_secret_1'
        )
        app1.sites.add(site)

        app2 = SocialApp.objects.create(
            provider='google',
            name='Google',
            client_id='test_client_id_2',
            secret='test_secret_2'
        )
        app2.sites.add(site)

        github_account = SocialAccount.objects.create(
            user=self.user,
            provider='github',
            uid='12345',
            extra_data={'login': 'testuser'}
        )

        google_account = SocialAccount.objects.create(
            user=self.user,
            provider='google',
            uid='67890',
            extra_data={'email': 'test@example.com'}
        )

        # 解绑其中一个
        response = self.client.delete('/api/auth/bindings/unbind-social/github/')

        assert response.status_code == 200
        assert '账号解绑成功' in response.data['message']

        # 验证只解绑了github
        assert not SocialAccount.objects.filter(id=github_account.id).exists()
        assert SocialAccount.objects.filter(id=google_account.id).exists()

