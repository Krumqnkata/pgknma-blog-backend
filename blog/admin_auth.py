import json
import secrets
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.http import HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_GET

from .auth import verify_google_credential


GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = ["openid", "email", "profile"]


def _build_callback_uri(request):
    return request.build_absolute_uri(reverse("admin_google_login_callback"))


def _get_client_id():
    client_id = getattr(settings, "GOOGLE_CLIENT_ID", "").strip()
    if not client_id:
        raise ValueError("Google Client ID не е конфигуриран.")
    return client_id


def _get_client_secret():
    return getattr(settings, "GOOGLE_CLIENT_SECRET", "").strip()


@require_GET
def admin_google_login(request):
    try:
        client_id = _get_client_id()
    except ValueError:
        messages.error(request, "Входът с Google временно не е достъпен.")
        return redirect("admin:login")

    state = secrets.token_hex(32)
    request.session["google_admin_oauth_state"] = state
    request.session.modified = True

    params = {
        "client_id": client_id,
        "redirect_uri": _build_callback_uri(request),
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": state,
        "access_type": "online",
    }
    return HttpResponseRedirect(f"{GOOGLE_AUTH_URL}?{urlencode(params)}")


@require_GET
def admin_google_login_callback(request):
    code = request.GET.get("code")
    state = request.GET.get("state")
    saved_state = request.session.pop("google_admin_oauth_state", None)
    request.session.modified = True

    if not code or not state or state != saved_state:
        return HttpResponseBadRequest("Невалидна OAuth заявка. Моля, опитайте отново.")

    client_id = _get_client_id()
    client_secret = _get_client_secret()
    if not client_secret:
        messages.error(request, "Входът с Google временно не е достъпен.")
        return redirect("admin:login")

    token_data = urlencode({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": _build_callback_uri(request),
        "grant_type": "authorization_code",
    }).encode("utf-8")

    req = Request(
        GOOGLE_TOKEN_URL,
        data=token_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urlopen(req, timeout=10) as resp:
            token_response = json.loads(resp.read().decode("utf-8"))
    except Exception:
        messages.error(request, "Неуспешна връзка с Google. Опитайте отново.")
        return redirect("admin:login")

    id_token = token_response.get("id_token")
    if not id_token:
        messages.error(request, "Невалиден отговор от Google.")
        return redirect("admin:login")

    try:
        user, _ = verify_google_credential(id_token)
    except Exception:
        messages.error(request, "Невалиден Google токен.")
        return redirect("admin:login")

    if not user.is_active or not user.is_staff:
        messages.error(request, "Този Google акаунт няма достъп до админ панела.")
        return redirect("admin:login")

    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    return redirect("admin:index")