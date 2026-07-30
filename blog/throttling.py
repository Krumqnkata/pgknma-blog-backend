from rest_framework.throttling import (
    AnonRateThrottle,
    ScopedRateThrottle,
    UserRateThrottle,
)

from .network import get_client_ip


class TrustedProxyAnonRateThrottle(AnonRateThrottle):
    def get_ident(self, request):
        return get_client_ip(request) or 'unknown'


class TrustedProxyUserRateThrottle(UserRateThrottle):
    def get_ident(self, request):
        return get_client_ip(request) or 'unknown'


class TrustedProxyScopedRateThrottle(ScopedRateThrottle):
    def get_ident(self, request):
        return get_client_ip(request) or 'unknown'
