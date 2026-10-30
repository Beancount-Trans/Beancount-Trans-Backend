"""
Git 模板服务测试
"""

import os
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from django.test import TestCase
from django.contrib.auth import get_user_model

from ..template_service import GitHubTemplateService, TemplateServiceException

User = get_user_model()


class TestGitHubTemplateService(TestCase):
    """GitHub 模板服务测试"""

    def setUp(self):
        self.service = GitHubTemplateService()
        self.test_user = User.objects.create_user(
            username='testuser',
            email='test@example.com'
        )

    def test_parse_repository_url_valid(self):
        """测试解析有效的仓库 URL"""
        owner, repo = self.service._parse_repository_url(
            'https://github.com/Beancount-Trans/Beancount-Trans-Assets'
        )
        self.assertEqual(owner, 'Beancount-Trans')
        self.assertEqual(repo, 'Beancount-Trans-Assets')

    def test_parse_repository_url_with_git_suffix(self):
        """测试解析带 .git 后缀的仓库 URL"""
        owner, repo = self.service._parse_repository_url(
            'https://github.com/Beancount-Trans/Beancount-Trans-Assets.git'
        )
        self.assertEqual(owner, 'Beancount-Trans')
        self.assertEqual(repo, 'Beancount-Trans-Assets')

    def test_parse_repository_url_invalid(self):
        """测试解析无效的仓库 URL"""
        with self.assertRaises(TemplateServiceException):
            self.service._parse_repository_url('https://gitlab.com/user/repo')

        with self.assertRaises(TemplateServiceException):
            self.service._parse_repository_url('https://github.com/user')

    @patch('requests.get')
    def test_get_repository_tree_success(self, mock_get):
        """测试成功获取仓库树结构"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'tree': [
                {'type': 'blob', 'path': 'main.bean', 'url': 'https://api.github.com/...'},
                {'type': 'tree', 'path': 'account', 'url': 'https://api.github.com/...'}
            ]
        }
        mock_get.return_value = mock_response

        # 使用 session.get 而不是直接 requests.get
        with patch.object(self.service.session, 'get', return_value=mock_response):
            tree_data = self.service._get_repository_tree()

        self.assertIn('tree', tree_data)
        self.assertEqual(len(tree_data['tree']), 2)

    def test_get_repository_tree_not_found(self):
        """测试仓库不存在的情况"""
        mock_response = MagicMock()
        mock_response.status_code = 404

        with patch.object(self.service.session, 'get', return_value=mock_response):
            with self.assertRaises(TemplateServiceException) as cm:
                self.service._get_repository_tree()

        self.assertIn('模板仓库不存在或无权访问', str(cm.exception))

    def test_get_repository_tree_rate_limit(self):
        """测试 API 限制的情况"""
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.headers = {'X-RateLimit-Remaining': '0', 'X-RateLimit-Reset': '1640995200'}

        with patch.object(self.service.session, 'get', return_value=mock_response):
            with self.assertRaises(TemplateServiceException) as cm:
                self.service._get_repository_tree()

        self.assertIn('GitHub API 限流', str(cm.exception))

    def test_download_file_content_text(self):
        """测试下载文本文件内容"""
        mock_response = MagicMock()
        mock_response.status_code = 200
        # 修正 base64 编码：'option "title" "xxx的账本"'
        import base64
        test_content = 'option "title" "xxx的账本"'
        encoded_content = base64.b64encode(test_content.encode('utf-8')).decode('ascii')
        mock_response.json.return_value = {
            'encoding': 'base64',
            'content': encoded_content
        }

        with patch.object(self.service.session, 'get', return_value=mock_response):
            content = self.service._download_file_content('https://api.github.com/test')

        self.assertIsInstance(content, str)
        self.assertIn('option "title"', content)

    def test_customize_template_for_user(self):
        """测试为用户定制模板内容"""
        # 创建临时目录和文件
        temp_dir = Path(tempfile.mkdtemp())
        main_bean_path = temp_dir / 'main.bean'

        try:
            # 创建模拟的 main.bean 文件
            main_bean_content = '''option "title" "xxx的账本"
option "operating_currency" "CNY"

; 其他配置
plugin "beancount.plugins.auto_accounts"
'''
            main_bean_path.write_text(main_bean_content, encoding='utf-8')

            # 执行定制
            self.service.customize_template_for_user(temp_dir, self.test_user)

            # 验证结果
            customized_content = main_bean_path.read_text(encoding='utf-8')
            self.assertIn(f'option "title" "{self.test_user.username}的账本"', customized_content)
            self.assertNotIn('option "title" "xxx的账本"', customized_content)

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_customize_template_missing_main_bean(self):
        """测试模板缺少 main.bean 文件的情况"""
        temp_dir = Path(tempfile.mkdtemp())

        try:
            with self.assertRaises(TemplateServiceException) as cm:
                self.service.customize_template_for_user(temp_dir, self.test_user)

            self.assertIn('模板中缺少 main.bean 文件', str(cm.exception))

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_get_template_files_list(self):
        """测试获取模板文件列表"""
        temp_dir = Path(tempfile.mkdtemp())

        try:
            # 创建测试文件结构
            (temp_dir / 'main.bean').write_text('test')
            (temp_dir / 'account').mkdir()
            (temp_dir / 'account' / 'assets.bean').write_text('test')
            (temp_dir / 'document').mkdir()
            (temp_dir / 'document' / 'note.md').write_text('test')

            files = self.service.get_template_files_list(temp_dir)

            expected_files = [
                'account/assets.bean',
                'document/note.md', 
                'main.bean'
            ]

            self.assertEqual(sorted(files), sorted(expected_files))

        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class TestTemplateServiceIntegration(TestCase):
    """模板服务集成测试（需要网络连接）"""

    def setUp(self):
        self.service = GitHubTemplateService()
        self.test_user = User.objects.create_user(
            username='testuser',
            email='test@example.com'
        )

    @pytest.mark.integration
    def test_fetch_real_template_content(self):
        """测试从真实的 GitHub 仓库获取模板内容（集成测试）"""
        try:
            template_dir = self.service.fetch_template_content()

            # 验证基本文件存在
            self.assertTrue((template_dir / 'main.bean').exists())
            self.assertTrue((template_dir / 'account').exists())
            self.assertTrue((template_dir / 'account' / 'assets.bean').exists())

            # 验证文件列表
            files = self.service.get_template_files_list(template_dir)
            self.assertIn('main.bean', files)
            self.assertIn('account/assets.bean', files)

            # 清理
            shutil.rmtree(template_dir, ignore_errors=True)

        except TemplateServiceException as e:
            # 如果网络不可用或 API 限制，跳过测试
            self.skipTest(f"无法连接到 GitHub API: {e}")

    @pytest.mark.integration  
    def test_full_template_workflow(self):
        """测试完整的模板工作流程（集成测试）"""
        try:
            # 1. 获取模板内容
            template_dir = self.service.fetch_template_content()

            try:
                # 2. 定制模板内容
                self.service.customize_template_for_user(template_dir, self.test_user)

                # 3. 验证定制结果
                main_bean_path = template_dir / 'main.bean'
                content = main_bean_path.read_text(encoding='utf-8')
                self.assertIn(f'{self.test_user.username}的账本', content)

            finally:
                shutil.rmtree(template_dir, ignore_errors=True)

        except TemplateServiceException as e:
            self.skipTest(f"无法连接到 GitHub API: {e}")
