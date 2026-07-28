import csv
from datetime import date

from django.contrib import admin, messages
from django.db import transaction
from django.db.models import Count
from django.http import HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.utils import timezone
from unfold.admin import ModelAdmin

from .models import (
    ArchivedValentine,
    Profile,
    Register,
    Settings,
    ValentineCampaign,
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
        platform_settings = Settings.objects.first()
        if platform_settings is None:
            self.message_user(
                request,
                "Първо създай Valentine настройки и активна кампания.",
                messages.ERROR,
            )
            return
        campaign = platform_settings.ensure_active_campaign()
        if campaign.is_archived:
            self.message_user(
                request,
                "Не може да се изпраща в архивирана кампания.",
                messages.ERROR,
            )
            return
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
                    campaign=campaign,
                    audience_label=student.full_name,
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
        "campaign",
        "audience_label",
        "is_anonymous",
        "moderation_status",
        "date",
        "content_preview",
    )
    list_filter = (
        "campaign",
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
        "edited_at",
        "profile",
        "sender_profile",
        "campaign",
        "dispatch_id",
        "audience_type",
        "audience_label",
        "content",
        "theme",
        "is_anonymous",
        "is_reported",
        "report_reason",
        "reported_at",
        "moderation_status",
    )
    fields = (
        "profile",
        "sender_profile",
        "campaign",
        "dispatch_id",
        "audience_type",
        "audience_label",
        "date",
        "edited_at",
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

    def get_queryset(self, request):
        active_campaign_id = Settings.objects.values_list(
            "active_campaign_id",
            flat=True,
        ).first()
        return super().get_queryset(request).filter(
            moderation_status__in=(
                Valenitines.ModerationStatus.PENDING,
                Valenitines.ModerationStatus.HIDDEN,
                Valenitines.ModerationStatus.RESTORED,
            ),
            campaign_id=active_campaign_id,
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
        )
        self.message_user(request, f"Възстановени послания: {count}.")

    @admin.action(description="Експорт на модерираните (CSV)")
    def export_reported_valentines(self, request, queryset):
        reported = queryset.filter(
            moderation_status__in=(
                Valenitines.ModerationStatus.PENDING,
                Valenitines.ModerationStatus.HIDDEN,
                Valenitines.ModerationStatus.RESTORED,
            )
        ).select_related("sender_profile", "profile", "campaign")
        response = HttpResponse(content_type="text/csv; charset=utf-8-sig")
        response["Content-Disposition"] = (
            'attachment; filename="reported_valentines.csv"'
        )
        writer = csv.writer(response)
        writer.writerow(
            (
                "Кампания",
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
                    csv_safe(valentine.campaign),
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
        writer.writerow(("Кампания", "Клас", "Получени", "Изпратени"))

        campaign_ids = queryset.values_list(
            "campaign_id",
            flat=True,
        ).distinct()
        statistics_source = Valenitines.objects.filter(
            campaign_id__in=campaign_ids
        )
        received = {
            (row["campaign__name"], row["profile__clas"]): row["total"]
            for row in statistics_source.values(
                "campaign__name",
                "profile__clas",
            ).annotate(total=Count("id"))
        }
        sent = {
            (row["campaign__name"], row["sender_profile__clas"]): row["total"]
            for row in statistics_source.exclude(sender_profile=None)
            .values("campaign__name", "sender_profile__clas")
            .annotate(total=Count("id"))
        }
        for campaign_name, class_name in sorted(set(received) | set(sent)):
            writer.writerow(
                (
                    csv_safe(campaign_name),
                    csv_safe(class_name),
                    received.get((campaign_name, class_name), 0),
                    sent.get((campaign_name, class_name), 0),
                )
            )
        return response

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ArchivedValentine)
class ArchivedValentinesAdmin(ValentinesAdmin):
    def get_queryset(self, request):
        return ModelAdmin.get_queryset(self, request).filter(
            moderation_status__in=(
                Valenitines.ModerationStatus.PENDING,
                Valenitines.ModerationStatus.HIDDEN,
                Valenitines.ModerationStatus.RESTORED,
            ),
            campaign__is_archived=True,
        )


@admin.register(ValentineCampaign)
class ValentineCampaignAdmin(ModelAdmin):
    list_display = (
        "name",
        "year",
        "is_archived",
        "archived_at",
        "message_count",
    )
    ordering = ("-year",)
    search_fields = ("name", "=year")
    list_filter = ("is_archived", "year")
    readonly_fields = ("is_archived", "archived_at", "created_at")
    fields = (
        "name",
        "year",
        "is_archived",
        "archived_at",
        "created_at",
    )
    actions = (
        "make_active",
        "archive_campaigns",
        "restore_campaigns",
    )

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            total_messages=Count("valentines")
        )

    @admin.display(description="Валентинки", ordering="total_messages")
    def message_count(self, obj):
        return obj.total_messages

    @admin.action(
        description="Направи активна и архивирай предишната кампания"
    )
    def make_active(self, request, queryset):
        if queryset.count() != 1:
            self.message_user(
                request,
                "Избери точно една кампания.",
                messages.ERROR,
            )
            return
        campaign = queryset.first()
        with transaction.atomic():
            campaign.is_archived = False
            campaign.archived_at = None
            campaign.save(update_fields=["is_archived", "archived_at"])

            platform_settings = Settings.objects.select_for_update().first()
            if platform_settings is None:
                platform_settings = Settings.objects.create(
                    reveal_date=date(campaign.year, 2, 14),
                    active_campaign=campaign,
                )
            else:
                previous = platform_settings.active_campaign
                if previous and previous.pk != campaign.pk:
                    previous.is_archived = True
                    previous.archived_at = timezone.now()
                    previous.save(
                        update_fields=["is_archived", "archived_at"]
                    )
                Settings.objects.filter(pk=platform_settings.pk).update(
                    active_campaign=campaign
                )
        self.message_user(
            request,
            f"{campaign} вече е активната кампания.",
            messages.SUCCESS,
        )

    @admin.action(description="Архивирай избраните минали кампании")
    def archive_campaigns(self, request, queryset):
        active_campaign_id = (
            Settings.objects.values_list(
                "active_campaign_id",
                flat=True,
            ).first()
        )
        if active_campaign_id and queryset.filter(
            pk=active_campaign_id
        ).exists():
            self.message_user(
                request,
                "Активната кампания не може да бъде архивирана. "
                "Първо активирай новата година.",
                messages.ERROR,
            )
            return
        count = queryset.update(
            is_archived=True,
            archived_at=timezone.now(),
        )
        self.message_user(
            request,
            f"Архивирани кампании: {count}.",
            messages.SUCCESS,
        )

    @admin.action(description="Възстанови избраните кампании от архива")
    def restore_campaigns(self, request, queryset):
        count = queryset.update(
            is_archived=False,
            archived_at=None,
        )
        self.message_user(
            request,
            f"Възстановени кампании: {count}.",
            messages.SUCCESS,
        )

    def has_delete_permission(self, request, obj=None):
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
                    "active_campaign",
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
                    "group_daily_limit",
                    "cooldown_seconds",
                    "show_received_count",
                )
            },
        ),
    )
    readonly_fields = ("active_campaign",)

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
