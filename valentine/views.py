import hashlib
import uuid
from datetime import date, timedelta
from functools import wraps

from django.conf import settings as django_settings
from django.contrib import messages
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Max
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .forms import (
    AccessCodeForm,
    ReportValentineForm,
    ValentineCreateForm,
    ValentineEditForm,
)
from .models import Profile, Register, Settings, Valenitines


AUTH_COOKIE = "authv"
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_ATTEMPTS = 5
GROUP_AUDIENCE_TYPES = (
    Valenitines.AudienceType.CLASS,
    Valenitines.AudienceType.TEACHERS,
)


def _default_reveal_date():
    today = timezone.localdate()
    candidate = date(today.year, 2, 14)
    return candidate if today <= candidate else date(today.year + 1, 2, 14)


def get_platform_settings():
    platform_settings = Settings.objects.first()
    if platform_settings:
        platform_settings.ensure_active_campaign()
        return platform_settings
    platform_settings = Settings.objects.create(
        reveal_date=_default_reveal_date()
    )
    platform_settings.ensure_active_campaign()
    return platform_settings


def get_current_session(request):
    token = request.COOKIES.get(AUTH_COOKIE)
    if not token:
        return None
    try:
        session = Register.objects.select_related("profile").get(uuid=token)
    except (Register.DoesNotExist, ValidationError, ValueError):
        return None
    return session if session.is_valid else None


def valentine_login_required(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        session = get_current_session(request)
        if session is None:
            login_url = reverse("valentine:login")
            return redirect(f"{login_url}?next={request.path}")
        request.valentine_session = session
        request.valentine_profile = session.profile
        return view_func(request, *args, **kwargs)

    return wrapped


def _client_key(request):
    raw_ip = request.META.get("REMOTE_ADDR", "unknown")
    return hashlib.sha256(raw_ip.encode("utf-8")).hexdigest()[:24]


def _day_bounds(moment):
    local = timezone.localtime(moment)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def _class_label(class_name):
    if class_name == "U":
        return "Учители"
    mapping = {"A": "А", "B": "Б", "V": "В", "G": "Г"}
    if not class_name or len(class_name) < 2:
        return class_name
    return f"{class_name[:-1]}{mapping.get(class_name[-1].upper(), class_name[-1])} клас"


def _sent_dispatch_count(profile, campaign, day_start, day_end):
    return (
        Valenitines.objects.filter(
            sender_profile=profile,
            campaign=campaign,
            date__gte=day_start,
            date__lt=day_end,
        )
        .values("dispatch_id")
        .distinct()
        .count()
    )


def _group_dispatch_count(profile, campaign, day_start, day_end):
    return (
        Valenitines.objects.filter(
            sender_profile=profile,
            campaign=campaign,
            audience_type__in=GROUP_AUDIENCE_TYPES,
            date__gte=day_start,
            date__lt=day_end,
        )
        .values("dispatch_id")
        .distinct()
        .count()
    )


@never_cache
@require_http_methods(["GET", "POST"])
def login(request):
    if get_current_session(request):
        return redirect("valentine:home")

    form = AccessCodeForm(request.POST or None)
    rate_key = f"valentine-login:{_client_key(request)}"
    attempts = cache.get(rate_key, 0)

    if request.method == "POST":
        if attempts >= LOGIN_MAX_ATTEMPTS:
            form.add_error(
                None,
                "Има твърде много опити. Опитай отново след 15 минути.",
            )
        elif form.is_valid():
            profile = Profile.objects.filter(
                code__iexact=form.cleaned_data["code"]
            ).first()
            if profile is None:
                cache.set(rate_key, attempts + 1, LOGIN_WINDOW_SECONDS)
                form.add_error(None, "Кодът е невалиден или е изтекъл.")
            else:
                cache.delete(rate_key)
                with transaction.atomic():
                    Profile.objects.select_for_update().get(pk=profile.pk)
                    Register.objects.filter(
                        profile=profile,
                        revoked=False,
                    ).update(revoked=True)
                    session = Register.objects.create(profile=profile)
                next_url = request.GET.get("next")
                if not next_url or not url_has_allowed_host_and_scheme(
                    next_url,
                    allowed_hosts={request.get_host()},
                    require_https=request.is_secure(),
                ):
                    next_url = reverse("valentine:home")
                response = redirect(next_url)
                response.set_cookie(
                    AUTH_COOKIE,
                    str(session.uuid),
                    max_age=14 * 24 * 60 * 60,
                    httponly=True,
                    secure=not django_settings.DEBUG,
                    samesite="Lax",
                    path="/valentines/",
                )
                return response

    return render(request, "valentine/login.html", {"form": form})


@require_POST
@valentine_login_required
def logout(request):
    request.valentine_session.revoked = True
    request.valentine_session.save(update_fields=["revoked"])
    response = redirect("valentine:login")
    response.delete_cookie(AUTH_COOKIE, path="/valentines/")
    return response


@never_cache
@require_http_methods(["GET", "POST"])
@valentine_login_required
def home(request):
    profile = request.valentine_profile
    platform_settings = get_platform_settings()
    campaign = platform_settings.active_campaign
    now = timezone.now()
    form = ValentineCreateForm(request.POST or None, sender=profile)
    day_start, day_end = _day_bounds(now)
    sent_today = _sent_dispatch_count(
        profile,
        campaign,
        day_start,
        day_end,
    )
    group_sent_today = _group_dispatch_count(
        profile,
        campaign,
        day_start,
        day_end,
    )

    if request.method == "POST":
        form_is_valid = form.is_valid()
        if not platform_settings.sending_is_open(now):
            form.add_error(None, "Изпращането на валентинки в момента е затворено.")
        if form_is_valid and not form.errors:
            created = False
            delivered_count = 0
            with transaction.atomic():
                Profile.objects.select_for_update().get(pk=profile.pk)
                locked_sent_today = _sent_dispatch_count(
                    profile,
                    campaign,
                    day_start,
                    day_end,
                )
                if locked_sent_today >= platform_settings.daily_limit:
                    form.add_error(
                        None,
                        f"Достигна дневния лимит от {platform_settings.daily_limit} валентинки.",
                    )
                else:
                    target_type = form.cleaned_data["target_type"]
                    is_group = target_type in GROUP_AUDIENCE_TYPES
                    locked_group_sent_today = _group_dispatch_count(
                        profile,
                        campaign,
                        day_start,
                        day_end,
                    )
                    if (
                        is_group
                        and locked_group_sent_today
                        >= platform_settings.group_daily_limit
                    ):
                        form.add_error(
                            None,
                            "Достигна дневния лимит от "
                            f"{platform_settings.group_daily_limit} "
                            "групови пратки.",
                        )
                    else:
                        last_sent = (
                            Valenitines.objects.filter(
                                sender_profile=profile,
                                campaign=campaign,
                            )
                            .order_by("-date")
                            .first()
                        )
                        if last_sent:
                            remaining = (
                                platform_settings.cooldown_seconds
                                - int((now - last_sent.date).total_seconds())
                            )
                            if remaining > 0:
                                form.add_error(
                                    None,
                                    "Изчакай още "
                                    f"{remaining} секунди преди "
                                    "следващото послание.",
                                )
                        if not form.errors:
                            recipients = form.cleaned_data["recipients"]
                            dispatch_id = uuid.uuid4()
                            if (
                                target_type
                                == Valenitines.AudienceType.INDIVIDUAL
                            ):
                                audience_label = recipients[0].full_name
                            elif (
                                target_type
                                == Valenitines.AudienceType.CLASS
                            ):
                                audience_label = _class_label(
                                    form.cleaned_data["target_class"]
                                )
                            else:
                                audience_label = "Всички учители"
                            rows = [
                                Valenitines(
                                    profile=recipient,
                                    sender_profile=profile,
                                    campaign=campaign,
                                    dispatch_id=dispatch_id,
                                    audience_type=target_type,
                                    audience_label=audience_label,
                                    content=form.cleaned_data["content"],
                                    theme=form.cleaned_data["theme"],
                                    is_anonymous=form.cleaned_data[
                                        "is_anonymous"
                                    ],
                                )
                                for recipient in recipients
                            ]
                            Valenitines.objects.bulk_create(rows)
                            delivered_count = len(rows)
                            created = True
            if created:
                if delivered_count == 1:
                    success_message = (
                        "Валентинката е запечатана и доставена! 💌"
                    )
                else:
                    success_message = (
                        "Груповата валентинка е запечатана и доставена "
                        f"на {delivered_count} получатели! 💌"
                    )
                messages.success(request, success_message)
                return redirect("valentine:home")

    class_names = (
        Profile.objects.exclude(pk=profile.pk)
        .order_by("clas")
        .values_list("clas", flat=True)
        .distinct()
    )
    classes = [
        {"value": class_name, "label": _class_label(class_name)}
        for class_name in class_names
    ]
    all_recipients = (
        Profile.objects.exclude(pk=profile.pk)
        .order_by("clas", "first_name", "last_name")
    )
    teacher_count = all_recipients.filter(clas="U").count()
    reveal_at = platform_settings.get_reveal_at()
    received_count = Valenitines.objects.filter(
        profile=profile,
        campaign=campaign,
    ).count()
    selected_recipient_id = form.data.get("recipient") if form.is_bound else ""
    selected_target_type = (
        form.data.get("target_type")
        if form.is_bound
        else Valenitines.AudienceType.INDIVIDUAL
    ) or Valenitines.AudienceType.INDIVIDUAL
    selected_class = (
        ValentineCreateForm.normalize_class_name(
            form.data.get("target_class")
        )
        if form.is_bound
        else ""
    )
    if selected_recipient_id:
        try:
            if not selected_class:
                selected_class = (
                    Profile.objects.filter(pk=selected_recipient_id)
                    .values_list("clas", flat=True)
                    .first()
                    or ""
                )
        except (ValidationError, ValueError):
            selected_recipient_id = ""

    return render(
        request,
        "valentine/home.html",
        {
            "form": form,
            "profile": profile,
            "classes": classes,
            "all_recipients": all_recipients,
            "teacher_count": teacher_count,
            "platform_settings": platform_settings,
            "campaign": campaign,
            "sending_open": platform_settings.sending_is_open(now),
            "reveal_at": reveal_at,
            "is_revealed": now >= reveal_at,
            "received_count": received_count,
            "sent_today": sent_today,
            "remaining_today": max(
                platform_settings.daily_limit - sent_today,
                0,
            ),
            "group_sent_today": group_sent_today,
            "group_remaining_today": max(
                platform_settings.group_daily_limit - group_sent_today,
                0,
            ),
            "total_sent": Valenitines.objects.filter(
                campaign=campaign
            ).count(),
            "selected_recipient_id": selected_recipient_id,
            "selected_class": selected_class,
            "selected_target_type": selected_target_type,
        },
    )


@never_cache
@require_GET
@valentine_login_required
def get_students(request, class_name):
    students = (
        Profile.objects.filter(clas=class_name)
        .exclude(pk=request.valentine_profile.pk)
        .order_by("first_name", "last_name")
    )
    return JsonResponse(
        {
            "students": [
                {
                    "id": str(student.pk),
                    "name": student.full_name,
                }
                for student in students
            ]
        }
    )


@never_cache
@require_GET
@valentine_login_required
def inbox(request):
    profile = request.valentine_profile
    platform_settings = get_platform_settings()
    campaign = platform_settings.active_campaign
    reveal_at = platform_settings.get_reveal_at()
    now = timezone.now()
    is_revealed = now >= reveal_at
    received = Valenitines.objects.filter(
        profile=profile,
        campaign=campaign,
        is_reported=False,
    ).exclude(
        moderation_status=Valenitines.ModerationStatus.HIDDEN
    ).select_related("sender_profile")

    return render(
        request,
        "valentine/inbox.html",
        {
            "profile": profile,
            "campaign": campaign,
            "valentines": received if is_revealed else received.none(),
            "received_count": received.count(),
            "show_received_count": platform_settings.show_received_count,
            "reveal_at": reveal_at,
            "is_revealed": is_revealed,
            "report_choices": Valenitines.ReportReason.choices,
        },
    )


@never_cache
@require_GET
@valentine_login_required
def outbox(request):
    platform_settings = get_platform_settings()
    campaign = platform_settings.active_campaign
    sent_dispatches = (
        Valenitines.objects.filter(
            sender_profile=request.valentine_profile,
            campaign=campaign,
        )
        .values(
            "dispatch_id",
            "audience_type",
            "audience_label",
            "content",
            "theme",
            "is_anonymous",
        )
        .annotate(
            sent_at=Max("date"),
            edited_at=Max("edited_at"),
            recipient_count=Count("id"),
        )
        .order_by("-sent_at")
    )
    reveal_at = platform_settings.get_reveal_at()
    return render(
        request,
        "valentine/outbox.html",
        {
            "profile": request.valentine_profile,
            "campaign": campaign,
            "dispatches": sent_dispatches,
            "can_edit": timezone.now() < reveal_at,
            "reveal_at": reveal_at,
        },
    )


@never_cache
@require_http_methods(["GET", "POST"])
@valentine_login_required
def edit_valentine(request, dispatch_id):
    platform_settings = get_platform_settings()
    campaign = platform_settings.active_campaign
    dispatch_queryset = Valenitines.objects.filter(
        sender_profile=request.valentine_profile,
        dispatch_id=dispatch_id,
        campaign=campaign,
    )
    representative = dispatch_queryset.order_by("date").first()
    if representative is None:
        raise Http404

    reveal_at = platform_settings.get_reveal_at()
    if timezone.now() >= reveal_at:
        messages.error(
            request,
            "Валентинките не могат да се редактират след разкриването.",
        )
        return redirect("valentine:outbox")
    if dispatch_queryset.exclude(
        moderation_status=Valenitines.ModerationStatus.CLEAR
    ).exists():
        messages.error(
            request,
            "Докладвана или модерирана валентинка не може да се редактира.",
        )
        return redirect("valentine:outbox")

    form = ValentineEditForm(
        request.POST or None,
        initial={
            "content": representative.content,
            "theme": representative.theme,
            "is_anonymous": representative.is_anonymous,
        },
    )
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            locked_dispatch = list(
                dispatch_queryset.select_for_update()
            )
            if not locked_dispatch:
                raise Http404
            if timezone.now() >= get_platform_settings().get_reveal_at():
                messages.error(
                    request,
                    "Моментът за разкриване настъпи. "
                    "Промените не бяха запазени.",
                )
                return redirect("valentine:outbox")
            if any(
                valentine.moderation_status
                != Valenitines.ModerationStatus.CLEAR
                for valentine in locked_dispatch
            ):
                messages.error(
                    request,
                    "Докладвана или модерирана валентинка "
                    "не може да се редактира.",
                )
                return redirect("valentine:outbox")
            dispatch_queryset.update(
                content=form.cleaned_data["content"],
                theme=form.cleaned_data["theme"],
                is_anonymous=form.cleaned_data["is_anonymous"],
                edited_at=timezone.now(),
            )
        messages.success(request, "Промените по валентинката са запазени.")
        return redirect("valentine:outbox")

    return render(
        request,
        "valentine/edit.html",
        {
            "form": form,
            "profile": request.valentine_profile,
            "audience_label": representative.audience_label,
            "recipient_count": dispatch_queryset.count(),
            "reveal_at": reveal_at,
        },
    )


@require_POST
@valentine_login_required
def report_valentine(request, valentine_id):
    campaign = get_platform_settings().active_campaign
    valentine = get_object_or_404(
        Valenitines,
        pk=valentine_id,
        profile=request.valentine_profile,
        campaign=campaign,
    )
    form = ReportValentineForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Избери причина за сигнала.")
        return redirect("valentine:inbox")

    valentine.is_reported = True
    valentine.report_reason = form.cleaned_data["reason"]
    valentine.reported_at = timezone.now()
    valentine.moderation_status = Valenitines.ModerationStatus.PENDING
    valentine.save(
        update_fields=[
            "is_reported",
            "report_reason",
            "reported_at",
            "moderation_status",
        ]
    )
    messages.success(
        request,
        "Съобщението е скрито и изпратено за преглед.",
    )
    return redirect("valentine:inbox")


@require_GET
def guide(request):
    session = get_current_session(request)
    if session:
        request.valentine_profile = session.profile
    return render(
        request,
        "valentine/guide.html",
        {"is_logged_in": session is not None},
    )
