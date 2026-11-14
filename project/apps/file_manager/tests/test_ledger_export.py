"""账本导出：ZIP 打包与接口行为测试。"""
import io
import shutil
import zipfile
from pathlib import Path

import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APIClient

from project.apps.git_repository.models import GitRepository

User = get_user_model()

pytestmark = pytest.mark.django_db


def _write_ledger(root: Path) -> None:
    (root / 'trans').mkdir(parents=True, exist_ok=True)
    (root / '2025').mkdir(parents=True, exist_ok=True)
    (root / '.git').mkdir(parents=True, exist_ok=True)

    (root / 'main.bean').write_text('include "trans/main.bean"\n', encoding='utf-8')
    (root / 'trans' / 'main.bean').write_text('include "collect.bean"\n', encoding='utf-8')
    (root / 'trans' / 'collect.bean').write_text(
        '2025-05-01 * "商户A" "午餐"\n'
        '  Expenses:Food:Lunch          30.00 CNY\n'
        '  Assets:Savings:Cash         -30.00 CNY\n',
        encoding='utf-8',
    )
    (root / '2025' / '05.bean').write_text(
        '2025-05-02 * "商户B" "晚餐"\n'
        '  Expenses:Food:Dinner         50.00 CNY\n'
        '  Assets:Savings:Cash         -50.00 CNY\n',
        encoding='utf-8',
    )
    (root / '.git' / 'HEAD').write_text('ref: refs/heads/main\n', encoding='utf-8')


@pytest.fixture
def assets_dir(tmp_path, monkeypatch):
    base = tmp_path / 'Assets'
    base.mkdir()
    monkeypatch.setattr(settings, 'ASSETS_BASE_PATH', str(base))
    return base


def _client_for(user) -> APIClient:
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def test_export_ledger_zip_for_plain_user(assets_dir):
    user = User.objects.create_user(username='plainuser', password='x')
    _write_ledger(assets_dir / 'plainuser')

    response = _client_for(user).get(reverse('ledger-export'))

    assert response.status_code == 200
    assert response['Content-Type'] == 'application/zip'
    assert '.zip' in response['Content-Disposition']

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = set(archive.namelist())
    assert {'main.bean', 'trans/main.bean', 'trans/collect.bean', '2025/05.bean'} <= names
    assert '.git/HEAD' not in names


def test_export_ledger_zip_empty_ledger(assets_dir):
    user = User.objects.create_user(username='emptyuser', password='x')
    root = assets_dir / 'emptyuser'
    # 新建用户会自动初始化账本结构，这里清空以模拟「取消同步后账本被清空」的边界
    shutil.rmtree(root)
    root.mkdir()
    (root / '.gitignore').write_text('trans/\n', encoding='utf-8')

    response = _client_for(user).get(reverse('ledger-export'))

    assert response.status_code == 404
    assert '账本还没有内容' in response.data['error']


def test_export_ledger_zip_requires_login(assets_dir):
    response = APIClient().get(reverse('ledger-export'))
    assert response.status_code == 401


def test_export_ledger_zip_for_git_user(assets_dir):
    user = User.objects.create_user(username='gituser', password='x')
    GitRepository.objects.create(
        owner=user,
        repo_name='abc123-assets',
        deploy_key_private='-----BEGIN PRIVATE KEY-----\nMII\n-----END PRIVATE KEY-----\n',
        deploy_key_public='ssh-rsa AAAAB3',
    )
    _write_ledger(assets_dir / 'abc123-assets')
    # 用户名目录由新建用户自动创建，不应参与 Git 用户的导出
    (assets_dir / 'gituser' / 'should_not_export.bean').write_text(
        '; marker\n', encoding='utf-8'
    )

    response = _client_for(user).get(reverse('ledger-export'))

    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = set(archive.namelist())
    assert 'main.bean' in names
    assert 'trans/collect.bean' in names
    assert 'should_not_export.bean' not in names
