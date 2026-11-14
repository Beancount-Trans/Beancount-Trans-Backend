"""project URL Configuration

The `urlpatterns` list routes URLs to view.py. For more information please see:
    https://docs.djangoproject.com/en/4.0/topics/http/urls/
Examples:
Function view.py
    1. Add an import:  from my_app import view.py
    2. Add a URL to urlpatterns:  path('', view.py.home, name='home')
Class-based view.py
    1. Add an import:  from other_app.view.py import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from project.apps.maps.views import ExpenseViewSet, AssetsViewSet, IncomeViewSet, TemplateViewSet,TemplateItemViewSet
from project.apps.translate.views.views import UserConfigAPI
from project.apps.file_manager.views import DirectoryViewSet, FileViewSet, LedgerExportView
from project.apps.account.views import AccountViewSet, AccountTemplateViewSet
from project.apps.tags.views import TagViewSet
from project.views import authenticateByToken
from rest_framework_simplejwt.views import TokenRefreshView
# from .views import GoogleLogin
from rest_framework import routers
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView
# from rest_framework import permissions
# from drf_yasg.views import get_schema_view
# from drf_yasg import openapi
# from project.views import DRFLoginView

# from users.views import UserViewSet, GroupViewSet, CreateUserView, LoginView, GitHubLogin
# schema_view = get_schema_view(
#     openapi.Info(
#         title="文件管理API",
#         default_version='v1',
#         description="文件上传、管理和检索接口文档",
#     ),
#     public=True,
#     permission_classes=(permissions.AllowAny,),
# )

router = routers.DefaultRouter()
router.register(r'account', AccountViewSet, basename='account')
router.register(r'account-templates', AccountTemplateViewSet, basename='account-template')
router.register(r'tags', TagViewSet, basename='tag')
router.register(r'expense', ExpenseViewSet, basename="expense")
router.register(r'assets', AssetsViewSet, basename="assets")
router.register(r'income', IncomeViewSet, basename="income")
router.register(r'directories', DirectoryViewSet, basename='directory')
router.register(r'files', FileViewSet, basename='files')
router.register(r'templates', TemplateViewSet, basename='template')
router.register(
    r'templates/(?P<template_pk>\d+)/items', 
    TemplateItemViewSet, 
    basename='templateitem'
)


urlpatterns = [
    path('api/', include(router.urls)),
    path('admin/', admin.site.urls),  # 管理地址

    # API文档 - drf-spectacular
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),  # OpenAPI schema
    path('api/docs/', SpectacularRedocView.as_view(url_name='schema'), name='docs'),  # ReDoc UI

    # allauth
    path("api/_allauth/", include("allauth.headless.urls")),
    path('api/_allauth/browser/v1/auth/github/token', authenticateByToken, name='authenticateByGithubToken'),
    path('api/auth/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path("api/accounts/", include("allauth.urls")),  # 提供 GitHub OAuth 登录回调

    # 业务相关的urls
    path('api/auth/', include('project.apps.authentication.urls')),  # 用户认证
    path('api/config/', UserConfigAPI.as_view(), name='user-config'),  # 格式化输出配置
    path('api/ledger/export/', LedgerExportView.as_view(), name='ledger-export'),  # 账本导出
    path('api/translate/', include('project.apps.translate.urls')),  # 解析地址
    path('api/fava/', include('project.apps.fava_instances.urls')),  # fava容器服务
    path('api/git/', include('project.apps.git_repository.urls')),  # Git仓库管理
    path('api/reconciliation/', include('project.apps.reconciliation.urls')),  # 对账功能
    path('api/assistant/', include('project.apps.assistant.urls')),  # AI 账本助手

    # OAuth 2.1 授权服务器（MCP 客户端浏览器授权流程）
    # 挂在根路径，使 /.well-known/oauth-authorization-server 与
    # /.well-known/oauth-protected-resource/<path> 符合 RFC 8414 / RFC 9728
    path('', include('oauth2_provider.urls', namespace='oauth2_provider')),
]
