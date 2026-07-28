import csv

from django.contrib import admin, messages
from django.db.models import Count
from django.http import HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.utils import timezone
from unfold.admin import ModelAdmin

from .models import (
    Profile,
    Register,
    Settings,
    Valenitines,
    generate_unique_code,
)


def csv_safe(value):
    """Prevent spreadsheet formulas in exported user-controlled values."""
    text = "" if value is None else str(value)
    if text.startswith(("=", "+", "-", "@")):
        return f"'{text}"
    return text


@admin.register(Profile)
class ProfileAdmin(ModelAdmin):
    list_display = ("full_name_display", "clas", "email", "code")
    search_fields = ("code", "first_name", "last_name", "email", "clas")
    list_filter = ("clas",)
    readonly_fields = ("uuid", "code")
    actions = ("send_system_valentine", "regenerate_access_codes")

    @admin.display(description="Име")
    def full_name_display(self, obj):
        return obj.full_name

    @admin.action(description="Изпрати позитивна системна валентинка")
    def send_system_valentine(self, request, queryset):
        wishes = (
            "Продължавай да бъдеш причината някой да се усмихне днес. ✨",
            "Твоята енергия прави училището по-хубаво място. 💛",
            "Някой днес иска да ти напомни, че си страхотен човек. 🌟",
            "Не спирай да бъдеш себе си — точно това е суперсилата ти. 💫",
            "Едно малко послание за един наистина ценен човек. 🫶",
        )
        rows = []
        for index, student in enumerate(queryset):
            rows.append(
                Valenitines(
                    profile=student,
                    sender_profile=None,
                    content=wishes[index % len(wishes)],
                    is_anonymous=True,
                    theme=Valenitines.Theme.CLASSIC,
                )
            )
        Valenitines.objects.bulk_create(rows)
        self.message_user(
            request,
            f"Изпратени са {len(rows)} системни валентинки.",
            messages.SUCCESS,
        )

    @admin.action(description="Генерирай нов код за достъп")
    def regenerate_access_codes(self, request, queryset):
        updated = 0
        for profile in queryset:
            profile.code = generate_unique_code()
            profile.save(update_fields=["code"])
            profile.sessions.filter(revoked=False).update(revoked=True)
            updated += 1
        self.message_user(
            request,
            f"Обновени са {updated} кода и активните им сесии са прекратени.",
            messages.WARNING,
        )


@admin.register(Register)
class RegisterAdmin(ModelAdmin):
    list_display = ("profile", "created_at", "expires_at", "revoked", "valid_display")
    list_filter = ("revoked", "created_at", "expires_at")
    search_fields = (
        "profile__first_name",
        "profile__last_name",
        "profile__code",
    )
    readonly_fields = ("profile", "uuid", "created_at", "expires_at")
    actions = ("revoke_sessions", "delete_expired_sessions")

    @admin.display(description="Валидна", boolean=True)
    def valid_display(self, obj):
        return obj.is_valid

    @admin.action(description="Прекрати избраните сесии")
    def revoke_sessions(self, request, queryset):
        count = queryset.update(revoked=True)
        self.message_user(request, f"Прекратени сесии: {count}.")

    @admin.action(description="Изтрий всички изтекли сесии")
    def delete_expired_sessions(self, request, queryset):
        count, _ = Register.objects.filter(expires_at__lte=timezone.now()).delete()
        self.message_user(request, f"Изтрити изтекли сесии: {count}.")

    def has_add_permission(self, request):
        return False


@admin.register(Valenitines)
class ValentinesAdmin(ModelAdmin):
    date_hierarchy = "date"
    list_display = (
        "profile",
        "sender_display",
        "is_anonymous",
        "moderation_status",
        "date",
        "content_preview",
    )
    list_filter = (
        "moderation_status",
        "is_reported",
        "is_anonymous",
        "theme",
        "date",
    )
    search_fields = (
        "profile__first_name",
        "profile__last_name",
        "sender_profile__first_name",
        "sender_profile__last_name",
        "content",
        "profile__code",
    )
    readonly_fields = (
        "date",
        "profile",
        "sender_profile",
        "content",
        "theme",
        "is_anonymous",
        "report_reason",
        "reported_at",
    )
    fields = (
        "profile",
        "sender_profile",
        "date",
        "content",
        "theme",
        "is_anonymous",
        "is_reported",
        "report_reason",
        "reported_at",
        "moderation_status",
    )
    actions = (
        "hide_reported",
        "restore_messages",
        "export_reported_valentines",
        "export_valentine_stats",
    )

    @admin.display(description="Изпращач")
    def sender_display(self, obj):
        return obj.sender_profile or "Valentine екип"

    @admin.display(description="Послание")
    def content_preview(self, obj):
        return obj.content[:80] + ("…" if len(obj.content) > 80 else "")

    @admin.action(description="Скрий избраните сигнали")
    def hide_reported(self, request, queryset):
        count = queryset.update(
            is_reported=True,
            moderation_status=Valenitines.ModerationStatus.HIDDEN,
        )
        self.message_user(request, f"Скрити послания: {count}.")

    @admin.action(description="Възстанови избраните послания")
    def restore_messages(self, request, queryset):
        count = queryset.update(
            is_reported=False,
            moderation_status=Valenitines.ModerationStatus.RESTORED,
            report_reason="",
            reported_at=None,
        )
        self.message_user(request, f"Възстановени послания: {count}.")

    @admin.action(description="Експорт на докладваните (CSV)")
    def export_reported_valentines(self, request, queryset):
        reported = queryset.filter(is_reported=True).select_related(
            "sender_profile",
            "profile",
        )
        response = HttpResponse(content_type="text/csv; charset=utf-8-sig")
        response["Content-Disposition"] = (
            'attachment; filename="reported_valentines.csv"'
        )
        writer = csv.writer(response)
        writer.writerow(
            (
                "Изпращач",
                "Клас на изпращача",
                "Получател",
                "Клас на получателя",
                "Причина",
                "Послание",
            )
        )
        for valentine in reported:
            sender = valentine.sender_profile
            writer.writerow(
                (
                    csv_safe(sender.full_name if sender else "Valentine екип"),
                    csv_safe(sender.clas_bg if sender else "-"),
                    csv_safe(valentine.profile.full_name),
                    csv_safe(valentine.profile.clas_bg),
                    csv_safe(valentine.get_report_reason_display()),
                    csv_safe(valentine.content),
                )
            )
        return response

    @admin.action(description="Експорт на обобщена статистика (CSV)")
    def export_valentine_stats(self, request, queryset):
        response = HttpResponse(content_type="text/csv; charset=utf-8-sig")
        response["Content-Disposition"] = (
            'attachment; filename="valentine_statistics.csv"'
        )
        writer = csv.writer(response)
        writer.writerow(("Клас", "Получени", "Изпратени"))

        received = {
            row["profile__clas"]: row["total"]
            for row in Valenitines.objects.values("profile__clas").annotate(
                total=Count("id")
            )
        }
        sent = {
            row["sender_profile__clas"]: row["total"]
            for row in Valenitines.objects.exclude(sender_profile=None)
            .values("sender_profile__clas")
            .annotate(total=Count("id"))
        }
        for class_name in sorted(set(received) | set(sent)):
            writer.writerow(
                (
                    csv_safe(class_name),
                    received.get(class_name, 0),
                    sent.get(class_name, 0),
                )
            )
        return response

    def has_add_permission(self, request):
        return False


@admin.register(Settings)
class SettingsAdmin(ModelAdmin):
    list_display = (
        "__str__",
        "valentines_enabled",
        "sending_starts_at",
        "sending_ends_at",
        "reveal_at",
        "daily_limit",
    )
    fieldsets = (
        (
            "Кампания",
            {
                "fields": (
                    "valentines_enabled",
                    "sending_starts_at",
                    "sending_ends_at",
                    "reveal_at",
                    "reveal_date",
                )
            },
        ),
        (
            "Ограничения и поверителност",
            {
                "fields": (
                    "daily_limit",
                    "cooldown_seconds",
                    "show_received_count",
                )
            },
        ),
    )

    def changelist_view(self, request, extra_context=None):
        obj = Settings.objects.first()
        if obj:
            return HttpResponseRedirect(
                reverse("admin:valentine_settings_change", args=(obj.pk,))
            )
        return super().changelist_view(request, extra_context)

    def has_add_permission(self, request):
        return not Settings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
