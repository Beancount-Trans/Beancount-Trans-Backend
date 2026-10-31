from django.contrib import admin

from .models import UsageEvent, UsageSnapshot


class ReadOnlyAdmin(admin.ModelAdmin):
    """用量数据只读：只由埋点与定时任务写入，后台不允许改动。"""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(UsageEvent)
class UsageEventAdmin(ReadOnlyAdmin):
    list_display = (
        'occurred_at', 'dimension', 'quantity', 'unit',
        'owner_type', 'owner_id', 'status', 'source_type',
    )
    list_filter = ('dimension', 'owner_type', 'status', 'source_type')
    date_hierarchy = 'occurred_at'
    search_fields = ('event_key', 'source_id')
    ordering = ('-occurred_at',)


@admin.register(UsageSnapshot)
class UsageSnapshotAdmin(ReadOnlyAdmin):
    list_display = ('period', 'dimension', 'amount', 'unit', 'owner_type', 'owner_id', 'measured_at')
    list_filter = ('dimension', 'owner_type')
    date_hierarchy = 'period'
    ordering = ('-period',)
