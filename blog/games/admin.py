from django.contrib import admin
from unfold.admin import ModelAdmin
from .content import validate_database
from .models import GamePuzzle, GameSession, GameSettings


@admin.register(GameSettings)
class GameSettingsAdmin(ModelAdmin):
    fields = ('enabled', 'start_date', 'daily_points_cap', 'pack_version', 'coverage')
    readonly_fields = ('pack_version', 'coverage')

    @admin.display(description='Проверка на съдържанието')
    def coverage(self, obj):
        errors = validate_database()
        return '; '.join(errors[:10]) if errors else '400 дни за всяка от шестте игри. Съдържанието е проверено.'

    def has_add_permission(self, request):
        return not GameSettings.objects.exists() and super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False


class ReadOnlyGameAdmin(ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(GamePuzzle)
class GamePuzzleAdmin(ReadOnlyGameAdmin):
    list_display = ('key', 'game', 'pool', 'slot', 'version')
    list_filter = ('game', 'pool', 'version')
    search_fields = ('key',)


@admin.register(GameSession)
class GameSessionAdmin(ReadOnlyGameAdmin):
    list_display = ('id', 'user', 'puzzle', 'mode', 'status', 'points_awarded', 'created_at')
    list_filter = ('puzzle__game', 'mode', 'status', 'day')
    list_select_related = ('user', 'puzzle')
    search_fields = ('user__username',)
