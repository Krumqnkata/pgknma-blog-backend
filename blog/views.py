import mimetypes

from django.conf import settings
from django.core.exceptions import ValidationError
from django.contrib.auth import password_validation
from django.db import IntegrityError, transaction
from django.http import FileResponse, Http404
from django.utils.cache import patch_vary_headers
from rest_framework import viewsets, generics, status
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404
from django.utils import timezone
from datetime import timedelta
from django.db.models import BooleanField, Count, Exists, OuterRef, Q, Value
from django.contrib.auth.models import User
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.tokens import RefreshToken
from .models import Posts, Comments, PollQuestion, PollAnswer, PollOption, ContactSubmission, Notification, Event, TermsOfService, BellSongSuggestion, PrivacyPolicy, MemeOfWeek, ConsentRecord, SiteSettings, Changelog
from .permissions import IsOwner, SiteIsAvailable
from .network import get_client_ip
from .serializer import (
    PostSerializer, RegisterSerializer, CommentSerializer,
    PollQuestionSerializer, UserPollStatusSerializer, PollAnswerSerializer,
    PollStatisticsSerializer, ContactSubmissionSerializer, NotificationSerializer,
    EventSerializer, TermsOfServiceSerializer, BellSongSuggestionSerializer,
    PrivacyPolicySerializer, MemeOfWeekSerializer, ConsentRecordSerializer, SiteSettingsSerializer,
    ChangelogSerializer, PasswordChangeSerializer, UsernameChangeSerializer
)


def _with_vote_status(queryset, request, relation):
    if not request.user.is_authenticated:
        return queryset.annotate(
            user_has_voted=Value(False, output_field=BooleanField())
        )
    through = relation.through
    related_field = (
        'memeofweek_id'
        if relation is MemeOfWeek.voted_by
        else 'bellsongsuggestion_id'
    )
    return queryset.annotate(
        user_has_voted=Exists(
            through.objects.filter(
                **{
                    related_field: OuterRef('pk'),
                    'user_id': request.user.pk,
                }
            )
        )
    )


class PostViewSet(viewsets.ModelViewSet):
    queryset = (
        Posts.objects.filter(published=True, allowed=True)
        .select_related('author', 'category')
        .prefetch_related('images', 'documents')
        .order_by('-created_at')
    )
    serializer_class = PostSerializer
    permission_classes = [SiteIsAvailable, AllowAny]
    http_method_names = ['get', 'head', 'options']


class ProtectedMemeImageView(APIView):
    """Serve meme files without exposing pending uploads through MEDIA_URL."""

    authentication_classes = [JWTAuthentication, SessionAuthentication]
    permission_classes = [SiteIsAvailable, AllowAny]
    http_method_names = ['get', 'head', 'options']

    def get(self, request, pk):
        meme = get_object_or_404(
            MemeOfWeek.objects.only(
                'pk',
                'user_id',
                'image',
                'is_approved',
            ),
            pk=pk,
        )
        user = request.user
        is_authenticated = bool(user and user.is_authenticated)
        is_owner = is_authenticated and meme.user_id == user.pk
        is_staff = is_authenticated and user.is_staff

        if not (is_owner or is_staff):
            feature_enabled = (
                SiteSettings.objects.filter(pk=1)
                .values_list('enable_meme_of_the_week', flat=True)
                .first()
            )
            if not meme.is_approved or feature_enabled is False:
                raise Http404

        if not meme.image or not meme.image.name:
            raise Http404

        try:
            image_file = meme.image.open('rb')
        except (FileNotFoundError, OSError, ValueError):
            raise Http404

        content_type, _ = mimetypes.guess_type(meme.image.name)
        unsafe_inline_type = (
            not content_type
            or not content_type.startswith('image/')
            or content_type == 'image/svg+xml'
        )
        if unsafe_inline_type:
            content_type = 'application/octet-stream'

        response = FileResponse(
            image_file,
            as_attachment=unsafe_inline_type,
            filename=meme.image.name.rsplit('/', 1)[-1],
            content_type=content_type,
        )
        response['Cache-Control'] = 'private, no-store'
        response['Pragma'] = 'no-cache'
        response['X-Content-Type-Options'] = 'nosniff'
        patch_vary_headers(response, ('Authorization', 'Cookie'))
        return response


def blocked_meme_media(request, media_path=None):
    """Never serve meme uploads directly from the public MEDIA_URL."""

    raise Http404


class MemeOfWeekViewSet(viewsets.ModelViewSet):
    serializer_class = MemeOfWeekSerializer
    http_method_names = ['get', 'post', 'head', 'options']

    def get_queryset(self):
        site_settings = SiteSettings.objects.filter(pk=1).first()
        if site_settings and not site_settings.enable_meme_of_the_week:
            return MemeOfWeek.objects.none()
        queryset = (
            MemeOfWeek.objects.filter(is_approved=True)
            .select_related('user')
            .order_by('-votes', '-created_at')
        )
        return _with_vote_status(queryset, self.request, MemeOfWeek.voted_by)

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            self.permission_classes = [SiteIsAvailable, AllowAny]
        else:
            self.permission_classes = [SiteIsAvailable, IsAuthenticated]
        return super().get_permissions()

    def get_throttles(self):
        self.throttle_scope = (
            'meme_submit' if self.action == 'create' else None
        )
        return super().get_throttles()

    def perform_create(self, serializer):
        site_settings, created = SiteSettings.objects.get_or_create(pk=1)
        if not site_settings.enable_meme_of_the_week:
            raise PermissionDenied("Функцията 'Меме на седмицата' в момента е деактивирана.")
        serializer.save(user=self.request.user)
class RegisterView(generics.CreateAPIView):
    permission_classes = [SiteIsAvailable, AllowAny]
    throttle_scope = 'register'
    queryset = User.objects.all()
    serializer_class = RegisterSerializer

    def post(self, request, *args, **kwargs):
        site_settings, created = SiteSettings.objects.get_or_create(pk=1)
        if not site_settings.enable_user_registration:
            return Response(
                {"detail": "Регистрацията на потребители е временно деактивирана."},
                status=status.HTTP_403_FORBIDDEN
            )
        return super().post(request, *args, **kwargs)


class PasswordChangeView(generics.UpdateAPIView):
    serializer_class = PasswordChangeSerializer
    model = User
    permission_classes = (SiteIsAvailable, IsAuthenticated)

    def get_object(self, queryset=None):
        return self.request.user

    def update(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)

        if serializer.is_valid():
            with transaction.atomic():
                self.object = User.objects.select_for_update().get(
                    pk=request.user.pk
                )
                if not self.object.check_password(
                    serializer.validated_data["current_password"]
                ):
                    return Response(
                        {"current_password": ["Грешна парола."]},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                self.object.set_password(
                    serializer.validated_data["new_password"]
                )
                self.object.save(update_fields=['password'])
            return Response({"status": "Паролата е сменена успешно"}, status=status.HTTP_200_OK)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class UsernameChangeView(generics.UpdateAPIView):
    serializer_class = UsernameChangeSerializer
    model = User
    permission_classes = (SiteIsAvailable, IsAuthenticated)

    def get_object(self, queryset=None):
        return self.request.user

    def update(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)

        if serializer.is_valid():
            try:
                with transaction.atomic():
                    SiteSettings.objects.select_for_update().get_or_create(pk=1)
                    self.object = User.objects.select_for_update().get(
                        pk=request.user.pk
                    )
                    if not self.object.check_password(
                        serializer.validated_data["current_password"]
                    ):
                        return Response(
                            {"current_password": ["Грешна парола."]},
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                    new_username = serializer.validated_data["new_username"]
                    if User.objects.exclude(pk=self.object.pk).filter(
                        username__iexact=new_username
                    ).exists():
                        return Response(
                            {
                                "new_username": [
                                    "Това потребителско име вече е заето."
                                ]
                            },
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                    self.object.username = new_username
                    self.object.save(update_fields=['username'])
            except IntegrityError:
                return Response(
                    {
                        "new_username": [
                            "Това потребителско име вече е заето."
                        ]
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return Response({"status": "Потребителското име е сменено успешно"}, status=status.HTTP_200_OK)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class AccountDeactivateView(APIView):
    permission_classes = (SiteIsAvailable, IsAuthenticated)

    def _deactivate(self, request):
        with transaction.atomic():
            instance = User.objects.select_for_update().get(
                pk=request.user.pk
            )
            instance.is_active = False
            instance.save(update_fields=['is_active'])
        return Response({"status": "Профилът е деактивиран успешно"}, status=status.HTTP_200_OK)

    def post(self, request, *args, **kwargs):
        return self._deactivate(request)

    def put(self, request, *args, **kwargs):
        return self._deactivate(request)

    def patch(self, request, *args, **kwargs):
        return self._deactivate(request)


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        refresh_token = request.data.get('refresh')
        if not refresh_token:
            return Response(
                {"detail": "Липсва refresh token."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            token = RefreshToken(refresh_token)
            user_id_claim = settings.SIMPLE_JWT.get(
                'USER_ID_CLAIM',
                'user_id',
            )
            if str(token.get(user_id_claim)) != str(request.user.pk):
                raise TokenError()
            token.blacklist()
        except TokenError:
            return Response(
                {"detail": "Невалиден refresh token."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(status=status.HTTP_205_RESET_CONTENT)


class MySongSuggestionsView(generics.ListAPIView):
    serializer_class = BellSongSuggestionSerializer
    permission_classes = (SiteIsAvailable, IsAuthenticated)

    def get_queryset(self):
        queryset = (
            BellSongSuggestion.objects.filter(user=self.request.user)
            .select_related('user')
        )
        return _with_vote_status(
            queryset, self.request, BellSongSuggestion.voted_by
        )

class MyMemesView(generics.ListAPIView):
    serializer_class = MemeOfWeekSerializer
    permission_classes = (SiteIsAvailable, IsAuthenticated)

    def get_queryset(self):
        queryset = (
            MemeOfWeek.objects.filter(user=self.request.user)
            .select_related('user')
        )
        return _with_vote_status(queryset, self.request, MemeOfWeek.voted_by)

    def get_serializer_context(self):
        return {'request': self.request}

class MyCommentsView(generics.ListAPIView):
    serializer_class = CommentSerializer
    permission_classes = (SiteIsAvailable, IsAuthenticated)

    def get_queryset(self):
        return (
            Comments.objects.filter(user=self.request.user)
            .select_related('user', 'post', 'parent__user')
            .annotate(reply_count_value=Count('replies'))
        )

class MyCommentDeleteView(generics.DestroyAPIView):
    queryset = Comments.objects.all()
    serializer_class = CommentSerializer
    permission_classes = (SiteIsAvailable, IsAuthenticated, IsOwner)


class MySongSuggestionDeleteView(generics.DestroyAPIView):
    queryset = BellSongSuggestion.objects.all()
    serializer_class = BellSongSuggestionSerializer
    permission_classes = (SiteIsAvailable, IsAuthenticated, IsOwner)

    def destroy(self, request, *args, **kwargs):
        with transaction.atomic():
            instance = get_object_or_404(
                BellSongSuggestion.objects.select_for_update(),
                pk=kwargs['pk'],
            )
            self.check_object_permissions(request, instance)
            if instance.status != 'pending':
                return Response({"detail": "Можете да триете само чакащи предложения."}, status=status.HTTP_403_FORBIDDEN)
            self.perform_destroy(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MyMemeDeleteView(generics.DestroyAPIView):
    queryset = MemeOfWeek.objects.all()
    serializer_class = MemeOfWeekSerializer
    permission_classes = (SiteIsAvailable, IsAuthenticated, IsOwner)

    def destroy(self, request, *args, **kwargs):
        with transaction.atomic():
            instance = get_object_or_404(
                MemeOfWeek.objects.select_for_update(),
                pk=kwargs['pk'],
            )
            self.check_object_permissions(request, instance)
            if instance.is_approved:
                return Response({"detail": "Можете да триете само неодобрени мемета."}, status=status.HTTP_403_FORBIDDEN)
            self.perform_destroy(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)


class BellSongSuggestionCreateAPIView(generics.CreateAPIView):
    queryset = BellSongSuggestion.objects.all()
    serializer_class = BellSongSuggestionSerializer
    permission_classes = [SiteIsAvailable, IsAuthenticated]
    throttle_scope = 'song_submit'
    def perform_create(self, serializer):
        site_settings, created = SiteSettings.objects.get_or_create(pk=1)
        if not site_settings.enable_bell_suggestions:
            raise PermissionDenied("Функцията 'Предложения за звънец' в момента е деактивирана.")
        serializer.save(user=self.request.user, status='pending')
class ApprovedBellSongListView(generics.ListAPIView):
    serializer_class = BellSongSuggestionSerializer
    permission_classes = [SiteIsAvailable, AllowAny]

    def get_queryset(self):
        site_settings = SiteSettings.objects.filter(pk=1).first()
        if site_settings and not site_settings.enable_bell_suggestions:
            return BellSongSuggestion.objects.none()
        queryset = (
            BellSongSuggestion.objects.filter(status='approved')
            .select_related('user')
            .order_by('-votes', '-submitted_at')
        )
        return _with_vote_status(
            queryset, self.request, BellSongSuggestion.voted_by
        )

    def get_serializer_context(self):
        return {'request': self.request}
class BellSongVoteView(APIView):
    permission_classes = [SiteIsAvailable, IsAuthenticated]
    throttle_scope = 'vote'

    def post(self, request, pk):
        site_settings = SiteSettings.objects.filter(pk=1).first()
        if site_settings and not site_settings.enable_bell_suggestions:
            return Response(
                {'detail': "Функцията 'Предложения за звънец' е деактивирана."},
                status=status.HTTP_404_NOT_FOUND,
            )
        with transaction.atomic():
            song = get_object_or_404(
                BellSongSuggestion.objects.select_for_update(),
                pk=pk,
            )
            if song.status != 'approved':
                return Response({'detail': 'Може да гласувате само за одобрени песни.'}, status=status.HTTP_400_BAD_REQUEST)
            if song.voted_by.filter(pk=request.user.pk).exists():
                return Response({'detail': 'Вече сте гласували за тази песен.'}, status=status.HTTP_400_BAD_REQUEST)
            song.voted_by.add(request.user)
            song.refresh_from_db(fields=['votes'])

        # Pass context to serializer to access the request
        serializer = BellSongSuggestionSerializer(song, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)


class MemeVoteView(APIView):
    permission_classes = [SiteIsAvailable, IsAuthenticated]
    throttle_scope = 'vote'

    def post(self, request, pk):
        site_settings = SiteSettings.objects.filter(pk=1).first()
        if site_settings and not site_settings.enable_meme_of_the_week:
            return Response(
                {'detail': "Функцията 'Меме на седмицата' е деактивирана."},
                status=status.HTTP_404_NOT_FOUND,
            )
        with transaction.atomic():
            meme = get_object_or_404(
                MemeOfWeek.objects.select_for_update(),
                pk=pk,
            )
            if not meme.is_approved:
                return Response({'detail': 'Може да гласувате само за одобрени мемета.'}, status=status.HTTP_400_BAD_REQUEST)
            if meme.voted_by.filter(pk=request.user.pk).exists():
                return Response({'detail': 'Вече сте гласували за това меме.'}, status=status.HTTP_400_BAD_REQUEST)
            meme.voted_by.add(request.user)
            meme.refresh_from_db(fields=['votes'])

        # Pass context to serializer to access the request
        serializer = MemeOfWeekSerializer(meme, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)


class CommentList(generics.ListAPIView):
    serializer_class = CommentSerializer
    permission_classes = [SiteIsAvailable, AllowAny]
    http_method_names = ['get', 'post', 'options', 'head']

    def get_queryset(self):
        post_id = self.kwargs.get('post_pk')
        if not post_id:
            return Comments.objects.none()
        post = get_object_or_404(
            Posts,
            id=post_id,
            published=True,
            allowed=True,
        )
        # Fetch all comments for the post, ordered by creation date.
        # The frontend will be responsible for grouping replies.
        return (
            Comments.objects.filter(post=post)
            .select_related('user', 'parent__user')
            .annotate(reply_count_value=Count('replies'))
            .order_by('created_at')
        )


class AddCommentAPIView(APIView):
    permission_classes = [SiteIsAvailable, IsAuthenticated]

    def post(self, request, post_pk):
        user = request.user
        post = get_object_or_404(
            Posts,
            id=post_pk,
            published=True,
            allowed=True,
        )

        with transaction.atomic():
            # Locking the user serializes cooldown/count checks for this author.
            locked_user = User.objects.select_for_update().get(pk=user.pk)
            now = timezone.now()

            # Cooldown: 60 seconds between comments
            last_comment = Comments.objects.filter(
                user=locked_user
            ).order_by('-created_at').first()
            if last_comment:
                time_since_last_comment = now - last_comment.created_at
                if time_since_last_comment < timedelta(seconds=60):
                    wait_time = 60 - int(time_since_last_comment.total_seconds())
                    return Response(
                        {"detail": f"Трябва да изчакате още {wait_time} секунди, преди да коментирате отново."},
                        status=status.HTTP_429_TOO_MANY_REQUESTS
                    )

            # Rate Limit: 5 comments per 30 minutes
            thirty_minutes_ago = now - timedelta(minutes=30)
            comment_count = Comments.objects.filter(
                user=locked_user,
                created_at__gte=thirty_minutes_ago,
            ).count()
            if comment_count >= 5:
                return Response(
                    {"detail": "Надвишихте лимита от 5 коментара за 30 минути."},
                    status=status.HTTP_429_TOO_MANY_REQUESTS
                )

            serializer = CommentSerializer(
                data=request.data,
                context={'request': request, 'post': post},
            )
            if serializer.is_valid():
                comment = serializer.save(user=locked_user, post=post)
                return Response(
                    CommentSerializer(comment).data,
                    status=status.HTTP_201_CREATED,
                )
            return Response(
                serializer.errors,
                status=status.HTTP_400_BAD_REQUEST,
            )
class WeeklyPollViewSet(viewsets.ViewSet):
    permission_classes = [SiteIsAvailable, IsAuthenticated]

    def get_throttles(self):
        self.throttle_scope = 'vote' if self.action == 'submit' else None
        return super().get_throttles()

    def get_serializer_class(self):
        if self.action == 'submit':
            return PollAnswerSerializer
        elif self.action == 'statistics':
            return PollStatisticsSerializer
        return UserPollStatusSerializer

    @action(detail=False, methods=['get'])
    def status(self, request):
        site_settings, created = SiteSettings.objects.get_or_create(pk=1)
        if not site_settings.enable_weekly_poll:
            return Response({"detail": "Функцията 'Анкети' в момента е деактивирана."}, status=status.HTTP_404_NOT_FOUND)

        user = request.user
        now = timezone.now()

        active_question = (
            PollQuestion.objects.filter(start_date__lte=now, end_date__gte=now)
            .prefetch_related('options')
            .first()
        )

        if not active_question:
            return Response({"detail": "В момента няма активни въпроси за анкети."}, status=status.HTTP_404_NOT_FOUND)

        latest_answer = (
            PollAnswer.objects.filter(user=user, question=active_question)
            .select_related('selected_option')
            .first()
        )

        if latest_answer:
            correct_option = PollOption.objects.filter(question=active_question, is_correct=True).first()
            data = {
                'is_locked': True,
                'unlocks_at': active_question.end_date,
                'question': active_question,
                'last_result': {
                    'questionId': active_question.id,
                    'selected': latest_answer.selected_option.key,
                    'correct': correct_option.key if correct_option else None
                }
            }
            return Response(UserPollStatusSerializer(data).data)
        else:
            data = {
                'is_locked': False,
                'unlocks_at': None,
                'question': active_question,
                'last_result': None
            }
            return Response(UserPollStatusSerializer(data).data)

    @action(detail=False, methods=['post'])
    def submit(self, request):
        site_settings, created = SiteSettings.objects.get_or_create(pk=1)
        if not site_settings.enable_weekly_poll:
            return Response({"detail": "Функцията 'Анкети' в момента е деактивирана."}, status=status.HTTP_403_FORBIDDEN)

        user = request.user

        serializer = PollAnswerSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        question = serializer.validated_data['question']
        selected_option = serializer.validated_data['selected_option']

        try:
            with transaction.atomic():
                active_question = (
                    PollQuestion.objects.select_for_update()
                    .prefetch_related('options')
                    .filter(id=question.id)
                    .first()
                )

                locked_now = timezone.now()
                if (
                    not active_question
                    or active_question.start_date > locked_now
                    or active_question.end_date < locked_now
                ):
                    return Response({"detail": "Тази анкета в момента не е активна."}, status=status.HTTP_403_FORBIDDEN)

                selected_option = (
                    PollOption.objects.filter(
                        pk=selected_option.pk,
                        question=active_question,
                    )
                    .first()
                )
                if not selected_option:
                    return Response({"detail": "Избраната опция не принадлежи към този въпрос."}, status=status.HTTP_400_BAD_REQUEST)

                PollAnswer.objects.create(
                    user=user,
                    question=active_question,
                    selected_option=selected_option,
                )
        except (IntegrityError, ValidationError):
            return Response(
                {"detail": "Вече сте отговорили на тази анкета."},
                status=status.HTTP_409_CONFLICT,
            )

        correct_option = PollOption.objects.filter(question=active_question, is_correct=True).first()
        data = {
            'is_locked': True,
            'unlocks_at': active_question.end_date,
            'question': active_question,
            'last_result': {
                'questionId': active_question.id,
                'selected': selected_option.key,
                'correct': correct_option.key if correct_option else None
            }
        }
        return Response(UserPollStatusSerializer(data).data, status=status.HTTP_201_CREATED)

    @action(
        detail=False,
        methods=['get'],
        permission_classes=[SiteIsAvailable, AllowAny],
    )
    def statistics(self, request):
        site_settings = SiteSettings.objects.filter(pk=1).first()
        if site_settings and not site_settings.enable_weekly_poll:
            return Response(
                {"detail": "Функцията 'Анкети' в момента е деактивирана."},
                status=status.HTTP_404_NOT_FOUND,
            )
        aggregates = PollAnswer.objects.aggregate(
            total_participants=Count('user', distinct=True),
            total_answers=Count('id'),
            correct_answers=Count(
                'id',
                filter=Q(selected_option__is_correct=True),
            ),
        )
        aggregates['leaderboard'] = list(
            User.objects.filter(
                is_active=True,
                pollanswer__selected_option__is_correct=True,
            )
            .annotate(
                correct_answers=Count(
                    'pollanswer',
                    filter=Q(pollanswer__selected_option__is_correct=True),
                )
            )
            .order_by('-correct_answers', 'username', 'id')
            .values('id', 'username', 'correct_answers')[:5]
        )
        serializer = PollStatisticsSerializer(aggregates)
        return Response(serializer.data)


class ContactFormSubmitView(APIView):
    permission_classes = [SiteIsAvailable, AllowAny]
    throttle_scope = 'contact'
    def post(self, request):
        serializer = ContactSubmissionSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(user=request.user if request.user.is_authenticated else None)
            return Response({"detail": "Съобщението е изпратено успешно!"}, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
class NotificationListView(generics.ListAPIView):
    queryset = Notification.objects.filter(enabled=True).order_by('-created_at')
    serializer_class = NotificationSerializer
    permission_classes = [SiteIsAvailable, AllowAny]

class EventListView(generics.ListAPIView):
    queryset = Event.objects.filter(published=True).order_by('-start_datetime')
    serializer_class = EventSerializer
    permission_classes = [SiteIsAvailable, AllowAny]

class ChangelogListView(generics.ListAPIView):
    queryset = Changelog.objects.filter(is_active=True).order_by('-updated_at')
    serializer_class = ChangelogSerializer
    permission_classes = [SiteIsAvailable, AllowAny]

class TermsOfServiceView(generics.GenericAPIView):
    serializer_class = TermsOfServiceSerializer
    permission_classes = [SiteIsAvailable, AllowAny]
    def get(self, request):
        tos = TermsOfService.objects.order_by('-date').first()
        if tos:
            return Response(self.get_serializer(tos).data)
        return Response({"detail": "Няма намерени Условия за ползване."}, status=status.HTTP_404_NOT_FOUND)
class PrivacyPolicyView(generics.GenericAPIView):
    serializer_class = PrivacyPolicySerializer
    permission_classes = [SiteIsAvailable, AllowAny]
    def get(self, request):
        pp = PrivacyPolicy.objects.order_by('-date').first()
        if pp:
            return Response(self.get_serializer(pp).data)
        return Response({"detail": "Няма намерена Политика за поверителност."}, status=status.HTTP_404_NOT_FOUND)
class ConsentRecordCreateView(generics.CreateAPIView):
    queryset = ConsentRecord.objects.all()
    serializer_class = ConsentRecordSerializer
    permission_classes = [AllowAny]
    throttle_scope = 'consent'

    def perform_create(self, serializer):
        user = self.request.user if self.request.user.is_authenticated else None
        ip_address = get_client_ip(self.request)
        serializer.save(user=user, ip_address=ip_address)
class SiteStatusView(generics.RetrieveAPIView):
    permission_classes = [AllowAny]
    serializer_class = SiteSettingsSerializer

    def get_object(self):
        obj, created = SiteSettings.objects.get_or_create(pk=1)
        return obj
class CheckUsernameView(APIView):
    permission_classes = [SiteIsAvailable, AllowAny]
    throttle_scope = 'username_check'

    def get(self, request, *args, **kwargs):
        username = request.query_params.get('username', None)
        if not username:
            return Response({'error': 'Username parameter is required.'}, status=status.HTTP_400_BAD_REQUEST)

        username = username.strip()
        try:
            User._meta.get_field('username').clean(username, None)
        except ValidationError as exc:
            return Response(
                {'is_available': False, 'errors': exc.messages},
                status=status.HTTP_400_BAD_REQUEST,
            )
        is_available = not User.objects.filter(username__iexact=username).exists()
        return Response({'is_available': is_available})
class ValidatePasswordView(APIView):
    permission_classes = [SiteIsAvailable, AllowAny]
    throttle_scope = 'password_validate'

    def post(self, request, *args, **kwargs):
        password = request.data.get('password', '')
        try:
            password_validation.validate_password(password)
            return Response({'is_valid': True})
        except ValidationError as e:
            return Response({'is_valid': False, 'errors': e.messages}, status=status.HTTP_400_BAD_REQUEST)


def hqs(request, video_name):
    try:
        # Променяме само името на твоя шаблон, ако е различно
        return render(request, 'hqs.html', {
            'video_url': f'/files/films/{video_name}/index.m3u8'
        })
    except Exception as e:
        # Ако има грешка, ще я видиш в браузъра вместо 500
        return HttpResponse(f"Грешка в Django: {str(e)}")
