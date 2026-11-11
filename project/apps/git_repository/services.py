import os
import secrets
import shutil
import tempfile
import logging
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from .models import GitRepository
from .clients import GiteaAPIClient, GiteaAPIException
from .git_remote import guess_external_full_name_from_ssh, guess_provider_from_ssh

User = get_user_model()
logger = logging.getLogger(__name__)


class GitServiceException(Exception):
    """Git 服务异常"""
    pass


class PlatformGitService:
    """Git 同步服务：平台托管 Gitea 与关联外部远程（MVP：用户手动建库 + SSH Deploy Key）。"""

    def __init__(self):
        self._gitea_client: Optional[GiteaAPIClient] = None
        self.assets_base_path = Path(settings.BASE_DIR) / 'Assets'

    def _get_gitea_client(self) -> GiteaAPIClient:
        if self._gitea_client is None:
            self._gitea_client = GiteaAPIClient()
        return self._gitea_client

    def _pick_unique_local_repo_name(self) -> str:
        import uuid
        for _ in range(10):
            name = f"{uuid.uuid4().hex[:6]}-assets"
            if not GitRepository.objects.filter(repo_name=name).exists():
                return name
        raise GitServiceException("无法生成唯一的本地目录名，请稍后重试")

    def _resolve_external_full_name(self, remote_ssh_url: str, external_full_name: str) -> str:
        name = (external_full_name or '').strip()
        if not name:
            name = guess_external_full_name_from_ssh(remote_ssh_url)
        return name.strip()

    def link_external_repository(
        self,
        user: User,
        remote_ssh_url: str,
        provider: str,
        default_branch: str = 'main',
        external_full_name: str = '',
    ) -> GitRepository:
        """关联用户已在远程 Git 平台上的远程仓库（仅平台侧生成密钥与 Webhook secret，不调用第三方 API）。

        provider 为空时按 SSH 地址自动识别，支持 GitHub / GitLab / Gitea / Gogs / 自建等任意远程。
        """
        if hasattr(user, 'git_repo'):
            raise GitServiceException("用户已有 Git 仓库")
        remote_ssh_url = (remote_ssh_url or '').strip()
        if not remote_ssh_url:
            raise GitServiceException("请提供 remote_ssh_url")
        provider = (provider or '').strip() or guess_provider_from_ssh(remote_ssh_url)
        full_name = self._resolve_external_full_name(remote_ssh_url, external_full_name)
        if not full_name:
            raise GitServiceException("无法解析仓库全名，请填写 external_full_name 或检查 SSH URL 格式")
        if GitRepository.objects.filter(external_full_name__iexact=full_name).exists():
            raise GitServiceException("该远程仓库已被其他账号关联")

        repo_name = self._pick_unique_local_repo_name()
        user_assets_path = self.assets_base_path / user.username
        user_assets_path.rename(self.assets_base_path / repo_name)
        private_key, public_key = self._generate_ssh_key_pair()
        wh_secret = secrets.token_hex(32)

        git_repo = GitRepository.objects.create(
            owner=user,
            repo_name=repo_name,
            setup_mode='link',
            provider=provider,
            remote_ssh_url=remote_ssh_url,
            external_full_name=full_name,
            default_branch=default_branch or 'main',
            created_with_template=False,
            gitea_repo_id=None,
            deploy_key_id=None,
            deploy_key_private=private_key,
            deploy_key_public=public_key,
            webhook_secret=wh_secret,
            sync_status='pending',
        )
        logger.info(f"Linked external Git repo for user {user.username}: {full_name}")
        return git_repo

    def bootstrap_external_repository(
        self,
        user: User,
        remote_ssh_url: str,
        provider: str,
        use_template: bool,
        default_branch: str = 'main',
        external_full_name: str = '',
    ) -> GitRepository:
        """用户在托管方已手动创建空仓库后，绑定远程并可选择推送模板（需 Deploy Key 写权限）。

        注意：POST /api/git/repository/ 已不再调用本方法；已有外部远程请走 link_external_repository。
        本方法保留供扩展、脚本或运维场景使用。
        """
        if hasattr(user, 'git_repo'):
            raise GitServiceException("用户已有 Git 仓库")
        remote_ssh_url = (remote_ssh_url or '').strip()
        if not remote_ssh_url:
            raise GitServiceException("请提供 remote_ssh_url")
        full_name = self._resolve_external_full_name(remote_ssh_url, external_full_name)
        if not full_name:
            raise GitServiceException("无法解析仓库全名，请填写 external_full_name 或检查 SSH URL 格式")
        if GitRepository.objects.filter(external_full_name__iexact=full_name).exists():
            raise GitServiceException("该远程仓库已被其他账号关联")

        repo_name = self._pick_unique_local_repo_name()
        user_assets_path = self.assets_base_path / user.username
        user_assets_path.rename(self.assets_base_path / repo_name)
        private_key, public_key = self._generate_ssh_key_pair()
        wh_secret = secrets.token_hex(32)

        git_repo = GitRepository.objects.create(
            owner=user,
            repo_name=repo_name,
            setup_mode='create',
            provider=provider,
            remote_ssh_url=remote_ssh_url,
            external_full_name=full_name,
            default_branch=default_branch or 'main',
            created_with_template=use_template,
            gitea_repo_id=None,
            deploy_key_id=None,
            deploy_key_private=private_key,
            deploy_key_public=public_key,
            webhook_secret=wh_secret,
            sync_status='pending',
        )
        if use_template:
            self._initialize_repository_with_template(git_repo)
        logger.info(f"Bootstrapped external Git repo for user {user.username}: {full_name}")
        return git_repo

    def create_user_repository(self, user: User, use_template: bool = True) -> GitRepository:
        """为用户创建 Git 仓库

        Args:
            user: 用户对象
            use_template: 是否基于模板初始化
                - True: 基于 Beancount-Trans-Assets 模板创建
                - False: 创建空仓库，等待用户推送

        流程：
        1. 检查用户是否已有仓库
        2. 调用 Gitea API 创建仓库
        3. 用户目录重命名，从 Assets/{username} 重命名到 Assets/{repo_name}
        4. 生成 SSH 密钥对
        5. 添加 Deploy Key 到仓库
        6. 配置 Webhook
        7. 如果 use_template=True，初始化仓库内容
        8. 保存仓库信息到数据库

        Returns:
            GitRepository 实例
        """
        # 检查用户是否已有仓库
        if hasattr(user, 'git_repo'):
            raise GitServiceException("用户已有 Git 仓库")

        # 生成唯一的仓库名称（检查冲突）
        import uuid
        max_attempts = 10  # 最多尝试 10 次
        repo_name = None
        
        for attempt in range(max_attempts):
            repo_id = uuid.uuid4().hex[:6]
            candidate_name = f"{repo_id}-assets"
            
            # 检查数据库中是否已存在
            if not GitRepository.objects.filter(repo_name=candidate_name).exists():
                # 检查 Gitea 上是否已存在
                try:
                    # 尝试获取仓库信息，如果不存在会抛出异常
                    self._get_gitea_client().get_repository(candidate_name)
                    # 如果成功获取，说明已存在，继续尝试
                    logger.warning(f"Repository {candidate_name} already exists in Gitea, retrying...")
                    continue
                except GiteaAPIException:
                    # 仓库不存在，可以使用
                    repo_name = candidate_name
                    break
        
        if not repo_name:
            raise GitServiceException("无法生成唯一的仓库名称，请稍后重试")
        
        try:
            # 1. 创建 Gitea 仓库
            logger.info(f"Creating Gitea repository for user {user.username}: {repo_name}")
            repo_data = self._get_gitea_client().create_repository(
                repo_name=repo_name,
                description=f"Beancount account book for {user.username}",
                private=True
            )

            # 2. 用户目录重命名，从 Assets/{username} 重命名到 Assets/{repo_name}
            user_assets_path = self.assets_base_path / user.username
            user_assets_path.rename(self.assets_base_path / repo_name)

            # 2. 生成 SSH 密钥对
            private_key, public_key = self._generate_ssh_key_pair()

            # 3. 添加 Deploy Key
            deploy_key_data = self._get_gitea_client().add_deploy_key(
                repo_name=repo_name,
                title=f"Platform Deploy Key - {user.username}",
                public_key=public_key,
                read_only=False  # 需要写权限
            )

            # 4. 配置 Webhook (如果有配置)
            if settings.GITEA_WEBHOOK_SECRET:
                try:
                    webhook_url = f"https://{settings.BASE_URL}/api/git/webhook/"
                    self._get_gitea_client().create_webhook(
                        repo_name=repo_name,
                        webhook_url=webhook_url
                    )
                    logger.info(f"Webhook created for {repo_name}")
                except GiteaAPIException as e:
                    logger.warning(f"Failed to create webhook for {repo_name}: {e}")
                    # 不阻断流程，Webhook 可以后续手动配置

            # 5. 创建数据库记录
            git_repo = GitRepository.objects.create(
                owner=user,
                repo_name=repo_name,
                setup_mode='create',
                provider='gitea_hosted',
                default_branch='main',
                gitea_repo_id=repo_data['id'],
                created_with_template=use_template,
                deploy_key_id=deploy_key_data['id'],
                deploy_key_private=private_key,
                deploy_key_public=public_key,
                sync_status='pending'
            )

            # 6. 如果基于模板创建，初始化仓库内容
            if use_template:
                self._initialize_repository_with_template(git_repo)

            logger.info(f"Successfully created Git repository for user {user.username}")
            return git_repo

        except Exception as e:
            logger.error(f"Failed to create repository for user {user.username}: {e}")
            # 清理已创建的资源
            try:
                self._get_gitea_client().delete_repository(repo_name)
            except:
                pass

            # 清理数据库记录（如果已创建）
            if 'git_repo' in locals():
                try:
                    git_repo.delete()
                    logger.info(f"Cleaned up database record for {repo_name}")
                except:
                    pass

            raise GitServiceException(f"创建仓库失败: {e}")

    def sync_repository(self, user: User, *, manual: bool = False) -> Dict[str, Any]:
        """从远程仓库同步到本地

        核心处理逻辑：
        1. 备份 trans/ 目录下所有解析文件
        2. 执行 git fetch + git reset --hard origin/main（以远程为准）
        3. 恢复备份的解析文件到 trans/ 目录

        冲突处理：始终以远程仓库为准，平台本地修改会被覆盖

        Args:
            user: 用户对象
            manual: 是否为用户手动触发的同步。手动同步视为显式重新授权，
                会解除「取消同步」状态并执行拉取；自动同步（Webhook）
                在已取消同步时直接跳过

        Returns:
            同步结果信息
        """
        try:
            git_repo = user.git_repo
        except GitRepository.DoesNotExist:
            raise GitServiceException("用户未启用 Git 功能")

        # 已取消同步：自动拉取（Webhook）直接跳过；手动同步视为显式恢复
        if git_repo.sync_paused:
            if not manual:
                logger.info(
                    f"Skipped sync for user {user.username}: sync cancelled"
                )
                return {
                    'status': 'paused',
                    'message': '同步已取消，已跳过拉取',
                }
            git_repo.sync_paused = False
            git_repo.save()

        # 更新同步状态
        git_repo.sync_status = 'syncing'
        git_repo.sync_error = ''
        git_repo.save()

        user_assets_path = self.assets_base_path / git_repo.repo_name

        try:
            # 1. 备份 trans/ 目录
            trans_backup = None
            trans_path = user_assets_path / 'trans'
            had_local_trans = trans_path.exists()
            if had_local_trans:
                trans_backup = self._backup_trans_directory(trans_path)
                logger.info(f"Backed up trans/ directory for user {user.username}")

            # 2. 检查是否为初次克隆
            git_path = user_assets_path / '.git'
            if not git_path.exists():
                # 先克隆到临时目录，成功后再替换用户目录。切勿在 clone 成功前 rmtree 用户路径，
                # 否则 ls-remote/clone 失败（如未配置 Deploy Key）会导致本地账本被删。
                self.assets_base_path.mkdir(parents=True, exist_ok=True)
                tmp_clone = Path(
                    tempfile.mkdtemp(
                        prefix=f"clone-{git_repo.repo_name}-",
                        dir=str(self.assets_base_path),
                    )
                )
                try:
                    self._clone_repository(git_repo, tmp_clone)
                except Exception:
                    shutil.rmtree(tmp_clone, ignore_errors=True)
                    raise

                if (tmp_clone / '.git').exists():
                    backup_path: Optional[Path] = None
                    if user_assets_path.exists():
                        backup_path = user_assets_path.parent / (
                            f"{user_assets_path.name}.presync-{secrets.token_hex(6)}"
                        )
                        user_assets_path.rename(backup_path)
                    try:
                        tmp_clone.rename(user_assets_path)
                    except Exception:
                        if backup_path is not None and backup_path.exists():
                            backup_path.rename(user_assets_path)
                        shutil.rmtree(tmp_clone, ignore_errors=True)
                        raise
                    if backup_path is not None and backup_path.exists():
                        shutil.rmtree(backup_path, ignore_errors=True)
                else:
                    # 远程空仓库：不替换本地，仅清理临时目录
                    shutil.rmtree(tmp_clone, ignore_errors=True)
            else:
                # 更新现有仓库 - 确保目录存在
                # user_assets_path.mkdir(parents=True, exist_ok=True)

                # 更新现有仓库
                self._pull_repository(git_repo, user_assets_path)

            # 3. 恢复 trans/ 目录
            if trans_backup:
                self._restore_trans_directory(trans_backup, user_assets_path / 'trans')
                logger.info(f"Restored trans/ directory for user {user.username}")

            # 4. 重建 trans/main.bean
            # self._rebuild_trans_main_bean(user_assets_path)

            # 4.1 远程可能带来新的账户 open，去重并记录残留错误（不中断同步）
            dedupe_result = self._dedupe_ledger_opens(user)
            ledger_warning = None
            if dedupe_result.residual_errors:
                ledger_warning = '账本仍有账户类错误：' + '; '.join(
                    error['message'] for error in dedupe_result.residual_errors
                )

            # 4.2 可选：把账本中显式 open、平台缺失的账户导入平台（默认开启）
            imported_accounts: list = []
            if git_repo.auto_import_ledger_accounts:
                from project.apps.account.account_reconcile import (
                    import_declared_accounts,
                )

                try:
                    import_result = import_declared_accounts(user)
                except Exception as e:
                    logger.error(
                        "自动导入账本账户失败 %s: %s", user.username, e
                    )
                    message = f'自动导入账本账户失败：{e}'
                    ledger_warning = (
                        f'{ledger_warning}；{message}' if ledger_warning else message
                    )
                else:
                    imported_accounts = import_result['created']
                    if imported_accounts:
                        logger.info(
                            "用户 %s 自动导入账本账户 %d 个：%s",
                            user.username,
                            len(imported_accounts),
                            ', '.join(imported_accounts),
                        )
                    if import_result['errors']:
                        message = (
                            '部分账本账户导入失败：'
                            + '; '.join(import_result['errors'])
                        )
                        ledger_warning = (
                            f'{ledger_warning}；{message}'
                            if ledger_warning
                            else message
                        )

            # 4.3 账本托管的账户币种以账本 open 为准，同步回平台
            updated_account_currencies: list = []
            from project.apps.account.account_reconcile import (
                sync_declared_currencies,
            )

            try:
                currency_result = sync_declared_currencies(user)
            except Exception as e:
                logger.error(
                    "同步账本账户币种失败 %s: %s", user.username, e
                )
                message = f'同步账本账户币种失败：{e}'
                ledger_warning = (
                    f'{ledger_warning}；{message}' if ledger_warning else message
                )
            else:
                updated_account_currencies = currency_result['updated']
                if updated_account_currencies:
                    logger.info(
                        "用户 %s 从账本同步账户币种 %d 个：%s",
                        user.username,
                        len(updated_account_currencies),
                        ', '.join(
                            item['account'] for item in updated_account_currencies
                        ),
                    )

            if ledger_warning:
                git_repo.sync_error = ledger_warning[:1000]
            # 5. 更新同步状态
            git_repo.sync_status = 'success'
            git_repo.last_sync_at = timezone.now()
            git_repo.save()

            logger.info(f"Successfully synced repository for user {user.username}")

            return {
                'status': 'success',
                'message': '同步成功',
                'synced_at': git_repo.last_sync_at,
                'ledger_warning': ledger_warning,
                'imported_accounts': imported_accounts,
                'updated_account_currencies': updated_account_currencies,
            }

        except Exception as e:
            # 记录错误并更新状态
            error_msg = str(e)
            logger.error(f"Failed to sync repository for user {user.username}: {error_msg}")

            git_repo.sync_status = 'failed'
            git_repo.sync_error = error_msg
            git_repo.save()

            return {
                'status': 'failed',
                'message': f'同步失败: {error_msg}',
                'error': error_msg
            }

    DEFAULT_LEDGER_COMMIT_MESSAGE = 'chore(ledger): 提交平台解析条目到月度账本'

    @staticmethod
    def _rev_short() -> str:
        return subprocess.run(
            ['git', 'rev-parse', '--short', 'HEAD'],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

    def _push_pending_repo(self, env, branch: str) -> Dict[str, Any]:
        """在已切换到仓库目录的前提下，推送本地未推送提交

        - 先 fetch 刷新 origin 引用；
        - 无未推送提交时返回 skipped；
        - 有提交时 pull --rebase 后再 push；rebase 失败会 abort，避免仓库停留在中间态。
        """
        subprocess.run(
            ['git', 'fetch', 'origin'],
            env=env, check=True, capture_output=True, text=True,
        )

        remote_ref = f'origin/{branch}'
        has_remote = subprocess.run(
            ['git', 'rev-parse', '--verify', '--quiet', remote_ref],
            capture_output=True, text=True,
        ).returncode == 0

        count_cmd = (
            ['git', 'rev-list', '--count', f'{remote_ref}..HEAD']
            if has_remote
            else ['git', 'rev-list', '--count', 'HEAD']
        )
        count_result = subprocess.run(count_cmd, capture_output=True, text=True)
        ahead = int(count_result.stdout.strip() or 0) if count_result.returncode == 0 else 0
        if ahead == 0:
            return {'status': 'skipped', 'message': '没有需要推送的账本变更'}

        if has_remote:
            try:
                subprocess.run(
                    ['git', 'pull', '--rebase', 'origin', branch],
                    env=env, check=True, capture_output=True, text=True,
                )
            except subprocess.CalledProcessError:
                subprocess.run(
                    ['git', 'rebase', '--abort'],
                    capture_output=True, text=True,
                )
                raise

        subprocess.run(
            ['git', 'push', 'origin', branch],
            env=env, check=True, capture_output=True, text=True,
        )
        return {'status': 'success', 'message': '已推送到远程仓库'}

    def push_ledger(
        self,
        user: User,
        paths: Optional[list] = None,
        commit_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        """把平台写入的账本内容提交并推送到远程（仅 Git 用户）

        流程：git add → commit（如有变更）→ fetch → 判断未推送提交 → pull --rebase → push。
        没有暂存变更但存在本地未推送提交时，也会补推，便于推送失败后重试。

        Args:
            user: 用户对象
            paths: 需要 add 的相对路径列表（相对仓库根）；为空时提交全部变更
            commit_message: 自定义提交信息；为空时使用默认文案

        Returns:
            Dict[str, Any]: 含 status（success/skipped）、message、commit、files

        Raises:
            GitServiceException: 未启用 Git、仓库未初始化或 Git 操作失败
        """
        try:
            git_repo = user.git_repo
        except GitRepository.DoesNotExist:
            raise GitServiceException("用户未启用 Git 功能")

        user_assets_path = self.assets_base_path / git_repo.repo_name
        if not (user_assets_path / '.git').exists():
            raise GitServiceException("本地仓库尚未初始化，请先执行「立即同步」")

        ssh_key_file = self._prepare_ssh_key(git_repo)
        original_cwd = os.getcwd()

        try:
            env = os.environ.copy()
            env['GIT_SSH_COMMAND'] = f'ssh -i {ssh_key_file} -o StrictHostKeyChecking=no'

            os.chdir(user_assets_path)
            branch = (git_repo.default_branch or 'main').strip() or 'main'

            subprocess.run(
                ['git', 'config', 'user.name', 'Beancount-Trans Platform'],
                check=True, capture_output=True, text=True,
            )
            subprocess.run(
                ['git', 'config', 'user.email', 'platform@beancount-trans.local'],
                check=True, capture_output=True, text=True,
            )

            if paths:
                subprocess.run(['git', 'add', '--', *paths], check=True, capture_output=True, text=True)
            else:
                subprocess.run(['git', 'add', '-A'], check=True, capture_output=True, text=True)

            staged = subprocess.run(
                ['git', 'diff', '--cached', '--name-only'],
                check=True, capture_output=True, text=True,
            ).stdout.strip()

            if staged:
                subprocess.run(
                    ['git', 'commit', '-m', commit_message or self.DEFAULT_LEDGER_COMMIT_MESSAGE],
                    check=True, capture_output=True, text=True,
                )

            push_result = self._push_pending_repo(env, branch)
            if push_result['status'] == 'skipped':
                return {
                    'status': 'skipped',
                    'message': '没有需要提交或推送的账本变更',
                    'files': [],
                }

            commit_hash = self._rev_short()
            logger.info(f"Pushed ledger for user {user.username}: {commit_hash}")
            return {
                'status': 'success',
                'message': '已提交并推送到远程仓库' if staged else '已推送到远程仓库',
                'commit': commit_hash,
                'files': staged.splitlines(),
            }

        except subprocess.CalledProcessError as e:
            err = (e.stderr or e.stdout or '').strip() or str(e)
            logger.error(f"Failed to push ledger for user {user.username}: {err[:800]}")
            raise GitServiceException(f"推送失败: {err[:800]}")
        except GitServiceException:
            raise
        except Exception as e:
            logger.error(f"Failed to push ledger for user {user.username}: {e}")
            raise GitServiceException(f"推送失败: {e}")
        finally:
            os.chdir(original_cwd)
            if os.path.exists(ssh_key_file):
                os.remove(ssh_key_file)

    def push_pending(self, user: User) -> Dict[str, Any]:
        """只推送本地已有提交，不执行 git add / commit

        用于「提交到账本」在 trans/ 无新条目时补推此前失败的币种改动。
        """
        try:
            git_repo = user.git_repo
        except GitRepository.DoesNotExist:
            raise GitServiceException("用户未启用 Git 功能")

        user_assets_path = self.assets_base_path / git_repo.repo_name
        if not (user_assets_path / '.git').exists():
            raise GitServiceException("本地仓库尚未初始化，请先执行「立即同步」")

        ssh_key_file = self._prepare_ssh_key(git_repo)
        original_cwd = os.getcwd()

        try:
            env = os.environ.copy()
            env['GIT_SSH_COMMAND'] = f'ssh -i {ssh_key_file} -o StrictHostKeyChecking=no'

            os.chdir(user_assets_path)
            branch = (git_repo.default_branch or 'main').strip() or 'main'

            push_result = self._push_pending_repo(env, branch)
            if push_result['status'] == 'skipped':
                return {
                    'status': 'skipped',
                    'message': '没有需要推送的账本变更',
                    'files': [],
                }

            commit_hash = self._rev_short()
            logger.info(f"Pushed pending ledger for user {user.username}: {commit_hash}")
            return {
                'status': 'success',
                'message': '已推送到远程仓库',
                'commit': commit_hash,
                'files': [],
            }

        except subprocess.CalledProcessError as e:
            err = (e.stderr or e.stdout or '').strip() or str(e)
            logger.error(f"Failed to push pending ledger for user {user.username}: {err[:800]}")
            raise GitServiceException(f"推送失败: {err[:800]}")
        except GitServiceException:
            raise
        except Exception as e:
            logger.error(f"Failed to push pending ledger for user {user.username}: {e}")
            raise GitServiceException(f"推送失败: {e}")
        finally:
            os.chdir(original_cwd)
            if os.path.exists(ssh_key_file):
                os.remove(ssh_key_file)

    def regenerate_deploy_key(self, user: User) -> Dict[str, str]:
        """重新生成 Deploy Key

        流程：
        1. 生成新的 SSH 密钥对
        2. 删除 Gitea 上的旧 Deploy Key
        3. 添加新的 Deploy Key
        4. 更新数据库记录

        Returns:
            包含新密钥信息的字典
        """
        try:
            git_repo = user.git_repo
        except GitRepository.DoesNotExist:
            raise GitServiceException("用户未启用 Git 功能")

        try:
            # 1. 生成新的密钥对
            private_key, public_key = self._generate_ssh_key_pair()

            new_key_id = None
            if git_repo.provider == 'gitea_hosted' and git_repo.gitea_repo_id is not None:
                # 2. 删除旧的 Deploy Key（如果存在）
                if git_repo.deploy_key_id:
                    try:
                        self._get_gitea_client().delete_deploy_key(git_repo.repo_name, git_repo.deploy_key_id)
                    except GiteaAPIException as e:
                        logger.warning(f"Failed to delete old deploy key: {e}")

                # 3. 添加新的 Deploy Key
                deploy_key_data = self._get_gitea_client().add_deploy_key(
                    repo_name=git_repo.repo_name,
                    title=f"Platform Deploy Key - {user.username} (Regenerated)",
                    public_key=public_key,
                    read_only=False
                )
                new_key_id = deploy_key_data['id']
            else:
                # 外部远程：请在托管平台手动移除旧 Deploy Key 并添加新公钥
                git_repo.deploy_key_id = None

            git_repo.deploy_key_private = private_key
            git_repo.deploy_key_public = public_key
            if new_key_id is not None:
                git_repo.deploy_key_id = new_key_id
            git_repo.save()

            logger.info(f"Successfully regenerated deploy key for user {user.username}")

            return {
                'private_key': private_key,
                'public_key': public_key,
                'key_id': new_key_id
            }

        except Exception as e:
            logger.error(f"Failed to regenerate deploy key for user {user.username}: {e}")
            raise GitServiceException(f"重新生成 Deploy Key 失败: {e}")

    def _generate_ssh_key_pair(self) -> tuple[str, str]:
        """生成 SSH 密钥对

        Returns:
            (private_key_pem, public_key_openssh) 元组
        """
        # 生成 RSA 密钥对
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=4096
        )

        # 序列化私钥为 PEM 格式
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        ).decode('utf-8')

        # 序列化公钥为 OpenSSH 格式
        public_key = private_key.public_key()
        public_openssh = public_key.public_bytes(
            encoding=serialization.Encoding.OpenSSH,
            format=serialization.PublicFormat.OpenSSH
        ).decode('utf-8')

        return private_pem, public_openssh

    def _initialize_repository_with_template(self, git_repo: GitRepository):
        """使用 Beancount-Trans-Assets 模板初始化仓库

        流程：
        1. 从 GitHub 获取模板仓库内容
        2. 为用户定制模板内容
        3. 在用户目录中初始化 git 仓库、提交并推送
        """
        from .template_service import GitHubTemplateService, TemplateServiceException

        try:
            logger.info(f"开始为仓库 {git_repo.repo_name} 初始化模板内容")

            # 1. 获取模板内容
            template_service = GitHubTemplateService()
            template_dir = template_service.fetch_template_content()

            try:
                # 2. 为用户定制模板内容
                template_service.customize_template_for_user(template_dir, git_repo.owner)

                # 3. 在用户目录中初始化 git 仓库、提交并推送
                self._init_and_push_template_in_user_directory(git_repo, template_dir)

                logger.info(f"成功为仓库 {git_repo.repo_name} 初始化模板内容")

            finally:
                # 清理临时目录
                import shutil
                shutil.rmtree(template_dir, ignore_errors=True)

        except TemplateServiceException as e:
            logger.error(f"模板初始化失败: {e}")
            raise GitServiceException(f"模板初始化失败: {e}")
        except Exception as e:
            logger.error(f"模板初始化过程中发生未知错误: {e}")
            raise GitServiceException(f"模板初始化失败: {e}")

    def _dedupe_ledger_opens(self, user: User, *, strict: bool = False):
        """删除平台账本文件中与其它生效账本文件重复的 open

        Args:
            user: 用户对象
            strict: 为 True 时若仍存在账户类错误则抛 GitServiceException，
                用于模板初始化，避免生成一个 Beancount 加载报错的仓库

        Returns:
            DedupeResult
        """
        from project.apps.account.ledger_dedupe import (
            DedupeResult,
            dedupe_platform_opens,
        )

        try:
            result = dedupe_platform_opens(user)
        except Exception as e:
            logger.error(
                "账本 open 去重失败 %s: %s", getattr(user, 'username', user), e
            )
            if strict:
                raise GitServiceException(f"账本去重失败: {e}")
            return DedupeResult(False, [], [], [])

        if result.removed_opens:
            logger.info(
                "用户 %s 账本去重：删除重复 open %d 条",
                getattr(user, 'username', user),
                len(result.removed_opens),
            )

        if result.residual_errors:
            messages = '; '.join(
                error['message'] for error in result.residual_errors
            )
            logger.error(
                "用户 %s 账本仍有账户类错误: %s",
                getattr(user, 'username', user),
                messages,
            )
            if strict:
                raise GitServiceException(
                    f"账本初始化后仍有账户类错误：{messages[:800]}"
                )

        return result

    def _clone_repository(self, git_repo: GitRepository, target_path: Path):
        """克隆仓库到本地"""
        # 准备 SSH 密钥
        ssh_key_file = self._prepare_ssh_key(git_repo)

        try:
            # 使用 SSH 克隆
            clone_url = git_repo.ssh_clone_url

            # 设置 GIT_SSH_COMMAND 环境变量
            env = os.environ.copy()
            env['GIT_SSH_COMMAND'] = f'ssh -i {ssh_key_file} -o StrictHostKeyChecking=no'

            # 检查远程仓库是否为空
            ls_remote_result = subprocess.run([
                'git', 'ls-remote', '--heads', '--tags', clone_url
            ], env=env, capture_output=True, text=True)

            # ls-remote 失败（认证/网络等）与「远程无提交」必须区分：前者应报错，不能当作空仓库静默跳过
            if ls_remote_result.returncode != 0:
                err = (ls_remote_result.stderr or "").strip() or (
                    f"git ls-remote 退出码 {ls_remote_result.returncode}"
                )
                logger.error(
                    "git ls-remote failed for %s: %s",
                    git_repo.repo_name,
                    err[:500],
                )
                raise GitServiceException(
                    "无法访问远程仓库，请检查 Deploy Key 是否已添加到托管平台且具备读权限。"
                    f" 详情: {err[:800]}"
                )

            if not ls_remote_result.stdout.strip():
                logger.info(f"Repository {git_repo.repo_name} is empty, skipping clone")
                return

            # 仓库不为空，进行克隆
            clone_result = subprocess.run(
                ['git', 'clone', clone_url, str(target_path)],
                env=env,
                capture_output=True,
                text=True,
            )
            clone_result.check_returncode()

            logger.info(f"Cloned repository {git_repo.repo_name} to {target_path}")

        finally:
            # 清理临时 SSH 密钥文件
            if os.path.exists(ssh_key_file):
                os.remove(ssh_key_file)

    def _pull_repository(self, git_repo: GitRepository, repo_path: Path):
        """更新现有仓库"""
        ssh_key_file = self._prepare_ssh_key(git_repo)

        try:
            # 设置环境变量
            env = os.environ.copy()
            env['GIT_SSH_COMMAND'] = f'ssh -i {ssh_key_file} -o StrictHostKeyChecking=no'

            # 切换到仓库目录
            original_cwd = os.getcwd()
            os.chdir(repo_path)

            try:
                # fetch 最新内容
                subprocess.run(['git', 'fetch', 'origin'], env=env, check=True)

                branch = (git_repo.default_branch or 'main').strip() or 'main'
                # 采用 rebase 拉取：平台可能已有本地提交（月度账本），reset --hard 会将其抹除
                subprocess.run(
                    ['git', 'pull', '--rebase', 'origin', branch],
                    env=env, check=True,
                )

                logger.info(f"Updated repository {git_repo.repo_name}")

            finally:
                os.chdir(original_cwd)

        finally:
            if os.path.exists(ssh_key_file):
                os.remove(ssh_key_file)

    def _prepare_ssh_key(self, git_repo: GitRepository) -> str:
        """准备临时 SSH 密钥文件"""
        # 创建临时文件
        fd, ssh_key_file = tempfile.mkstemp(suffix='.pem')

        try:
            # 写入私钥内容
            with os.fdopen(fd, 'w') as f:
                f.write(git_repo.deploy_key_private)

            # 设置文件权限
            os.chmod(ssh_key_file, 0o600)

            return ssh_key_file

        except:
            os.close(fd)
            if os.path.exists(ssh_key_file):
                os.remove(ssh_key_file)
            raise

    def _backup_trans_directory(self, trans_path: Path) -> str:
        """备份 trans/ 目录"""
        backup_dir = tempfile.mkdtemp(prefix='trans_backup_')
        shutil.copytree(trans_path, Path(backup_dir) / 'trans')
        return backup_dir

    def _restore_trans_directory(self, backup_dir: str, trans_path: Path):
        """恢复 trans/ 目录"""
        if trans_path.exists():
            shutil.rmtree(trans_path)

        backup_trans = Path(backup_dir) / 'trans'
        if backup_trans.exists():
            shutil.copytree(backup_trans, trans_path)

        # 清理备份
        shutil.rmtree(backup_dir)

    # def _rebuild_trans_main_bean(self, user_assets_path: Path):
    #     """重建 trans/main.bean 的 include 关系"""
    #     trans_path = user_assets_path / 'trans'
    #     trans_main_file = trans_path / 'main.bean'

    #     if not trans_path.exists():
    #         return

    #     # 收集所有 .bean 文件（除了 main.bean）
    #     bean_files = []
    #     for bean_file in trans_path.glob('*.bean'):
    #         if bean_file.name != 'main.bean':
    #             bean_files.append(bean_file.name)

    #     # 生成 include 语句
    #     if bean_files:
    #         bean_files.sort()  # 按字母顺序排序

    #         content = "; Trans directory - Auto-generated includes\n"
    #         content += "; This file is automatically generated by the Beancount-Trans\n\n"

    #         for bean_file in bean_files:
    #             content += f'include "{bean_file}"\n'

    #         # 写入文件
    #         trans_main_file.write_text(content, encoding='utf-8')
    #         logger.info(f"Rebuilt trans/main.bean with {len(bean_files)} includes")
    #     else:
    #         # 如果没有文件，创建空的 main.bean
    #         if not trans_main_file.exists():
    #             trans_main_file.write_text(
    #                 "; Trans directory - No parsed files yet\n",
    #                 encoding='utf-8'
    #             )

    def clear_synced_ledger(self, user: User) -> Dict[str, Any]:
        """清除服务器本地由 Git 同步引入的账本内容，恢复至启用 Git 前的形态

        流程：
        1. 删除 Assets/{repo_name}/ 下除 trans/ 之外的全部内容（含 .git/ 全量历史）
        2. 重建标准 main.bean
        3. 置 sync_paused=True，使后续 Webhook 不再自动拉取

        不修改任何既有 Git 同步配置（仓库记录、远端地址、Deploy Key、Webhook、
        默认分支、目录名、last_sync_at/sync_status/sync_error）。

        Args:
            user: 用户对象

        Returns:
            清除结果信息，含 cleaned_files / trans_preserved / repo_name
        """
        try:
            git_repo = user.git_repo
        except GitRepository.DoesNotExist:
            raise GitServiceException("用户未启用 Git 功能")

        user_assets_path = self.assets_base_path / git_repo.repo_name
        # 必须在清除前记录，清除后 trans/ 是唯一被保留的目录
        trans_preserved = (user_assets_path / 'trans').exists()

        try:
            if user_assets_path.exists():
                cleaned_files = self._cleanup_git_files_and_restore_structure(
                    user_assets_path, user
                )
            else:
                self._rebuild_standard_main_bean(user)
                cleaned_files = ['重建标准 main.bean']

            # 清除与暂停需同时生效：否则远端一次 push 即会把内容拉回
            git_repo.sync_paused = True
            git_repo.save()

            logger.info(f"Cleared synced ledger for user {user.username}")

            return {
                'message': '已清除服务器本地账本副本',
                'cleaned_files': cleaned_files,
                'trans_preserved': trans_preserved,
                'repo_name': git_repo.repo_name,
            }

        except Exception as e:
            logger.error(f"Failed to clear synced ledger for user {user.username}: {e}")
            raise GitServiceException(f"清除本地账本副本失败: {e}")

    def delete_user_repository(self, user: User) -> Dict[str, Any]:
        """删除用户 Git 仓库和相关资源

        流程：
        1. 删除 Gitea 远程仓库
        2. 删除用户目录除了 trans/ 外的全部内容，然后重建标准的 main.bean 文件
        3. 用户目录重命名，从 Assets/{repo_name} 重命名到 Assets/{username}
        4. 删除数据库记录

        Args:
            user: 用户对象

        Returns:
            删除结果信息
        """
        try:
            git_repo = user.git_repo
        except GitRepository.DoesNotExist:
            raise GitServiceException("用户未启用 Git 功能")

        cleaned_files = []

        try:
            # 1. 删除平台托管的 Gitea 远程仓库（外部远程不调用第三方删库）
            if git_repo.provider == 'gitea_hosted' and git_repo.gitea_repo_id is not None:
                logger.info(f"Deleting Gitea repository for user {user.username}: {git_repo.repo_name}")
                try:
                    self._get_gitea_client().delete_repository(git_repo.repo_name)
                    cleaned_files.append(f"远程仓库: {git_repo.repo_name}")
                except GiteaAPIException as e:
                    logger.warning(f"Failed to delete remote repository {git_repo.repo_name}: {e}")
            else:
                cleaned_files.append('外部远程：未删除托管方仓库（仅解绑平台）')

            # 2. 删除用户目录除了 trans/ 外的全部内容，然后重建标准的 main.bean 文件
            user_assets_path = self.assets_base_path / git_repo.repo_name
            if user_assets_path.exists():
                cleaned_files.extend(self._cleanup_git_files_and_restore_structure(user_assets_path, user))

            # 3. 用户目录重命名，从 Assets/{repo_name} 重命名到 Assets/{username}
            user_assets_path.rename(self.assets_base_path / user.username)
            cleaned_files.append(f"用户目录重命名: {git_repo.repo_name} -> {user.username}")

            # 4. 删除数据库记录
            repo_name = git_repo.repo_name
            git_repo.delete()
            cleaned_files.append(f"数据库记录: {repo_name}")

            logger.info(f"Successfully deleted Git repository for user {user.username}")

            return {
                'message': 'Git 仓库已成功删除',
                'cleaned_files': cleaned_files
            }

        except Exception as e:
            logger.error(f"Failed to delete repository for user {user.username}: {e}")
            raise GitServiceException(f"删除仓库失败: {e}")

    def _cleanup_git_files_and_restore_structure(self, user_assets_path: Path, user: User) -> list:
        """清理用户文件并恢复原始目录结构

        删除用户目录除了 trans/ 外的全部内容，然后重建标准的 main.bean 文件

        Args:
            user_assets_path: 用户 Assets 目录路径
            user: 用户对象

        Returns:
            已清理的文件列表
        """
        cleaned_files = []

        try:
            # 1. 删除除 trans/ 外的所有内容
            if user_assets_path.exists():
                for item in user_assets_path.iterdir():
                    if item.name != 'trans':
                        if item.is_dir():
                            shutil.rmtree(item)
                            cleaned_files.append(f'{item.name}/ 目录')
                        else:
                            item.unlink()
                            cleaned_files.append(item.name)

            # 2. 重建标准的 main.bean 文件
            self._rebuild_standard_main_bean(user)
            cleaned_files.append('重建标准 main.bean')

            return cleaned_files

        except Exception as e:
            logger.error(f"Failed to cleanup git files: {e}")
            raise

    def _rebuild_standard_main_bean(self, user: User):
        """重建标准的 main.bean 文件（未启用 Git 前的格式）

        Args:
            user: 用户对象
        """
        from project.utils.file import BeanFileManager

        # 使用 BeanFileManager 的标准方法重建 main.bean
        BeanFileManager.ensure_main_bean(user)
        logger.info(f"Rebuilt standard main.bean for user {user.username}")

    def _init_and_push_template_in_user_directory(self, git_repo: GitRepository, template_dir: Path):
        """在用户目录中初始化 git 仓库、提交并推送模板内容

        流程：
        1. 确保用户目录存在
        2. 备份现有的 trans/ 目录（如果存在）
        3. 在用户目录中初始化 git 仓库（git init）
        4. 配置 git 用户信息
        5. 添加 origin 远程仓库
        6. 复制模板文件到用户目录（跳过 trans/）
        7. 恢复 trans/ 目录
        8. 添加所有文件（git add .，由于 .gitignore，trans/ 不会被添加）
        9. 创建初始提交（git commit）
        10. 推送到远程（git push origin main）

        Args:
            git_repo: Git 仓库对象
            template_dir: 模板内容目录
        """
        user_assets_path = self.assets_base_path / git_repo.repo_name

        # 1. 确保用户目录存在
        user_assets_path.mkdir(parents=True, exist_ok=True)

        # 2. 备份现有的 trans/ 目录（如果存在）
        trans_path = user_assets_path / 'trans'
        trans_backup = None
        if trans_path.exists():
            trans_backup = self._backup_trans_directory(trans_path)
            logger.info(f"备份现有 trans/ 目录")

        # 准备 SSH 密钥
        ssh_key_file = self._prepare_ssh_key(git_repo)

        try:
            # 设置 Git 环境变量
            env = os.environ.copy()
            env['GIT_SSH_COMMAND'] = f'ssh -i {ssh_key_file} -o StrictHostKeyChecking=no'

            # 切换到用户目录
            original_cwd = os.getcwd()
            os.chdir(user_assets_path)

            try:
                branch = (git_repo.default_branch or 'main').strip() or 'main'
                # 3. 在用户目录中初始化 git 仓库（如果还没有）
                git_path = user_assets_path / '.git'
                if not git_path.exists():
                    subprocess.run(['git', 'init', '-b', branch], check=True, capture_output=True, text=True)
                    logger.info(f"初始化 git 仓库: {user_assets_path}")

                # 4. 配置 git 用户信息
                subprocess.run(['git', 'config', 'user.name', 'Beancount-Trans Platform'], check=True)
                subprocess.run(['git', 'config', 'user.email', 'platform@beancount-trans.local'], check=True)

                # 5. 配置 origin 远程仓库
                clone_url = git_repo.ssh_clone_url
                chk = subprocess.run(['git', 'remote', 'get-url', 'origin'], capture_output=True, text=True)
                if chk.returncode == 0:
                    subprocess.run(
                        ['git', 'remote', 'set-url', 'origin', clone_url],
                        check=True, capture_output=True, text=True
                    )
                else:
                    subprocess.run(
                        ['git', 'remote', 'add', 'origin', clone_url],
                        check=True, capture_output=True, text=True
                    )

                # 6. 复制模板文件到用户目录（跳过 trans/ 目录）
                for item in template_dir.iterdir():
                    target_path = user_assets_path / item.name

                    # 跳过 trans/ 目录，保留现有内容
                    if item.name == 'trans':
                        continue

                    if item.is_file():
                        shutil.copy2(item, target_path)
                    elif item.is_dir():
                        if target_path.exists():
                            shutil.rmtree(target_path)
                        shutil.copytree(item, target_path)

                # 7. 恢复 trans/ 目录
                if trans_backup:
                    self._restore_trans_directory(trans_backup, trans_path)
                    logger.info(f"恢复 trans/ 目录")
                else:
                    # 如果没有备份，说明存在重要错误，需要日志输出
                    logger.error(f"没有备份的 trans/ 目录，可能存在重要错误")
                    # 如果没有现有的 trans/ 目录，创建基本结构
                    # self._ensure_trans_directory_structure(git_repo.owner.username)

                # 7.1 模板 account/*.bean 可能与平台写入的 open 重复，先对账再提交
                self._dedupe_ledger_opens(git_repo.owner, strict=True)

                # 8. 添加所有文件（由于 .gitignore，trans/ 不会被添加）
                subprocess.run(['git', 'add', '.'], check=True, capture_output=True, text=True)

                # 9. 创建初始提交
                commit_message = """feat: 基于 Beancount-Trans-Assets 模板初始化仓库

- 添加标准账户结构
- 添加年度账本模板  
- 配置 Fava 显示选项
- 设置 Git 忽略规则"""

                subprocess.run(['git', 'commit', '-m', commit_message], check=True, capture_output=True, text=True)
                logger.info(f"创建初始提交")

                # 10. 推送到远程（使用 origin）
                subprocess.run([
                    'git', 'push', '-u', 'origin', branch
                ], env=env, check=True, capture_output=True, text=True)

                logger.info(f"成功推送模板内容到仓库 {git_repo.repo_name}")

            finally:
                os.chdir(original_cwd)

        finally:
            # 清理 SSH 密钥文件
            if os.path.exists(ssh_key_file):
                os.remove(ssh_key_file)
            # 清理备份
            if trans_backup and Path(trans_backup).exists():
                shutil.rmtree(trans_backup, ignore_errors=True)

#     def _push_template_to_repository(self, git_repo: GitRepository, template_dir: Path):
#         """[已废弃] 将模板内容推送到 Gitea 仓库

#         此方法已废弃，请使用 _init_and_push_template_in_user_directory 方法。
#         """
#         """将模板内容推送到 Gitea 仓库

#         Args:
#             git_repo: Git 仓库对象
#             template_dir: 模板内容目录
#         """
#         # 创建临时工作目录
#         temp_work_dir = tempfile.mkdtemp(prefix='git_work_')

#         try:
#             # 准备 SSH 密钥
#             ssh_key_file = self._prepare_ssh_key(git_repo)

#             try:
#                 # 设置 Git 环境变量
#                 env = os.environ.copy()
#                 env['GIT_SSH_COMMAND'] = f'ssh -i {ssh_key_file} -o StrictHostKeyChecking=no'

#                 # 克隆空仓库
#                 clone_url = git_repo.ssh_clone_url
#                 subprocess.run([
#                     'git', 'clone', clone_url, temp_work_dir
#                 ], env=env, capture_output=True, text=True, check=True)

#                 # 复制模板文件到工作目录
#                 self._copy_template_files(template_dir, Path(temp_work_dir))

#                 # 切换到工作目录
#                 original_cwd = os.getcwd()
#                 os.chdir(temp_work_dir)

#                 try:
#                     # 配置 Git 用户信息
#                     subprocess.run(['git', 'config', 'user.name', 'Beancount-Trans Platform'], check=True)
#                     subprocess.run(['git', 'config', 'user.email', 'platform@beancount-trans.local'], check=True)

#                     # 添加所有文件
#                     subprocess.run(['git', 'add', '.'], check=True)

#                     # 创建初始提交
#                     commit_message = """feat: 基于 Beancount-Trans-Assets 模板初始化仓库

# - 添加标准账户结构
# - 添加年度账本模板  
# - 配置 Fava 显示选项
# - 设置 Git 忽略规则"""

#                     subprocess.run(['git', 'commit', '-m', commit_message], check=True)

#                     # 推送到远程仓库
#                     subprocess.run(['git', 'push', 'origin', 'main'], env=env, check=True)

#                     logger.info(f"成功推送模板内容到仓库 {git_repo.repo_name}")

#                 finally:
#                     os.chdir(original_cwd)

#             finally:
#                 # 清理 SSH 密钥文件
#                 if os.path.exists(ssh_key_file):
#                     os.remove(ssh_key_file)

#         finally:
#             # 清理临时工作目录
#             shutil.rmtree(temp_work_dir, ignore_errors=True)

    # def _copy_template_files(self, template_dir: Path, work_dir: Path):
    #     """复制模板文件到工作目录

    #     Args:
    #         template_dir: 模板目录
    #         work_dir: Git 工作目录
    #     """
    #     for item in template_dir.iterdir():
    #         if item.is_file():
    #             shutil.copy2(item, work_dir / item.name)
    #         elif item.is_dir():
    #             shutil.copytree(item, work_dir / item.name)

    #     logger.debug(f"复制模板文件到工作目录: {work_dir}")

    # def _sync_template_to_local(self, git_repo: GitRepository, template_dir: Path):
    #     """[已废弃] 同步模板内容到本地用户目录（保留现有 trans/ 目录）

    #     此方法已废弃，请使用 _init_and_push_template_in_user_directory 方法。

    #     Args:
    #         git_repo: Git 仓库对象
    #         template_dir: 模板目录
    #     """
    #     user_assets_path = self.assets_base_path / git_repo.repo_name

    #     # 确保用户目录存在
    #     user_assets_path.mkdir(parents=True, exist_ok=True)

    #     # 备份现有的 trans/ 目录
    #     trans_path = user_assets_path / 'trans'
    #     trans_backup = None
    #     if trans_path.exists():
    #         trans_backup = self._backup_trans_directory(trans_path)
    #         logger.info(f"备份现有 trans/ 目录")

    #     try:
    #         # 复制模板文件到用户目录（跳过 trans/ 目录）
    #         for item in template_dir.iterdir():
    #             target_path = user_assets_path / item.name

    #             # 跳过 trans/ 目录，保留现有内容
    #             if item.name == 'trans':
    #                 continue

    #             if item.is_file():
    #                 shutil.copy2(item, target_path)
    #             elif item.is_dir():
    #                 if target_path.exists():
    #                     shutil.rmtree(target_path)
    #                 shutil.copytree(item, target_path)

    #         # 恢复 trans/ 目录
    #         if trans_backup:
    #             self._restore_trans_directory(trans_backup, trans_path)
    #             logger.info(f"恢复 trans/ 目录")
    #         else:
    #             # 如果没有现有的 trans/ 目录，创建基本结构
    #             self._ensure_trans_directory_structure(git_repo.owner.username)
    #             # from project.utils.file import BeanFileManager
    #             # BeanFileManager.ensure_trans_directory(git_repo.owner)
    #             # BeanFileManager.update_main_bean_include(git_repo.owner, None, 'add')

    #         logger.info(f"成功同步模板内容到本地用户目录: {user_assets_path}")

    #     except Exception as e:
    #         # 如果同步失败，尝试恢复 trans/ 目录
    #         if trans_backup and trans_path.exists():
    #             try:
    #                 self._restore_trans_directory(trans_backup, trans_path)
    #             except:
    #                 pass
    #         raise
    #     finally:
    #         # 清理备份
    #         if trans_backup and Path(trans_backup).exists():
    #             shutil.rmtree(trans_backup, ignore_errors=True)

    # def _ensure_trans_directory_structure(self, username: str):
    #     """确保 trans/ 目录结构存在

    #     Args:
    #         username: 用户名
    #     """
    #     from project.utils.file import BeanFileManager

    #     # 使用现有的 BeanFileManager 方法确保 trans/ 目录结构
    #     BeanFileManager.ensure_trans_directory(username)
    #     BeanFileManager.update_main_bean_include(username, None, 'add')