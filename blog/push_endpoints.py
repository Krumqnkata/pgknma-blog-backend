import ipaddress
import socket
from urllib.parse import urlsplit

import requests
from django.conf import settings


class UnsafePushEndpoint(ValueError):
    """Raised when a Web Push endpoint is not safe to contact."""


class NoRedirectWebPushSession(requests.Session):
    """A requests session that cannot follow redirects or environment proxies."""

    def __init__(self):
        super().__init__()
        self.trust_env = False

    def request(self, method, url, **kwargs):
        kwargs['allow_redirects'] = False
        return super().request(method, url, **kwargs)


def _normalized_allowed_hosts():
    hosts = []
    for value in getattr(settings, 'WEB_PUSH_ALLOWED_ENDPOINT_HOSTS', ()):
        host = str(value).strip().lower().rstrip('.')
        if host.startswith('*.'):
            host = host[2:]
        if host:
            try:
                hosts.append(host.encode('idna').decode('ascii'))
            except UnicodeError as exc:
                raise UnsafePushEndpoint(
                    'Конфигурацията съдържа невалиден Web Push доставчик.'
                ) from exc
    return tuple(hosts)


def _host_is_allowed(host):
    return any(
        host == allowed_host or host.endswith(f'.{allowed_host}')
        for allowed_host in _normalized_allowed_hosts()
    )


def _assert_public_dns(host, port):
    try:
        address_info = socket.getaddrinfo(
            host,
            port,
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise UnsafePushEndpoint(
            'Web Push доставчикът не може да бъде намерен.'
        ) from exc

    addresses = {row[4][0].split('%', 1)[0] for row in address_info if row[4]}
    if not addresses:
        raise UnsafePushEndpoint('Web Push доставчикът няма валиден IP адрес.')

    for raw_address in addresses:
        try:
            address = ipaddress.ip_address(raw_address)
        except ValueError as exc:
            raise UnsafePushEndpoint(
                'Web Push доставчикът върна невалиден IP адрес.'
            ) from exc
        if address.version == 6 and address.ipv4_mapped is not None:
            address = address.ipv4_mapped
        if not address.is_global:
            raise UnsafePushEndpoint(
                'Web Push endpoint-ът сочи към непублична мрежа.'
            )


def validate_web_push_endpoint(endpoint, *, resolve_dns=False):
    """Validate endpoint syntax/provider and optionally its resolved addresses."""

    if not isinstance(endpoint, str):
        raise UnsafePushEndpoint('Невалиден Web Push endpoint.')
    endpoint = endpoint.strip()
    if not endpoint or any(ord(character) < 32 for character in endpoint):
        raise UnsafePushEndpoint('Невалиден Web Push endpoint.')

    try:
        parsed = urlsplit(endpoint)
        hostname = parsed.hostname
        port = parsed.port
    except (UnicodeError, ValueError) as exc:
        raise UnsafePushEndpoint('Невалиден Web Push endpoint.') from exc

    if not hostname:
        raise UnsafePushEndpoint('Web Push endpoint-ът няма валиден host.')
    try:
        normalized_host = hostname.lower().rstrip('.').encode('idna').decode('ascii')
    except UnicodeError as exc:
        raise UnsafePushEndpoint('Web Push endpoint-ът няма валиден host.') from exc

    if parsed.username is not None or parsed.password is not None:
        raise UnsafePushEndpoint('Web Push endpoint-ът не може да съдържа потребителски данни.')
    if parsed.fragment:
        raise UnsafePushEndpoint('Web Push endpoint-ът не може да съдържа fragment.')

    debug_local = bool(
        settings.DEBUG
        and parsed.scheme.lower() == 'http'
        and normalized_host in {'localhost', '127.0.0.1', '::1'}
    )
    if not debug_local:
        if parsed.scheme.lower() != 'https':
            raise UnsafePushEndpoint('Push endpoint-ът трябва да използва HTTPS.')
        if port not in (None, 443):
            raise UnsafePushEndpoint('Web Push endpoint-ът използва непозволен порт.')
        if not _host_is_allowed(normalized_host):
            raise UnsafePushEndpoint('Web Push доставчикът не е разрешен.')

    if resolve_dns and not debug_local:
        _assert_public_dns(normalized_host, port or 443)

    return endpoint
