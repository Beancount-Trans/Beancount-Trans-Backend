from django.db import migrations


def sync_legacy_mappings(apps, schema_editor):
    """删除 Assets.full 前，把既有映射回填成外部账户、别名与绑定。"""
    Assets = apps.get_model('maps', 'Assets')
    from project.apps.external_accounts.legacy_sync import sync_owner

    owner_ids = (
        Assets.objects.order_by('owner_id')
        .values_list('owner_id', flat=True)
        .distinct()
    )
    for owner_id in owner_ids:
        sync_owner(owner_id, assets_model=Assets)


class Migration(migrations.Migration):

    dependencies = [
        ('external_accounts', '0002_seed_institutions'),
        ('maps', '0013_templateitem_tag_paths'),
    ]

    operations = [
        migrations.RunPython(sync_legacy_mappings, migrations.RunPython.noop),
    ]
