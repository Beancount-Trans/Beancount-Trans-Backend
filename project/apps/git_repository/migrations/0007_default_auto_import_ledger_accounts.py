from django.db import migrations, models


def enable_auto_import(apps, schema_editor):
    GitRepository = apps.get_model('git_repository', 'GitRepository')
    GitRepository.objects.filter(auto_import_ledger_accounts=False).update(
        auto_import_ledger_accounts=True
    )


class Migration(migrations.Migration):

    dependencies = [
        ('git_repository', '0006_gitrepository_auto_import_ledger_accounts'),
    ]

    operations = [
        migrations.AlterField(
            model_name='gitrepository',
            name='auto_import_ledger_accounts',
            field=models.BooleanField(default=True, help_text='同步后是否自动导入账本中显式 open、平台缺失的账户'),
        ),
        migrations.RunPython(enable_auto_import, migrations.RunPython.noop),
    ]
