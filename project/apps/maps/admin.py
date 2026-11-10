from django.contrib import admin

from project.apps.maps.models import Expense, Assets, Income, Template, TemplateItem


def _format_tag_paths(tag_paths) -> str:
    if not tag_paths:
        return '-'
    return ', '.join(tag_paths)


def _format_tags(tags) -> str:
    names = [tag.name for tag in tags.all()]
    return ', '.join(names) if names else '-'


class TemplateItemInline(admin.TabularInline):
    model = TemplateItem
    extra = 0
    fields = ('key', 'account', 'payee', 'payer', 'currency', 'tag_paths')
    show_change_link = True


@admin.register(Expense)
class ExpenseMapAdmin(admin.ModelAdmin):
    list_display = ('key', 'payee', 'expend', 'tags_display', 'owner', 'currency', 'enable')
    list_per_page = 500
    list_filter = ['owner', 'payee', 'currency', 'enable', 'tags']
    search_fields = ['key', 'payee']
    filter_horizontal = ('tags',)
    raw_id_fields = ('expend', 'owner')

    def get_form(self, request, obj=None, **kwargs):
        form = super().get_form(request, obj, **kwargs)
        form.base_fields['payee'].required = False
        form.base_fields['payee'].allow_blank = True
        return form

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related('tags')

    def tags_display(self, obj):
        return _format_tags(obj.tags)
    tags_display.short_description = '标签'


@admin.register(Assets)
class AssetsMapAdmin(admin.ModelAdmin):
    list_display = ('key', 'assets', 'tags_display', 'owner', 'enable')
    list_per_page = 500
    list_filter = ['owner', 'enable', 'tags']
    search_fields = ['key']
    filter_horizontal = ('tags',)
    raw_id_fields = ('assets', 'owner')

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related('tags')

    def tags_display(self, obj):
        return _format_tags(obj.tags)
    tags_display.short_description = '标签'


@admin.register(Income)
class IncomeMapAdmin(admin.ModelAdmin):
    list_display = ('key', 'payer', 'income', 'tags_display', 'owner', 'enable')
    list_per_page = 500
    list_filter = ['owner', 'enable', 'tags']
    search_fields = ['key', 'payer']
    filter_horizontal = ('tags',)
    raw_id_fields = ('income', 'owner')

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related('tags')

    def tags_display(self, obj):
        return _format_tags(obj.tags)
    tags_display.short_description = '标签'


@admin.register(Template)
class TemplateAdmin(admin.ModelAdmin):
    list_display = ('name', 'type', 'is_public', 'is_official', 'owner', 'version', 'items_count')
    list_per_page = 100
    list_filter = ['type', 'is_public', 'is_official', 'owner']
    search_fields = ['name', 'description', 'update_notes']
    inlines = [TemplateItemInline]
    readonly_fields = ['items_count']

    def items_count(self, obj):
        return obj.items.count()
    items_count.short_description = '模板项数量'


@admin.register(TemplateItem)
class TemplateItemAdmin(admin.ModelAdmin):
    list_display = (
        'template', 'key', 'account', 'tag_paths_display',
        'payee', 'payer', 'currency',
    )
    list_per_page = 500
    list_filter = ['template', 'currency']
    search_fields = ['key', 'account', 'payee', 'payer']
    fields = (
        'template', 'key', 'account', 'payee', 'payer', 'currency', 'tag_paths',
    )

    def tag_paths_display(self, obj):
        return _format_tag_paths(obj.tag_paths)
    tag_paths_display.short_description = '默认标签'
