import pytest
from django.contrib.auth.models import User
from django.test import override_settings

from project.apps.file_manager.models import Directory, File
from project.apps.usage.models import Dimension, UsageSnapshot
from project.apps.usage.tasks import snapshot_usage_task


@pytest.fixture
def user(db):
    return User.objects.create_user(username='snapshot_user', password='pass12345')


@pytest.fixture
def root_dir(user):
    return Directory.objects.create(name='UsageRoot', owner=user)


def _create_file(user, directory, name, size):
    return File.objects.create(
        name=name,
        directory=directory,
        storage_name=name,
        size=size,
        owner=user,
        content_type='text/csv',
    )


@pytest.mark.django_db
class TestSnapshotTask:
    @pytest.fixture(autouse=True)
    def _no_gitea(self, settings):
        settings.GITEA_ADMIN_TOKEN = ''

    def test_snapshots_file_storage_bytes(self, user, root_dir):
        _create_file(user, root_dir, 'a.csv', 1024)
        _create_file(user, root_dir, 'b.csv', 2048)

        result = snapshot_usage_task(period='2026-01-01')

        assert result['status'] == 'ok'
        snapshot = UsageSnapshot.objects.get(
            owner_id=user.id,
            dimension=Dimension.STORAGE_FILE,
            period='2026-01-01',
        )
        assert snapshot.amount == 3072
        assert snapshot.unit == 'B'

    def test_rerun_overwrites_instead_of_accumulating(self, user, root_dir):
        file_obj = _create_file(user, root_dir, 'a.csv', 1024)
        snapshot_usage_task(period='2026-01-02')

        file_obj.size = 5000
        file_obj.save(update_fields=['size'])
        snapshot_usage_task(period='2026-01-02')

        rows = UsageSnapshot.objects.filter(
            owner_id=user.id,
            dimension=Dimension.STORAGE_FILE,
            period='2026-01-02',
        )
        assert rows.count() == 1
        assert rows.first().amount == 5000

    def test_deleted_files_write_zero(self, user, root_dir):
        file_obj = _create_file(user, root_dir, 'a.csv', 1024)
        snapshot_usage_task(period='2026-01-03')

        file_obj.delete()
        snapshot_usage_task(period='2026-01-04')

        snapshot = UsageSnapshot.objects.get(
            owner_id=user.id,
            dimension=Dimension.STORAGE_FILE,
            period='2026-01-04',
        )
        assert snapshot.amount == 0

    def test_repo_dimension_skipped_without_gitea_token(self, user, root_dir):
        _create_file(user, root_dir, 'a.csv', 10)
        result = snapshot_usage_task(period='2026-01-05')
        assert result['storage.repo'] == 0

    @override_settings(USAGE_METERING_ENABLED=False)
    def test_disabled_metering_writes_nothing(self, user, root_dir):
        _create_file(user, root_dir, 'a.csv', 1024)
        result = snapshot_usage_task(period='2026-01-06')
        assert result == {'status': 'disabled'}
        assert UsageSnapshot.objects.count() == 0
