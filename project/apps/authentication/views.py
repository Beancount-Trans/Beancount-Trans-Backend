import logging
from datetime import timedelta
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.tokens import RefreshToken
from django.conf import settings
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import get_random_string
from allauth.socialaccount.models import SocialAccount

from project.apps.authentication.models import PersonalAccessToken, UserProfile
from project.apps.authentication.utils import generate_unique_username, extract_local_phone_number
from project.apps.external_accounts.models import ExternalAccount
from project.apps.fava_instances.tasks import schedule_fava_warmup
from project.apps.authentication.serializers import (
    PhoneSendCodeSerializer,
    PhoneLoginByCodeSerializer,
    PhoneLoginByPasswordSerializer,
    PhoneRegisterSerializer,
    PhoneBindingSerializer,
    UserProfileSerializer,
    UserUpdateSerializer,
    TOTPEnableSerializer,
    TOTPDisableSerializer,
    UsernameLoginByPasswordSerializer,
    PersonalAccessTokenSerializer,
    PersonalAccessTokenCreateSerializer,
    PersonalAccessTokenUpdateSerializer,
)

logger = logging.getLogger(__name__)


def _issue_refresh_token(user):
    """签发 JWT，并在事务提交后异步预热 Fava 容器。"""
    schedule_fava_warmup(user)
    return RefreshToken.for_user(user)


def _sms_disabled_response():
    return Response(
        {'error': '短信验证码功能未启用', 'code': 'SMS_DISABLED'},
        status=status.HTTP_503_SERVICE_UNAVAILABLE,
    )


class PhoneAuthViewSet(viewsets.GenericViewSet):
    """手机号认证视图集"""
    # 只使用JWT认证，不使用Session认证，避免CSRF检查
    authentication_classes = [JWTAuthentication]

    def get_permissions(self):
        """根据不同的 action 设置不同的权限"""
        if self.action in ['send_code', 'login_by_code', 'login_by_password', 'register']:
            return [AllowAny()]
        return [IsAuthenticated()]


    @action(detail=False, methods=['post'], url_path='send-code')
    def send_code(self, request):
        """发送验证码"""
        if not settings.SMS_ENABLED:
            return _sms_disabled_response()
        serializer = PhoneSendCodeSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        phone_number = serializer.validated_data['phone_number']

        try:
            # 创建临时 UserProfile 实例用于发送验证码（不保存到数据库）
            temp_profile = UserProfile(phone_number=phone_number)
            temp_profile.send_sms_code(phone_number)

            logger.info(f"验证码发送成功: {phone_number}")
            return Response({
                'message': '验证码已发送',
                'phone_number': str(phone_number)
            }, status=status.HTTP_200_OK)

        except ValueError as e:
            return Response({
                'error': str(e)
            }, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error(f"发送验证码失败: {str(e)}")
            return Response({
                'error': '发送验证码失败，请稍后重试'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['post'], url_path='login-by-code')
    def login_by_code(self, request):
        """验证码登录（如果用户不存在则自动注册）"""
        if not settings.SMS_ENABLED:
            return _sms_disabled_response()
        serializer = PhoneLoginByCodeSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        phone_number = serializer.validated_data['phone_number']
        code = serializer.validated_data['code']

        # 先验证验证码
        # temp_profile = UserProfile(phone_number=phone_number)
        # if not temp_profile.verify_sms_code(code, phone_number):
        #     return Response({
        #         'error': '验证码错误或已过期'
        #     }, status=status.HTTP_401_UNAUTHORIZED)

        # 使用验证码认证后端查找用户
        user = authenticate(request, phone=str(phone_number), code=code)

        # 记录用户是否是新注册的
        is_new_user = False
        
        # 如果用户不存在但验证码正确，自动注册
        if not user:
            is_new_user = True
            try:
                with transaction.atomic():
                    # 根据手机号生成唯一用户名（只使用本地号码部分，去掉国家代码）
                    local_number = extract_local_phone_number(phone_number)
                    if local_number:
                        base_username = local_number
                    else:
                        base_username = get_random_string(8)

                    username = generate_unique_username(base_username)

                    # 创建用户（不设置密码）
                    user = User.objects.create_user(
                        username=username,
                        password=None,
                        email=''
                    )

                    # 创建 UserProfile
                    profile = user.profile
                    profile.phone_number = phone_number
                    profile.phone_verified = True
                    profile.save()

                    # 应用初始化数据（官方账户模板、标签模板和映射模板）
                    try:
                        from project.apps.account.signals import apply_official_account_templates
                        from project.apps.maps.signals import apply_official_templates
                        from project.apps.tags.signals import apply_official_tag_templates

                        apply_official_account_templates(user)
                        logger.info(f"为用户 {username} 应用官方账户模板成功")

                        apply_official_tag_templates(user)
                        logger.info(f"为用户 {username} 应用官方标签模板成功")

                        apply_official_templates(user)
                        logger.info(f"为用户 {username} 应用官方映射模板成功")
                    except Exception as init_error:
                        logger.warning(f"为用户 {username} 应用初始化数据时出错: {str(init_error)}")

                    logger.info(f"用户 {username} 通过验证码登录自动注册成功")
            except Exception as e:
                logger.error(f"自动注册失败: {str(e)}")
                return Response({
                    'error': '注册失败，请稍后重试'
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        # 记录用户之前的最后登录时间（用于判断是否为新用户）
        previous_last_login = user.last_login
        
        # 更新最后登录时间
        user.last_login = timezone.now()
        user.save(update_fields=['last_login'])

        # 如果之前没有记录 is_new_user，通过检查 previous_last_login 来判断
        if not is_new_user:
            is_new_user = previous_last_login is None

        # 生成 JWT token
        refresh = _issue_refresh_token(user)

        logger.info(f"用户 {user.username} 通过验证码登录成功")

        # 检查是否需要2FA验证
        profile = user.profile
        requires_2fa = profile.has_2fa_enabled()

        response_data = {
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'user': {
                'id': user.id,
                'username': user.username,
                'email': user.email,
                'phone_number': str(phone_number)
            },
            'requires_2fa': requires_2fa,
            'is_new_user': is_new_user
        }

        if requires_2fa:
            # 如果启用了2FA，返回需要验证的提示
            response_data['2fa_methods'] = []
            if profile.totp_enabled:
                response_data['2fa_methods'].append('totp')

        return Response(response_data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='login-by-password')
    def login_by_password(self, request):
        """密码登录（支持TOTP验证）"""
        serializer = PhoneLoginByPasswordSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        phone_number = serializer.validated_data['phone_number']
        password = serializer.validated_data['password']
        totp_code = serializer.validated_data.get('totp_code', '').strip()

        # 使用密码认证后端
        user = authenticate(request, phone=str(phone_number), password=password)

        if user:
            profile = user.profile

            # 检查用户是否启用了TOTP
            if profile.totp_enabled:
                if not totp_code:
                    return Response({
                        'error': '该账户已启用TOTP二次验证，请输入TOTP验证码',
                        'requires_totp': True
                    }, status=status.HTTP_400_BAD_REQUEST)

                # 验证TOTP码
                try:
                    from django_otp.plugins.otp_totp.models import TOTPDevice
                    if profile.totp_device_id:
                        try:
                            device = TOTPDevice.objects.get(id=profile.totp_device_id, user=user)
                            if not device.verify_token(totp_code):
                                return Response({
                                    'error': 'TOTP验证码错误'
                                }, status=status.HTTP_400_BAD_REQUEST)
                        except TOTPDevice.DoesNotExist:
                            return Response({
                                'error': 'TOTP设备不存在'
                            }, status=status.HTTP_400_BAD_REQUEST)
                    else:
                        return Response({
                            'error': 'TOTP设备不存在，请联系管理员'
                        }, status=status.HTTP_400_BAD_REQUEST)
                except Exception as e:
                    logger.error(f"TOTP验证失败: {str(e)}")
                    return Response({
                        'error': 'TOTP验证失败，请稍后重试'
                    }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            # 更新最后登录时间
            user.last_login = timezone.now()
            user.save(update_fields=['last_login'])

            # 生成 JWT token
            refresh = _issue_refresh_token(user)

            logger.info(f"用户 {user.username} 通过手机号密码登录成功")

            return Response({
                'access': str(refresh.access_token),
                'refresh': str(refresh),
                'user': {
                    'id': user.id,
                    'username': user.username,
                    'email': user.email,
                    'phone_number': str(phone_number)
                }
            }, status=status.HTTP_200_OK)
        else:
            return Response({
                'error': '手机号或密码错误'
            }, status=status.HTTP_401_UNAUTHORIZED)

    @action(detail=False, methods=['post'])
    def register(self, request):
        """手机号注册（用户名和密码自动生成）"""
        if not settings.SMS_ENABLED:
            return _sms_disabled_response()
        serializer = PhoneRegisterSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        phone_number = serializer.validated_data['phone_number']
        code = serializer.validated_data['code']
        email = (serializer.validated_data.get('email') or '').strip()

        try:
            with transaction.atomic():
                # 验证验证码
                temp_profile = UserProfile(phone_number=phone_number)
                if not temp_profile.verify_sms_code(code, phone_number):
                    return Response({
                        'error': '验证码错误或已过期'
                    }, status=status.HTTP_400_BAD_REQUEST)

                # 根据手机号生成唯一用户名（只使用本地号码部分，去掉国家代码）
                local_number = extract_local_phone_number(phone_number)
                if local_number:
                    base_username = local_number
                else:
                    base_username = get_random_string(8)

                username = generate_unique_username(base_username)

                # 创建用户（不设置密码）
                user = User.objects.create_user(
                    username=username,
                    password=None,
                    email=email or ''
                )

                # 更新 UserProfile
                profile = user.profile
                profile.phone_number = phone_number
                profile.phone_verified = True
                profile.save()

                # 应用初始化数据（官方账户模板、标签模板和映射模板）
                try:
                    from project.apps.account.signals import apply_official_account_templates
                    from project.apps.maps.signals import apply_official_templates
                    from project.apps.tags.signals import apply_official_tag_templates

                    apply_official_account_templates(user)
                    logger.info(f"为用户 {username} 应用官方账户模板成功")

                    apply_official_tag_templates(user)
                    logger.info(f"为用户 {username} 应用官方标签模板成功")

                    apply_official_templates(user)
                    logger.info(f"为用户 {username} 应用官方映射模板成功")
                except Exception as init_error:
                    logger.warning(f"为用户 {username} 应用初始化数据时出错: {str(init_error)}")
                    # 不阻断注册流程，只记录警告

                # 生成 JWT token
                refresh = _issue_refresh_token(user)

                logger.info(f"用户 {username} 注册成功")
                return Response({
                    'message': '注册成功',
                    'access': str(refresh.access_token),
                    'refresh': str(refresh),
                    'user': {
                        'id': user.id,
                        'username': user.username,
                        'email': user.email,
                        'phone_number': str(phone_number)
                    }
                }, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.error(f"注册失败: {str(e)}")
            return Response({
                'error': f'注册失败: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class UsernameAuthViewSet(viewsets.GenericViewSet):
    """用户名/邮箱+密码认证视图集"""
    authentication_classes = [JWTAuthentication]  # 只使用JWT认证，避免CSRF检查
    permission_classes = [AllowAny]

    @action(detail=False, methods=['post'], url_path='login-by-password')
    def login_by_password(self, request):
        """用户名/邮箱+密码登录（支持TOTP验证）"""
        serializer = UsernameLoginByPasswordSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        username = serializer.validated_data['username']
        password = serializer.validated_data['password']
        totp_code = serializer.validated_data.get('totp_code', '').strip()

        # 使用用户名/邮箱+密码认证后端
        user = authenticate(request, username=username, password=password)

        if user:
            try:
                profile = user.profile
            except UserProfile.DoesNotExist:
                profile = UserProfile.objects.create(user=user)

            # 检查用户是否启用了TOTP
            if profile.totp_enabled:
                if not totp_code:
                    return Response({
                        'error': '该账户已启用TOTP二次验证，请输入TOTP验证码',
                        'requires_totp': True
                    }, status=status.HTTP_400_BAD_REQUEST)

                # 验证TOTP码
                try:
                    from django_otp.plugins.otp_totp.models import TOTPDevice
                    if profile.totp_device_id:
                        try:
                            device = TOTPDevice.objects.get(id=profile.totp_device_id, user=user)
                            if not device.verify_token(totp_code):
                                return Response({
                                    'error': 'TOTP验证码错误'
                                }, status=status.HTTP_400_BAD_REQUEST)
                        except TOTPDevice.DoesNotExist:
                            return Response({
                                'error': 'TOTP设备不存在'
                            }, status=status.HTTP_400_BAD_REQUEST)
                    else:
                        return Response({
                            'error': 'TOTP设备不存在，请联系管理员'
                        }, status=status.HTTP_400_BAD_REQUEST)
                except Exception as e:
                    logger.error(f"TOTP验证失败: {str(e)}")
                    return Response({
                        'error': 'TOTP验证失败，请稍后重试'
                    }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            # 更新最后登录时间
            user.last_login = timezone.now()
            user.save(update_fields=['last_login'])

            # 生成 JWT token
            refresh = _issue_refresh_token(user)

            logger.info(f"用户 {user.username} 通过用户名/邮箱密码登录成功")

            return Response({
                'access': str(refresh.access_token),
                'refresh': str(refresh),
                'user': {
                    'id': user.id,
                    'username': user.username,
                    'email': user.email,
                    'phone_number': str(profile.phone_number) if profile.phone_number else None
                }
            }, status=status.HTTP_200_OK)
        else:
            return Response({
                'error': '用户名/邮箱或密码错误'
            }, status=status.HTTP_401_UNAUTHORIZED)


class AccountBindingViewSet(viewsets.GenericViewSet):
    """账号绑定管理视图集"""
    authentication_classes = [JWTAuthentication]  # 只使用JWT认证，避免CSRF检查
    permission_classes = [IsAuthenticated]

    def list(self, request):
        """获取当前用户的绑定信息"""
        user = request.user
        profile = user.profile

        # 获取社交账号绑定信息
        from allauth.socialaccount.models import SocialAccount
        social_accounts = SocialAccount.objects.filter(user=user)

        data = {
            'username': user.username,
            'email': user.email,
            'phone_number': str(profile.phone_number) if profile.phone_number else None,
            'phone_verified': profile.phone_verified,
            'social_accounts': [
                {
                    'provider': account.provider,
                    'uid': account.uid,
                    'extra_data': account.extra_data,
                    'date_joined': account.date_joined
                }
                for account in social_accounts
            ],
            'has_password': user.has_usable_password()
        }

        return Response(data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='bind-phone')
    def bind_phone(self, request):
        """绑定手机号"""
        if not settings.SMS_ENABLED:
            return _sms_disabled_response()
        serializer = PhoneBindingSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        phone_number = serializer.validated_data['phone_number']
        code = serializer.validated_data['code']

        try:
            # 验证验证码
            temp_profile = UserProfile(phone_number=phone_number)
            if not temp_profile.verify_sms_code(code, phone_number):
                return Response({
                    'error': '验证码错误或已过期'
                }, status=status.HTTP_400_BAD_REQUEST)

            # 更新用户的 profile
            profile = request.user.profile
            profile.phone_number = phone_number
            profile.phone_verified = True
            profile.save()

            logger.info(f"用户 {request.user.username} 绑定手机号成功: {phone_number}")
            return Response({
                'message': '手机号绑定成功',
                'phone_number': str(phone_number)
            }, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"绑定手机号失败: {str(e)}")
            return Response({
                'error': f'绑定失败: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['delete'], url_path='unbind-phone')
    def unbind_phone(self, request):
        """解绑手机号"""
        user = request.user
        profile = user.profile

        # 检查是否至少有一种登录方式
        from allauth.socialaccount.models import SocialAccount
        has_social_account = SocialAccount.objects.filter(user=user).exists()
        has_password = user.has_usable_password()

        if not has_social_account and not has_password:
            return Response({
                'error': '无法解绑手机号，请至少保留一种登录方式'
            }, status=status.HTTP_400_BAD_REQUEST)

        if not profile.phone_number:
            return Response({
                'error': '您还未绑定手机号'
            }, status=status.HTTP_400_BAD_REQUEST)

        # 解绑手机号
        phone_number = str(profile.phone_number)
        profile.phone_number = None
        profile.phone_verified = False
        profile.save()

        logger.info(f"用户 {user.username} 解绑手机号: {phone_number}")
        return Response({
            'message': '手机号解绑成功'
        }, status=status.HTTP_200_OK)

    @action(detail=False, methods=['delete'], url_path='unbind-social/(?P<provider>[^/.]+)')
    def unbind_social(self, request, provider=None):
        """解绑社交账号"""
        user = request.user
        profile = user.profile

        # 检查是否至少有一种登录方式
        from allauth.socialaccount.models import SocialAccount
        social_accounts = SocialAccount.objects.filter(user=user)
        has_phone = bool(profile.phone_number)
        has_password = user.has_usable_password()

        if social_accounts.count() == 1 and not has_phone and not has_password:
            return Response({
                'error': '无法解绑社交账号，请至少保留一种登录方式'
            }, status=status.HTTP_400_BAD_REQUEST)

        # 查找要解绑的社交账号
        try:
            social_account = social_accounts.get(provider=provider)
            social_account.delete()

            logger.info(f"用户 {user.username} 解绑社交账号: {provider}")
            return Response({
                'message': f'{provider} 账号解绑成功'
            }, status=status.HTTP_200_OK)

        except SocialAccount.DoesNotExist:
            return Response({
                'error': f'未找到 {provider} 账号绑定'
            }, status=status.HTTP_404_NOT_FOUND)


class UserProfileViewSet(viewsets.GenericViewSet):
    """用户信息管理视图集"""
    authentication_classes = [JWTAuthentication]  # 只使用JWT认证，避免CSRF检查
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=['get'])
    def me(self, request):
        """获取当前用户信息"""
        profile = request.user.profile
        serializer = UserProfileSerializer(profile)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['patch'])
    def update_me(self, request):
        """更新当前用户信息"""
        serializer = UserUpdateSerializer(data=request.data, context={'request': request})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        user = request.user
        old_username = user.username

        # 更新用户名
        if 'username' in serializer.validated_data:
            new_username = serializer.validated_data['username']
            
            # 检查是否启用了 Git
            has_git = hasattr(user, 'git_repo')
            
            from project.utils.file import BeanFileManager
            
            if not has_git:
                # 未开启 Git：需要重命名目录
                if BeanFileManager.rename_user_directory(old_username, new_username):
                    # 更新 main.bean 中的用户名引用
                    BeanFileManager.update_main_bean_username(new_username, new_username)
                else:
                    # 重命名失败，返回错误
                    return Response(
                        {'error': '无法重命名用户目录，请检查目录权限或联系管理员'},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR
                    )
            
            user.username = new_username
            user.save(update_fields=['username'])
            logger.info(f"用户 {old_username} (ID: {user.id}) 更新了用户名: {new_username}")

        # 更新邮箱
        if 'email' in serializer.validated_data:
            user.email = serializer.validated_data['email']
            user.save(update_fields=['email'])
            logger.info(f"用户 {user.username} 更新了邮箱")

        logger.info(f"用户 {user.username} 更新了个人信息")

        # 返回更新后的信息
        profile = user.profile
        profile_serializer = UserProfileSerializer(profile)
        return Response(profile_serializer.data, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='set_password')
    def set_password(self, request):
        """设置密码（不需要原密码）"""
        from django.contrib.auth.password_validation import validate_password
        new_password = request.data.get('new_password', '')
        if not new_password:
            return Response({'error': '参数不完整'}, status=status.HTTP_400_BAD_REQUEST)
        user = request.user
        try:
            validate_password(new_password, user)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        user.set_password(new_password)
        user.save(update_fields=['password'])
        logger.info(f"用户 {user.username} 设置密码成功")
        return Response({'message': '密码设置成功'}, status=status.HTTP_200_OK)

    @action(detail=False, methods=['delete'], url_path='delete_account')
    def delete_account(self, request):
        """删除账户
        
        清理顺序：
        1. 停止 Fava 容器
        2. 删除 Gitea 远程仓库（如果启用）
        3. 删除 OSS/MinIO 文件
        4. 删除文件系统 Assets 目录
        5. 删除数据库记录
        """
        user = request.user
        username = user.username

        try:
            import os
            import shutil
            from django.db import transaction
            from django.apps import apps
            from django.conf import settings
            from pathlib import Path

            # 获取所有需要删除的模型
            Account = apps.get_model('account_config', 'Account')
            Expense = apps.get_model('maps', 'Expense')
            Assets = apps.get_model('maps', 'Assets')
            Income = apps.get_model('maps', 'Income')
            File = apps.get_model('file_manager', 'File')
            Directory = apps.get_model('file_manager', 'Directory')
            Tag = apps.get_model('tags', 'Tag')
            Template = apps.get_model('maps', 'Template')
            FormatConfig = apps.get_model('translate', 'FormatConfig')
            FavaInstance = apps.get_model('fava_instances', 'FavaInstance')

            # 统计要删除的数据
            account_count = Account.objects.filter(owner=user).count()
            expense_count = Expense.objects.filter(owner=user).count()
            assets_count = Assets.objects.filter(owner=user).count()
            income_count = Income.objects.filter(owner=user).count()
            file_count = File.objects.filter(owner=user).count()
            directory_count = Directory.objects.filter(owner=user).count()
            tag_count = Tag.objects.filter(owner=user).count()
            template_count = Template.objects.filter(owner=user).count()
            fava_count = FavaInstance.objects.filter(owner=user).count()

            logger.warning(f"用户 {username} (ID: {user.id}) 请求删除账户，将删除:")
            logger.warning(f"  账户: {account_count}, 映射: {expense_count + assets_count + income_count}, "
                         f"文件: {file_count}, 目录: {directory_count}, 标签: {tag_count}, "
                         f"模板: {template_count}, Fava实例: {fava_count}")

            # ========== 1. 停止 Fava 容器 ==========
            try:
                from project.apps.fava_instances.services.fava_manager import FavaContainerManager
                manager = FavaContainerManager()
                running_instances = FavaInstance.objects.filter(
                    owner=user,
                    status__in=['running', 'starting']
                )
                for instance in running_instances:
                    try:
                        instance.status = 'stopping'
                        instance.save()
                        if instance.container_id:
                            manager.stop_container(instance.container_id)
                        instance.status = 'stopped'
                        instance.container_id = ''
                        instance.container_name = ''
                        instance.save()
                        logger.info(f"已停止 Fava 容器: {instance.container_id}")
                    except Exception as e:
                        logger.warning(f"停止 Fava 容器失败 {instance.container_id}: {str(e)}")
                        instance.status = 'error'
                        instance.save()
            except Exception as e:
                logger.warning(f"停止 Fava 容器时出错: {str(e)}")

            # ========== 2. 删除 Gitea 远程仓库 ==========
            try:
                from project.apps.git_repository.services import PlatformGitService
                git_service = PlatformGitService()
                try:
                    git_repo = user.git_repo
                    # 删除 Gitea 远程仓库和相关资源
                    git_service.delete_user_repository(user)
                    logger.info(f"已删除 Gitea 远程仓库: {git_repo.repo_name}")
                except Exception:
                    # 用户未启用 Git 功能，跳过
                    pass
            except Exception as e:
                logger.warning(f"删除 Gitea 仓库时出错: {str(e)}")

            # ========== 3. 删除 OSS/MinIO 文件 ==========
            try:
                from project.utils.storage_factory import get_storage_client
                storage_client = get_storage_client()
                
                # 获取用户的所有文件
                user_files = File.objects.filter(owner=user)
                deleted_storage_files = 0
                
                for file_obj in user_files:
                    storage_name = file_obj.storage_name
                    # 检查是否有其他文件引用相同的存储文件
                    other_references = File.objects.filter(
                        storage_name=storage_name
                    ).exclude(id=file_obj.id).exists()
                    
                    if not other_references:
                        try:
                            if storage_client.delete_file(storage_name):
                                deleted_storage_files += 1
                        except Exception as e:
                            logger.warning(f"删除存储文件失败 {storage_name}: {str(e)}")
                
                logger.info(f"已删除 {deleted_storage_files} 个存储文件")
            except Exception as e:
                logger.warning(f"删除 OSS/MinIO 文件时出错: {str(e)}")

            # ========== 4. 删除文件系统 Assets 目录 ==========
            try:
                from project.utils.file import BeanFileManager
                user_assets_path = Path(BeanFileManager.get_user_assets_path(user))
                
                if user_assets_path.exists() and user_assets_path.is_dir():
                    try:
                        shutil.rmtree(user_assets_path)
                        logger.info(f"已删除用户 Assets 目录: {user_assets_path}")
                    except Exception as e:
                        logger.warning(f"删除用户 Assets 目录失败 {user_assets_path}: {str(e)}")
            except Exception as e:
                logger.warning(f"删除文件系统 Assets 目录时出错: {str(e)}")

            # ========== 5. 删除数据库记录（在事务中执行）==========
            with transaction.atomic():
                # 删除所有关联数据
                Directory.objects.filter(owner=user).delete()
                File.objects.filter(owner=user).delete()
                Tag.objects.filter(owner=user).delete()
                Template.objects.filter(owner=user).delete()
                FormatConfig.objects.filter(owner=user).delete()
                FavaInstance.objects.filter(owner=user).delete()
                
                # 在删除账户之前，需要先清除所有账户的 parent 引用
                # 因为 parent 字段使用 PROTECT 约束，必须先清除引用才能删除
                Account.objects.filter(owner=user).update(parent=None)

                # 外部账户绑定对 Account 是 PROTECT；删除外部账户会级联别名与绑定
                ExternalAccount.objects.filter(owner=user).delete()

                # 现在可以安全地删除所有账户
                Account.objects.filter(owner=user).delete()
                Expense.objects.filter(owner=user).delete()
                Assets.objects.filter(owner=user).delete()
                Income.objects.filter(owner=user).delete()

                # 删除社交账号绑定
                SocialAccount.objects.filter(user=user).delete()

                # 删除TOTP设备
                try:
                    from django_otp.plugins.otp_totp.models import TOTPDevice
                    TOTPDevice.objects.filter(user=user).delete()
                except Exception:
                    pass  # 如果没有安装django-otp则忽略

                # 删除用户profile
                if hasattr(user, 'profile'):
                    user.profile.delete()

                # 删除用户（最后删除）
                user.delete()

            logger.info(f"用户 {username} 的账户已完全删除")

            return Response({
                'message': '账户已删除'
            }, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"删除账户失败: {str(e)}", exc_info=True)
            return Response({
                'error': f'删除账户失败: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class TwoFactorAuthViewSet(viewsets.GenericViewSet):
    """双因素认证视图集"""
    authentication_classes = [JWTAuthentication]  # 只使用JWT认证，避免CSRF检查
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=['get'], url_path='status')
    def status(self, request):
        """获取2FA状态"""
        profile = request.user.profile
        return Response({
            'totp_enabled': profile.totp_enabled,
            'has_2fa': profile.has_2fa_enabled()
        }, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'], url_path='totp/qrcode')
    def totp_qrcode(self, request):
        """生成TOTP二维码"""
        try:
            from django_otp.plugins.otp_totp.models import TOTPDevice
            from django_otp.util import random_hex
            import qrcode
            import io
            import base64

            profile = request.user.profile

            # 检查是否已启用TOTP
            if profile.totp_enabled and profile.totp_device_id:
                return Response({
                    'error': 'TOTP已启用，请先禁用'
                }, status=status.HTTP_400_BAD_REQUEST)

            # 生成或获取TOTP设备
            device, created = TOTPDevice.objects.get_or_create(
                user=request.user,
                defaults={
                    'name': 'default',
                    'confirmed': False,
                    'key': random_hex(16)  # 16字节=128位，转换为Base32是26个字符
                }
            )

            if not created and device.confirmed:
                # 如果设备已确认，重新生成密钥
                device.key = random_hex(16)
                device.confirmed = False
                device.save()

            # 生成配置URL
            config_url = device.config_url

            # 从config_url中提取并返回 Base32 secret（权威来源）
            # 格式: otpauth://totp/...?secret=XXXXX&...
            import urllib.parse
            parsed_url = urllib.parse.urlparse(config_url)
            query_params = urllib.parse.parse_qs(parsed_url.query)
            secret_key = query_params.get('secret', [''])[0]
            # 去掉可能的填充，保证兼容（KeePass/GA通常都接受去填充形式）
            if secret_key:
                secret_key = secret_key.strip().replace(' ', '')
                if secret_key.endswith('='):
                    secret_key = secret_key.rstrip('=')

            # 生成二维码
            qr = qrcode.QRCode(version=1, box_size=10, border=5)
            qr.add_data(config_url)
            qr.make(fit=True)

            img = qr.make_image(fill_color="black", back_color="white")
            buffer = io.BytesIO()
            img.save(buffer, format='PNG')
            img_str = base64.b64encode(buffer.getvalue()).decode()

            # 保存设备ID到profile
            profile.totp_device_id = device.id
            profile.save()

            return Response({
                'qr_code': f'data:image/png;base64,{img_str}',
                'secret': secret_key,
                'device_id': device.id
            }, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"生成TOTP二维码失败: {str(e)}")
            return Response({
                'error': f'生成二维码失败: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['post'], url_path='totp/enable')
    def totp_enable(self, request):
        """启用TOTP"""
        serializer = TOTPEnableSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        code = serializer.validated_data['code']
        profile = request.user.profile

        if not profile.totp_device_id:
            return Response({
                'error': '请先生成TOTP二维码'
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            from django_otp.plugins.otp_totp.models import TOTPDevice

            device = TOTPDevice.objects.get(id=profile.totp_device_id, user=request.user)

            # 验证TOTP码
            if device.verify_token(code):
                # 确认设备
                device.confirmed = True
                device.save()

                # 启用TOTP
                profile.totp_enabled = True
                profile.save()

                logger.info(f"用户 {request.user.username} 启用TOTP成功")
                return Response({
                    'message': 'TOTP启用成功'
                }, status=status.HTTP_200_OK)
            else:
                return Response({
                    'error': '验证码错误'
                }, status=status.HTTP_400_BAD_REQUEST)

        except TOTPDevice.DoesNotExist:
            return Response({
                'error': 'TOTP设备不存在'
            }, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.error(f"启用TOTP失败: {str(e)}")
            return Response({
                'error': f'启用TOTP失败: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['post'], url_path='totp/disable')
    def totp_disable(self, request):
        """禁用TOTP"""
        serializer = TOTPDisableSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        code = serializer.validated_data['code']
        profile = request.user.profile

        if not profile.totp_enabled:
            return Response({
                'error': 'TOTP未启用'
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            from django_otp.plugins.otp_totp.models import TOTPDevice

            device = None
            if profile.totp_device_id:
                device = TOTPDevice.objects.get(id=profile.totp_device_id, user=request.user)

                # 验证TOTP码
                if not device.verify_token(code):
                    return Response({
                        'error': '验证码错误'
                    }, status=status.HTTP_400_BAD_REQUEST)

            # 禁用TOTP
            device_id = profile.totp_device_id
            profile.totp_enabled = False
            profile.totp_device_id = None
            profile.save()

            # 删除设备（可选）
            if device_id and device:
                try:
                    device.delete()
                except:
                    pass

            logger.info(f"用户 {request.user.username} 禁用TOTP成功")
            return Response({
                'message': 'TOTP禁用成功'
            }, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"禁用TOTP失败: {str(e)}")
            return Response({
                'error': f'禁用TOTP失败: {str(e)}'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class AuthPublicConfigAPIView(APIView):
    """登录页等使用的公开认证策略（无需登录）"""

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        return Response(
            {
                'phone_binding_required': settings.PHONE_BINDING_REQUIRED,
                'sms_enabled': settings.SMS_ENABLED,
                'fava_deploy_mode': getattr(settings, 'FAVA_DEPLOY_MODE', 'dynamic'),
            },
            status=status.HTTP_200_OK,
        )


class PersonalAccessTokenViewSet(viewsets.GenericViewSet):
    """个人访问令牌管理（供 MCP 等外部客户端使用）"""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]
    serializer_class = PersonalAccessTokenSerializer

    def get_queryset(self):
        return PersonalAccessToken.objects.filter(user=self.request.user).order_by('-created')

    def list(self, request):
        serializer = self.get_serializer(self.get_queryset(), many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def create(self, request):
        serializer = PersonalAccessTokenCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        days = data.get('expires_in_days')
        expires_at = timezone.now() + timedelta(days=days) if days else None

        token, raw_token = PersonalAccessToken.issue(
            request.user, data['name'], expires_at=expires_at
        )
        logger.info(f"用户 {request.user.username} 创建访问令牌 {token.prefix}…")
        payload = PersonalAccessTokenSerializer(token).data
        payload['token'] = raw_token
        return Response(payload, status=status.HTTP_201_CREATED)

    def partial_update(self, request, pk=None):
        """修改令牌用途说明"""
        token = self.get_object()
        serializer = PersonalAccessTokenUpdateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        token.name = serializer.validated_data['name']
        token.save()
        logger.info(f"用户 {request.user.username} 更新访问令牌 {token.prefix}… 用途为 {token.name}")
        return Response(
            PersonalAccessTokenSerializer(token).data, status=status.HTTP_200_OK
        )

    def destroy(self, request, pk=None):
        """删除访问令牌（硬删除；对方的共享账本绑定保留但显示为已失效）"""
        token = self.get_object()
        prefix = token.prefix
        token.delete()
        logger.info(f"用户 {request.user.username} 删除访问令牌 {prefix}…")
        return Response(status=status.HTTP_204_NO_CONTENT)


