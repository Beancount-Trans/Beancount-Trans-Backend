from django.db import migrations, models


def migrate_platform_to_bert(apps, schema_editor):
    FormatConfig = apps.get_model('translate', 'FormatConfig')
    # 平台 LLM 改为解析首页的可选参数，历史默认配置回到本地 BERT
    FormatConfig.objects.filter(ai_model__iexact='platform').update(ai_model='BERT')


class Migration(migrations.Migration):

    dependencies = [
        ('translate', '0012_default_platform_parse_engine'),
    ]

    operations = [
        migrations.AlterField(
            model_name='formatconfig',
            name='ai_model',
            field=models.CharField(default='BERT', help_text='AI模型', max_length=16),
        ),
        migrations.RunPython(migrate_platform_to_bert, migrations.RunPython.noop),
    ]
