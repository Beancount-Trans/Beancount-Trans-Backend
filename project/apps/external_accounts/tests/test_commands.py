import os

import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project.settings.test')
django.setup()

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase, override_settings

from project.apps.account.models import Account
from project.apps.external_accounts.legacy_split import SPLIT_ACCOUNT_PATHS
from project.utils.file import BeanFileManager


class SyncExternalAccountsCommandTest(TestCase):
    """迁移用历史模型建账户，不会写账本 open；命令负责补齐。"""

    def setUp(self):
        self.tmp = self.enterContext(override_settings(ASSETS_BASE_PATH=self._temp_dir()))
        self.user = User.objects.create_user(username='cmd_owner', password='pass')
        Account.objects.bulk_create(
            [
                Account(owner=self.user, account=path, enable=True)
                for path in SPLIT_ACCOUNT_PATHS
            ]
        )

    def _temp_dir(self):
        import tempfile

        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return temp.name

    def _collect_text(self):
        path = BeanFileManager.get_collect_bean_path(self.user)
        if not os.path.exists(path):
            return ''
        with open(path, encoding='utf-8') as f:
            return f.read()

    def test_skips_ledger_without_flag(self):
        call_command('sync_external_accounts', owner=self.user.id, verbosity=0)

        text = self._collect_text()
        for path in SPLIT_ACCOUNT_PATHS:
            self.assertNotIn(f'open {path}', text)

    def test_open_accounts_writes_missing_open(self):
        call_command(
            'sync_external_accounts', owner=self.user.id, open_accounts=True, verbosity=0
        )

        text = self._collect_text()
        for path in SPLIT_ACCOUNT_PATHS:
            self.assertIn(f'open {path}', text)

    def test_open_accounts_is_idempotent(self):
        call_command(
            'sync_external_accounts', owner=self.user.id, open_accounts=True, verbosity=0
        )
        call_command(
            'sync_external_accounts', owner=self.user.id, open_accounts=True, verbosity=0
        )

        text = self._collect_text()
        for path in SPLIT_ACCOUNT_PATHS:
            self.assertEqual(text.count(f'open {path}'), 1)
