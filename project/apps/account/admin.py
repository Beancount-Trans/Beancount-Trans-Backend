from django.contrib import admin
from django.utils.html import format_html
# from django.urls import reverse
# from django.utils.safestring import mark_safe
from django.db import models
from django.forms import TextInput

from project.apps.account.models import Account, AccountTemplate, AccountTemplateItem


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    """账户管理"""

    class AccountTypeFilter(admin.SimpleListFilter):
        """账户类型过滤器"""
        title = '账户类型'
        parameter_name = 'account_type'

        def lookups(self, request, model_admin):
            return [
                ('Assets', '资产账户'),
                ('Liabilities', '负债账户'),
                ('Equity', '权益账户'),
                ('Income', '收入账户'),
                ('Expenses', '支出账户'),
            ]

        def queryset(self, request, queryset):
            if self.value():
                return queryset.filter(account__startswith=self.value())
            return queryset

    list_display = [
        'account', 'owner', 'enable', 'account_type',
        'has_children_display', 'mapping_count_display'
    ]
    list_filter = [
        'enable', 'owner',
        AccountTypeFilter,
    ]
    search_fields = ['account', 'owner__username']
    ordering = ['account']
    readonly_fields = ['has_children_display', 'mapping_count_display']

    fieldsets = (
        ('基本信息', {
            'fields': ('account', 'owner', 'enable', 'parent', 'description')
        }),
        ('统计信息', {
            'fields': ('has_children_display', 'mapping_count_display'),
            'classes': ('collapse',)
        }),
    )

    formfield_overrides = {
        models.CharField: {'widget': TextInput(attrs={'size': '50'})},
    }

    def get_queryset(self, request):
        """优化查询"""
        return super().get_queryset(request).select_related(
            'owner', 'parent'
        ).prefetch_related('children')

    def account_type(self, obj):
        """显示账户类型"""
        return obj.get_account_type()
    account_type.short_description = '账户类型'
    account_type.admin_order_field = 'account'

    def has_children_display(self, obj):
        """显示是否有子账户"""
        if obj.has_children():
            count = obj.children.count()
            return format_html(
                '<span style="color: green;">是 ({})</span>',
                count
            )
        return format_html('<span style="color: gray;">{}</span>', '否')
    has_children_display.short_description = '有子账户'

    def mapping_count_display(self, obj):
        """显示映射数量"""
        try:
            from django.apps import apps
            Expense = apps.get_model('maps', 'Expense')
            Assets = apps.get_model('maps', 'Assets')
            Income = apps.get_model('maps', 'Income')

            expense_count = Expense.objects.filter(expend=obj, enable=True).count()
            assets_count = Assets.objects.filter(assets=obj, enable=True).count()
            income_count = Income.objects.filter(income=obj, enable=True).count()

            total = expense_count + assets_count + income_count

            if total > 0:
                return format_html(
                    '<span style="color: blue;">总计: {}<br/>'
                    '支出: {} | 资产: {} | 收入: {}</span>',
                    total, expense_count, assets_count, income_count
                )
            else:
                return format_html('<span style="color: gray;">{}</span>', '无映射')
        except:
            return format_html('<span style="color: gray;">{}</span>', '无法获取')
    mapping_count_display.short_description = '映射统计'

    def get_account_type(self, obj):
        """获取账户类型"""
        return obj.get_account_type()
    get_account_type.short_description = '账户类型'

    def save_model(self, request, obj, form, change):
        """保存模型时设置属主"""
        if not change:  # 新建时
            obj.owner = request.user
        super().save_model(request, obj, form, change)

    def get_readonly_fields(self, request, obj=None):
        """根据用户权限设置只读字段"""
        readonly_fields = list(self.readonly_fields)

        # 非管理员不能修改owner字段
        if not request.user.is_superuser:
            readonly_fields.append('owner')

        return readonly_fields

    def has_change_permission(self, request, obj=None):
        """检查修改权限"""
        if obj and not request.user.is_superuser:
            return obj.owner == request.user
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        """检查删除权限"""
        if obj and not request.user.is_superuser:
            return obj.owner == request.user
        return super().has_delete_permission(request, obj)

    actions = ['enable_accounts', 'disable_accounts', 'close_accounts']

    def enable_accounts(self, request, queryset):
        """批量启用账户"""
        updated = 0
        for account in queryset:
            if account.enable:
                continue
            account.enable = True
            account.save()
            updated += 1
        self.message_user(
            request,
            f'成功启用了 {updated} 个账户。'
        )
    enable_accounts.short_description = "启用选中的账户"

    def disable_accounts(self, request, queryset):
        """批量禁用账户"""
        updated = 0
        for account in queryset:
            if not account.enable:
                continue
            account.enable = False
            account.save()
            updated += 1
        self.message_user(
            request,
            f'成功禁用了 {updated} 个账户。'
        )
    disable_accounts.short_description = "禁用选中的账户"

    def close_accounts(self, request, queryset):
        """批量关闭账户"""
        closed_count = 0
        for account in queryset:
            try:
                account.close()
                closed_count += 1
            except Exception as e:
                self.message_user(
                    request,
                    f'关闭账户 {account.account} 失败: {str(e)}',
                    level='ERROR'
                )

        self.message_user(
            request,
            f'成功关闭了 {closed_count} 个账户。'
        )
    close_accounts.short_description = "关闭选中的账户"


@admin.register(AccountTemplate)
class AccountTemplateAdmin(admin.ModelAdmin):
    """账户模板管理"""
    list_display = ['name', 'type_display', 'is_public', 'is_official', 'owner', 'version', 'items_count']
    list_per_page = 100
    list_filter = ['is_public', 'is_official', 'owner']
    search_fields = ['name', 'description', 'update_notes']
    readonly_fields = ['items_count']

    def type_display(self, obj):
        """显示模板类型"""
        return '账户'
    type_display.short_description = '模板类型'

    def items_count(self, obj):
        """显示模板项数量"""
        return obj.items.count()
    items_count.short_description = '账户数量'


@admin.register(AccountTemplateItem)
class AccountTemplateItemAdmin(admin.ModelAdmin):
    """账户模板项管理"""
    list_display = ['template', 'account_path', 'enable', 'description']
    list_per_page = 500
    list_filter = ['template', 'enable']
    search_fields = ['account_path']
