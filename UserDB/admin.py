from django.contrib import admin
from unfold.admin import ModelAdmin
from UserDB.models import UserData, Verified


@admin.register(UserData)
class UserDataAdmin(ModelAdmin):
    # ТУК се добавя autocomplete, за да търсиш в списъка с верифицирани
    autocomplete_fields = ['verify', 'user']

    list_display = ('get_username', 'verify', 'get_full_name', 'get_class')
    list_filter = ('verify__clas',)
    search_fields = ('user__username', 'user__email', 'verify__first_name', 'verify__last_name', 'verify__code')
    ordering = ('-id',)

    @admin.display(description='Потребител', empty_value='Изтрит профил')
    def get_username(self, obj):
        return obj.user.username if obj.user else None

    @admin.display(description='Имена според Потребителски профил')
    def get_full_name(self, obj):
        if obj.user:
            return f"{obj.user.first_name} {obj.user.last_name}"
        return "Няма име"

    @admin.display(description='Клас')
    def get_class(self, obj):
        return obj.verify.clas if obj.verify else None


@admin.register(Verified)
class VerifiedAdmin(ModelAdmin):
    # ЗАДЪЛЖИТЕЛНО search_fields тук, за да работи autocomplete-а горе
    search_fields = ["first_name", "last_name", "code"]

    list_display = ("display_header", "clas", "code")
    list_filter = ["clas"]

    fieldsets = (
        ("Лична информация", {
            "fields": (("first_name", "last_name"), "clas"),
        }),
        ("Сигурност", {
            "fields": ("code",),
        }),
    )

    @admin.display(description="Ученик")
    def display_header(self, instance):
        return f"{instance.first_name} {instance.last_name}"