from django.db import migrations


INSTITUTIONS = [
    ('BOC', '中国银行', False, ['中国银行', '中行', 'BOC']),
    ('ICBC', '中国工商银行', False, ['中国工商银行', '工商银行', '工行', 'ICBC']),
    ('CCB', '中国建设银行', False, ['中国建设银行', '建设银行', '建行', 'CCB']),
    ('ABC', '中国农业银行', False, ['中国农业银行', '农业银行', '农行', 'ABC']),
    ('BOCOM', '交通银行', False, ['交通银行', '交行', 'BOCOM']),
    ('PSBC', '中国邮政储蓄银行', False, ['中国邮政储蓄银行', '邮政储蓄银行', '邮储银行', 'PSBC']),
    ('CMB', '招商银行', False, ['招商银行', '招行', 'CMB']),
    ('CITIC', '中信银行', False, ['中信银行', '中信', 'CITIC']),
    ('SPDB', '上海浦东发展银行', False, ['上海浦东发展银行', '浦发银行', '浦发', 'SPDB']),
    ('CIB', '兴业银行', False, ['兴业银行', 'CIB']),
    ('CMBC', '中国民生银行', False, ['中国民生银行', '民生银行', '民生', 'CMBC']),
    ('CEB', '中国光大银行', False, ['中国光大银行', '光大银行', '光大', 'CEB']),
    ('HXB', '华夏银行', False, ['华夏银行', 'HXB']),
    ('PAB', '平安银行', False, ['平安银行', 'PAB']),
    ('CGB', '广发银行', False, ['广发银行', '广发', 'CGB']),
    ('NBCB', '宁波银行', False, ['宁波银行', 'NBCB']),
    ('ALIPAY', '支付宝', True, ['支付宝', 'alipay', 'Alipay', 'ALIPAY']),
    ('WECHAT', '微信', True, ['微信', '微信支付', '财付通', 'wechat', 'WeChat', 'WECHAT']),
]


def seed_institutions(apps, schema_editor):
    Institution = apps.get_model('external_accounts', 'Institution')
    InstitutionAlias = apps.get_model('external_accounts', 'InstitutionAlias')

    for code, name, is_platform, aliases in INSTITUTIONS:
        institution, _ = Institution.objects.get_or_create(
            code=code,
            defaults={'name': name, 'is_platform': is_platform, 'enable': True},
        )
        for text in aliases:
            InstitutionAlias.objects.get_or_create(
                text=text,
                defaults={'institution_id': institution.pk},
            )


def unseed_institutions(apps, schema_editor):
    """只移除别名，保留机构，避免外部账户引用被 PROTECT 阻断。"""
    InstitutionAlias = apps.get_model('external_accounts', 'InstitutionAlias')
    texts = [text for _, _, _, aliases in INSTITUTIONS for text in aliases]
    InstitutionAlias.objects.filter(text__in=texts).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('external_accounts', '0001_initial'),
    ]

    operations = [
        migrations.RunPython(seed_institutions, unseed_institutions),
    ]
