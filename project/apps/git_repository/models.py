from django.db import models
from django.conf import settings
from django.contrib.auth import get_user_model
from project.models import BaseModel

User = get_user_model()


class GitRepository(BaseModel):
    """平台托管的用户 Git 仓库

    设计说明：
    - 每个用户只能有一个 Git 仓库（OneToOneField）
    - 仓库由平台在 Gitea 上创建和管理
    - 用户通过 Deploy Key 进行 Git 操作
    """

    # 模式信息
    setup_mode = models.CharField(
        max_length=20,
        default='create',
        choices=[
            ('create', '创建远程'),
            ('link', '关联远程'),
        ],
        help_text="启用 Git 时的模式：create=创建远程，link=关联已有远程"
    )
    provider = models.CharField(
        max_length=20,
        default='gitea_hosted',
        choices=[
            ('gitea_hosted', '平台托管 Gitea'),
            ('github', 'GitHub'),
            ('gitlab', 'GitLab'),
            ('gitea', 'Gitea'),
            ('gogs', 'Gogs'),
            ('other', '其他'),
        ],
        help_text="远程代码托管平台"
    )

    # 基本信息
    owner = models.OneToOneField(
        User, 
        on_delete=models.CASCADE, 
        related_name='git_repo',
        help_text="仓库所有者"
    )
    repo_name = models.CharField(
        max_length=200, 
        help_text="仓库名称，如 {uuid}-assets"
    )
    remote_ssh_url = models.CharField(
        max_length=500,
        blank=True,
        default='',
        help_text="远程仓库 SSH 地址（用于 clone/pull）"
    )
    external_full_name = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text="外部仓库全名，如 owner/repo，用于 webhook 匹配"
    )
    default_branch = models.CharField(
        max_length=100,
        default='main',
        help_text="默认同步分支"
    )
    gitea_repo_id = models.IntegerField(
        null=True,
        blank=True,
        help_text="Gitea 仓库 ID，用于 API 调用"
    )

    # 创建方式
    created_with_template = models.BooleanField(
        default=True,
        help_text="是否基于模板创建：True=基于模板，False=空仓库"
    )

    # Deploy Key 信息
    deploy_key_id = models.IntegerField(
        null=True, blank=True,
        help_text="Gitea Deploy Key ID"
    )
    deploy_key_private = models.TextField(
        help_text="Deploy Key 私钥（加密存储）"
    )
    deploy_key_public = models.TextField(
        help_text="Deploy Key 公钥"
    )
    webhook_secret = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text="Webhook 验签密钥（每仓独立）"
    )

    # 同步状态
    last_sync_at = models.DateTimeField(
        null=True, blank=True, 
        help_text="最后一次从仓库拉取时间"
    )
    sync_status = models.CharField(
        max_length=20, 
        default='pending', 
        choices=[
            ('pending', '待同步'), 
            ('syncing', '同步中'), 
            ('success', '成功'), 
            ('failed', '失败')
        ],
        help_text="同步状态"
    )
    sync_error = models.TextField(
        blank=True, 
        help_text="最后一次同步错误信息"
    )
    sync_paused = models.BooleanField(
        default=False,
        help_text="是否已取消同步（暂停自动拉取）"
    )
    auto_import_ledger_accounts = models.BooleanField(
        default=False,
        help_text="同步后是否自动导入账本中显式 open、平台缺失的账户"
    )

    class Meta:
        db_table = 'git_repository'
        verbose_name = 'Git 仓库'
        verbose_name_plural = 'Git 仓库'

    def __str__(self):
        return f"{self.owner.username} - {self.repo_name}"

    @property
    def ssh_clone_url(self):
        """获取 SSH clone 地址"""
        if self.remote_ssh_url:
            return self.remote_ssh_url.strip()
        gitea_ssh_base = getattr(
            settings,
            'GITEA_SSH_BASE',
            'ssh://git@gitea.dhr2333.cn:30022',
        ).rstrip('/')
        return f"{gitea_ssh_base}/beancount-trans/{self.repo_name}.git"