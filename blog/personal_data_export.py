from django.utils import timezone

from .models import (
    BellSongSuggestion,
    BlogLoginSession,
    Comments,
    ConsentRecord,
    ContactSubmission,
    GoogleIdentity,
    MemeOfWeek,
    PollAnswer,
    Posts,
    UserProfile,
)


def _iso(value):
    return value.isoformat() if value is not None else None


def _file_name(file_field):
    return file_field.name if file_field else None


def build_personal_data_export(user):
    """Return only data that is directly linked to the authenticated user."""
    generated_at = timezone.now()

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
            'blog_profile_exists': UserProfile.objects.filter(user=user).exists(),
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
        },
        'privacy_and_security': {
            'consent_records': consent_records,
            'login_sessions': login_sessions,
        },
        'excluded_for_security': [
            'password and password hash',
            'JWT access and refresh tokens',
            'session refresh identifiers',
        ],
        'notes': [
            'The Valentine platform uses a separate profile system and is not linked automatically to this blog account.',
            'Uploaded binary files are not embedded; the export contains their stored file names where applicable.',
        ],
    }
