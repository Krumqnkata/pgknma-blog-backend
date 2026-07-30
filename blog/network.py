import ipaddress

from django.conf import settings


def _normalized_ip(value):
    try:
        return str(ipaddress.ip_address(str(value or '').strip()))
    except ValueError:
        return ''


def _trusted_proxy_ips():
    configured = getattr(settings, 'TRUSTED_PROXY_IPS', ())
    if isinstance(configured, str):
        configured = configured.split(',')
    return {
        normalized
        for value in configured
        if (normalized := _normalized_ip(value))
    }


def get_client_ip(request):
    """Return a forwarded client IP only when the immediate peer is trusted."""
    remote_addr = _normalized_ip(request.META.get('REMOTE_ADDR'))
    if not remote_addr:
        return None

    trusted_proxies = _trusted_proxy_ips()
    if remote_addr not in trusted_proxies:
        return remote_addr

    forwarded = [
        _normalized_ip(value)
        for value in request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')
    ]
    if not forwarded or any(not value for value in forwarded):
        return remote_addr

    # Walk from the application-facing proxy back towards the real client.
    for candidate in reversed(forwarded):
        if candidate not in trusted_proxies:
            return candidate
    return forwarded[0]
