from django.conf import settings


def google_client_id(request):
    return {"GOOGLE_CLIENT_ID": getattr(settings, "GOOGLE_CLIENT_ID", "")}