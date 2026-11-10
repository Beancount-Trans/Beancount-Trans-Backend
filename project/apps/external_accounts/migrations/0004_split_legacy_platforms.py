from django.db import migrations


SPLIT_KEYS = ['花呗', '借呗', '备用金', '7929']


def split_legacy_accounts(apps, schema_editor):
    """把平台负债产品拆成独立账户，并收敛中信旧卡号。"""
    Assets = apps.get_model('maps', 'Assets')
    Account = apps.get_model('account_config', 'Account')
    from project.apps.external_accounts.legacy_split import split_legacy_platform_accounts

    owner_ids = (
        Assets.objects.filter(key__in=SPLIT_KEYS)
        .order_by('owner_id')
        .values_list('owner_id', flat=True)
        .distinct()
    )
    for owner_id in owner_ids:
        split_legacy_platform_accounts(
            owner_id,
            assets_model=Assets,
            account_model=Account,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('external_accounts', '0003_sync_legacy_mappings'),
        ('maps', '0014_remove_assets_full_remove_templateitem_full_and_more'),
    ]

    operations = [
        migrations.RunPython(split_legacy_accounts, migrations.RunPython.noop),
    ]
