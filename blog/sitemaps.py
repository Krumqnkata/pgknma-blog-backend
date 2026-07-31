from django.contrib.sitemaps import Sitemap
from django.conf import settings
from .models import Posts


class CanonicalSitemap(Sitemap):
    """Sitemap base that never derives the public host from the proxy request."""

    def get_domain(self, site=None):
        return settings.SITEMAP_DOMAIN

    def get_protocol(self, protocol=None):
        return settings.SITEMAP_PROTOCOL


class PostSitemap(CanonicalSitemap):
    changefreq = "daily"
    priority = 0.9

    def items(self):
        return Posts.objects.filter(
            published=True,
            allowed=True,
        ).order_by("pk")

    def location(self, obj):
        return f'/post/{obj.id}'

    def lastmod(self, obj):
        return obj.created_at


class StaticSitemap(CanonicalSitemap):

    def items(self):
        return [
            '/',
            '/news',
            '/events',
            '/program',
            '/faq',
            '/contact',
            '/developers',
            '/privacy-policy',
            '/terms-of-service',
        ]

    def location(self, item):
        return item

    def priority(self, item):
        if item in ['/', '/news']:
            return 1.0
        if item in ['/events', '/program']:
            return 0.8
        if item in ['/faq', '/contact', '/developers']:
            return 0.7
        return 0.5

    def changefreq(self, item):
        if item in ['/', '/news']:
            return 'daily'
        if item in ['/events', '/program']:
            return 'weekly'
        if item in ['/privacy-policy', '/terms-of-service']:
            return 'yearly'
        return 'monthly'
