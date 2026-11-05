from django.conf import settings
from rest_framework import serializers
from .models import GitRepository
from .git_remote import is_valid_ssh_git_url


def build_webhook_callback_url() -> str:
    return f"https://{settings.BASE_URL}/api/git/webhook/"


PROVIDER_WEBHOOK_HINT = {
    'github': 'GitHub 账本仓库：Settings → Webhooks → Add webhook，Secret 填 webhook_secret',
    'gitlab': 'GitLab 账本仓库：Settings → Webhooks，Secret token 填 webhook_secret，勾选 Push events',
    'gitea': 'Gitea 账本仓库：Settings → Webhooks → Add Webhook (Gitea)，Secret 填 webhook_secret',
    'gogs': 'Gogs 账本仓库：Settings → Webhooks → Add Webhook，Secret 填 webhook_secret',
    'other': '自建 Git 平台：添加 push Webhook；GitHub/Gitea/Gogs 使用 HMAC-SHA256 签名，GitLab 使用明文 Token 匹配',
}


class GitRepositorySerializer(serializers.ModelSerializer):
    """Git 仓库序列化器"""

    ssh_clone_url = serializers.ReadOnlyField()
    deploy_key_download_url = serializers.SerializerMethodField()
    webhook_callback_url = serializers.SerializerMethodField()
    setup_instructions = serializers.SerializerMethodField()

    class Meta:
        model = GitRepository
        fields = [
            'id', 'ssh_clone_url', 'repo_name', 'setup_mode', 'provider',
            'remote_ssh_url', 'external_full_name', 'default_branch',
            'deploy_key_public', 'webhook_callback_url', 'setup_instructions',
            'created_with_template',
            'last_sync_at', 'sync_status', 'sync_error', 'sync_paused',
            'auto_import_ledger_accounts',
            'deploy_key_download_url', 'created', 'modified',
        ]
        read_only_fields = [
            'id', 'ssh_clone_url', 'repo_name', 'setup_mode', 'provider',
            'remote_ssh_url', 'external_full_name', 'default_branch',
            'deploy_key_public', 'webhook_callback_url', 'setup_instructions',
            'last_sync_at', 'sync_status', 'sync_error', 'sync_paused',
            'created', 'modified',
        ]

    def get_deploy_key_download_url(self, obj):
        return "/api/git/repository/deploy-key/"

    def get_webhook_callback_url(self, obj):
        if obj.provider == 'gitea_hosted' and not obj.remote_ssh_url:
            return ''
        return build_webhook_callback_url()

    def get_setup_instructions(self, obj):
        if obj.provider == 'gitea_hosted' and not obj.remote_ssh_url:
            return []
        url = build_webhook_callback_url()
        lines = [
            f'在托管平台添加 Webhook，Payload URL：{url}',
            'Content type 选择 application/json；事件勾选 push。',
            'Secret 填入启用时返回的 webhook_secret（仅此一次展示，请保存）。',
            f'仅当推送分支为 {obj.default_branch or "main"} 时触发平台同步。',
        ]
        lines.insert(0, PROVIDER_WEBHOOK_HINT.get(obj.provider, PROVIDER_WEBHOOK_HINT['other']))
        return lines


class GitRepositorySettingsSerializer(serializers.ModelSerializer):
    """仓库可写设置（当前仅自动导入账本新增账户）"""

    class Meta:
        model = GitRepository
        fields = ['auto_import_ledger_accounts']


class CreateRepositorySerializer(serializers.Serializer):
    """启用 Git：仅在集成 Gitea 上创建仓库（模板或空库）。已有外部远程请使用 POST .../repository/link/。"""

    template = serializers.BooleanField(
        default=True,
        help_text="是否基于模板创建；False 则为空仓库",
    )


class LinkRepositorySerializer(serializers.Serializer):
    """关联已有远程仓库。支持任意可公网访问的 SSH 仓库（GitHub / GitLab / Gitea / Gogs / 自建）。"""

    remote_ssh_url = serializers.CharField(required=True, max_length=500)
    provider = serializers.ChoiceField(
        choices=['', 'github', 'gitlab', 'gitea', 'gogs', 'other'],
        required=False,
        allow_blank=True,
        default='',
        help_text="留空则由平台从 SSH 地址自动识别",
    )
    default_branch = serializers.CharField(required=False, default='main', max_length=100)
    external_full_name = serializers.CharField(
        required=False, allow_blank=True, default='', max_length=255,
    )

    def validate_remote_ssh_url(self, value: str) -> str:
        url = (value or '').strip()
        if not is_valid_ssh_git_url(url):
            raise serializers.ValidationError(
                "请填写 SSH 格式的仓库地址，如 git@host:owner/repo.git 或 ssh://git@host[:port]/owner/repo.git"
            )
        return url


class SyncStatusSerializer(serializers.Serializer):
    """同步状态序列化器"""

    status = serializers.CharField(help_text="同步状态")
    last_sync_at = serializers.DateTimeField(
        allow_null=True,
        help_text="最后同步时间"
    )
    error = serializers.CharField(
        allow_null=True,
        allow_blank=True,
        help_text="错误信息"
    )
    paused = serializers.BooleanField(help_text="是否已取消同步")


class SyncResponseSerializer(serializers.Serializer):
    """同步响应序列化器"""

    status = serializers.CharField(help_text="同步结果状态")
    message = serializers.CharField(help_text="同步结果消息")
    synced_at = serializers.DateTimeField(
        allow_null=True,
        help_text="同步完成时间"
    )
    error = serializers.CharField(
        required=False,
        help_text="错误详情"
    )
    imported_accounts = serializers.ListField(
        child=serializers.CharField(),
        required=False,
        default=list,
        help_text="本次同步自动导入的平台账户",
    )


class WebhookPayloadSerializer(serializers.Serializer):
    """GitHub / GitLab / Gitea / Gogs push Webhook 载荷（宽松校验，具体分支在视图中比对）。"""

    ref = serializers.CharField(required=False, allow_blank=True)
    repository = serializers.DictField(required=False)
    project = serializers.DictField(required=False)
    pusher = serializers.DictField(required=False, allow_null=True)
    commits = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        default=list,
    )


class DeployKeyResponseSerializer(serializers.Serializer):
    """Deploy Key 响应序列化器"""

    filename = serializers.CharField(help_text="文件名")
    content_type = serializers.CharField(help_text="内容类型")
    message = serializers.CharField(help_text="操作消息")
    key_id = serializers.IntegerField(
        required=False,
        help_text="密钥ID"
    )


class DeleteRepositoryResponseSerializer(serializers.Serializer):
    """删除仓库响应序列化器"""

    message = serializers.CharField(help_text="删除结果消息")
    cleaned_files = serializers.ListField(
        child=serializers.CharField(),
        help_text="已清理的文件列表"
    )


class ClearSyncedLedgerResponseSerializer(serializers.Serializer):
    """取消同步响应序列化器"""

    message = serializers.CharField(help_text="操作结果消息")
    cleaned_files = serializers.ListField(
        child=serializers.CharField(),
        help_text="已清理的文件/目录列表"
    )
    trans_preserved = serializers.BooleanField(
        help_text="是否保留了 trans/ 解析结果"
    )
    repo_name = serializers.CharField(help_text="仓库目录名（未变更）")


class LedgerCommitPlanSerializer(serializers.Serializer):
    """单个月度文件的迁移计划"""

    year = serializers.IntegerField(help_text="年度")
    month = serializers.IntegerField(help_text="月份（1-12）")
    target = serializers.CharField(help_text="目标月度文件相对路径，如 2025/05.bean")
    count = serializers.IntegerField(help_text="将写入该文件的条目数")


class LedgerCommitErrorSerializer(serializers.Serializer):
    """trans/ 文件解析错误"""

    file = serializers.CharField(help_text="相对 trans/ 的文件路径")
    error = serializers.CharField(help_text="错误信息")


class LedgerCommitPreviewSerializer(serializers.Serializer):
    """迁移预览响应序列化器"""

    total_entries = serializers.IntegerField(help_text="扫描到的条目总数")
    files_scanned = serializers.IntegerField(help_text="扫描的文件数")
    plans = LedgerCommitPlanSerializer(many=True, help_text="各月度文件迁移计划")
    errors = LedgerCommitErrorSerializer(many=True, help_text="解析失败的文件")


class LedgerPushResultSerializer(serializers.Serializer):
    """推送结果序列化器"""

    status = serializers.CharField(help_text="推送状态：success / skipped")
    message = serializers.CharField(help_text="结果消息")
    commit = serializers.CharField(required=False, allow_null=True, help_text="提交短哈希")
    files = serializers.ListField(
        child=serializers.CharField(), required=False, help_text="本次提交的文件"
    )


class LedgerCommitResultSerializer(serializers.Serializer):
    """迁移提交响应序列化器"""

    status = serializers.CharField(help_text="结果状态：success / skipped")
    message = serializers.CharField(help_text="结果消息")
    plans = LedgerCommitPlanSerializer(many=True, help_text="各月度文件迁移计划")
    entries_appended = serializers.IntegerField(help_text="写入的条目数")
    files_cleared = serializers.IntegerField(help_text="清空的 trans/ 文件数")
    push = LedgerPushResultSerializer(
        required=False, allow_null=True, help_text="推送结果"
    )
