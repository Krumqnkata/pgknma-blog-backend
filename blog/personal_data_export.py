from django.utils import timezone

from .models import (
    BellSongSuggestion,
    BlogLoginSession,
    Comments,
    ConsentRecord,
    ContactSubmission,
    GoogleIdentity,
    MemeOfWeek,
    NotificationPreference,
    NotificationReadState,
    PageView,
    PollAnswer,
    Posts,
    PushSubscription,
    UserProfile,
)


def _iso(value):
    return value.isoformat() if value is not None else None


def _file_name(file_field):
    return file_field.name if file_field else None


def build_personal_data_export(user):
    """Return only data that is directly linked to the authenticated user."""
    generated_at = timezone.now()
    profile = UserProfile.objects.filter(user=user).first()
    avatar = {
        'source': (
            profile.avatar_source
            if profile
            else UserProfile.AvatarSource.INITIALS
        ),
        'preset': profile.avatar_preset if profile else '',
        'url': profile.avatar_url if profile else None,
        'file_name': _file_name(profile.avatar) if profile else None,
        'updated_at': _iso(profile.avatar_updated_at) if profile else None,
    }

    google_identity = (
        GoogleIdentity.objects.filter(user=user)
        .values(
            'issuer',
            'subject',
            'email_at_link_time',
            'created_at',
            'updated_at',
        )
        .first()
    )
    if google_identity:
        google_identity = {
            **google_identity,
            'created_at': _iso(google_identity['created_at']),
            'updated_at': _iso(google_identity['updated_at']),
        }

    posts = [
        {
            'id': post.pk,
            'title': post.title,
            'category': post.category.full_name,
            'hook': post.hook,
            'content': post.content,
            'banner_file': _file_name(post.banner),
            'created_at': _iso(post.created_at),
            'published_at': _iso(post.published_at),
            'published': post.published,
            'allowed': post.allowed,
        }
        for post in (
            Posts.objects.filter(author=user)
            .select_related('category')
            .order_by('created_at', 'pk')
        )
    ]

    comments = [
        {
            'id': comment.pk,
            'post_id': comment.post_id,
            'post_title': comment.post.title,
            'parent_comment_id': comment.parent_id,
            'content': comment.content,
            'created_at': _iso(comment.created_at),
        }
        for comment in (
            Comments.objects.filter(user=user)
            .select_related('post')
            .order_by('created_at', 'pk')
        )
    ]

    song_suggestions = [
        {
            'id': suggestion.pk,
            'title': suggestion.title,
            'link': suggestion.link,
            'slot': suggestion.slot,
            'slot_display': suggestion.get_slot_display(),
            'note': suggestion.note,
            'status': suggestion.status,
            'status_display': suggestion.get_status_display(),
            'submitted_at': _iso(suggestion.submitted_at),
            'votes': suggestion.votes,
        }
        for suggestion in BellSongSuggestion.objects.filter(user=user).order_by(
            'submitted_at',
            'pk',
        )
    ]

    memes = [
        {
            'id': meme.pk,
            'title': meme.title,
            'image_file': _file_name(meme.image),
            'status': meme.status,
            'status_display': meme.get_status_display(),
            'created_at': _iso(meme.created_at),
            'votes': meme.votes,
        }
        for meme in MemeOfWeek.objects.filter(user=user).order_by(
            'created_at',
            'pk',
        )
    ]

    poll_answers = [
        {
            'id': answer.pk,
            'question_id': answer.question_id,
            'question': answer.question.title,
            'selected_option_id': answer.selected_option_id,
            'selected_option_key': answer.selected_option.key,
            'selected_option': answer.selected_option.text,
            'is_correct': answer.selected_option.is_correct,
            'created_at': _iso(answer.created_at),
        }
        for answer in (
            PollAnswer.objects.filter(user=user)
            .select_related('question', 'selected_option')
            .order_by('created_at', 'pk')
        )
    ]

    contact_submissions = [
        {
            'id': submission.pk,
            'name': submission.name,
            'email': submission.email,
            'reason': submission.reason,
            'reason_display': submission.get_reason_display(),
            'message': submission.message,
            'submitted_at': _iso(submission.submitted_at),
        }
        for submission in ContactSubmission.objects.filter(user=user).order_by(
            'submitted_at',
            'pk',
        )
    ]

    song_votes = [
        {
            'suggestion_id': suggestion.pk,
            'title': suggestion.title,
        }
        for suggestion in BellSongSuggestion.objects.filter(
            voted_by=user
        ).order_by('pk')
    ]
    meme_votes = [
        {
            'meme_id': meme.pk,
            'title': meme.title,
        }
        for meme in MemeOfWeek.objects.filter(voted_by=user).order_by('pk')
    ]

    consent_records = [
        {
            'id': consent.pk,
            'ip_address': consent.ip_address,
            'network_operator': consent.network_operator,
            'network_asn': consent.network_asn,
            'consent_status': consent.consent_status,
            'consent_status_display': consent.get_consent_status_display(),
            'timestamp': _iso(consent.timestamp),
            'policy_version': consent.policy_version,
        }
        for consent in ConsentRecord.objects.filter(user=user).order_by(
            'timestamp',
            'pk',
        )
    ]

    login_sessions = [
        {
            'id': str(login_session.pk),
            'auth_method': login_session.auth_method,
            'auth_method_display': login_session.get_auth_method_display(),
            'ip_address': login_session.ip_address,
            'network_operator': login_session.network_operator,
            'network_asn': login_session.network_asn,
            'device_label': login_session.device_label,
            'device_identifier_hash': login_session.device_identifier_hash,
            'created_at': _iso(login_session.created_at),
            'last_seen_at': _iso(login_session.last_seen_at),
            'expires_at': _iso(login_session.expires_at),
            'revoked_at': _iso(login_session.revoked_at),
            'revoked_reason': login_session.revoked_reason,
            'revoked_reason_display': (
                login_session.get_revoked_reason_display()
                if login_session.revoked_reason
                else ''
            ),
        }
        for login_session in BlogLoginSession.objects.filter(user=user).order_by(
            'created_at',
            'pk',
        )
    ]

    notification_preferences = NotificationPreference.objects.filter(
        user=user,
    ).values(
        'general_enabled',
        'points_enabled',
        'levels_enabled',
        'badges_enabled',
        'missions_enabled',
        'rewards_enabled',
        'posts_enabled',
        'events_enabled',
        'polls_enabled',
        'comment_replies_enabled',
        'submissions_enabled',
        'mission_available_enabled',
        'security_enabled',
        'updated_at',
    ).first()
    if notification_preferences:
        notification_preferences['updated_at'] = _iso(
            notification_preferences['updated_at'],
        )

    notification_reads = [
        {
            'notification_id': state.notification_id,
            'read_at': _iso(state.read_at),
        }
        for state in NotificationReadState.objects.filter(user=user).order_by(
            'read_at',
            'pk',
        )
    ]
    push_subscriptions = [
        {
            'endpoint': subscription.endpoint,
            'browser': subscription.user_agent,
            'active': subscription.active,
            'topics': {
                'official': subscription.general_enabled,
                'posts': subscription.posts_enabled,
                'events': subscription.events_enabled,
                'polls': subscription.polls_enabled,
            },
            'failure_count': subscription.failure_count,
            'created_at': _iso(subscription.created_at),
            'updated_at': _iso(subscription.updated_at),
            'last_success_at': _iso(subscription.last_success_at),
        }
        for subscription in PushSubscription.objects.filter(user=user).order_by(
            'created_at',
            'pk',
        )
    ]
    page_views = [
        {
            'path': page_view.path,
            'post_id': page_view.post_id,
            'created_at': _iso(page_view.created_at),
        }
        for page_view in PageView.objects.filter(user=user).order_by(
            'created_at',
            'pk',
        )
    ]

    return {
        'format_version': '1.0',
        'scope': 'pgknma-blog',
        'generated_at': _iso(generated_at),
        'account': {
            'id': user.pk,
            'username': user.username,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'email': user.email,
            'date_joined': _iso(user.date_joined),
            'last_login': _iso(user.last_login),
            'is_active': user.is_active,
            'is_staff': user.is_staff,
            'is_superuser': user.is_superuser,
            'has_usable_password': user.has_usable_password(),
            'groups': list(
                user.groups.order_by('name').values_list('name', flat=True)
            ),
            'blog_profile_exists': profile is not None,
            'email_verification': {
                'verified': profile.email_verified if profile else True,
                'verified_at': (
                    _iso(profile.email_verified_at) if profile else None
                ),
            },
            'avatar': avatar,
            'google_identity': google_identity,
        },
        'created_content': {
            'posts': posts,
            'comments': comments,
            'song_suggestions': song_suggestions,
            'memes': memes,
            'contact_submissions': contact_submissions,
        },
        'activity': {
            'poll_answers': poll_answers,
            'song_votes': song_votes,
            'meme_votes': meme_votes,
            'page_views': page_views,
        },
        'privacy_and_security': {
            'consent_records': consent_records,
            'login_sessions': login_sessions,
            'notification_preferences': notification_preferences,
            'notification_reads': notification_reads,
            'push_subscriptions': push_subscriptions,
        },
        'excluded_for_security': [
            'password and password hash',
            'JWT access and refresh tokens',
            'session refresh identifiers',
            'browser Push encryption keys',
        ],
        'notes': [
            'The Valentine platform uses a separate profile system and is not linked automatically to this blog account.',
            'Uploaded binary files are not embedded; the export contains their stored file names where applicable.',
        ],
    }
