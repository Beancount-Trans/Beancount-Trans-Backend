import json
import hashlib
import hmac
import logging
from django.conf import settings
from django.http import HttpResponse
from rest_framework import status, permissions
from rest_framework.decorators import api_view, permission_classes, action
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ViewSet
from drf_spectacular.utils import extend_schema, OpenApiResponse

from .models import GitRepository
from .serializers import (
    GitRepositorySerializer, CreateRepositorySerializer, LinkRepositorySerializer,
    GitRepositorySettingsSerializer,
    SyncStatusSerializer, SyncResponseSerializer,
    WebhookPayloadSerializer, DeployKeyResponseSerializer,
    DeleteRepositoryResponseSerializer, ClearSyncedLedgerResponseSerializer,
    LedgerCommitPreviewSerializer, LedgerCommitResultSerializer,
)
from .services import PlatformGitService, GitServiceException
from .ledger_commit_service import LedgerCommitService

logger = logging.getLogger(__name__)


class GitRepositoryViewSet(ViewSet):
    """Git 仓库管理视图集"""

    permission_classes = [permissions.IsAuthenticated]

    def get_git_service(self):
        """获取 Git 服务实例"""
        return PlatformGitService()

    @extend_schema(
        summary="获取用户 Git 仓库信息",
        responses={
            200: GitRepositorySerializer,
            404: OpenApiResponse(description="用户未启用 Git 功能")
        }
    )
    def list(self, request: Request) -> Response:
        """获取用户的 Git 仓库信息"""
        try:
            git_repo = request.user.git_repo
            serializer = GitRepositorySerializer(git_repo)
            return Response(serializer.data)
        except GitRepository.DoesNotExist:
            return Response(
                {'error': '用户未启用 Git 功能'}, 
                status=status.HTTP_404_NOT_FOUND
            )

    @extend_schema(
        summary="启用 Git 功能",
        request=CreateRepositorySerializer,
        responses={
            201: GitRepositorySerializer,
            400: OpenApiResponse(description="请求参数错误或用户已有仓库")
        }
    )
    def create(self, request: Request) -> Response:
        """启用 Git：仅在平台托管 Gitea 创建仓库。关联已有外部远程请使用 link 接口。"""
        serializer = CreateRepositorySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        use_template = serializer.validated_data['template']

        try:
            git_service = self.get_git_service()
            git_repo = git_service.create_user_repository(
                user=request.user,
                use_template=use_template,
            )

            response_serializer = GitRepositorySerializer(git_repo)
            payload = dict(response_serializer.data)
            if git_repo.remote_ssh_url and git_repo.webhook_secret:
                payload['webhook_secret'] = git_repo.webhook_secret
            return Response(payload, status=status.HTTP_201_CREATED)

        except GitServiceException as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )

    @extend_schema(
        summary="关联已有外部远程仓库",
        request=LinkRepositorySerializer,
        responses={
            201: GitRepositorySerializer,
            400: OpenApiResponse(description="参数错误或已有关联"),
        }
    )
    @action(detail=False, methods=['POST'], url_path='link')
    def link(self, request: Request) -> Response:
        serializer = LinkRepositorySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            git_service = self.get_git_service()
            git_repo = git_service.link_external_repository(
                user=request.user,
                remote_ssh_url=data['remote_ssh_url'],
                provider=data['provider'],
                default_branch=data.get('default_branch') or 'main',
                external_full_name=data.get('external_full_name') or '',
            )
            response_serializer = GitRepositorySerializer(git_repo)
            payload = dict(response_serializer.data)
            if git_repo.webhook_secret:
                payload['webhook_secret'] = git_repo.webhook_secret
            return Response(payload, status=status.HTTP_201_CREATED)
        except GitServiceException as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @extend_schema(
        summary="更新 Git 仓库设置",
        request=GitRepositorySettingsSerializer,
        responses={
            200: GitRepositorySerializer,
            404: OpenApiResponse(description="用户未启用 Git 功能"),
        }
    )
    @action(detail=False, methods=['PATCH'], url_path='settings')
    def update_settings(self, request: Request) -> Response:
        """更新仓库级开关（如自动导入账本新增账户）"""
        try:
            git_repo = request.user.git_repo
        except GitRepository.DoesNotExist:
            return Response(
                {'error': '用户未启用 Git 功能'},
                status=status.HTTP_404_NOT_FOUND
            )

        serializer = GitRepositorySettingsSerializer(
            git_repo, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(GitRepositorySerializer(git_repo).data)

    @extend_schema(
        summary="下载 Deploy Key",
        responses={
            200: DeployKeyResponseSerializer,
            404: OpenApiResponse(description="用户未启用 Git 功能")
        }
    )
    @action(detail=False, methods=['GET'], url_path='deploy-key')
    def download_deploy_key(self, request: Request) -> Response:
        """下载 Deploy Key 私钥文件"""
        try:
            git_repo = request.user.git_repo

            # 创建文件响应
            filename = f"{request.user.username}_deploy_key.pem"

            response = HttpResponse(
                git_repo.deploy_key_private,
                content_type='application/x-pem-file'
            )
            response['Content-Disposition'] = f'attachment; filename="{filename}"'

            return response

        except GitRepository.DoesNotExist:
            return Response(
                {'error': '用户未启用 Git 功能'}, 
                status=status.HTTP_404_NOT_FOUND
            )

    @extend_schema(
        summary="重新生成 Deploy Key",
        responses={
            200: DeployKeyResponseSerializer,
            404: OpenApiResponse(description="用户未启用 Git 功能"),
            500: OpenApiResponse(description="重新生成失败")
        }
    )
    @action(detail=False, methods=['POST'], url_path='deploy-key/regenerate')
    def regenerate_deploy_key(self, request: Request) -> Response:
        """重新生成 Deploy Key 并下载"""
        try:
            git_service = self.get_git_service()
            key_data = git_service.regenerate_deploy_key(request.user)

            # 创建文件响应
            filename = f"{request.user.username}_deploy_key.pem"

            response = HttpResponse(
                key_data['private_key'],
                content_type='application/x-pem-file'
            )
            response['Content-Disposition'] = f'attachment; filename="{filename}"'

            return response

        except GitRepository.DoesNotExist:
            return Response(
                {'error': '用户未启用 Git 功能'}, 
                status=status.HTTP_404_NOT_FOUND
            )
        except GitServiceException as e:
            return Response(
                {'error': str(e)}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @extend_schema(
        summary="删除用户 Git 仓库",
        responses={
            200: DeleteRepositoryResponseSerializer,
            404: OpenApiResponse(description="用户未启用 Git 功能"),
            500: OpenApiResponse(description="删除失败")
        }
    )
    @action(detail=False, methods=['DELETE'], url_path='delete')
    def delete_repository(self, request: Request) -> Response:
        """删除用户 Git 仓库并清理相关资源"""
        try:
            git_repo = request.user.git_repo

            git_service = self.get_git_service()
            result = git_service.delete_user_repository(request.user)

            serializer = DeleteRepositoryResponseSerializer(result)
            return Response(serializer.data, status=status.HTTP_200_OK)

        except GitRepository.DoesNotExist:
            return Response(
                {'error': '用户未启用 Git 功能'}, 
                status=status.HTTP_404_NOT_FOUND
            )
        except GitServiceException as e:
            return Response(
                {'error': str(e)}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class GitSyncView(APIView):
    """Git 同步视图"""

    permission_classes = [permissions.IsAuthenticated]

    def get_git_service(self):
        return PlatformGitService()

    @extend_schema(
        summary="手动触发同步",
        responses={
            200: SyncResponseSerializer,
            404: OpenApiResponse(description="用户未启用 Git 功能")
        }
    )
    def post(self, request: Request) -> Response:
        """手动触发从远程仓库同步"""
        try:
            git_service = self.get_git_service()
            # 手动同步视为显式重新授权：会解除「取消同步」状态并执行拉取
            result = git_service.sync_repository(request.user, manual=True)

            serializer = SyncResponseSerializer(result)
            return Response(serializer.data)

        except GitServiceException as e:
            return Response(
                {'error': str(e)}, 
                status=status.HTTP_404_NOT_FOUND
            )


class GitSyncCancelView(APIView):
    """取消同步视图：清除服务器本地账本副本并暂停自动拉取"""

    permission_classes = [permissions.IsAuthenticated]

    def get_git_service(self):
        """获取 Git 服务实例"""
        return PlatformGitService()

    @extend_schema(
        summary="取消同步：清除服务器本地账本副本",
        responses={
            200: ClearSyncedLedgerResponseSerializer,
            404: OpenApiResponse(description="用户未启用 Git 功能"),
            500: OpenApiResponse(description="清除失败")
        }
    )
    def post(self, request: Request) -> Response:
        """清除服务器本地由 Git 同步引入的账本内容，并暂停后续自动拉取"""
        if not GitRepository.objects.filter(owner=request.user).exists():
            return Response(
                {'error': '用户未启用 Git 功能'},
                status=status.HTTP_404_NOT_FOUND
            )

        try:
            result = self.get_git_service().clear_synced_ledger(request.user)
            serializer = ClearSyncedLedgerResponseSerializer(result)
            return Response(serializer.data)

        except GitServiceException as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class GitSyncStatusView(APIView):
    """Git 同步状态视图"""

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        summary="获取同步状态",
        responses={
            200: SyncStatusSerializer,
            404: OpenApiResponse(description="用户未启用 Git 功能")
        }
    )
    def get(self, request: Request) -> Response:
        """获取同步状态和错误信息"""
        try:
            git_repo = request.user.git_repo

            data = {
                'status': git_repo.sync_status,
                'last_sync_at': git_repo.last_sync_at,
                'error': git_repo.sync_error,
                'paused': git_repo.sync_paused,
            }

            serializer = SyncStatusSerializer(data)
            return Response(serializer.data)

        except GitRepository.DoesNotExist:
            return Response(
                {'error': '用户未启用 Git 功能'}, 
                status=status.HTTP_404_NOT_FOUND
            )


def _webhook_secret_for_repo(git_repo: GitRepository) -> str:
    if git_repo.webhook_secret:
        return git_repo.webhook_secret
    if git_repo.provider == 'gitea_hosted':
        return settings.GITEA_WEBHOOK_SECRET or ''
    return ''


def _ref_matches_branch(ref: str, branch: str) -> bool:
    if not ref or not branch:
        return False
    return ref == f'refs/heads/{branch}'


def _extract_full_name(data: dict) -> str:
    """GitHub/Gitea/Gogs 用 repository.full_name；GitLab 用 project.path_with_namespace。"""
    repo = data.get('repository') or {}
    project = data.get('project') or {}
    return (
        (repo.get('full_name') or '').strip()
        or (project.get('path_with_namespace') or '').strip()
    )


def _find_repo_by_full_name(full_name: str):
    """按 full_name 匹配仓库，返回 (git_repo, ambiguous)。未命中时 git_repo 为 None。"""
    qs = GitRepository.objects.filter(external_full_name__iexact=full_name)
    count = qs.count()
    if count > 1:
        logger.error('Multiple GitRepository for external_full_name=%s', full_name)
        return None, True
    return (qs.first() if count == 1 else None), False


def _locate_repo_by_full_name(full_name: str):
    """要求 full_name 唯一命中仓库，返回 (git_repo, error_response)。"""
    git_repo, ambiguous = _find_repo_by_full_name(full_name)
    if ambiguous:
        return None, Response({'error': 'Ambiguous repository'}, status=status.HTTP_400_BAD_REQUEST)
    if git_repo is None:
        return None, Response({'error': 'Repository not found'}, status=status.HTTP_404_NOT_FOUND)
    return git_repo, None


class GitWebhookView(APIView):
    """Git Webhook：支持 GitHub、GitLab、Gitea（含平台托管）与 Gogs。"""

    permission_classes = []

    def get_git_service(self):
        return PlatformGitService()

    @extend_schema(
        summary="处理 Git push Webhook",
        request=WebhookPayloadSerializer,
        responses={
            200: OpenApiResponse(description="处理成功"),
            400: OpenApiResponse(description="签名验证失败"),
            404: OpenApiResponse(description="仓库不存在"),
        }
    )
    def post(self, request: Request) -> Response:
        body = request.body
        try:
            data = json.loads(body.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return Response({'error': 'Invalid JSON'}, status=status.HTTP_400_BAD_REQUEST)

        if request.headers.get('X-Gitea-Signature'):
            return self._handle_gitea(body, data, request)
        if request.headers.get('X-Gogs-Signature'):
            return self._handle_gogs(body, data, request)
        if request.headers.get('X-Gitlab-Token'):
            return self._handle_gitlab(body, data, request)
        if request.headers.get('X-Hub-Signature-256'):
            return self._handle_github(body, data, request)

        return Response({'error': 'Unknown webhook type'}, status=status.HTTP_400_BAD_REQUEST)

    def _handle_github(self, body: bytes, data: dict, request: Request) -> Response:
        full_name = _extract_full_name(data)
        if not full_name:
            return Response({'error': 'Missing repository.full_name'}, status=status.HTTP_400_BAD_REQUEST)

        git_repo, error = _locate_repo_by_full_name(full_name)
        if error is not None:
            return error

        secret = _webhook_secret_for_repo(git_repo)
        if not secret:
            return Response({'error': 'Webhook secret not configured'}, status=status.HTTP_400_BAD_REQUEST)

        sig_header = request.headers.get('X-Hub-Signature-256', '')
        if not sig_header.startswith('sha256='):
            return Response({'error': 'Invalid signature'}, status=status.HTTP_400_BAD_REQUEST)
        expected = 'sha256=' + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig_header, expected):
            return Response({'error': 'Invalid signature'}, status=status.HTTP_400_BAD_REQUEST)

        ref = data.get('ref') or ''
        branch = (git_repo.default_branch or 'main').strip() or 'main'
        if not _ref_matches_branch(ref, branch):
            return Response({'message': 'Ignored non-default branch', 'status': 'ignored'})

        return self._sync_and_respond(git_repo, full_name)

    def _handle_gogs(self, body: bytes, data: dict, request: Request) -> Response:
        """Gogs push Webhook：X-Gogs-Signature 为 body 的 HMAC-SHA256 十六进制摘要。"""
        full_name = _extract_full_name(data)
        if not full_name:
            return Response({'error': 'Missing repository.full_name'}, status=status.HTTP_400_BAD_REQUEST)

        git_repo, error = _locate_repo_by_full_name(full_name)
        if error is not None:
            return error

        secret = _webhook_secret_for_repo(git_repo)
        if not secret:
            return Response({'error': 'Webhook secret not configured'}, status=status.HTTP_400_BAD_REQUEST)

        sig = request.headers.get('X-Gogs-Signature', '')
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return Response({'error': 'Invalid signature'}, status=status.HTTP_400_BAD_REQUEST)

        ref = data.get('ref') or ''
        branch = (git_repo.default_branch or 'main').strip() or 'main'
        if not _ref_matches_branch(ref, branch):
            return Response({'message': 'Ignored non-default branch', 'status': 'ignored'})

        return self._sync_and_respond(git_repo, full_name)

    def _handle_gitlab(self, body: bytes, data: dict, request: Request) -> Response:
        """GitLab push Webhook：X-Gitlab-Token 与仓库密钥做明文比对（GitLab 不使用 HMAC）。"""
        full_name = _extract_full_name(data)
        if not full_name:
            return Response(
                {'error': 'Missing project.path_with_namespace'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        git_repo, error = _locate_repo_by_full_name(full_name)
        if error is not None:
            return error

        secret = _webhook_secret_for_repo(git_repo)
        token = request.headers.get('X-Gitlab-Token', '')
        if not secret or not hmac.compare_digest(token, secret):
            return Response({'error': 'Invalid token'}, status=status.HTTP_400_BAD_REQUEST)

        ref = data.get('ref') or ''
        branch = (git_repo.default_branch or 'main').strip() or 'main'
        if not _ref_matches_branch(ref, branch):
            return Response({'message': 'Ignored non-default branch', 'status': 'ignored'})

        return self._sync_and_respond(git_repo, full_name)

    def _handle_gitea(self, body: bytes, data: dict, request: Request) -> Response:
        full_name = _extract_full_name(data)
        short_name = ((data.get('repository') or {}).get('name') or '').strip()

        git_repo = None
        if full_name:
            git_repo, ambiguous = _find_repo_by_full_name(full_name)
            if ambiguous:
                return Response({'error': 'Ambiguous repository'}, status=status.HTTP_400_BAD_REQUEST)

        # 平台托管仓库不写 external_full_name，按 repo_name 兜底匹配（仅限托管仓库，避免跨仓误命中）
        if git_repo is None and short_name.endswith('-assets'):
            git_repo = GitRepository.objects.filter(
                repo_name=short_name, provider='gitea_hosted'
            ).first()

        if git_repo is None:
            return Response({'error': 'Repository not found'}, status=status.HTTP_404_NOT_FOUND)

        secret = _webhook_secret_for_repo(git_repo)
        sig = request.headers.get('X-Gitea-Signature', '')
        if secret:
            expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(sig, expected):
                return Response({'error': 'Invalid signature'}, status=status.HTTP_400_BAD_REQUEST)
        elif settings.GIT_WEBHOOK_STRICT:
            return Response({'error': 'Webhook secret not configured'}, status=status.HTTP_400_BAD_REQUEST)

        ref = data.get('ref') or ''
        branch = (git_repo.default_branch or 'main').strip() or 'main'
        if not _ref_matches_branch(ref, branch):
            return Response({'message': 'Ignored non-default branch', 'status': 'ignored'})

        label = full_name or short_name
        return self._sync_and_respond(git_repo, label)

    def _sync_and_respond(self, git_repo: GitRepository, label: str) -> Response:
        try:
            git_service = self.get_git_service()
            result = git_service.sync_repository(git_repo.owner)
            logger.info('Webhook triggered sync for %s: %s', label, result.get('status'))
            return Response({
                'message': 'Sync triggered successfully',
                'status': result.get('status'),
            })
        except Exception as e:
            logger.error('Webhook processing error: %s', e, exc_info=True)
            return Response({'error': 'Processing failed'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GitTransCommitPreviewView(APIView):
    """trans/ 条目迁移到月度账本：预览视图（仅 Git 用户）"""

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        summary="预览 trans 条目迁移到月度账本",
        responses={
            200: LedgerCommitPreviewSerializer,
            400: OpenApiResponse(description="用户未启用 Git 功能或参数错误"),
        },
    )
    def post(self, request: Request) -> Response:
        """返回迁移计划（各月度文件将新增/跳过的条目数），不落盘、不提交"""
        try:
            result = LedgerCommitService.preview(request.user)
            return Response(LedgerCommitPreviewSerializer(result).data)
        except GitServiceException as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


class GitTransCommitView(APIView):
    """trans/ 条目迁移到月度账本：执行视图（仅 Git 用户）"""

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        summary="提交 trans 条目到月度账本并推送远程",
        responses={
            200: LedgerCommitResultSerializer,
            400: OpenApiResponse(description="用户未启用 Git 功能或提交失败"),
        },
    )
    def post(self, request: Request) -> Response:
        """迁移 trans/ 条目到月度文件，提交并推送到远程仓库"""
        try:
            result = LedgerCommitService.execute(request.user)
            return Response(LedgerCommitResultSerializer(result).data)
        except GitServiceException as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)