from django.contrib import admin

from project.apps.external_accounts.models import (
    ExternalAccount,
    ExternalAccountAlias,
    ExternalAccountBinding,
    Institution,
    InstitutionAlias,
)


class InstitutionAliasInline(admin.TabularInline):
    model = InstitutionAlias
    extra = 0


@admin.register(Institution)
class InstitutionAdmin(admin.ModelAdmin):
    list_display = ('code', 'name', 'is_platform', 'enable')
    list_filter = ('is_platform', 'enable')
    search_fields = ('code', 'name')
    inlines = [InstitutionAliasInline]


@admin.register(ExternalAccount)
class ExternalAccountAdmin(admin.ModelAdmin):
    list_display = ('canonical_name', 'owner', 'kind', 'tail', 'product', 'slot', 'enable')
    list_filter = ('kind', 'enable')
    search_fields = ('display_name', 'tail', 'product')


@admin.register(ExternalAccountAlias)
class ExternalAccountAliasAdmin(admin.ModelAdmin):
    list_display = ('text', 'external_account', 'owner', 'source')
    list_filter = ('source',)
    search_fields = ('text',)


@admin.register(ExternalAccountBinding)
class ExternalAccountBindingAdmin(admin.ModelAdmin):
    list_display = ('external_account', 'account', 'owner')
    search_fields = ('external_account__display_name', 'account__account')
