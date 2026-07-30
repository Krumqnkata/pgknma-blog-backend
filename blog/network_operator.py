import ipaddress
import logging
from functools import lru_cache
from pathlib import Path

from django.conf import settings


logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _get_reader(database_path):
    import geoip2.database

    return geoip2.database.Reader(database_path)


def lookup_network_operator(ip_address):
    """Return a local MaxMind ASN lookup as (organization, ASN), or empty data."""
    database_path = str(getattr(settings, "MAXMIND_ASN_DATABASE", "") or "").strip()
    if not database_path:
        return "", None

    try:
        ip = ipaddress.ip_address(str(ip_address or "").strip())
    except ValueError:
        return "", None

    if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_unspecified:
        return "", None

    if not Path(database_path).is_file():
        logger.warning("MaxMind ASN database is not available at %s", database_path)
        return "", None

    try:
        response = _get_reader(database_path).asn(str(ip))
    except ImportError:
        logger.warning("geoip2 is not installed; MaxMind ASN lookup is disabled")
        return "", None
    except Exception:
        logger.exception("MaxMind ASN lookup failed")
        return "", None

    organization = (response.autonomous_system_organization or "").strip()[:255]
    return organization, response.autonomous_system_number
