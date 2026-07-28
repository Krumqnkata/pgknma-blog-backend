import csv
import re
from datetime import date

from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from unfold.admin import ModelAdmin

from .forms import ProfileCsvImportForm
from .models import (
    ArchivedValentine,
    Profile,
    Register,
    Settings,
    ValentineCampaign,
    Valenitines,
    generate_unique_code,
    normalize_class_code,
)
from .profile_import import (
    ProfileImportError,
    apply_profile_import,
    build_profile_import_plan,
    parse_profile_csv,
)
from .profile_pdf import ProfilePdfError, build_profiles_pdf


def csv_safe(value):
    """Prevent spreadsheet formulas in exported user-controlled values."""
    text = "" if value is None else str(value)
    if text.startswith(("=", "+", "-", "@")):
        return f"'{text}"
    return text


def class_label(class_name):
    class_name = normalize_class_code(class_name)
    if class_name == "U":
        return "Учители"
    mapping = {"A": "А", "B": "Б", "V": "В", "G": "Г"}
    if not class_name or len(class_name) < 2:
        return class_name or "Без клас"
    return (
        f"{class_name[:-1]}"
        f"{mapping.get(class_name[-1].upper(), class_name[-1])} клас"
    )


def class_sort_key(class_name):
    class_name = normalize_class_code(class_name)
    if class_name == "U":
        return (10_000, "")
    try:
        return (int(class_name[:-1]), class_name[-1])
    except (TypeError, ValueError):
        return (9_999, class_name or "")


STUDENT_CLASS_PATTERN = re.compile(r"^(?P<grade>\d{1,2})(?P<letter>[ABVG])$")


def next_student_class(class_name):
    normalized_class = normalize_class_code(class_name)
    match = STUDENT_CLASS_PATTERN.fullmatch(normalized_class)
    if not match:
        return None
    grade = int(match.group("grade"))
    if grade >= 12:
        return None
    return f"{grade + 1}{match.group('letter')}"


@admin.register(Profile)
class ProfileAdmin(ModelAdmin):
    change_list_template = "admin/valentine/profile/change_list.html"
    list_display = (
        "full_name_display",
        "clas",
        "academic_year_display",
        "email",
        "code",
        "is_active",
    )
    search_fields = ("code", "first_name", "last_name", "email", "clas")
    list_filter = ("is_active", "academic_year_start", "clas")
    readonly_fields = ("uuid", "code")
    actions = (
        "promote_to_next_academic_year",
        "deactivate_profiles",
        "reactivate_profiles",
        "export_selected_profiles_pdf",
        "send_system_valentine",
        "regenerate_access_codes",
    )

    @admin.display(description="Име")
    def full_name_display(self, obj):
        return obj.full_name

    @admin.display(
        description="Учебна година",
        ordering="academic_year_start",
    )
    def academic_year_display(self, obj):
        return obj.academic_year_label

    def get_urls(self):
        custom_urls = [
            path(
                "import-csv/",
                self.admin_site.admin_view(self.import_profiles_csv),
                name="valentine_profile_import_csv",
            ),
            path(
                "import-template.csv",
                self.admin_site.admin_view(self.download_import_template),
                name="valentine_profile_import_template",
            ),
            path(
                "active-codes.pdf",
                self.admin_site.admin_view(self.export_active_profiles_pdf),
                name="valentine_profile_active_codes_pdf",
            ),
        ]
        return custom_urls + super().get_urls()

    def changelist_view(self, request, extra_context=None):
        extra_context = dict(extra_context or {})
        extra_context.update(
            {
                "profile_import_url": reverse(
                    "admin:valentine_profile_import_csv"
                ),
                "profile_pdf_url": reverse(
                    "admin:valentine_profile_active_codes_pdf"
                ),
                "can_import_profiles": self.has_add_permission(request),
            }
        )
        return super().changelist_view(request, extra_context)

    def _pdf_response(self, request, queryset, title, filename):
        try:
            pdf_data = build_profiles_pdf(queryset, title=title)
        except ProfilePdfError as exc:
            self.message_user(request, str(exc), messages.ERROR)
            return HttpResponseRedirect(
                reverse("admin:valentine_profile_changelist")
            )
        response = HttpResponse(pdf_data, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        response["X-Content-Type-Options"] = "nosniff"
        response["Cache-Control"] = "private, no-store"
        return response

    @admin.action(description="Изтегли избраните профили като PDF")
    def export_selected_profiles_pdf(self, request, queryset):
        queryset = queryset.order_by(
            "clas",
            "first_name",
            "last_name",
        )
        return self._pdf_response(
            request,
            queryset,
            "Избрани кодове за Valentine платформата",
            "valentine-selected-codes.pdf",
        )

    def export_active_profiles_pdf(self, request):
        if not self.has_view_permission(request):
            raise PermissionDenied
        queryset = Profile.objects.filter(is_active=True).order_by(
            "clas",
            "first_name",
            "last_name",
        )
        return self._pdf_response(
            request,
            queryset,
            "Активни кодове за Valentine платформата",
            f"valentine-active-codes-{timezone.localdate():%Y-%m-%d}.pdf",
        )

    def download_import_template(self, request):
        if not self.has_add_permission(request):
            raise PermissionDenied
        response = HttpResponse(
            content_type="text/csv; charset=utf-8",
        )
        response["Content-Disposition"] = (
            'attachment; filename="valentine-students-template.csv"'
        )
        response.write("\ufeff")
        writer = csv.writer(response, delimiter=";")
        writer.writerow(
            ("first_name", "last_name", "class", "email", "code")
        )
        return response

    def import_profiles_csv(self, request):
        if not self.has_add_permission(request):
            raise PermissionDenied

        form = ProfileCsvImportForm(
            request.POST or None,
            request.FILES or None,
        )
        preview_rows = []
        import_errors = []
        import_token = ""

        if (
            request.method == "POST"
            and request.POST.get("confirm") == "yes"
        ):
            try:
                payload = signing.loads(
                    request.POST.get("import_token", ""),
                    salt="valentine-profile-csv-import",
                    max_age=60 * 60,
                )
                if payload.get("user_id") != request.user.pk:
                    raise signing.BadSignature
                result = apply_profile_import(
                    payload["rows"],
                    payload["academic_year_start"],
                )
            except signing.SignatureExpired:
                import_errors.append(
                    "Предварителният преглед е изтекъл. "
                    "Качи CSV файла отново."
                )
            except (signing.BadSignature, KeyError, TypeError):
                import_errors.append(
                    "Невалидно потвърждение. Качи CSV файла отново."
                )
            except ProfileImportError as exc:
                import_errors.extend(exc.errors)
            else:
                self.message_user(
                    request,
                    f"CSV импортът завърши: {result['created']} създадени, "
                    f"{result['updated']} обновени"
                    + (
                        f", {result['reactivated']} възстановени"
                        if result["reactivated"]
                        else ""
                    )
                    + ".",
                    messages.SUCCESS,
                )
                return HttpResponseRedirect(
                    reverse("admin:valentine_profile_changelist")
                )

        elif request.method == "POST" and form.is_valid():
            try:
                rows, parse_errors = parse_profile_csv(
                    form.cleaned_data["csv_file"]
                )
            except ProfileImportError as exc:
                import_errors.extend(exc.errors)
            else:
                plans, plan_errors = build_profile_import_plan(rows)
                import_errors.extend(parse_errors)
                import_errors.extend(plan_errors)
                if not import_errors:
                    action_labels = {
                        "create": "Нов профил",
                        "update": "Обновяване",
                        "reactivate": "Възстановяване",
                    }
                    preview_rows = [
                        {
                            **plan["row"],
                            "action_label": action_labels[plan["action"]],
                            "display_code": plan["display_code"],
                            "display_email": plan["display_email"],
                        }
                        for plan in plans
                    ]
                    import_token = signing.dumps(
                        {
                            "user_id": request.user.pk,
                            "academic_year_start": form.cleaned_data[
                                "academic_year_start"
                            ],
                            "rows": rows,
                        },
                        salt="valentine-profile-csv-import",
                        compress=True,
                    )

        context = {
            **self.admin_site.each_context(request),
            "title": "Импорт на ученици от CSV",
            "opts": self.model._meta,
            "media": self.media + form.media,
            "form": form,
            "preview_rows": preview_rows,
            "import_errors": import_errors,
            "import_token": import_token,
            "template_url": reverse(
                "admin:valentine_profile_import_template"
            ),
            "changelist_url": reverse(
                "admin:valentine_profile_changelist"
            ),
        }
        return TemplateResponse(
            request,
            "admin/valentine/profile/import_csv.html",
            context,
        )

    @admin.action(
        description="Мигрирай към следващата учебна година"
    )
    def promote_to_next_academic_year(self, request, queryset):
        if request.POST.get("apply") == "yes":
            profile_ids = list(queryset.values_list("pk", flat=True))
            promoted = []
            with transaction.atomic():
                locked_profiles = list(
                    Profile.objects.select_for_update()
                    .filter(pk__in=profile_ids)
                    .order_by("clas", "first_name", "last_name")
                )
                for profile in locked_profiles:
                    target_class = (
                        next_student_class(profile.clas)
                        if profile.is_active
                        else None
                    )
                    if target_class is None:
                        continue
                    profile.clas = target_class
                    profile.academic_year_start += 1
                    promoted.append(profile)
                if promoted:
                    Profile.objects.bulk_update(
                        promoted,
                        ("clas", "academic_year_start"),
                    )
            skipped = len(profile_ids) - len(promoted)
            self.message_user(
                request,
                f"Преместени профили: {len(promoted)}."
                + (
                    f" Пропуснати: {skipped} "
                    "(учители, 12. клас, неактивни или невалиден клас)."
                    if skipped
                    else ""
                ),
                messages.SUCCESS,
            )
            return None

        preview_rows = []
        for profile in queryset.order_by(
            "clas",
            "first_name",
            "last_name",
        ):
            normalized_class = normalize_class_code(profile.clas)
            target_class = (
                next_student_class(normalized_class)
                if profile.is_active
                else None
            )
            if not profile.is_active:
                note = "Неактивен профил"
            elif normalized_class == "U":
                note = "Учител – не се променя"
            elif normalized_class.startswith("12"):
                note = "12. клас – деактивирай след завършване"
            elif target_class is None:
                note = "Неподдържан формат на класа"
            else:
                note = ""
            preview_rows.append(
                {
                    "profile": profile,
                    "target_class": target_class,
                    "target_class_label": (
                        class_label(target_class)
                        if target_class
                        else ""
                    ),
                    "target_year": (
                        f"{profile.academic_year_start + 1}/"
                        f"{profile.academic_year_start + 2}"
                        if target_class
                        else ""
                    ),
                    "note": note,
                }
            )

        context = {
            **self.admin_site.each_context(request),
            "title": "Мигриране към следващата учебна година",
            "opts": self.model._meta,
            "profiles": preview_rows,
            "action_checkbox_name": helpers.ACTION_CHECKBOX_NAME,
            "media": self.media,
        }
        return TemplateResponse(
            request,
            "admin/valentine/profile/promote_confirmation.html",
            context,
        )

    @admin.action(
        description="Деактивирай без изтриване на историята"
    )
    def deactivate_profiles(self, request, queryset):
        profile_ids = list(queryset.values_list("pk", flat=True))
        count = Profile.objects.filter(
            pk__in=profile_ids,
            is_active=True,
        ).update(is_active=False)
        Register.objects.filter(
            profile_id__in=profile_ids,
            revoked=False,
        ).update(revoked=True)
        self.message_user(
            request,
            f"Деактивирани профили: {count}. Историята е запазена.",
            messages.SUCCESS,
        )

    @admin.action(description="Възстанови избраните профили")
    def reactivate_profiles(self, request, queryset):
        count = queryset.filter(is_active=False).update(is_active=True)
        self.message_user(
            request,
            f"Възстановени профили: {count}. "
            "Потребителите трябва да влязат отново.",
            messages.SUCCESS,
        )

    @admin.action(description="Изпрати позитивна системна валентинка")
    def send_system_valentine(self, request, queryset):
        inactive_count = queryset.filter(is_active=False).count()
        queryset = queryset.filter(is_active=True)
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
                    recipient_class_snapshot=normalize_class_code(
                        student.clas
                    ),
                    audience_label=student.full_name,
                    content=wishes[index % len(wishes)],
                    is_anonymous=True,
                    theme=Valenitines.Theme.CLASSIC,
                )
            )
        Valenitines.objects.bulk_create(rows)
        self.message_user(
            request,
            f"Изпратени са {len(rows)} системни валентинки."
            + (
                f" Пропуснати неактивни профили: {inactive_count}."
                if inactive_count
                else ""
            ),
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

    def save_model(self, request, obj, form, change):
        was_active = (
            Profile.objects.filter(pk=obj.pk)
            .values_list("is_active", flat=True)
            .first()
            if change
            else None
        )
        super().save_model(request, obj, form, change)
        if was_active and not obj.is_active:
            obj.sessions.filter(revoked=False).update(revoked=True)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Register)
class RegisterAdmin(ModelAdmin):
    list_display = ("profile", "created_at", "expires_at", "revoked", "valid_display")
    list_select_related = ("profile",)
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
        "recipient_class_snapshot",
        "sender_class_snapshot",
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
        "recipient_class_snapshot",
        "sender_class_snapshot",
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
                    csv_safe(
                        class_label(valentine.sender_class_snapshot)
                        if sender
                        else "-"
                    ),
                    csv_safe(valentine.profile.full_name),
                    csv_safe(
                        class_label(valentine.recipient_class_snapshot)
                    ),
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
            (
                row["campaign__name"],
                row["recipient_class_snapshot"],
            ): row["total"]
            for row in statistics_source.values(
                "campaign__name",
                "recipient_class_snapshot",
            ).annotate(total=Count("id"))
        }
        sent = {
            (
                row["campaign__name"],
                row["sender_class_snapshot"],
            ): row["total"]
            for row in statistics_source.exclude(sender_profile=None)
            .values("campaign__name", "sender_class_snapshot")
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
    change_form_before_template = (
        "admin/valentine/valentinecampaign/dashboard.html"
    )
    list_display = (
        "name",
        "year",
        "is_archived",
        "archived_at",
        "dispatch_count",
        "delivery_count",
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

    class Media:
        css = {
            "all": ("valentines/admin_campaign.css",),
        }

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            total_dispatches=Count(
                "valentines__dispatch_id",
                distinct=True,
            ),
            total_deliveries=Count("valentines"),
        )

    @admin.display(description="Пратки", ordering="total_dispatches")
    def dispatch_count(self, obj):
        return obj.total_dispatches

    @admin.display(description="Доставки", ordering="total_deliveries")
    def delivery_count(self, obj):
        return obj.total_deliveries

    def get_urls(self):
        custom_urls = [
            path(
                "<path:object_id>/statistics.csv",
                self.admin_site.admin_view(self.export_statistics),
                name="valentine_valentinecampaign_statistics",
            ),
        ]
        return custom_urls + super().get_urls()

    def change_view(
        self,
        request,
        object_id,
        form_url="",
        extra_context=None,
    ):
        extra_context = dict(extra_context or {})
        campaign = self.get_object(request, object_id)
        if (
            campaign is not None
            and self.has_view_or_change_permission(request, campaign)
        ):
            extra_context["campaign_stats"] = self.build_statistics(
                campaign
            )
            extra_context["campaign_statistics_export_url"] = reverse(
                "admin:valentine_valentinecampaign_statistics",
                args=(campaign.pk,),
            )
        return super().change_view(
            request,
            object_id,
            form_url,
            extra_context,
        )

    def build_statistics(self, campaign):
        source = Valenitines.objects.filter(campaign=campaign)
        totals = source.aggregate(
            deliveries=Count("id"),
            dispatches=Count("dispatch_id", distinct=True),
            unique_senders=Count(
                "sender_profile",
                distinct=True,
                filter=Q(sender_profile__isnull=False),
            ),
            unique_recipients=Count("profile", distinct=True),
            system_dispatches=Count(
                "dispatch_id",
                distinct=True,
                filter=Q(sender_profile__isnull=True),
            ),
            anonymous_dispatches=Count(
                "dispatch_id",
                distinct=True,
                filter=Q(is_anonymous=True),
            ),
            signed_dispatches=Count(
                "dispatch_id",
                distinct=True,
                filter=Q(is_anonymous=False),
            ),
            individual_dispatches=Count(
                "dispatch_id",
                distinct=True,
                filter=Q(
                    audience_type=Valenitines.AudienceType.INDIVIDUAL
                ),
            ),
            individual_deliveries=Count(
                "id",
                filter=Q(
                    audience_type=Valenitines.AudienceType.INDIVIDUAL
                ),
            ),
            class_dispatches=Count(
                "dispatch_id",
                distinct=True,
                filter=Q(
                    audience_type=Valenitines.AudienceType.CLASS
                ),
            ),
            class_deliveries=Count(
                "id",
                filter=Q(
                    audience_type=Valenitines.AudienceType.CLASS
                ),
            ),
            teacher_dispatches=Count(
                "dispatch_id",
                distinct=True,
                filter=Q(
                    audience_type=Valenitines.AudienceType.TEACHERS
                ),
            ),
            teacher_deliveries=Count(
                "id",
                filter=Q(
                    audience_type=Valenitines.AudienceType.TEACHERS
                ),
            ),
            reports=Count(
                "id",
                filter=Q(reported_at__isnull=False),
            ),
            reported_dispatches=Count(
                "dispatch_id",
                distinct=True,
                filter=Q(reported_at__isnull=False),
            ),
            pending=Count(
                "id",
                filter=Q(
                    moderation_status=(
                        Valenitines.ModerationStatus.PENDING
                    )
                ),
            ),
            hidden=Count(
                "id",
                filter=Q(
                    moderation_status=(
                        Valenitines.ModerationStatus.HIDDEN
                    )
                ),
            ),
            restored=Count(
                "id",
                filter=Q(
                    moderation_status=(
                        Valenitines.ModerationStatus.RESTORED
                    )
                ),
            ),
        )

        received_by_class = {
            row["recipient_class_snapshot"]: row
            for row in source.values(
                "recipient_class_snapshot"
            ).annotate(
                received=Count("id"),
                reports=Count(
                    "id",
                    filter=Q(reported_at__isnull=False),
                ),
            )
        }
        sent_by_class = {
            row["sender_class_snapshot"]: row
            for row in source.exclude(sender_profile=None)
            .values("sender_class_snapshot")
            .annotate(
                active_senders=Count(
                    "sender_profile",
                    distinct=True,
                ),
                sent_dispatches=Count(
                    "dispatch_id",
                    distinct=True,
                ),
            )
        }
        class_rows = []
        for class_name in sorted(
            set(received_by_class) | set(sent_by_class),
            key=class_sort_key,
        ):
            received = received_by_class.get(class_name, {})
            sent = sent_by_class.get(class_name, {})
            class_rows.append(
                {
                    "label": class_label(class_name),
                    "active_senders": sent.get("active_senders", 0),
                    "sent_dispatches": sent.get("sent_dispatches", 0),
                    "received": received.get("received", 0),
                    "reports": received.get("reports", 0),
                }
            )

        daily_rows = list(
            source.annotate(
                day=TruncDate(
                    "date",
                    tzinfo=timezone.get_current_timezone(),
                )
            )
            .values("day")
            .annotate(
                dispatches=Count("dispatch_id", distinct=True),
                deliveries=Count("id"),
                reports=Count(
                    "id",
                    filter=Q(reported_at__isnull=False),
                ),
            )
            .order_by("day")
        )
        daily_max = max(
            (row["deliveries"] for row in daily_rows),
            default=1,
        )

        reason_labels = dict(Valenitines.ReportReason.choices)
        reason_rows = [
            {
                "label": reason_labels.get(
                    row["report_reason"],
                    row["report_reason"],
                ),
                "count": row["total"],
            }
            for row in source.filter(reported_at__isnull=False)
            .exclude(report_reason="")
            .values("report_reason")
            .annotate(total=Count("id"))
            .order_by("-total", "report_reason")
        ]

        active_campaign_id = Settings.objects.values_list(
            "active_campaign_id",
            flat=True,
        ).first()
        if campaign.pk == active_campaign_id:
            status_label = "Активна кампания"
            status_class = "active"
        elif campaign.is_archived:
            status_label = "Архивирана кампания"
            status_class = "archived"
        else:
            status_label = "Неактивна кампания"
            status_class = "inactive"

        cards = (
            {
                "label": "Пратки",
                "value": totals["dispatches"],
                "description": "Уникални изпращания",
            },
            {
                "label": "Доставки",
                "value": totals["deliveries"],
                "description": "Всички получени копия",
            },
            {
                "label": "Изпращачи",
                "value": totals["unique_senders"],
                "description": "Уникални профили",
            },
            {
                "label": "Получатели",
                "value": totals["unique_recipients"],
                "description": "Уникални профили",
            },
            {
                "label": "Сигнали",
                "value": totals["reports"],
                "description": (
                    f"{totals['reported_dispatches']} засегнати пратки"
                ),
            },
            {
                "label": "Анонимни",
                "value": totals["anonymous_dispatches"],
                "description": "Уникални пратки",
            },
        )
        audience_rows = (
            {
                "label": "До един човек",
                "dispatches": totals["individual_dispatches"],
                "deliveries": totals["individual_deliveries"],
            },
            {
                "label": "До цял клас",
                "dispatches": totals["class_dispatches"],
                "deliveries": totals["class_deliveries"],
            },
            {
                "label": "До всички учители",
                "dispatches": totals["teacher_dispatches"],
                "deliveries": totals["teacher_deliveries"],
            },
        )
        moderation_rows = (
            {
                "label": "Чака преглед",
                "value": totals["pending"],
            },
            {
                "label": "Скрито от модератор",
                "value": totals["hidden"],
            },
            {
                "label": "Възстановено",
                "value": totals["restored"],
            },
        )
        return {
            "status_label": status_label,
            "status_class": status_class,
            "totals": totals,
            "cards": cards,
            "audience_rows": audience_rows,
            "moderation_rows": moderation_rows,
            "reason_rows": reason_rows,
            "class_rows": class_rows,
            "daily_rows": daily_rows,
            "daily_max": daily_max,
        }

    def export_statistics(self, request, object_id):
        campaign = get_object_or_404(
            self.get_queryset(request),
            pk=object_id,
        )
        if not self.has_view_or_change_permission(request, campaign):
            raise PermissionDenied

        statistics = self.build_statistics(campaign)
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = (
            f'attachment; filename="valentine-{campaign.year}-statistics.csv"'
        )
        response.write("\ufeff")
        writer = csv.writer(response)
        writer.writerow(("Кампания", csv_safe(campaign.name)))
        writer.writerow(("Статус", statistics["status_label"]))
        writer.writerow(())
        writer.writerow(("Обобщение", "Стойност", "Пояснение"))
        for card in statistics["cards"]:
            writer.writerow(
                (
                    card["label"],
                    card["value"],
                    card["description"],
                )
            )
        writer.writerow(
            (
                "Системни пратки",
                statistics["totals"]["system_dispatches"],
                "Без потребителски изпращач",
            )
        )
        writer.writerow(
            (
                "Подписани пратки",
                statistics["totals"]["signed_dispatches"],
                "С видимо име за получателя",
            )
        )
        writer.writerow(())
        writer.writerow(("Тип получатели", "Пратки", "Доставки"))
        for row in statistics["audience_rows"]:
            writer.writerow(
                (
                    row["label"],
                    row["dispatches"],
                    row["deliveries"],
                )
            )
        writer.writerow(())
        writer.writerow(("Модерация", "Брой"))
        for row in statistics["moderation_rows"]:
            writer.writerow((row["label"], row["value"]))
        for row in statistics["reason_rows"]:
            writer.writerow((f"Причина: {row['label']}", row["count"]))
        writer.writerow(())
        writer.writerow(
            (
                "Клас",
                "Активни изпращачи",
                "Изпратени пратки",
                "Получени доставки",
                "Сигнали",
            )
        )
        for row in statistics["class_rows"]:
            writer.writerow(
                (
                    csv_safe(row["label"]),
                    row["active_senders"],
                    row["sent_dispatches"],
                    row["received"],
                    row["reports"],
                )
            )
        writer.writerow(())
        writer.writerow(("Ден", "Пратки", "Доставки", "Сигнали"))
        for row in statistics["daily_rows"]:
            writer.writerow(
                (
                    row["day"].isoformat(),
                    row["dispatches"],
                    row["deliveries"],
                    row["reports"],
                )
            )
        return response

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
