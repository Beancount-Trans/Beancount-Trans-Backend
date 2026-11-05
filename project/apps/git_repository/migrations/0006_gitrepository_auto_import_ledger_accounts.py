from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('git_repository', '0005_gitrepository_sync_paused'),
    ]

    operations = [
        migrations.AddField(
            model_name='gitrepository',
            name='auto_import_ledger_accounts',
            field=models.BooleanField(default=False, help_text='同步后是否自动导入账本中显式 open、平台缺失的账户'),
        ),
    ]
