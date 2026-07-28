import hashlib
from datetime import date, timedelta
from functools import wraps

from django.conf import settings as django_settings
from django.contrib import messages
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .forms import AccessCodeForm, ReportValentineForm, ValentineCreateForm
from .models import Profile, Register, Settings, Valenitines


AUTH_COOKIE = "authv"
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_ATTEMPTS = 5


def _default_reveal_date():
    today = timezone.localdate()
    candidate = date(today.year, 2, 14)
    return candidate if today <= candidate else date(today.year + 1, 2, 14)


def get_platform_settings():
    platform_settings = Settings.objects.first()
    if platform_settings:
        return platform_settings
    return Settings.objects.create(reveal_date=_default_reveal_date())


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
    now = timezone.now()
    form = ValentineCreateForm(request.POST or None, sender=profile)
    day_start, day_end = _day_bounds(now)
    sent_today = Valenitines.objects.filter(
        sender_profile=profile,
        date__gte=day_start,
        date__lt=day_end,
    ).count()

    if request.method == "POST":
        form_is_valid = form.is_valid()
        if not platform_settings.sending_is_open(now):
            form.add_error(None, "Изпращането на валентинки в момента е затворено.")
        if form_is_valid and not form.errors:
            created = False
            with transaction.atomic():
                Profile.objects.select_for_update().get(pk=profile.pk)
                locked_sent_today = Valenitines.objects.filter(
                    sender_profile=profile,
                    date__gte=day_start,
                    date__lt=day_end,
                ).count()
                if locked_sent_today >= platform_settings.daily_limit:
                    form.add_error(
                        None,
                        f"Достигна дневния лимит от {platform_settings.daily_limit} валентинки.",
                    )
                else:
                    last_sent = (
                        Valenitines.objects.filter(sender_profile=profile)
                        .order_by("-date")
                        .first()
                    )
                    if last_sent:
                        remaining = platform_settings.cooldown_seconds - int(
                            (now - last_sent.date).total_seconds()
                        )
                        if remaining > 0:
                            form.add_error(
                                None,
                                f"Изчакай още {remaining} секунди преди следващото послание.",
                            )
                    if not form.errors:
                        Valenitines.objects.create(
                            profile=form.cleaned_data["recipient"],
                            sender_profile=profile,
                            content=form.cleaned_data["content"],
                            theme=form.cleaned_data["theme"],
                            is_anonymous=form.cleaned_data["is_anonymous"],
                        )
                        created = True
            if created:
                messages.success(
                    request,
                    "Валентинката е запечатана и доставена! 💌",
                )
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
    reveal_at = platform_settings.get_reveal_at()
    received_count = Valenitines.objects.filter(profile=profile).count()
    selected_recipient_id = form.data.get("recipient") if form.is_bound else ""
    selected_class = ""
    if selected_recipient_id:
        try:
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
            "platform_settings": platform_settings,
            "sending_open": platform_settings.sending_is_open(now),
            "reveal_at": reveal_at,
            "is_revealed": now >= reveal_at,
            "received_count": received_count,
            "sent_today": sent_today,
            "remaining_today": max(
                platform_settings.daily_limit - sent_today,
                0,
            ),
            "total_sent": Valenitines.objects.count(),
            "selected_recipient_id": selected_recipient_id,
            "selected_class": selected_class,
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
    reveal_at = platform_settings.get_reveal_at()
    now = timezone.now()
    is_revealed = now >= reveal_at
    received = Valenitines.objects.filter(
        profile=profile,
        is_reported=False,
    ).exclude(moderation_status=Valenitines.ModerationStatus.HIDDEN)

    return render(
        request,
        "valentine/inbox.html",
        {
            "profile": profile,
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
    sent = (
        Valenitines.objects.filter(sender_profile=request.valentine_profile)
        .select_related("profile")
        .order_by("-date")
    )
    return render(
        request,
        "valentine/outbox.html",
        {
            "profile": request.valentine_profile,
            "valentines": sent,
        },
    )


@require_POST
@valentine_login_required
def report_valentine(request, valentine_id):
    valentine = get_object_or_404(
        Valenitines,
        pk=valentine_id,
        profile=request.valentine_profile,
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
