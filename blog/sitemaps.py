from django.contrib.sitemaps import Sitemap
from .models import Posts 
class PostSitemap(Sitemap):
    changefreq = "daily"
    priority = 0.9
    protocol = 'https'

    def items(self):
        return Posts.objects.filter(published=True, allowed=True)
    def location(self, obj):
        return f'/post/{obj.id}'

    def lastmod(self, obj):
        return obj.created_at
    
class StaticSitemap(Sitemap):
        protocol = 'https'

        # 1. Добавяме всички публични статични адреси
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
                '/terms-of-service'
            ]

        def location(self, item):
            return item

        # 2. Динамичен приоритет според важността на страницата
        def priority(self, item):
            if item in ['/', '/news']:
                return 1.0
            elif item in ['/events', '/program']:
                return 0.8
            elif item in ['/faq', '/contact', '/developers']:
                return 0.7
            else:  # '/privacy-policy', '/terms-of-service'
                return 0.5

        # 3. Динамична честота на сканиране според това колко често се обновява
        def changefreq(self, item):
            if item in ['/', '/news']:
                return 'daily'
            elif item in ['/events', '/program']:
                return 'weekly'
            elif item in ['/privacy-policy', '/terms-of-service']:
                return 'yearly'  # или 'monthly'
            else:
                return 'monthly'
    