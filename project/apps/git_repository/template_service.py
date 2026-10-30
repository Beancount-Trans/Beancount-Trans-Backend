"""
Git 仓库模板服务
负责从 GitHub 获取模板仓库内容并进行用户定制
"""

import os
import re
import json
import base64
import tempfile
import logging
import time
from pathlib import Path
from typing import Dict, Any, Optional
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from django.contrib.auth import get_user_model
from django.conf import settings

User = get_user_model()
logger = logging.getLogger(__name__)


class TemplateServiceException(Exception):
    """模板服务异常"""
    pass


class GitHubTemplateService:
    """GitHub 模板仓库服务"""

    # 固定的模板仓库配置
    TEMPLATE_REPOSITORY_URL = 'https://github.com/Beancount-Trans/Beancount-Trans-Assets'
    TEMPLATE_REPOSITORY_BRANCH = 'main'

    def __init__(self):
        self.template_url = self.TEMPLATE_REPOSITORY_URL
        self.template_branch = self.TEMPLATE_REPOSITORY_BRANCH

        # 解析仓库信息
        self.owner, self.repo = self._parse_repository_url(self.template_url)

        # 设置请求头，支持 GitHub Token
        self.headers = {'Accept': 'application/vnd.github.v3+json'}

        # 如果配置了 GitHub Token，添加到请求头中
        github_token = getattr(settings, 'GITHUB_TOKEN', None)
        if github_token:
            self.headers['Authorization'] = f'token {github_token}'
            logger.info("使用 GitHub Token 进行 API 请求")
        else:
            logger.warning("未配置 GitHub Token，使用匿名访问（限制较严格）")

        # 配置带重试机制的 requests session
        self.session = self._create_session_with_retry()

    def _parse_repository_url(self, url: str) -> tuple[str, str]:
        """解析 GitHub 仓库 URL

        Args:
            url: GitHub 仓库 URL

        Returns:
            (owner, repo) 元组
        """
        # 支持 https://github.com/owner/repo 格式
        parsed = urlparse(url)
        if parsed.hostname != 'github.com':
            raise TemplateServiceException(f"不支持的仓库 URL: {url}")

        path_parts = parsed.path.strip('/').split('/')
        if len(path_parts) < 2:
            raise TemplateServiceException(f"无效的仓库 URL: {url}")

        owner = path_parts[0]
        repo = path_parts[1].replace('.git', '')  # 移除 .git 后缀

        return owner, repo

    def _create_session_with_retry(self) -> requests.Session:
        """创建带重试机制的 requests session"""
        session = requests.Session()

        # 配置 HTTP 适配器，增加连接池大小
        adapter = HTTPAdapter(
            pool_connections=10,
            pool_maxsize=20,
            max_retries=0  # 我们使用自定义重试逻辑
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)

        # 设置默认超时
        session.timeout = 30

        return session

    def fetch_template_content(self) -> Path:
        """从 GitHub 获取模板仓库内容

        Returns:
            临时目录路径，包含模板仓库内容
        """
        try:
            # 检查 API 限制状态
            rate_limit = self.check_api_rate_limit()
            if rate_limit:
                remaining = rate_limit.get('remaining', 0)
                limit = rate_limit.get('limit', 0)
                logger.info(f"GitHub API 限制状态: {remaining}/{limit} 剩余")

                if remaining < 10:  # 如果剩余请求数少于10，发出警告
                    reset_time = rate_limit.get('reset', 0)
                    current_time = int(time.time())
                    wait_seconds = max(0, reset_time - current_time)
                    logger.warning(f"GitHub API 限制即将用尽，将在 {wait_seconds} 秒后重置")

            # 创建临时目录
            temp_dir = Path(tempfile.mkdtemp(prefix='template_'))

            # 获取仓库树结构
            tree_data = self._get_repository_tree()

            # 下载所有文件
            self._download_files(tree_data, temp_dir)

            logger.info(f"成功获取模板仓库内容到: {temp_dir}")
            return temp_dir

        except Exception as e:
            logger.error(f"获取模板仓库内容失败: {e}")
            raise TemplateServiceException(f"获取模板仓库内容失败: {e}")

    def _get_repository_tree(self) -> Dict[str, Any]:
        """获取仓库树结构"""
        api_url = f"https://api.github.com/repos/{self.owner}/{self.repo}/git/trees/{self.template_branch}"

        # 递归获取所有文件
        params = {'recursive': '1'}

        try:
            response = self.session.get(api_url, headers=self.headers, params=params)

            if response.status_code == 404:
                raise TemplateServiceException(f"模板仓库不存在或无权访问: {self.owner}/{self.repo}")
            elif response.status_code == 403:
                # 检查是否是限流
                rate_limit_remaining = response.headers.get('X-RateLimit-Remaining', '0')
                rate_limit_reset = response.headers.get('X-RateLimit-Reset', '')

                if rate_limit_remaining == '0':
                    reset_time = int(rate_limit_reset) if rate_limit_reset.isdigit() else 0
                    current_time = int(time.time())
                    wait_seconds = max(0, reset_time - current_time)

                    error_msg = f"GitHub API 限流，限制将在 {wait_seconds} 秒后重置"
                    logger.warning(error_msg)
                    raise TemplateServiceException(error_msg)
                else:
                    raise TemplateServiceException("GitHub API 访问被拒绝，可能需要配置 GitHub Token")
            elif response.status_code != 200:
                raise TemplateServiceException(f"GitHub API 请求失败: {response.status_code}")

            return response.json()

        except requests.exceptions.SSLError as e:
            logger.error(f"GitHub API SSL 连接错误: {e}")
            raise TemplateServiceException(f"GitHub API SSL 连接失败，请检查网络连接: {e}")
        except requests.exceptions.ConnectionError as e:
            logger.error(f"GitHub API 连接错误: {e}")
            raise TemplateServiceException(f"无法连接到 GitHub API，请检查网络连接: {e}")
        except requests.exceptions.Timeout as e:
            logger.error(f"GitHub API 请求超时: {e}")
            raise TemplateServiceException(f"GitHub API 请求超时，请稍后重试: {e}")
        except requests.exceptions.RequestException as e:
            logger.error(f"GitHub API 请求异常: {e}")
            raise TemplateServiceException(f"GitHub API 请求失败: {e}")

    def _download_files(self, tree_data: Dict[str, Any], temp_dir: Path):
        """下载所有文件到临时目录"""
        total_files = len([item for item in tree_data.get('tree', []) if item['type'] == 'blob'])
        downloaded_files = 0

        for item in tree_data.get('tree', []):
            if item['type'] == 'blob':  # 只处理文件，不处理目录
                file_path = item['path']
                file_url = item['url']

                # 下载文件内容（带重试机制）
                file_content = self._download_file_content_with_retry(file_url, file_path)

                # 创建文件路径
                full_path = temp_dir / file_path
                full_path.parent.mkdir(parents=True, exist_ok=True)

                # 写入文件
                if isinstance(file_content, bytes):
                    full_path.write_bytes(file_content)
                else:
                    full_path.write_text(file_content, encoding='utf-8')

                downloaded_files += 1
                logger.debug(f"下载文件 ({downloaded_files}/{total_files}): {file_path}")

    def _download_file_content(self, file_url: str) -> bytes | str:
        """下载单个文件内容"""
        try:
            response = self.session.get(file_url, headers=self.headers)

            if response.status_code == 403:
                # 检查是否是限流
                rate_limit_remaining = response.headers.get('X-RateLimit-Remaining', '0')
                if rate_limit_remaining == '0':
                    raise TemplateServiceException("GitHub API 限流")
                else:
                    raise TemplateServiceException("GitHub API 访问被拒绝")
            elif response.status_code != 200:
                raise TemplateServiceException(f"下载文件失败: {response.status_code}")

            file_data = response.json()

            # GitHub API 返回 base64 编码的内容
            if file_data.get('encoding') == 'base64':
                content = base64.b64decode(file_data['content'])

                # 尝试解码为文本（对于文本文件）
                try:
                    return content.decode('utf-8')
                except UnicodeDecodeError:
                    # 二进制文件，返回原始字节
                    return content
            else:
                # 其他编码方式（理论上不应该出现）
                return file_data.get('content', '')

        except requests.exceptions.SSLError as e:
            logger.error(f"下载文件 SSL 连接错误: {file_url}, 错误: {e}")
            raise TemplateServiceException(f"文件下载 SSL 连接失败: {e}")
        except requests.exceptions.ConnectionError as e:
            logger.error(f"下载文件连接错误: {file_url}, 错误: {e}")
            raise TemplateServiceException(f"文件下载连接失败: {e}")
        except requests.exceptions.Timeout as e:
            logger.error(f"下载文件超时: {file_url}, 错误: {e}")
            raise TemplateServiceException(f"文件下载超时: {e}")
        except requests.exceptions.RequestException as e:
            logger.error(f"下载文件请求异常: {file_url}, 错误: {e}")
            raise TemplateServiceException(f"文件下载失败: {e}")

    def _download_file_content_with_retry(self, file_url: str, file_path: str, max_retries: int = 3) -> bytes | str:
        """带重试机制的文件内容下载"""
        last_exception = None

        for attempt in range(max_retries):
            try:
                logger.debug(f"下载文件尝试 {attempt + 1}/{max_retries}: {file_path}")
                return self._download_file_content(file_url)

            except TemplateServiceException as e:
                last_exception = e
                if attempt < max_retries - 1:
                    # 根据错误类型调整等待时间
                    if "限流" in str(e) or "API 限制" in str(e):
                        # 限流错误使用更长的等待时间：10秒、30秒、60秒
                        wait_time = 10 * (3 ** attempt)
                        logger.warning(f"GitHub API 限流，{wait_time}秒后重试: {file_path}")
                    else:
                        # 其他错误使用标准等待时间：1秒、2秒、4秒
                        wait_time = 2 ** attempt
                        logger.warning(f"下载文件失败，{wait_time}秒后重试: {file_path}, 错误: {e}")

                    time.sleep(wait_time)
                else:
                    logger.error(f"下载文件最终失败: {file_path}, 错误: {e}")

        # 所有重试都失败了
        raise TemplateServiceException(f"下载文件 {file_path} 失败，已重试 {max_retries} 次: {last_exception}")

    def customize_template_for_user(self, template_dir: Path, user: User) -> None:
        """为用户定制模板内容

        Args:
            template_dir: 模板目录路径
            user: 用户对象
        """
        try:
            # 只需要定制 main.bean 文件
            main_bean_path = template_dir / 'main.bean'

            if not main_bean_path.exists():
                raise TemplateServiceException("模板中缺少 main.bean 文件")

            # 读取原始内容
            content = main_bean_path.read_text(encoding='utf-8')

            # 替换用户名
            # 查找 option "title" "xxx的账本" 并替换
            title_pattern = r'option\s+"title"\s+"[^"]*的账本"'
            new_title = f'option "title" "{user.username}的账本"'

            if re.search(title_pattern, content):
                content = re.sub(title_pattern, new_title, content)
                logger.info(f"为用户 {user.username} 定制了账本标题")
            else:
                logger.warning(f"模板中未找到标题配置，跳过用户名替换")

            # 写回文件
            main_bean_path.write_text(content, encoding='utf-8')

            logger.info(f"成功为用户 {user.username} 定制模板内容")

        except Exception as e:
            logger.error(f"定制模板内容失败: {e}")
            raise TemplateServiceException(f"定制模板内容失败: {e}")

    def get_template_files_list(self, template_dir: Path) -> list[str]:
        """获取模板文件列表（相对路径）

        Args:
            template_dir: 模板目录路径

        Returns:
            文件相对路径列表
        """
        files = []
        for file_path in template_dir.rglob('*'):
            if file_path.is_file():
                relative_path = file_path.relative_to(template_dir)
                files.append(str(relative_path))

        return sorted(files)

    def check_api_rate_limit(self) -> Dict[str, Any]:
        """检查 GitHub API 限制状态

        Returns:
            包含限制信息的字典
        """
        try:
            response = self.session.get('https://api.github.com/rate_limit', headers=self.headers)

            if response.status_code == 200:
                data = response.json()
                core_limit = data.get('resources', {}).get('core', {})

                return {
                    'limit': core_limit.get('limit', 0),
                    'remaining': core_limit.get('remaining', 0),
                    'reset': core_limit.get('reset', 0),
                    'used': core_limit.get('used', 0)
                }
            else:
                logger.warning(f"无法获取 API 限制信息: {response.status_code}")
                return {}

        except Exception as e:
            logger.error(f"检查 API 限制时出错: {e}")
            return {}
