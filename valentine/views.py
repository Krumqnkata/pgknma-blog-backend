import hashlib
import ipaddress
import json
import uuid
from datetime import date, timedelta
from functools import wraps

from django.conf import settings as django_settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import Count, Max, Q
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from blog.network_operator import lookup_network_operator

from .forms import (
    AccessCodeForm,
    PinAuthenticationForm,
    PinChangeForm,
    PinSetupForm,
    ReportValentineForm,
    ValentineDataExportForm,
    ValentineCreateForm,
    ValentineEditForm,
)
from .models import (
    Profile,
    Register,
    Settings,
    ValentineLoginAttempt,
    ValentineModerationEvent,
    Valenitines,
    normalize_class_code,
)
from .personal_data_export import build_valentine_personal_data_export


AUTH_COOKIE = "authv"
PIN_PENDING_PROFILE_KEY = "valentine_pin_profile"
PIN_PENDING_NEXT_KEY = "valentine_pin_next"
PIN_PENDING_CREATED_KEY = "valentine_pin_started_at"
PIN_PENDING_TTL_SECONDS = 10 * 60
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_ATTEMPTS = 5
PIN_LOCK_SECONDS = 15 * 60
PIN_MAX_ATTEMPTS = 5
LOGIN_DATABASE_RETRIES = 3
LOGIN_STATUS_BLOCKED = "blocked"
LOGIN_STATUS_INVALID = "invalid"
LOGIN_STATUS_SUCCESS = "success"
GROUP_AUDIENCE_TYPES = (
    Valenitines.AudienceType.CLASS,
    Valenitines.AudienceType.TEACHERS,
)


def _default_reveal_date():
    today = timezone.localdate()
    candidate = date(today.year, 2, 14)
    return candidate if today <= candidate else date(today.year + 1, 2, 14)


def get_platform_settings():
    try:
        platform_settings = Settings.objects.get(singleton_key="default")
    except Settings.DoesNotExist:
        try:
            with transaction.atomic():
                platform_settings = Settings.objects.create(
                    singleton_key="default",
                    reveal_date=_default_reveal_date(),
                )
        except IntegrityError:
            # Another worker created the singleton after our first lookup.
            platform_settings = Settings.objects.get(singleton_key="default")
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


def _normalized_ip(value):
    try:
        return str(ipaddress.ip_address(str(value or "").strip()))
    except ValueError:
        return ""


def _trusted_proxy_ips():
    configured = getattr(
        django_settings,
        "VALENTINE_TRUSTED_PROXY_IPS",
        (),
    )
    if isinstance(configured, str):
        configured = configured.split(",")
    return {
        normalized
        for value in configured
        if (normalized := _normalized_ip(value))
    }


def _client_ip(request):
    remote_addr = _normalized_ip(request.META.get("REMOTE_ADDR"))
    if not remote_addr:
        return "unknown"
    trusted_proxies = _trusted_proxy_ips()
    if remote_addr not in trusted_proxies:
        return remote_addr
    forwarded = [
        _normalized_ip(value)
        for value in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")
    ]
    if not forwarded or any(not value for value in forwarded):
        return remote_addr
    # Walk from the trusted application-facing proxy towards the client.
    for candidate in reversed(forwarded):
        if candidate not in trusted_proxies:
            return candidate
    return forwarded[0]


def _client_key(request):
    raw_ip = _client_ip(request)
    return hashlib.sha256(raw_ip.encode("utf-8")).hexdigest()[:24]


def _login_limit():
    try:
        configured = int(
            getattr(
                django_settings,
                "VALENTINE_LOGIN_MAX_ATTEMPTS",
                LOGIN_MAX_ATTEMPTS,
            )
        )
    except (TypeError, ValueError):
        configured = LOGIN_MAX_ATTEMPTS
    return max(1, configured)


def _login_window():
    try:
        configured = int(
            getattr(
                django_settings,
                "VALENTINE_LOGIN_WINDOW_SECONDS",
                LOGIN_WINDOW_SECONDS,
            )
        )
    except (TypeError, ValueError):
        configured = LOGIN_WINDOW_SECONDS
    return max(60, configured)


def _pin_limit():
    try:
        configured = int(
            getattr(django_settings, "VALENTINE_PIN_MAX_ATTEMPTS", PIN_MAX_ATTEMPTS)
        )
    except (TypeError, ValueError):
        configured = PIN_MAX_ATTEMPTS
    return max(1, configured)


def _pin_lock_window():
    try:
        configured = int(
            getattr(django_settings, "VALENTINE_PIN_LOCK_SECONDS", PIN_LOCK_SECONDS)
        )
    except (TypeError, ValueError):
        configured = PIN_LOCK_SECONDS
    return max(60, configured)


def _authenticate_login(code, client_key, *, now=None):
    """Authenticate and update the DB-authoritative failure window.

    A row lock serializes attempts for an existing client key. The unique
    key plus a short retry loop handles two workers racing to create the
    first row. Exhausted/deadlocked retries fail closed.
    """
    now = now or timezone.now()
    window_expires = now + timedelta(seconds=_login_window())
    for _retry in range(LOGIN_DATABASE_RETRIES):
        try:
            with transaction.atomic():
                counter = (
                    ValentineLoginAttempt.objects.select_for_update()
                    .filter(client_key=client_key)
                    .first()
                )
                if counter is None:
                    try:
                        with transaction.atomic():
                            counter = ValentineLoginAttempt.objects.create(
                                client_key=client_key,
                                attempts=0,
                                window_expires=window_expires,
                            )
                    except IntegrityError:
                        # A concurrent transaction won the unique-key race.
                        # Leave this transaction and retry with a row lock.
                        continue

                window_was_reset = counter.window_expires <= now
                if window_was_reset:
                    counter.attempts = 0
                    counter.window_expires = window_expires

                if counter.attempts >= _login_limit():
                    if window_was_reset:
                        counter.save(
                            update_fields=(
                                "attempts",
                                "window_expires",
                                "updated_at",
                            )
                        )
                    return LOGIN_STATUS_BLOCKED, None

                profile = (
                    Profile.objects.select_for_update()
                    .filter(code__iexact=code, is_active=True)
                    .first()
                )
                if profile is None:
                    counter.attempts += 1
                    counter.save(
                        update_fields=(
                            "attempts",
                            "window_expires",
                            "updated_at",
                        )
                    )
                    return LOGIN_STATUS_INVALID, None

                if window_was_reset:
                    counter.save(
                        update_fields=(
                            "attempts",
                            "window_expires",
                            "updated_at",
                        )
                    )
                return LOGIN_STATUS_SUCCESS, profile
        except OperationalError:
            # Deadlock/table-lock retries are bounded and ultimately fail
            # closed instead of allowing an uncounted authentication attempt.
            continue
    return LOGIN_STATUS_BLOCKED, None


def _clear_pending_pin_login(request):
    for key in (
        PIN_PENDING_PROFILE_KEY,
        PIN_PENDING_NEXT_KEY,
        PIN_PENDING_CREATED_KEY,
    ):
        request.session.pop(key, None)


def _pending_pin_profile(request):
    profile_id = request.session.get(PIN_PENDING_PROFILE_KEY)
    started_at = request.session.get(PIN_PENDING_CREATED_KEY)
    try:
        is_expired = (
            not profile_id
            or not started_at
            or timezone.now().timestamp() - float(started_at)
            > PIN_PENDING_TTL_SECONDS
        )
    except (TypeError, ValueError):
        is_expired = True
    if is_expired:
        _clear_pending_pin_login(request)
        return None
    return Profile.objects.filter(pk=profile_id, is_active=True).first()


def _complete_pin_login(request, profile):
    Register.objects.filter(profile=profile, revoked=False).update(revoked=True)
    ip_address = _client_ip(request)
    if ip_address == "unknown":
        ip_address = ""
    network_operator, network_asn = lookup_network_operator(ip_address)
    session = Register.objects.create(
        profile=profile,
        ip_address=ip_address or None,
        network_operator=network_operator,
        network_asn=network_asn,
    )
    next_url = request.session.get(PIN_PENDING_NEXT_KEY)
    _clear_pending_pin_login(request)
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


def _day_bounds(moment):
    local = timezone.localtime(moment)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def _class_label(class_name):
    class_name = normalize_class_code(class_name)
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

    if request.method == "POST":
        if form.is_valid():
            status, session = _authenticate_login(
                form.cleaned_data["code"],
                _client_key(request),
            )
            if status == LOGIN_STATUS_BLOCKED:
                form.add_error(
                    None,
                    "Има твърде много опити. Опитай отново по-късно.",
                )
            elif status == LOGIN_STATUS_INVALID:
                form.add_error(None, "Кодът е невалиден или е изтекъл.")
            else:
                next_url = request.GET.get("next")
                if next_url and not url_has_allowed_host_and_scheme(
                    next_url,
                    allowed_hosts={request.get_host()},
                    require_https=request.is_secure(),
                ):
                    next_url = ""
                request.session[PIN_PENDING_PROFILE_KEY] = str(session.pk)
                request.session[PIN_PENDING_CREATED_KEY] = timezone.now().timestamp()
                request.session[PIN_PENDING_NEXT_KEY] = next_url or ""
                return redirect("valentine:pin")

    return render(request, "valentine/login.html", {"form": form})


@never_cache
@require_http_methods(["GET", "POST"])
def pin(request):
    """Finish a code-authenticated Valentine login with a private PIN."""
    if get_current_session(request):
        _clear_pending_pin_login(request)
        return redirect("valentine:home")

    profile = _pending_pin_profile(request)
    if profile is None:
        messages.error(request, "Сесията за вход изтече. Въведи кода си отново.")
        return redirect("valentine:login")

    creating_pin = not profile.has_pin
    form_class = PinSetupForm if creating_pin else PinAuthenticationForm
    form = form_class(request.POST or None)

    if request.method == "POST" and form.is_valid():
        now = timezone.now()
        with transaction.atomic():
            profile = Profile.objects.select_for_update().get(pk=profile.pk)
            if not profile.is_active:
                _clear_pending_pin_login(request)
                messages.error(request, "Профилът вече не е активен.")
                return redirect("valentine:login")

            if profile.pin_locked_until and profile.pin_locked_until > now:
                form.add_error(
                    None,
                    "PIN кодът е временно заключен. Опитай отново по-късно.",
                )
            elif not profile.has_pin:
                # A PIN is created only after the code has been validated in
                # the previous step. It is stored as a Django password hash.
                profile.set_pin(form.cleaned_data["pin"])
                profile.save(
                    update_fields=(
                        "pin_hash",
                        "pin_set_at",
                        "pin_failed_attempts",
                        "pin_locked_until",
                    )
                )
                return _complete_pin_login(request, profile)
            elif profile.check_pin(form.cleaned_data["pin"]):
                if profile.pin_failed_attempts or profile.pin_locked_until:
                    profile.pin_failed_attempts = 0
                    profile.pin_locked_until = None
                    profile.save(
                        update_fields=("pin_failed_attempts", "pin_locked_until")
                    )
                return _complete_pin_login(request, profile)
            else:
                profile.pin_failed_attempts += 1
                if profile.pin_failed_attempts >= _pin_limit():
                    profile.pin_locked_until = now + timedelta(
                        seconds=_pin_lock_window()
                    )
                profile.save(
                    update_fields=("pin_failed_attempts", "pin_locked_until")
                )
                form.add_error(None, "PIN кодът е невалиден.")

    return render(
        request,
        "valentine/pin.html",
        {"form": form, "profile": profile, "creating_pin": creating_pin},
    )


@require_POST
@valentine_login_required
def logout(request):
    request.valentine_session.revoked = True
    request.valentine_session.save(update_fields=["revoked"])
    response = redirect("valentine:login")
    response.delete_cookie(AUTH_COOKIE, path="/valentines/")
    return response


@never_cache
@require_GET
@valentine_login_required
def active_sessions(request):
    now = timezone.now()
    sessions = list(
        Register.objects.filter(profile=request.valentine_profile).order_by(
            "-created_at"
        )
    )
    for session in sessions:
        session.is_current = session.pk == request.valentine_session.pk
        if session.is_current:
            session.status_label = "Текуща"
            session.status_class = "current"
        elif session.revoked:
            session.status_label = "Прекратена при следващ вход"
            session.status_class = "ended"
        elif session.expires_at <= now:
            session.status_label = "Изтекла"
            session.status_class = "ended"
        else:
            session.status_label = "Активна"
            session.status_class = "active"

    return render(
        request,
        "valentine/sessions.html",
        {"sessions": sessions},
    )


def _confirm_export_pin(profile, pin, form):
    now = timezone.now()
    with transaction.atomic():
        locked_profile = Profile.objects.select_for_update().get(pk=profile.pk)
        if not locked_profile.has_pin:
            form.add_error(
                None,
                "Профилът няма активен PIN. Влез отново или се свържи с администратор.",
            )
            return None
        if (
            locked_profile.pin_locked_until
            and locked_profile.pin_locked_until > now
        ):
            form.add_error(
                None,
                "PIN кодът е временно заключен. Опитай отново по-късно.",
            )
            return None
        if not locked_profile.check_pin(pin):
            locked_profile.pin_failed_attempts += 1
            if locked_profile.pin_failed_attempts >= _pin_limit():
                locked_profile.pin_locked_until = now + timedelta(
                    seconds=_pin_lock_window()
                )
            locked_profile.save(
                update_fields=("pin_failed_attempts", "pin_locked_until")
            )
            form.add_error(None, "Текущият PIN код е невалиден.")
            return None

        if (
            locked_profile.pin_failed_attempts
            or locked_profile.pin_locked_until is not None
        ):
            locked_profile.pin_failed_attempts = 0
            locked_profile.pin_locked_until = None
            locked_profile.save(
                update_fields=("pin_failed_attempts", "pin_locked_until")
            )
        return locked_profile


@never_cache
@require_http_methods(["GET", "POST"])
@valentine_login_required
def personal_data_export(request):
    form = ValentineDataExportForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        profile = _confirm_export_pin(
            request.valentine_profile,
            form.cleaned_data["pin"],
            form,
        )
        if profile is not None:
            payload = build_valentine_personal_data_export(
                profile,
                get_platform_settings(),
                current_session_id=request.valentine_session.pk,
            )
            content = json.dumps(
                payload,
                cls=DjangoJSONEncoder,
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8")
            filename = (
                f"pgknma-valentine-data-{timezone.localdate():%Y-%m-%d}.json"
            )
            response = HttpResponse(
                content,
                content_type="application/json; charset=utf-8",
            )
            response["Content-Disposition"] = (
                f'attachment; filename="{filename}"'
            )
            response["Cache-Control"] = "private, no-store, max-age=0"
            response["Pragma"] = "no-cache"
            response["X-Content-Type-Options"] = "nosniff"
            return response

    return render(
        request,
        "valentine/data_export.html",
        {"form": form},
    )


@never_cache
@require_http_methods(["GET", "POST"])
@valentine_login_required
def change_pin(request):
    profile = request.valentine_profile
    form = PinChangeForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        now = timezone.now()
        with transaction.atomic():
            profile = Profile.objects.select_for_update().get(pk=profile.pk)
            if profile.pin_locked_until and profile.pin_locked_until > now:
                form.add_error(
                    None,
                    "PIN кодът е временно заключен. Опитай отново по-късно.",
                )
            elif not profile.check_pin(form.cleaned_data["current_pin"]):
                profile.pin_failed_attempts += 1
                if profile.pin_failed_attempts >= _pin_limit():
                    profile.pin_locked_until = now + timedelta(
                        seconds=_pin_lock_window()
                    )
                profile.save(
                    update_fields=("pin_failed_attempts", "pin_locked_until")
                )
                form.add_error(None, "Текущият PIN код е невалиден.")
            else:
                profile.set_pin(form.cleaned_data["pin"])
                profile.save(
                    update_fields=(
                        "pin_hash",
                        "pin_set_at",
                        "pin_failed_attempts",
                        "pin_locked_until",
                    )
                )
                Register.objects.filter(
                    profile=profile,
                    revoked=False,
                ).exclude(pk=request.valentine_session.pk).update(revoked=True)
                messages.success(request, "PIN кодът е променен успешно.")
                return redirect("valentine:home")

    return render(request, "valentine/change_pin.html", {"form": form})


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
                                    recipient_class_snapshot=(
                                        normalize_class_code(recipient.clas)
                                    ),
                                    sender_class_snapshot=(
                                        normalize_class_code(profile.clas)
                                    ),
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
        Profile.objects.filter(is_active=True)
        .exclude(pk=profile.pk)
        .exclude(clas="U")
        .order_by("clas")
        .values_list("clas", flat=True)
        .distinct()
    )
    classes = [
        {"value": class_name, "label": _class_label(class_name)}
        for class_name in class_names
    ]
    teacher_count = (
        Profile.objects.filter(is_active=True, clas="U")
        .exclude(pk=profile.pk)
        .count()
    )
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
            selected_recipient = (
                Profile.objects.filter(
                    pk=selected_recipient_id,
                    is_active=True,
                )
                .exclude(pk=profile.pk)
                .first()
            )
            if selected_recipient is None:
                selected_recipient_id = ""
            elif not selected_class:
                selected_class = selected_recipient.clas
        except (ValidationError, ValueError):
            selected_recipient_id = ""
            selected_recipient = None
    else:
        selected_recipient = None

    return render(
        request,
        "valentine/home.html",
        {
            "form": form,
            "profile": profile,
            "classes": classes,
            "selected_recipient": selected_recipient,
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
            "total_sent": (
                Valenitines.objects.filter(campaign=campaign)
                .values("dispatch_id")
                .distinct()
                .count()
            ),
            "selected_recipient_id": selected_recipient_id,
            "selected_class": selected_class,
            "selected_target_type": selected_target_type,
        },
    )


@never_cache
@require_GET
@valentine_login_required
def get_students(request, class_name):
    class_name = normalize_class_code(class_name)
    students = (
        Profile.objects.filter(
            clas=class_name,
            is_active=True,
        )
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
def received_archive(request):
    """Show only the logged-in profile's visible valentines from past campaigns."""
    archived_received = (
        Valenitines.objects.filter(
            profile=request.valentine_profile,
            campaign__is_archived=True,
            is_reported=False,
        )
        .exclude(moderation_status=Valenitines.ModerationStatus.HIDDEN)
        .select_related("campaign", "sender_profile")
        .order_by("-campaign__year", "-date")
    )

    archive_groups = []
    for valentine in archived_received:
        if (
            not archive_groups
            or archive_groups[-1]["campaign"].pk != valentine.campaign_id
        ):
            archive_groups.append(
                {"campaign": valentine.campaign, "valentines": []}
            )
        archive_groups[-1]["valentines"].append(valentine)

    return render(
        request,
        "valentine/archive.html",
        {
            "profile": request.valentine_profile,
            "archive_groups": archive_groups,
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
            moderated_count=Count(
                "id",
                filter=~Q(
                    moderation_status=(
                        Valenitines.ModerationStatus.CLEAR
                    )
                ),
            ),
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
    platform_settings = get_platform_settings()
    campaign = platform_settings.active_campaign
    if timezone.now() < platform_settings.get_reveal_at():
        messages.error(
            request,
            "Валентинките могат да се докладват след разкриването.",
        )
        return redirect("valentine:inbox")
    form = ReportValentineForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Избери причина за сигнала.")
        return redirect("valentine:inbox")
    valentine = get_object_or_404(
        Valenitines,
        pk=valentine_id,
        profile=request.valentine_profile,
        campaign=campaign,
    )
    with transaction.atomic():
        dispatch = list(
            Valenitines.objects.select_for_update()
            .filter(
                campaign=campaign,
                dispatch_id=valentine.dispatch_id,
            )
            .order_by("pk")
        )
        if not any(
            delivery.pk == valentine.pk
            and delivery.profile_id == request.valentine_profile.pk
            for delivery in dispatch
        ):
            raise Http404
        ValentineModerationEvent.objects.get_or_create(
            campaign=campaign,
            dispatch_id=valentine.dispatch_id,
            reporter_profile=request.valentine_profile,
            event_type=ValentineModerationEvent.EventType.REPORTED,
            defaults={"reason": form.cleaned_data["reason"]},
        )
        terminal_statuses = {
            Valenitines.ModerationStatus.HIDDEN,
            Valenitines.ModerationStatus.RESTORED,
        }
        if any(
            delivery.moderation_status in terminal_statuses
            for delivery in dispatch
        ):
            moderator_already_decided = True
        else:
            moderator_already_decided = False
            existing_report = next(
                (
                    delivery
                    for delivery in dispatch
                    if delivery.reported_at is not None
                ),
                None,
            )
            reported_at = (
                existing_report.reported_at
                if existing_report
                else timezone.now()
            )
            report_reason = (
                existing_report.report_reason
                if existing_report and existing_report.report_reason
                else form.cleaned_data["reason"]
            )
            Valenitines.objects.filter(
                pk__in=[delivery.pk for delivery in dispatch]
            ).update(
                is_reported=True,
                report_reason=report_reason,
                reported_at=reported_at,
                moderation_status=Valenitines.ModerationStatus.PENDING,
            )
    if moderator_already_decided:
        messages.info(
            request,
            "Сигналът е записан, но модератор вече е прегледал тази "
            "пратка и статусът му не е променен.",
        )
    else:
        messages.success(
            request,
            "Цялата пратка е скрита и изпратена за преглед.",
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
