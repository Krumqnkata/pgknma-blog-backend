"""
URL configuration for cms project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,)
from django.views.generic.base import RedirectView
from valentine import views
from blog.views import hqs
from django.contrib.sitemaps.views import sitemap
from blog.sitemaps import PostSitemap, StaticSitemap

sitemaps = {
    'posts': PostSitemap,
    'static': StaticSitemap,
}

urlpatterns = [
    path('admin/', admin.site.urls),
    
    # Включване на всички пътища от приложението 'blog' под префикс 'api/'
    path('api/', include('blog.urls')),

    # Пътища за автентикация с JWT
    path('api/auth/token/', TokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/auth/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),

    path('valentines/', include(('valentine.urls', 'valentine'), namespace='valentine')),
    path('hqs/<str:video_name>/', hqs, name='video'),
    path('sitemap/', sitemap, {'sitemaps': sitemaps},
         name='django.contrib.sitemaps.views.sitemap'),
    
]

from django.conf import settings
from django.conf.urls.static import static

# Serve media files during development
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
