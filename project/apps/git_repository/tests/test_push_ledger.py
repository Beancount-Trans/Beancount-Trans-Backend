"""push_ledger / push_pending 的补推与重试行为测试"""
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model

from project.apps.git_repository.models import GitRepository
from project.apps.git_repository.services import GitServiceException, PlatformGitService

User = get_user_model()

pytestmark = pytest.mark.django_db


class Completed:
    def __init__(self, returncode=0, stdout='', stderr=''):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def git_user(tmp_path):
    user = User.objects.create_user(username='pushuser', password='testpass123')
    GitRepository.objects.create(
        owner=user,
        repo_name='pushuser-assets',
        remote_ssh_url='git@example.com:org/repo.git',
        deploy_key_private='-----BEGIN PRIVATE KEY-----\nMII\n-----END PRIVATE KEY-----\n',
        deploy_key_public='ssh-rsa AAAA',
    )
    (tmp_path / 'pushuser-assets' / '.git').mkdir(parents=True)
    return user


@pytest.fixture
def service(tmp_path):
    svc = PlatformGitService()
    svc.assets_base_path = Path(tmp_path)
    return svc


def _fake_run(calls, *, staged='', ahead='1', fail_pull=False):
    def runner(args, **kwargs):
        calls.append(list(args))
        if args[:2] == ['git', 'diff']:
            return Completed(stdout=staged)
        if args[:2] == ['git', 'config']:
            return Completed()
        if args[:2] == ['git', 'rev-parse'] and '--verify' in args:
            return Completed(returncode=0)
        if args[:2] == ['git', 'rev-parse']:
            return Completed(stdout='abc123')
        if args[:2] == ['git', 'rev-list']:
            return Completed(stdout=ahead)
        if 'pull' in args:
            if fail_pull:
                raise subprocess.CalledProcessError(1, args, stderr='conflict')
            return Completed()
        return Completed()

    return runner


def test_push_pending_commit_without_staged_changes(git_user, service):
    calls = []
    with patch(
        'project.apps.git_repository.services.subprocess.run',
        side_effect=_fake_run(calls, staged='', ahead='1'),
    ):
        result = service.push_ledger(git_user, paths=['2024/01.bean'])

    assert result['status'] == 'success'
    assert any('push' in call for call in calls)
    assert not any('commit' in call for call in calls)


def test_skip_when_no_staged_and_no_pending_commit(git_user, service):
    calls = []
    with patch(
        'project.apps.git_repository.services.subprocess.run',
        side_effect=_fake_run(calls, staged='', ahead='0'),
    ):
        result = service.push_ledger(git_user, paths=['2024/01.bean'])

    assert result['status'] == 'skipped'
    assert not any('push' in call for call in calls)


def test_custom_commit_message(git_user, service):
    calls = []
    with patch(
        'project.apps.git_repository.services.subprocess.run',
        side_effect=_fake_run(calls, staged='2024/01.bean', ahead='1'),
    ):
        result = service.push_ledger(
            git_user,
            paths=['2024/01.bean'],
            commit_message='chore(account): 更新账户币种',
        )

    assert result['status'] == 'success'
    commit_calls = [call for call in calls if 'commit' in call]
    assert commit_calls
    assert 'chore(account): 更新账户币种' in commit_calls[0]


def test_rebase_failure_aborts_and_raises(git_user, service):
    calls = []
    with patch(
        'project.apps.git_repository.services.subprocess.run',
        side_effect=_fake_run(calls, staged='', ahead='1', fail_pull=True),
    ):
        with pytest.raises(GitServiceException):
            service.push_ledger(git_user, paths=['2024/01.bean'])

    assert any('rebase' in call and '--abort' in call for call in calls)


def test_push_pending_does_not_add_or_commit(git_user, service):
    calls = []
    with patch(
        'project.apps.git_repository.services.subprocess.run',
        side_effect=_fake_run(calls, ahead='1'),
    ):
        result = service.push_pending(git_user)

    assert result['status'] == 'success'
    assert not any('add' in call for call in calls)
    assert not any('commit' in call for call in calls)
    assert any('push' in call for call in calls)
