from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('translate', '0014_billparsestat'),
    ]

    operations = [
        migrations.AddField(
            model_name='formatconfig',
            name='auto_provision_assets',
            field=models.BooleanField(
                default=True,
                help_text='解析成功后自动补齐资产账户与资产映射（付款方式能识别出机构/卡号/平台账户时）',
            ),
        ),
    ]
