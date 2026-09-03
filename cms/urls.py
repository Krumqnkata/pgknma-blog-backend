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
from rest_framework_simplejwt.views import TokenRefreshView
from django.views.generic.base import RedirectView
from valentine import views
from blog.serializer import MyTokenObtainPairView
from blog.session_auth import SessionTokenRefreshSerializer
from blog.views import blocked_meme_media, hqs, post_page
from blog.admin_auth import admin_google_login, admin_google_login_callback
from blog.admin_chat import (
    team_chat_attachment,
    team_chat_attachment_thumbnail,
    team_chat_delete,
    team_chat_discussion_access,
    team_chat_discussion_mark_read,
    team_chat_discussion_messages,
    team_chat_discussion_page,
    team_chat_discussion_resolve,
    team_chat_discussions_api,
    team_chat_discussions_page,
    team_chat_edit,
    team_chat_mark_read,
    team_chat_messages,
    team_chat_page,
    team_chat_pin,
    team_chat_reaction,
    team_chat_share,
    team_chat_share_to_discussion,
    team_chat_staff,
    team_chat_unread,
)
from django.contrib.sitemaps.views import sitemap
from blog.sitemaps import PostSitemap, StaticSitemap

sitemaps = {
    'posts': PostSitemap,
    'static': StaticSitemap,
}

urlpatterns = [
    path('post/<int:pk>', post_page, name='post-page'),
    path(
        'admin/team-chat/',
        admin.site.admin_view(team_chat_page),
        name='team-chat-index',
    ),
    path(
        'admin/team-chat/messages/',
        admin.site.admin_view(team_chat_messages),
        name='team-chat-messages',
    ),
    path(
        'admin/team-chat/discussions/',
        admin.site.admin_view(team_chat_discussions_page),
        name='team-chat-discussions',
    ),
    path(
        'admin/team-chat/discussions/api/',
        admin.site.admin_view(team_chat_discussions_api),
        name='team-chat-discussions-api',
    ),
    path(
        'admin/team-chat/discussions/<int:pk>/',
        admin.site.admin_view(team_chat_discussion_page),
        name='team-chat-discussion-detail',
    ),
    path(
        'admin/team-chat/discussions/<int:pk>/access/',
        admin.site.admin_view(team_chat_discussion_access),
        name='team-chat-discussion-access',
    ),
    path(
        'admin/team-chat/discussions/<int:pk>/messages/',
        admin.site.admin_view(team_chat_discussion_messages),
        name='team-chat-discussion-messages',
    ),
    path(
        'admin/team-chat/discussions/<int:pk>/read/',
        admin.site.admin_view(team_chat_discussion_mark_read),
        name='team-chat-discussion-read',
    ),
    path(
        'admin/team-chat/discussions/<int:pk>/resolve/',
        admin.site.admin_view(team_chat_discussion_resolve),
        name='team-chat-discussion-resolve',
    ),
    path(
        'admin/team-chat/attachments/<uuid:pk>/',
        admin.site.admin_view(team_chat_attachment),
        name='team-chat-attachment',
    ),
    path(
        'admin/team-chat/attachments/<uuid:pk>/thumbnail/',
        admin.site.admin_view(team_chat_attachment_thumbnail),
        name='team-chat-attachment-thumbnail',
    ),
    path(
        'admin/team-chat/messages/<int:pk>/edit/',
        admin.site.admin_view(team_chat_edit),
        name='team-chat-edit',
    ),
    path(
        'admin/team-chat/messages/<int:pk>/delete/',
        admin.site.admin_view(team_chat_delete),
        name='team-chat-delete',
    ),
    path(
        'admin/team-chat/messages/<int:pk>/pin/',
        admin.site.admin_view(team_chat_pin),
        name='team-chat-pin',
    ),
    path(
        'admin/team-chat/messages/<int:pk>/reaction/',
        admin.site.admin_view(team_chat_reaction),
        name='team-chat-reaction',
    ),
    path(
        'admin/team-chat/read/',
        admin.site.admin_view(team_chat_mark_read),
        name='team-chat-read',
    ),
    path(
        'admin/team-chat/unread/',
        admin.site.admin_view(team_chat_unread),
        name='team-chat-unread',
    ),
    path(
        'admin/team-chat/staff/',
        admin.site.admin_view(team_chat_staff),
        name='team-chat-staff',
    ),
    path(
        'admin/team-chat/share/',
        admin.site.admin_view(team_chat_share),
        name='team-chat-share',
    ),
    path(
        'admin/team-chat/share/discussion/',
        admin.site.admin_view(team_chat_share_to_discussion),
        name='team-chat-share-to-discussion',
    ),
    path("admin/google-login/", admin_google_login, name="admin_google_login"),
    path("admin/google-login/callback/", admin_google_login_callback, name="admin_google_login_callback"),
    path('admin/', admin.site.urls),

    # Включване на всички пътища от приложението 'blog' под префикс 'api/'
    path('api/', include('blog.urls')),

    # Пътища за автентикация с JWT
    path('api/auth/token/', MyTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path(
        'api/auth/token/refresh/',
        TokenRefreshView.as_view(serializer_class=SessionTokenRefreshSerializer),
        name='token_refresh',
    ),

    # Meme files are served only through the authorization-aware API endpoint.
    # These patterns must remain before the DEBUG static-media patterns below.
    path('files/memes/', blocked_meme_media, name='blocked-meme-media-root'),
    path(
        'files/memes/<path:media_path>',
        blocked_meme_media,
        name='blocked-meme-media',
    ),

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
