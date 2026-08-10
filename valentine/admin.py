import csv
import re
from datetime import datetime

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

from .forms import (
    CampaignActivationForm,
    ProfileCsvImportForm,
    ProfilePromotionForm,
    SystemValentineForm,
)
from .models import (
    ArchivedValentine,
    Profile,
    Register,
    Settings,
    ValentineCampaign,
    ValentineModerationEvent,
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
        "pin_status_display",
        "is_active",
    )
    search_fields = ("code", "first_name", "last_name", "email", "clas")
    list_filter = ("is_active", "academic_year_start", "clas")
    readonly_fields = (
        "uuid",
        "pin_status_display",
        "pin_set_at",
        "pin_failed_attempts",
        "pin_locked_until",
    )
    actions = (
        "promote_to_next_academic_year",
        "deactivate_profiles",
        "reactivate_profiles",
        "export_selected_profiles_pdf",
        "send_system_valentine",
        "regenerate_access_codes",
        "reset_pins",
    )

    @admin.display(description="Име")
    def full_name_display(self, obj):
        return obj.full_name

    @admin.display(description="PIN настроен", boolean=True)
    def pin_status_display(self, obj):
        return obj.has_pin

    @admin.display(
        description="Учебна година",
        ordering="academic_year_start",
    )
    def academic_year_display(self, obj):
        return obj.academic_year_label

    def has_view_codes_permission(self, request):
        return request.user.has_perm("valentine.view_profile_codes")

    def get_list_display(self, request):
        display = list(super().get_list_display(request))
        if not self.has_view_codes_permission(request):
            display.remove("code")
        return tuple(display)

    def get_search_fields(self, request):
        fields = list(super().get_search_fields(request))
        if not self.has_view_codes_permission(request):
            fields.remove("code")
        return tuple(fields)

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if self.has_view_codes_permission(request):
            fields.append("code")
        return tuple(fields)

    def get_exclude(self, request, obj=None):
        excluded = list(super().get_exclude(request, obj) or ())
        if not self.has_view_codes_permission(request):
            excluded.append("code")
        return tuple(excluded)

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not self.has_view_codes_permission(request):
            actions.pop("export_selected_profiles_pdf", None)
            actions.pop("regenerate_access_codes", None)
        return actions

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
            path(
                "send-system-valentine/",
                self.admin_site.admin_view(self.send_system_valentine_page),
                name="valentine_profile_send_system_valentine",
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
                "system_valentine_url": reverse(
                    "admin:valentine_profile_send_system_valentine"
                ),
                "can_import_profiles": self.has_add_permission(request),
                "can_send_system_valentine": self.has_change_permission(
                    request
                ),
                "can_view_profile_codes": (
                    self.has_view_codes_permission(request)
                ),
            }
        )
        return super().changelist_view(request, extra_context)

    def send_system_valentine_page(self, request):
        if not self.has_change_permission(request):
            raise PermissionDenied

        changelist_url = reverse("admin:valentine_profile_changelist")
        platform_settings = Settings.objects.first()
        if platform_settings is None:
            self.message_user(
                request,
                "Първо създай Valentine настройки и активна кампания.",
                messages.ERROR,
            )
            return HttpResponseRedirect(changelist_url)
        campaign = platform_settings.ensure_active_campaign()
        if campaign.is_archived:
            self.message_user(
                request,
                "Не може да се изпраща в архивирана кампания.",
                messages.ERROR,
            )
            return HttpResponseRedirect(changelist_url)

        preview = None
        form = SystemValentineForm(request.POST or None)
        if request.method == "POST" and request.POST.get("confirm") == "yes":
            try:
                payload = signing.loads(
                    request.POST.get("system_valentine_token", ""),
                    salt="valentine-system-message",
                    max_age=30 * 60,
                )
                if payload.get("user_id") != request.user.pk:
                    raise signing.BadSignature
                recipient_ids = payload["recipient_ids"]
                content = payload["content"]
                theme = payload["theme"]
            except (signing.BadSignature, signing.SignatureExpired, KeyError, TypeError):
                self.message_user(
                    request,
                    "Предварителният преглед е невалиден или е изтекъл.",
                    messages.ERROR,
                )
                return HttpResponseRedirect(
                    reverse("admin:valentine_profile_send_system_valentine")
                )

            recipients = list(
                Profile.objects.filter(pk__in=recipient_ids, is_active=True).order_by(
                    "clas", "first_name", "last_name"
                )
            )
            if not recipients:
                self.message_user(
                    request,
                    "Няма активни профили за изпращане.",
                    messages.ERROR,
                )
                return HttpResponseRedirect(changelist_url)
            Valenitines.objects.bulk_create(
                [
                    Valenitines(
                        profile=profile,
                        sender_profile=None,
                        campaign=campaign,
                        recipient_class_snapshot=normalize_class_code(profile.clas),
                        audience_label=profile.full_name,
                        content=content,
                        theme=theme,
                        is_anonymous=True,
                    )
                    for profile in recipients
                ]
            )
            self.message_user(
                request,
                f"Системната валентинка е изпратена до {len(recipients)} профила.",
                messages.SUCCESS,
            )
            return HttpResponseRedirect(changelist_url)

        if request.method == "POST" and form.is_valid():
            recipients = form.cleaned_data["resolved_recipients"]
            preview = {
                "recipient_count": len(recipients),
                "content": form.cleaned_data["content"],
                "theme": form.cleaned_data["theme"],
                "token": signing.dumps(
                    {
                        "user_id": request.user.pk,
                        "recipient_ids": [str(profile.pk) for profile in recipients],
                        "content": form.cleaned_data["content"],
                        "theme": form.cleaned_data["theme"],
                    },
                    salt="valentine-system-message",
                ),
            }

        context = {
            **self.admin_site.each_context(request),
            "title": "Изпрати системна валентинка",
            "form": form,
            "preview": preview,
            "campaign": campaign,
            "changelist_url": changelist_url,
        }
        return TemplateResponse(
            request,
            "admin/valentine/profile/send_system_valentine.html",
            context,
        )

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

    @admin.action(
        description="Изтегли избраните профили като PDF",
        permissions=("view_codes",),
    )
    def export_selected_profiles_pdf(self, request, queryset):
        if not self.has_view_codes_permission(request):
            raise PermissionDenied
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
        if (
            not self.has_view_permission(request)
            or not self.has_view_codes_permission(request)
        ):
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
        headers = ["first_name", "last_name", "class", "email"]
        if self.has_view_codes_permission(request):
            headers.append("code")
        writer.writerow(headers)
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
                    allow_existing_updates=self.has_change_permission(
                        request
                    ),
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
                import_errors.extend(parse_errors)
                supplied_code_rows = [
                    row["row_number"] for row in rows if row.get("code")
                ]
                if supplied_code_rows and not self.has_view_codes_permission(request):
                    import_errors.append(
                        "Нямаш право да задаваш Valentine кодове чрез CSV "
                        "(редове: "
                        + ", ".join(str(number) for number in supplied_code_rows)
                        + "). Премахни колоната с кодовете."
                    )
                    if not self.has_change_permission(request):
                        import_errors.append(
                            "Нямаш право да обновяваш или възстановяваш "
                            "съществуващи профили."
                        )
                    plans = []
                else:
                    plans, plan_errors = build_profile_import_plan(rows)
                    import_errors.extend(plan_errors)
                if (
                    not self.has_change_permission(request)
                    and any(
                        plan["action"] != "create"
                        for plan in plans
                    )
                ):
                    import_errors.append(
                        "Нямаш право да обновяваш или възстановяваш "
                        "съществуващи профили."
                    )
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
                            "display_code": (
                                plan["display_code"]
                                if self.has_view_codes_permission(request)
                                else (
                                    "генерира се при запис"
                                    if plan["action"] == "create"
                                    and not plan["row"]["code"]
                                    else "••••••"
                                )
                            ),
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
        description="Мигрирай към следващата учебна година",
        permissions=("change",),
    )
    def promote_to_next_academic_year(self, request, queryset):
        if not self.has_change_permission(request):
            raise PermissionDenied
        selected_years = list(
            queryset.exclude(clas="U").values_list(
                "academic_year_start",
                flat=True,
            )
        )
        default_target_year = (
            max(selected_years) + 1
            if selected_years
            else timezone.localdate().year
        )
        is_confirmation = request.POST.get("apply") == "yes"
        promotion_form = ProfilePromotionForm(
            request.POST if is_confirmation else None,
            initial={
                "target_academic_year_start": default_target_year,
            },
        )
        target_year = default_target_year
        if is_confirmation and promotion_form.is_valid():
            target_year = promotion_form.cleaned_data[
                "target_academic_year_start"
            ]
            profile_ids = list(queryset.values_list("pk", flat=True))
            promoted = []
            with transaction.atomic():
                locked_profiles = list(
                    Profile.objects.select_for_update()
                    .filter(pk__in=profile_ids)
                    .order_by("clas", "first_name", "last_name")
                )
                for profile in locked_profiles:
                    if profile.academic_year_start != target_year - 1:
                        continue
                    target_class = (
                        next_student_class(profile.clas)
                        if profile.is_active
                        else None
                    )
                    if target_class is None:
                        continue
                    profile.clas = target_class
                    profile.academic_year_start = target_year
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
                    "(вече мигрирани, от друга учебна година, учители, "
                    "12. клас, неактивни или невалиден клас)."
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
                if (
                    profile.is_active
                    and profile.academic_year_start == target_year - 1
                )
                else None
            )
            if not profile.is_active:
                note = "Неактивен профил"
            elif normalized_class == "U":
                note = "Учител – не се променя"
            elif profile.academic_year_start >= target_year:
                note = "Вече е мигриран за тази или по-нова учебна година"
            elif profile.academic_year_start < target_year - 1:
                note = "Профилът е от по-стара учебна година"
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
                        f"{target_year}/"
                        f"{target_year + 1}"
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
            "promotion_form": promotion_form,
            "action_checkbox_name": helpers.ACTION_CHECKBOX_NAME,
            "media": self.media,
        }
        return TemplateResponse(
            request,
            "admin/valentine/profile/promote_confirmation.html",
            context,
        )

    @admin.action(
        description="Деактивирай без изтриване на историята",
        permissions=("change",),
    )
    def deactivate_profiles(self, request, queryset):
        if not self.has_change_permission(request):
            raise PermissionDenied
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

    @admin.action(
        description="Възстанови избраните профили",
        permissions=("change",),
    )
    def reactivate_profiles(self, request, queryset):
        if not self.has_change_permission(request):
            raise PermissionDenied
        count = queryset.filter(is_active=False).update(is_active=True)
        self.message_user(
            request,
            f"Възстановени профили: {count}. "
            "Потребителите трябва да влязат отново.",
            messages.SUCCESS,
        )

    @admin.action(
        description="Изпрати позитивна системна валентинка",
        permissions=("change",),
    )
    def send_system_valentine(self, request, queryset):
        if not self.has_change_permission(request):
            raise PermissionDenied
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

    @admin.action(
        description="Генерирай нов код за достъп",
        permissions=("view_codes",),
    )
    def regenerate_access_codes(self, request, queryset):
        if not self.has_view_codes_permission(request):
            raise PermissionDenied
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

    @admin.action(
        description="Нулирай PIN кодовете и прекрати сесиите",
        permissions=("change",),
    )
    def reset_pins(self, request, queryset):
        if not self.has_change_permission(request):
            raise PermissionDenied
        profile_ids = list(queryset.values_list("pk", flat=True))
        if not profile_ids:
            return
        updated = Profile.objects.filter(pk__in=profile_ids).update(
            pin_hash="",
            pin_set_at=None,
            pin_failed_attempts=0,
            pin_locked_until=None,
        )
        Register.objects.filter(profile_id__in=profile_ids, revoked=False).update(
            revoked=True
        )
        self.message_user(
            request,
            (
                f"Нулирани PIN кодове: {updated}. "
                "Активните Valentine сесии на тези профили са прекратени."
            ),
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
    list_display = (
        "profile",
        "ip_address",
        "network_operator",
        "network_asn_display",
        "created_at",
        "expires_at",
        "revoked",
        "valid_display",
    )
    list_select_related = ("profile",)
    list_filter = ("revoked", "created_at", "expires_at")
    search_fields = (
        "profile__first_name",
        "profile__last_name",
        "profile__code",
        "ip_address",
        "network_operator",
    )
    readonly_fields = (
        "profile",
        "uuid",
        "ip_address",
        "network_operator",
        "network_asn",
        "created_at",
        "expires_at",
    )
    actions = ("revoke_sessions", "delete_expired_sessions")

    @admin.display(description="Валидна", boolean=True)
    def valid_display(self, obj):
        return obj.is_valid

    @admin.display(description="ASN")
    def network_asn_display(self, obj):
        return f"AS{obj.network_asn}" if obj.network_asn else "—"

    def get_search_fields(self, request):
        fields = list(super().get_search_fields(request))
        if not request.user.has_perm("valentine.view_profile_codes"):
            fields.remove("profile__code")
        return tuple(fields)

    @admin.action(
        description="Прекрати избраните сесии",
        permissions=("change",),
    )
    def revoke_sessions(self, request, queryset):
        if not self.has_change_permission(request):
            raise PermissionDenied
        count = queryset.update(revoked=True)
        self.message_user(request, f"Прекратени сесии: {count}.")

    @admin.action(
        description="Изтрий всички изтекли сесии",
        permissions=("delete",),
    )
    def delete_expired_sessions(self, request, queryset):
        if not self.has_delete_permission(request):
            raise PermissionDenied
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

    def get_search_fields(self, request):
        fields = list(super().get_search_fields(request))
        if not request.user.has_perm("valentine.view_profile_codes"):
            fields.remove("profile__code")
        return tuple(fields)

    @admin.display(description="Изпращач")
    def sender_display(self, obj):
        return obj.sender_profile or "Valentine екип"

    @admin.display(description="Послание")
    def content_preview(self, obj):
        return obj.content[:80] + ("…" if len(obj.content) > 80 else "")

    @admin.action(
        description="Скрий избраните сигнали",
        permissions=("change",),
    )
    def hide_reported(self, request, queryset):
        if not self.has_change_permission(request):
            raise PermissionDenied
        dispatch_count, delivery_count = self._moderate_dispatches(
            request,
            queryset,
            status=Valenitines.ModerationStatus.HIDDEN,
            is_reported=True,
            event_type=ValentineModerationEvent.EventType.HIDDEN,
        )
        self.message_user(
            request,
            f"Скрити пратки: {dispatch_count} "
            f"({delivery_count} доставки).",
        )

    @admin.action(
        description="Възстанови избраните послания",
        permissions=("change",),
    )
    def restore_messages(self, request, queryset):
        if not self.has_change_permission(request):
            raise PermissionDenied
        dispatch_count, delivery_count = self._moderate_dispatches(
            request,
            queryset,
            status=Valenitines.ModerationStatus.RESTORED,
            is_reported=False,
            event_type=ValentineModerationEvent.EventType.RESTORED,
        )
        self.message_user(
            request,
            f"Възстановени пратки: {dispatch_count} "
            f"({delivery_count} доставки).",
        )

    @staticmethod
    def _moderate_dispatches(
        request,
        queryset,
        *,
        status,
        is_reported,
        event_type,
    ):
        dispatch_keys = list(
            queryset.values_list("campaign_id", "dispatch_id").distinct()
        )
        if not dispatch_keys:
            return 0, 0
        dispatch_filter = Q()
        for campaign_id, dispatch_id in dispatch_keys:
            dispatch_filter |= Q(
                campaign_id=campaign_id,
                dispatch_id=dispatch_id,
            )
        now = timezone.now()
        with transaction.atomic():
            delivery_ids = list(
                Valenitines.objects.select_for_update()
                .filter(dispatch_filter)
                .values_list("pk", flat=True)
            )
            Valenitines.objects.filter(pk__in=delivery_ids).update(
                is_reported=is_reported,
                moderation_status=status,
            )
            ValentineModerationEvent.objects.bulk_create(
                [
                    ValentineModerationEvent(
                        campaign_id=campaign_id,
                        dispatch_id=dispatch_id,
                        event_type=event_type,
                        moderator=request.user,
                        created_at=now,
                    )
                    for campaign_id, dispatch_id in dispatch_keys
                ]
            )
        return len(dispatch_keys), len(delivery_ids)

    @admin.action(
        description="Експорт на модерираните (CSV)",
        permissions=("view",),
    )
    def export_reported_valentines(self, request, queryset):
        if not self.has_view_permission(request):
            raise PermissionDenied
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

    @admin.action(
        description="Експорт на обобщена статистика (CSV)",
        permissions=("view",),
    )
    def export_valentine_stats(self, request, queryset):
        if not self.has_view_permission(request):
            raise PermissionDenied
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
        active_campaign_id = Settings.objects.values_list(
            "active_campaign_id",
            flat=True,
        ).first()
        return ModelAdmin.get_queryset(self, request).filter(
            moderation_status__in=(
                Valenitines.ModerationStatus.PENDING,
                Valenitines.ModerationStatus.HIDDEN,
                Valenitines.ModerationStatus.RESTORED,
            ),
        ).exclude(campaign_id=active_campaign_id)


@admin.register(ValentineModerationEvent)
class ValentineModerationEventAdmin(ModelAdmin):
    list_display = (
        "event_type",
        "campaign",
        "dispatch_id",
        "reporter_profile",
        "moderator",
        "reason",
        "created_at",
    )
    list_filter = ("event_type", "campaign", "reason", "created_at")
    search_fields = (
        "=dispatch_id",
        "reporter_profile__first_name",
        "reporter_profile__last_name",
        "moderator__username",
    )
    list_select_related = ("campaign", "reporter_profile", "moderator")
    readonly_fields = (
        "campaign",
        "dispatch_id",
        "event_type",
        "reporter_profile",
        "moderator",
        "reason",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


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

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            fields.append("year")
        return tuple(fields)

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
                "dispatch_id",
                distinct=True,
                filter=Q(reported_at__isnull=False),
            ),
            reported_deliveries=Count(
                "id",
                filter=Q(reported_at__isnull=False),
            ),
            reported_dispatches=Count(
                "dispatch_id",
                distinct=True,
                filter=Q(reported_at__isnull=False),
            ),
            pending=Count(
                "dispatch_id",
                distinct=True,
                filter=Q(
                    moderation_status=(
                        Valenitines.ModerationStatus.PENDING
                    )
                ),
            ),
            hidden=Count(
                "dispatch_id",
                distinct=True,
                filter=Q(
                    moderation_status=(
                        Valenitines.ModerationStatus.HIDDEN
                    )
                ),
            ),
            restored=Count(
                "dispatch_id",
                distinct=True,
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
                    "dispatch_id",
                    distinct=True,
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
                    "dispatch_id",
                    distinct=True,
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
            .annotate(total=Count("dispatch_id", distinct=True))
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
                    f"{totals['reported_deliveries']} засегнати доставки"
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
        description="Направи активна и архивирай предишната кампания",
        permissions=("change",),
    )
    def make_active(self, request, queryset):
        if not self.has_change_permission(request):
            raise PermissionDenied
        if queryset.count() != 1:
            self.message_user(
                request,
                "Избери точно една кампания.",
                messages.ERROR,
            )
            return
        campaign = queryset.first()
        current_settings = Settings.objects.first()
        generated_initial = {
            "sending_starts_at": timezone.make_aware(
                datetime(campaign.year, 1, 15, 8, 0),
                timezone.get_current_timezone(),
            ),
            "sending_ends_at": timezone.make_aware(
                datetime(campaign.year, 2, 13, 23, 59),
                timezone.get_current_timezone(),
            ),
            "reveal_at": timezone.make_aware(
                datetime(campaign.year, 2, 14, 8, 0),
                timezone.get_current_timezone(),
            ),
        }
        if current_settings:
            for field_name in tuple(generated_initial):
                value = getattr(current_settings, field_name)
                if (
                    value
                    and timezone.localtime(value).year == campaign.year
                ):
                    generated_initial[field_name] = value
        is_confirmation = request.POST.get("apply") == "yes"
        activation_form = CampaignActivationForm(
            request.POST if is_confirmation else None,
            campaign=campaign,
            initial=generated_initial,
        )
        if not (is_confirmation and activation_form.is_valid()):
            context = {
                **self.admin_site.each_context(request),
                "title": f"Активиране на {campaign}",
                "opts": self.model._meta,
                "campaign": campaign,
                "activation_form": activation_form,
                "action_checkbox_name": helpers.ACTION_CHECKBOX_NAME,
                "media": self.media + activation_form.media,
            }
            return TemplateResponse(
                request,
                (
                    "admin/valentine/valentinecampaign/"
                    "activate_confirmation.html"
                ),
                context,
            )

        starts_at = activation_form.cleaned_data["sending_starts_at"]
        ends_at = activation_form.cleaned_data["sending_ends_at"]
        reveal_at = activation_form.cleaned_data["reveal_at"]
        with transaction.atomic():
            campaign = ValentineCampaign.objects.select_for_update().get(
                pk=campaign.pk
            )
            campaign.is_archived = False
            campaign.archived_at = None
            campaign.save(update_fields=["is_archived", "archived_at"])

            platform_settings, settings_created = (
                Settings.objects.select_for_update().get_or_create(
                    singleton_key="default",
                    defaults={
                        "reveal_date": timezone.localdate(reveal_at),
                        "reveal_at": reveal_at,
                        "sending_starts_at": starts_at,
                        "sending_ends_at": ends_at,
                        "active_campaign": campaign,
                    },
                )
            )
            if not settings_created:
                previous = platform_settings.active_campaign
                if previous and previous.pk != campaign.pk:
                    ValentineCampaign.objects.filter(pk=previous.pk).update(
                        is_archived=True,
                        archived_at=timezone.now(),
                    )
                platform_settings.active_campaign = campaign
                platform_settings.sending_starts_at = starts_at
                platform_settings.sending_ends_at = ends_at
                platform_settings.reveal_at = reveal_at
                platform_settings.reveal_date = timezone.localdate(reveal_at)
                platform_settings.save()
        self.message_user(
            request,
            f"{campaign} вече е активната кампания. "
            "Датите за изпращане и разкриване са обновени.",
            messages.SUCCESS,
        )

    @admin.action(
        description="Архивирай избраните минали кампании",
        permissions=("change",),
    )
    def archive_campaigns(self, request, queryset):
        if not self.has_change_permission(request):
            raise PermissionDenied
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

    @admin.action(
        description="Възстанови избраните кампании от архива",
        permissions=("change",),
    )
    def restore_campaigns(self, request, queryset):
        if not self.has_change_permission(request):
            raise PermissionDenied
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
        return (
            super().has_add_permission(request)
            and not Settings.objects.exists()
        )

    def has_delete_permission(self, request, obj=None):
        return False
