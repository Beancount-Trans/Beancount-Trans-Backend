# Generated manually: only adds BillParseStat, avoids unrelated ParseFile drift.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('translate', '0013_default_bert_parse_engine'),
    ]

    operations = [
        migrations.CreateModel(
            name='BillParseStat',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('bill_type', models.CharField(max_length=32, unique=True)),
                ('last_success_at', models.DateTimeField()),
                ('latest_bill_date', models.DateField(blank=True, null=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': '账单解析可用性',
                'verbose_name_plural': '账单解析可用性',
            },
        ),
    ]
