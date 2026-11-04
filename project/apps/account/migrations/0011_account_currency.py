from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('account_config', '0010_replace_reconciliation_cycle_with_rule'),
    ]

    operations = [
        migrations.AddField(
            model_name='account',
            name='currency',
            field=models.CharField(
                blank=True,
                default='',
                help_text='逗号分隔的货币代码，如 CNY,COIN；仅资产/负债账户可设置，留空表示不限制',
                max_length=64,
                verbose_name='币种',
            ),
        ),
    ]
