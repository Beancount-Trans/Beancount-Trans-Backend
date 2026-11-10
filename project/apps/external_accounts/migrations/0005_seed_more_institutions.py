from django.db import migrations


# 常见国内银行与农村金融机构。微信/支付宝账单里的付款方式常省略「储蓄卡 / 信用卡」，
# 机构别名越全，越能还原付款卡身份。
INSTITUTIONS = [
    ('RCB', '农村商业银行', False, ['农村商业银行', '农商银行', '农商行', '农村信用社', '农信社', '农信']),
    ('BOS', '上海银行', False, ['上海银行']),
    ('BOB', '北京银行', False, ['北京银行']),
    ('JSB', '江苏银行', False, ['江苏银行']),
    ('NJB', '南京银行', False, ['南京银行']),
    ('HZB', '杭州银行', False, ['杭州银行']),
    ('HSB', '徽商银行', False, ['徽商银行']),
    ('ZYB', '中原银行', False, ['中原银行']),
    ('BOHB', '渤海银行', False, ['渤海银行']),
    ('HFB', '恒丰银行', False, ['恒丰银行']),
    ('CZB', '浙商银行', False, ['浙商银行']),
    ('WZB', '温州银行', False, ['温州银行']),
    ('TZB', '台州银行', False, ['台州银行']),
    ('TLB', '泰隆银行', False, ['泰隆银行']),
    ('CZCB', '稠州银行', False, ['稠州银行', '浙江稠州商业银行']),
    ('MTB', '民泰银行', False, ['民泰银行']),
    ('JXB', '江西银行', False, ['江西银行']),
    ('HNB', '湖南银行', False, ['湖南银行']),
    ('HBB', '湖北银行', False, ['湖北银行']),
    ('SCB', '四川银行', False, ['四川银行']),
    ('CQB', '重庆银行', False, ['重庆银行']),
    ('GZB', '广州银行', False, ['广州银行']),
    ('SZB', '苏州银行', False, ['苏州银行']),
    ('QDB', '青岛银行', False, ['青岛银行']),
    ('DLB', '大连银行', False, ['大连银行']),
    ('XMB', '厦门银行', False, ['厦门银行']),
    ('CSB', '长沙银行', False, ['长沙银行']),
    ('WEBANK', '微众银行', False, ['微众银行', 'WeBank']),
    ('MYBANK', '网商银行', False, ['网商银行']),
    ('AIBANK', '百信银行', False, ['百信银行']),
    ('XWB', '新网银行', False, ['新网银行']),
    ('SNB', '苏宁银行', False, ['苏宁银行']),
    ('ZLB', '众邦银行', False, ['众邦银行']),
    ('CDB', '成都银行', False, ['成都银行']),
    ('TJB', '天津银行', False, ['天津银行']),
    ('XAB', '西安银行', False, ['西安银行']),
    ('ZZB', '郑州银行', False, ['郑州银行']),
    ('HEB', '河北银行', False, ['河北银行']),
    ('SJB', '盛京银行', False, ['盛京银行']),
    ('HRB', '哈尔滨银行', False, ['哈尔滨银行']),
    ('JLB', '吉林银行', False, ['吉林银行']),
    ('LJB', '龙江银行', False, ['龙江银行']),
    ('GSB', '甘肃银行', False, ['甘肃银行']),
    ('LZB', '兰州银行', False, ['兰州银行']),
    ('NXB', '宁夏银行', False, ['宁夏银行']),
    ('QHB', '青海银行', False, ['青海银行']),
    ('KLB', '昆仑银行', False, ['昆仑银行']),
    ('GYB', '贵阳银行', False, ['贵阳银行']),
    ('FDB', '富滇银行', False, ['富滇银行']),
    ('BBWB', '广西北部湾银行', False, ['广西北部湾银行', '北部湾银行']),
    ('HANB', '海南银行', False, ['海南银行']),
    ('GLB', '桂林银行', False, ['桂林银行']),
    ('NMB', '内蒙古银行', False, ['内蒙古银行']),
    ('SXB', '晋商银行', False, ['晋商银行']),
    ('QLB', '齐鲁银行', False, ['齐鲁银行']),
    ('WHB', '威海市商业银行', False, ['威海市商业银行', '威海银行']),
    ('RZB', '日照银行', False, ['日照银行']),
    ('WFB', '潍坊银行', False, ['潍坊银行']),
    ('DYB', '东营银行', False, ['东营银行']),
    ('TAB', '泰安银行', False, ['泰安银行']),
    ('LSB', '莱商银行', False, ['莱商银行']),
    ('LINSB', '临商银行', False, ['临商银行']),
    ('JNB', '济宁银行', False, ['济宁银行']),
    ('DZB', '德州银行', False, ['德州银行']),
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
        ('external_accounts', '0004_split_legacy_platforms'),
    ]

    operations = [
        migrations.RunPython(seed_institutions, unseed_institutions),
    ]
