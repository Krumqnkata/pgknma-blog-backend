from django.urls import path, include
from django.contrib.auth.decorators import user_passes_test
from rest_framework.routers import DefaultRouter
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView
from . import views as blog_views
from blog.auth import GoogleAuthView, GoogleLinkView

# Основен рутер за ViewSet-ове
router = DefaultRouter()
router.register('posts', blog_views.PostViewSet, basename='posts')
router.register('poll', blog_views.WeeklyPollViewSet, basename='poll')
router.register('memes', blog_views.MemeOfWeekViewSet, basename='memes')

# The schema itself is protected too, so no API map is exposed to staff users.
superuser_required = user_passes_test(
    lambda user: user.is_active and user.is_superuser,
    login_url='/admin/login/',
)

# URL пътища
urlpatterns = [
    # OpenAPI documentation: accessible only to active superusers.
    path(
        'schema/',
        superuser_required(SpectacularAPIView.as_view()),
        name='openapi-schema',
    ),
    path(
        'docs/',
        superuser_required(SpectacularSwaggerView.as_view(url_name='openapi-schema')),
        name='swagger-ui',
    ),
    path(
        'redoc/',
        superuser_required(SpectacularRedocView.as_view(url_name='openapi-schema')),
        name='redoc',
    ),

    # Meme binaries are authorization-aware and must not use MEDIA_URL.
    path(
        'memes/<int:pk>/image/',
        blog_views.ProtectedMemeImageView.as_view(),
        name='meme-image',
    ),

    # Включване на рутера
    path('', include(router.urls)),

    # google_auth
    path('auth/google/', GoogleAuthView.as_view(), name='google-auth'),
    path('auth/google/link/', GoogleLinkView.as_view(), name='google-link'),

    # Пътища за коментари
    path('posts/<int:post_pk>/comments/', blog_views.CommentList.as_view(), name='comment-list'),
    path('posts/<int:post_pk>/comments/add/', blog_views.AddCommentAPIView.as_view(), name='add-comment'),

    # Път за регистрация
    path('auth/register/', blog_views.RegisterView.as_view(), name='register'),
    path('auth/check-username/', blog_views.CheckUsernameView.as_view(), name='check-username'),
    path('auth/validate-password/', blog_views.ValidatePasswordView.as_view(), name='validate-password'),
    path('auth/profile/change-password/', blog_views.PasswordChangeView.as_view(), name='password-change'),
    path('auth/profile/change-username/', blog_views.UsernameChangeView.as_view(), name='username-change'),
    path('auth/profile/deactivate/', blog_views.AccountDeactivateView.as_view(), name='account-deactivate'),
    path('auth/logout/', blog_views.LogoutView.as_view(), name='logout'),


    # Пътища за съдържание на потребителя
    path('my-content/songs/', blog_views.MySongSuggestionsView.as_view(), name='my-songs'),
    path('my-content/songs/<int:pk>/delete/', blog_views.MySongSuggestionDeleteView.as_view(), name='delete-my-song'),
    path('my-content/memes/', blog_views.MyMemesView.as_view(), name='my-memes'),
    path('my-content/memes/<int:pk>/delete/', blog_views.MyMemeDeleteView.as_view(), name='delete-my-meme'),
    path('my-content/comments/', blog_views.MyCommentsView.as_view(), name='my-comments'),
    path('my-content/comments/<int:pk>/delete/', blog_views.MyCommentDeleteView.as_view(), name='delete-my-comment'),

    # Път за контактен формуляр
    path('contact/', blog_views.ContactFormSubmitView.as_view(), name='contact-submit'),

    # Път за известия
    path('notifications/', blog_views.NotificationListView.as_view(), name='notification-list'),

    # Път за събития
    path('events/', blog_views.EventListView.as_view(), name='event-list'),

    # Учебна програма, поддържана чрез CSV импорт в админ панела
    path('program/', blog_views.ProgramScheduleView.as_view(), name='program-schedule'),

    # Път за дневник на промените
    path('changelog/', blog_views.ChangelogListView.as_view(), name='changelog-list'),

    # Път за Условия за ползване
    path('terms-of-service/', blog_views.TermsOfServiceView.as_view(), name='terms-of-service'),

    # Път за Политика за поверителност
    path('privacy-policy/', blog_views.PrivacyPolicyView.as_view(), name='privacy-policy'),

    # Път за записване на съгласие за бисквитки
    path('consent/', blog_views.ConsentRecordCreateView.as_view(), name='record-consent'),

    # Път за статус на сайта (режим поддръжка)
    path('site-status/', blog_views.SiteStatusView.as_view(), name='site-status'),

    # Път за предложения за песни за звънец
    path('bell-song-suggestions/submit/', blog_views.BellSongSuggestionCreateAPIView.as_view(), name='bell-song-submit'),

    # Bell song voting system URLs
    path('approved-songs/', blog_views.ApprovedBellSongListView.as_view(), name='approved-songs-list'),
    path('songs/<int:pk>/vote/', blog_views.BellSongVoteView.as_view(), name='song-vote'),

    # Meme voting system URL
    path('memes/<int:pk>/vote/', blog_views.MemeVoteView.as_view(), name='meme-vote'),

]
