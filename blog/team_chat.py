"""Business rules for the staff-only Django administration team chat."""

import copy
from datetime import timedelta
import re

from django.apps import apps
from django.contrib import admin
from django.contrib.auth.models import Group, User
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Count, Max, Q
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.text import Truncator

from .models import (
    TeamChatAttachment,
    TeamChatDiscussion,
    TeamChatDiscussionReadState,
    TeamChatMessage,
    TeamChatMessageRevision,
    TeamChatReaction,
    TeamChatReadState,
    TeamChatSharedObject,
)
from .team_chat_images import create_message_attachments


MESSAGE_MAX_LENGTH = 4000
MESSAGE_EDIT_MINUTES = 15
MAX_SHARED_OBJECTS = 10
MAX_PINNED_MESSAGES = 10
ALLOWED_REACTIONS = tuple(TeamChatReaction.Emoji.values)
MENTION_PATTERN = re.compile(r'(?<![\w@])@([\w.@+-]{1,150})', re.UNICODE)
MANAGE_DISCUSSION_ACCESS_PERMISSION = 'blog.manage_team_chat_discussion_access'

# Deliberately explicit. Authentication, permissions, access codes, sessions,
# IP/network records, push secrets and technical logs are never shareable.
SHAREABLE_ADMIN_MODELS = frozenset({
    'blog.badge',
    'blog.bellsongsuggestion',
    'blog.category',
    'blog.changelog',
    'blog.comments',
    'blog.contactsubmission',
    'blog.digitalreward',
    'blog.event',
    'blog.gamificationlevel',
    'blog.gamificationpointrule',
    'blog.memeofweek',
    'blog.mission',
    'blog.missionactivity',
    'blog.notification',
    'blog.pointtransaction',
    'blog.pollanswer',
    'blog.pollquestion',
    'blog.postdocument',
    'blog.postimage',
    'blog.posts',
    'blog.privacypolicy',
    'blog.scheduleentry',
    'blog.scheduleimport',
    'blog.sitesettings',
    'blog.termsofservice',
    'blog.userbadge',
    'blog.userdigitalreward',
    'blog.usermissionprogress',
    'blog.userprofile',
    'valentine.archivedvalentine',
    'valentine.profile',
    'valentine.settings',
    'valentine.valentinecampaign',
    'valentine.valenitines',
    'valentine.valentinemoderationevent',
})


class TeamChatValidationError(ValueError):
    pass


class TeamChatPermissionError(PermissionError):
    pass


def staff_display_name(user):
    return (user.get_full_name() or user.get_username()).strip()[:300]


def can_manage_pins(user):
    return bool(user.is_authenticated and user.is_active and user.is_superuser)


def is_shareable_model(model):
    return model._meta.label_lower in SHAREABLE_ADMIN_MODELS


def _model_admin(model):
    return admin.site._registry.get(model)


def _prepared_pk(model, object_id):
    try:
        return model._meta.pk.to_python(object_id)
    except (TypeError, ValueError, OverflowError):
        return None


def get_shareable_object(request, app_label, model_name, object_id):
    """Resolve an object through the exact queryset and permissions of its admin."""

    try:
        model = apps.get_model(app_label, model_name)
    except LookupError:
        return None
    if model is None or not is_shareable_model(model):
        return None
    model_admin = _model_admin(model)
    if model_admin is None or not model_admin.has_module_permission(request):
        return None
    prepared_pk = _prepared_pk(model, object_id)
    if prepared_pk is None:
        return None
    obj = model_admin.get_queryset(request).filter(pk=prepared_pk).first()
    if obj is None or not model_admin.has_view_permission(request, obj):
        return None
    return obj


def can_share_object(request, obj):
    if obj is None or not is_shareable_model(obj.__class__):
        return False
    return get_shareable_object(
        request,
        obj._meta.app_label,
        obj._meta.model_name,
        obj.pk,
    ) is not None


def safe_object_title(obj):
    """Return a useful title without falling back to potentially secret __str__."""

    for field_name in ('title', 'name', 'full_name'):
        value = getattr(obj, field_name, None)
        if isinstance(value, str) and value.strip():
            normalized = ' '.join(value.split())
            return Truncator(normalized).chars(120)
    label = str(obj._meta.verbose_name).strip().capitalize()
    return f'{label} №{obj.pk}'


def share_reference_for_object(obj):
    return (
        obj._meta.app_label,
        obj._meta.model_name,
        str(obj.pk),
    )


def shared_object_payload(shared, request):
    model = shared.content_type.model_class()
    if model is None:
        return {
            'status': 'missing',
            'available': False,
            'label': 'Записът вече не е наличен',
        }

    prepared_pk = _prepared_pk(model, shared.object_id)
    exists = bool(
        prepared_pk is not None
        and model._base_manager.filter(pk=prepared_pk).exists()
    )
    if not exists:
        return {
            'status': 'missing',
            'available': False,
            'label': 'Записът вече не е наличен',
        }

    obj = get_shareable_object(
        request,
        model._meta.app_label,
        model._meta.model_name,
        shared.object_id,
    )
    if obj is None:
        return {
            'status': 'restricted',
            'available': False,
            'label': 'Споделен е ограничен административен запис',
        }

    try:
        url = reverse(
            f'admin:{model._meta.app_label}_{model._meta.model_name}_change',
            args=(obj.pk,),
        )
    except NoReverseMatch:
        return {
            'status': 'restricted',
            'available': False,
            'label': 'Споделен е ограничен административен запис',
        }
    return {
        'status': 'available',
        'available': True,
        'label': str(model._meta.verbose_name).strip().capitalize(),
        'title': safe_object_title(obj),
        'url': url,
    }


def _discussion_source_object(request, discussion):
    if discussion.source_content_type_id is None:
        return None
    cache = getattr(request, '_team_chat_discussion_source_cache', None)
    if cache is None:
        cache = {}
        request._team_chat_discussion_source_cache = cache
    key = (discussion.source_content_type_id, discussion.source_object_id)
    if key in cache:
        return cache[key]
    model = discussion.source_content_type.model_class()
    obj = (
        get_shareable_object(
            request,
            model._meta.app_label,
            model._meta.model_name,
            discussion.source_object_id,
        )
        if model is not None else None
    )
    cache[key] = obj
    return obj


def _prefetched_relation_ids(instance, relation_name):
    cached = getattr(instance, '_prefetched_objects_cache', {}).get(relation_name)
    if cached is not None:
        return {item.pk for item in cached}
    return set(getattr(instance, relation_name).values_list('pk', flat=True))


def _discussion_acl_allows_user(discussion, user):
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return False
    if (
        user.is_superuser
        or discussion.created_by_id == user.pk
        or user.has_perm(MANAGE_DISCUSSION_ACCESS_PERMISSION)
    ):
        return True
    if discussion.visibility == TeamChatDiscussion.Visibility.TEAM:
        return True
    if discussion.visibility == TeamChatDiscussion.Visibility.GROUPS:
        user_group_ids = getattr(user, '_team_chat_group_ids_cache', None)
        if user_group_ids is None:
            user_group_ids = set(user.groups.values_list('pk', flat=True))
            user._team_chat_group_ids_cache = user_group_ids
        return bool(
            user_group_ids
            & _prefetched_relation_ids(discussion, 'allowed_groups')
        )
    if discussion.visibility == TeamChatDiscussion.Visibility.PRIVATE:
        return user.pk in _prefetched_relation_ids(discussion, 'allowed_users')
    return False


def discussion_is_accessible(request, discussion):
    user = request.user
    if not _discussion_acl_allows_user(discussion, user):
        return False
    if discussion.source_content_type_id is None:
        return True
    return _discussion_source_object(request, discussion) is not None


def discussion_is_accessible_to_user(request, discussion, user):
    """Evaluate access as another staff user without reusing source caches."""

    viewer_request = copy.copy(request)
    viewer_request.user = user
    if hasattr(viewer_request, '_team_chat_discussion_source_cache'):
        delattr(viewer_request, '_team_chat_discussion_source_cache')
    return discussion_is_accessible(viewer_request, discussion)


def get_accessible_discussion(request, discussion_id):
    discussion = TeamChatDiscussion.objects.select_related(
        'created_by',
        'resolved_by',
        'origin_message',
        'origin_message__author',
        'source_content_type',
    ).prefetch_related(
        'participants',
        'allowed_groups',
        'allowed_users',
    ).get(pk=discussion_id)
    if not discussion_is_accessible(request, discussion):
        raise TeamChatPermissionError('Нямате достъп до тази дискусия.')
    return discussion


def _discussion_source_payload(discussion, request):
    if discussion.source_content_type_id is None:
        return None
    model = discussion.source_content_type.model_class()
    obj = _discussion_source_object(request, discussion)
    if obj is None:
        return None
    try:
        url = reverse(
            f'admin:{model._meta.app_label}_{model._meta.model_name}_change',
            args=(obj.pk,),
        )
    except NoReverseMatch:
        url = ''
    return {
        'label': str(model._meta.verbose_name).strip().capitalize(),
        'title': safe_object_title(obj),
        'url': url,
    }


def can_manage_discussion(request, discussion):
    if (
        request.user.is_superuser
        or discussion.created_by_id == request.user.pk
        or request.user.has_perm(MANAGE_DISCUSSION_ACCESS_PERMISSION)
    ):
        return True
    if discussion.source_content_type_id is None:
        return False
    model = discussion.source_content_type.model_class()
    obj = _discussion_source_object(request, discussion) if model is not None else None
    model_admin = _model_admin(model) if model is not None else None
    return bool(
        obj is not None
        and model_admin is not None
        and model_admin.has_change_permission(request, obj)
    )


def discussion_unread_count(discussion, user):
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return 0
    state = TeamChatDiscussionReadState.objects.filter(
        discussion=discussion,
        user=user,
    ).first()
    last_read_id = state.last_read_message_id if state else 0
    return TeamChatMessage.objects.filter(
        discussion=discussion,
        pk__gt=last_read_id,
        deleted_at__isnull=True,
    ).exclude(author_id=user.pk).count()


def discussion_unread_counts(discussions, user):
    rows = list(discussions)
    if not rows or not user.is_authenticated or not user.is_active or not user.is_staff:
        return {row.pk: 0 for row in rows}
    ids = [row.pk for row in rows]
    cursors = dict(
        TeamChatDiscussionReadState.objects.filter(
            discussion_id__in=ids,
            user=user,
        ).values_list('discussion_id', 'last_read_message_id')
    )
    counts = {discussion_id: 0 for discussion_id in ids}
    unread_filter = Q()
    for discussion_id in ids:
        unread_filter |= Q(
            discussion_id=discussion_id,
            pk__gt=cursors.get(discussion_id, 0),
        )
    grouped = TeamChatMessage.objects.filter(
        unread_filter,
        deleted_at__isnull=True,
    ).exclude(author_id=user.pk).values('discussion_id').annotate(
        unread_count=Count('pk'),
    )
    for row in grouped:
        counts[row['discussion_id']] = row['unread_count']
    return counts


def serialize_discussion(discussion, request, *, unread_count=None):
    message_count = getattr(discussion, 'active_message_count', None)
    if message_count is None:
        message_count = discussion.messages.filter(deleted_at__isnull=True).count()
    participants = [
        {
            'id': user.pk,
            'username': user.get_username(),
            'name': staff_display_name(user),
        }
        for user in discussion.participants.all()
        if user.is_active and user.is_staff
    ]
    allowed_groups = [
        {'id': group.pk, 'name': group.name}
        for group in discussion.allowed_groups.all()
    ]
    allowed_users = [
        {
            'id': user.pk,
            'username': user.get_username(),
            'name': staff_display_name(user),
        }
        for user in discussion.allowed_users.all()
        if user.is_active and user.is_staff
    ]
    if discussion.visibility == TeamChatDiscussion.Visibility.GROUPS:
        access_summary = 'Роли: ' + ', '.join(
            group['name'] for group in allowed_groups
        )
    elif discussion.visibility == TeamChatDiscussion.Visibility.PRIVATE:
        access_summary = f'{len(allowed_users)} избрани служители'
    else:
        access_summary = 'Всички с достъп до админ панела'
    return {
        'id': discussion.pk,
        'title': discussion.title,
        'status': discussion.status,
        'status_label': discussion.get_status_display(),
        'created_by_name': discussion.created_by_name,
        'created_at': discussion.created_at.isoformat(),
        'updated_at': discussion.updated_at.isoformat(),
        'resolved_at': (
            discussion.resolved_at.isoformat() if discussion.resolved_at else None
        ),
        'resolved_by_name': discussion.resolved_by_name,
        'participants': participants,
        'visibility': discussion.visibility,
        'visibility_label': discussion.get_visibility_display(),
        'access_summary': access_summary,
        'allowed_groups': allowed_groups,
        'allowed_users': allowed_users,
        'origin_message_id': discussion.origin_message_id,
        'origin_message': (
            {
                'id': discussion.origin_message.pk,
                'author_name': discussion.origin_message.author_name,
                'preview': (
                    'Съобщението е изтрито'
                    if discussion.origin_message.deleted_at
                    else Truncator(
                        discussion.origin_message.text.strip()
                        or 'Съобщение с прикачено съдържание'
                    ).chars(180)
                ),
                'url': (
                    f"{reverse('team-chat-index')}?message="
                    f'{discussion.origin_message.pk}'
                ),
            }
            if discussion.origin_message_id else None
        ),
        'source': _discussion_source_payload(discussion, request),
        'unread_count': (
            discussion_unread_count(discussion, request.user)
            if unread_count is None else unread_count
        ),
        'message_count': message_count,
        'can_manage': can_manage_discussion(request, discussion),
        'url': reverse('team-chat-discussion-detail', args=(discussion.pk,)),
    }


def _reply_payload(message):
    parent = message.reply_to
    if parent is None:
        return None
    if parent.deleted_at is not None:
        return {
            'id': parent.pk,
            'author_name': parent.author_name,
            'deleted': True,
            'preview': 'Съобщението е изтрито',
        }
    preview = parent.text.strip() or 'Споделен административен запис'
    return {
        'id': parent.pk,
        'author_name': parent.author_name,
        'deleted': False,
        'preview': Truncator(preview).chars(160),
    }


def _reaction_payload(message, user):
    grouped = {
        emoji: {'emoji': emoji, 'count': 0, 'reacted_by_me': False}
        for emoji in ALLOWED_REACTIONS
    }
    for reaction in message.reactions.all():
        item = grouped.get(reaction.emoji)
        if item is None:
            continue
        item['count'] += 1
        if reaction.user_id == user.pk:
            item['reacted_by_me'] = True
    return list(grouped.values())


def _attachment_payload(message):
    return [
        {
            'id': str(attachment.pk),
            'url': reverse('team-chat-attachment', args=(attachment.pk,)),
            'thumbnail_url': reverse(
                'team-chat-attachment-thumbnail',
                args=(attachment.pk,),
            ),
            'original_name': attachment.original_name,
            'content_type': attachment.content_type,
            'byte_size': attachment.byte_size,
            'width': attachment.width,
            'height': attachment.height,
            'is_animated': attachment.is_animated,
            'alt_text': attachment.alt_text,
        }
        for attachment in message.attachments.all()
    ]


def serialize_message(message, request, *, now=None):
    now = now or timezone.now()
    deleted = message.deleted_at is not None
    is_own = message.author_id == request.user.pk
    can_edit = bool(
        is_own
        and not deleted
        and message.created_at >= now - timedelta(minutes=MESSAGE_EDIT_MINUTES)
    )
    can_delete = bool(not deleted and (is_own or request.user.is_superuser))
    try:
        origin_discussion = message.origin_discussion
    except TeamChatDiscussion.DoesNotExist:
        origin_discussion = None
    return {
        'id': message.pk,
        'author_name': message.author_name,
        'author_username': (
            message.author.get_username() if message.author_id else ''
        ),
        'text': '' if deleted else message.text,
        'created_at': message.created_at.isoformat(),
        'edited_at': message.edited_at.isoformat() if message.edited_at else None,
        'updated_at': message.updated_at.isoformat(),
        'deleted': deleted,
        'deleted_at': message.deleted_at.isoformat() if message.deleted_at else None,
        'is_own': is_own,
        'can_edit': can_edit,
        'can_delete': can_delete,
        'is_pinned': bool(message.is_pinned and not deleted),
        'pinned_at': (
            message.pinned_at.isoformat()
            if message.is_pinned and message.pinned_at and not deleted
            else None
        ),
        'pinned_by_name': (
            staff_display_name(message.pinned_by)
            if message.is_pinned and message.pinned_by_id and not deleted
            else ''
        ),
        'can_pin': bool(not deleted and can_manage_pins(request.user)),
        'reply_to': _reply_payload(message),
        'mentions': (
            [] if deleted else list(message.mentions.values_list('username', flat=True))
        ),
        'shared_objects': (
            []
            if deleted
            else [
                shared_object_payload(shared, request)
                for shared in message.shared_objects.all()
            ]
        ),
        'reactions': [] if deleted else _reaction_payload(message, request.user),
        'attachments': [] if deleted else _attachment_payload(message),
        'discussion_id': message.discussion_id,
        'origin_discussion': (
            {
                'id': origin_discussion.pk,
                'title': origin_discussion.title,
                'url': reverse(
                    'team-chat-discussion-detail',
                    args=(origin_discussion.pk,),
                ),
            }
            if origin_discussion is not None else None
        ),
    }


def _mention_candidates(text):
    candidates = []
    for match in MENTION_PATTERN.finditer(text):
        candidate = match.group(1).rstrip('.,!?;:')
        if candidate and candidate.casefold() not in {
            item.casefold() for item in candidates
        }:
            candidates.append(candidate)
        if len(candidates) >= 20:
            break
    return candidates


def synchronize_mentions(message, text, *, request=None):
    candidates = _mention_candidates(text)
    if not candidates:
        message.mentions.clear()
        return []
    query = User.objects.filter(is_active=True, is_staff=True)
    matched = []
    for candidate in candidates:
        user = query.filter(username__iexact=candidate).first()
        if user is not None:
            matched.append(user)
    if message.discussion_id:
        if request is None and message.discussion.source_content_type_id is not None:
            matched = []
        elif request is None:
            matched = [
                user for user in matched
                if _discussion_acl_allows_user(message.discussion, user)
            ]
        else:
            matched = [
                user for user in matched
                if discussion_is_accessible_to_user(
                    request,
                    message.discussion,
                    user,
                )
            ]
    message.mentions.set(matched)
    return matched


def clean_message_text(value, *, required=True):
    text = str(value or '').strip()
    if len(text) > MESSAGE_MAX_LENGTH:
        raise TeamChatValidationError(
            f'Съобщението може да бъде най-много {MESSAGE_MAX_LENGTH} знака.'
        )
    if required and not text:
        raise TeamChatValidationError('Напишете съобщение.')
    return text


@transaction.atomic
def create_message(
    *,
    author,
    text,
    reply_to_id=None,
    shared_objects=(),
    discussion=None,
    uploaded_files=(),
    request=None,
):
    shared_objects = list(shared_objects)
    uploaded_files = list(uploaded_files or ())
    if len(shared_objects) > MAX_SHARED_OBJECTS:
        raise TeamChatValidationError(
            f'Може да споделите най-много {MAX_SHARED_OBJECTS} записа наведнъж.'
        )
    if discussion is not None and discussion.status != TeamChatDiscussion.Status.OPEN:
        raise TeamChatValidationError('Приключената дискусия не приема нови съобщения.')
    cleaned_text = clean_message_text(
        text,
        required=not shared_objects and not uploaded_files,
    )
    if uploaded_files:
        # Lock before inserting the message so concurrent uploads from one
        # account cannot race the hourly image limit.
        User.objects.select_for_update().only('pk').get(pk=author.pk)
    reply_to = None
    if reply_to_id not in (None, ''):
        try:
            reply_to = TeamChatMessage.objects.get(pk=int(reply_to_id))
        except (TypeError, ValueError, TeamChatMessage.DoesNotExist) as exc:
            raise TeamChatValidationError('Съобщението, на което отговаряте, липсва.') from exc
        if reply_to.deleted_at is not None:
            raise TeamChatValidationError('Не може да отговорите на изтрито съобщение.')
        expected_discussion_id = discussion.pk if discussion is not None else None
        if reply_to.discussion_id != expected_discussion_id:
            raise TeamChatValidationError('Отговорът е от друго чат пространство.')

    message = TeamChatMessage.objects.create(
        author=author,
        author_name=staff_display_name(author),
        text=cleaned_text,
        reply_to=reply_to,
        discussion=discussion,
    )
    mentioned_users = synchronize_mentions(message, cleaned_text, request=request)
    if shared_objects:
        TeamChatSharedObject.objects.bulk_create([
            TeamChatSharedObject(
                message=message,
                content_type=ContentType.objects.get_for_model(
                    obj,
                    for_concrete_model=False,
                ),
                object_id=str(obj.pk),
            )
            for obj in shared_objects
        ])
    if uploaded_files:
        create_message_attachments(
            message=message,
            actor=author,
            uploaded_files=uploaded_files,
            actor_name=staff_display_name(author),
        )
    if discussion is not None:
        discussion.participants.add(author, *mentioned_users)
        TeamChatDiscussion.objects.filter(pk=discussion.pk).update(
            updated_at=timezone.now(),
        )
    return message


@transaction.atomic
def edit_message(*, message_id, actor, text, now=None, request=None):
    now = now or timezone.now()
    message = TeamChatMessage.objects.select_for_update().get(pk=message_id)
    if message.deleted_at is not None:
        raise TeamChatValidationError('Изтрито съобщение не може да се редактира.')
    if message.author_id != actor.pk:
        raise TeamChatPermissionError('Може да редактирате само своите съобщения.')
    if message.created_at < now - timedelta(minutes=MESSAGE_EDIT_MINUTES):
        raise TeamChatPermissionError('Срокът за редактиране от 15 минути е изтекъл.')
    cleaned_text = clean_message_text(
        text,
        required=(
            not message.shared_objects.exists()
            and not message.attachments.exists()
        ),
    )
    if cleaned_text == message.text:
        return message
    TeamChatMessageRevision.objects.create(
        message=message,
        event=TeamChatMessageRevision.Event.EDITED,
        previous_text=message.text,
        actor=actor,
        actor_name=staff_display_name(actor),
    )
    message.text = cleaned_text
    message.edited_at = now
    message.updated_at = now
    message.save(update_fields=('text', 'edited_at', 'updated_at'))
    mentioned_users = synchronize_mentions(message, cleaned_text, request=request)
    if message.discussion_id:
        message.discussion.participants.add(*mentioned_users)
    return message


@transaction.atomic
def delete_message(*, message_id, actor, now=None):
    now = now or timezone.now()
    message = TeamChatMessage.objects.select_for_update().get(pk=message_id)
    if message.deleted_at is not None:
        return message
    if message.author_id != actor.pk and not actor.is_superuser:
        raise TeamChatPermissionError('Може да изтривате само своите съобщения.')
    TeamChatMessageRevision.objects.create(
        message=message,
        event=TeamChatMessageRevision.Event.DELETED,
        previous_text=message.text,
        actor=actor,
        actor_name=staff_display_name(actor),
    )
    message.deleted_at = now
    message.deleted_by = actor
    message.is_pinned = False
    message.pinned_at = None
    message.pinned_by = None
    message.updated_at = now
    message.save(update_fields=(
        'deleted_at',
        'deleted_by',
        'is_pinned',
        'pinned_at',
        'pinned_by',
        'updated_at',
    ))
    message.mentions.clear()
    # Keep attachment metadata for the audit window and hourly upload limit,
    # but remove the private image and thumbnail files immediately after this
    # transaction commits successfully.
    private_files = tuple(
        (field_file.storage, field_file.name)
        for attachment in message.attachments.only('image', 'thumbnail')
        for field_file in (attachment.image, attachment.thumbnail)
        if field_file and field_file.name
    )
    if private_files:
        def delete_private_files():
            for storage, file_name in private_files:
                storage.delete(file_name)

        transaction.on_commit(delete_private_files, robust=True)
    return message


@transaction.atomic
def set_message_pinned(*, message_id, actor, pinned, now=None):
    if not can_manage_pins(actor):
        raise TeamChatPermissionError(
            'Само суперпотребител може да закача съобщения.'
        )
    now = now or timezone.now()
    message = TeamChatMessage.objects.select_for_update().get(pk=message_id)
    if message.deleted_at is not None:
        raise TeamChatValidationError('Изтрито съобщение не може да се закача.')
    pinned = bool(pinned)
    if message.is_pinned == pinned:
        return message
    scope = TeamChatMessage.objects.filter(
        discussion_id=message.discussion_id,
        is_pinned=True,
        deleted_at__isnull=True,
    )
    if pinned and scope.exclude(pk=message.pk).count() >= MAX_PINNED_MESSAGES:
        raise TeamChatValidationError(
            f'Може да има най-много {MAX_PINNED_MESSAGES} закачени съобщения.'
        )

    TeamChatMessageRevision.objects.create(
        message=message,
        event=(
            TeamChatMessageRevision.Event.PINNED
            if pinned
            else TeamChatMessageRevision.Event.UNPINNED
        ),
        previous_text=message.text,
        actor=actor,
        actor_name=staff_display_name(actor),
    )
    message.is_pinned = pinned
    message.pinned_at = now if pinned else None
    message.pinned_by = actor if pinned else None
    message.updated_at = now
    message.save(update_fields=(
        'is_pinned',
        'pinned_at',
        'pinned_by',
        'updated_at',
    ))
    return message


@transaction.atomic
def toggle_message_reaction(*, message_id, actor, emoji, now=None):
    if emoji not in ALLOWED_REACTIONS:
        raise TeamChatValidationError('Неподдържана реакция.')
    now = now or timezone.now()
    message = TeamChatMessage.objects.select_for_update().get(pk=message_id)
    if message.deleted_at is not None:
        raise TeamChatValidationError('Не може да реагирате на изтрито съобщение.')

    reaction = TeamChatReaction.objects.filter(
        message=message,
        user=actor,
        emoji=emoji,
    ).first()
    if reaction is None:
        TeamChatReaction.objects.create(
            message=message,
            user=actor,
            emoji=emoji,
        )
        active = True
    else:
        reaction.delete()
        active = False
    message.updated_at = now
    message.save(update_fields=('updated_at',))
    return message, active


def unread_message_count(user):
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        return 0
    state = TeamChatReadState.objects.filter(user=user).first()
    last_read_id = state.last_read_message_id if state else 0
    return TeamChatMessage.objects.filter(
        discussion__isnull=True,
        pk__gt=last_read_id,
        deleted_at__isnull=True,
    ).exclude(author_id=user.pk).count()


@transaction.atomic
def mark_messages_read(user, requested_message_id=None):
    general_messages = TeamChatMessage.objects.filter(discussion__isnull=True)
    maximum_id = general_messages.aggregate(value=Max('pk'))['value'] or 0
    if requested_message_id not in (None, ''):
        try:
            maximum_id = min(maximum_id, max(0, int(requested_message_id)))
        except (TypeError, ValueError):
            raise TeamChatValidationError('Невалиден идентификатор на съобщение.')
    state, _ = TeamChatReadState.objects.select_for_update().get_or_create(user=user)
    if maximum_id > state.last_read_message_id:
        state.last_read_message_id = maximum_id
        state.save(update_fields=('last_read_message_id', 'read_at'))
    return state


def clean_discussion_title(value):
    title = ' '.join(str(value or '').split())
    if not title:
        raise TeamChatValidationError('Въведете заглавие на дискусията.')
    if len(title) > 200:
        raise TeamChatValidationError('Заглавието може да бъде най-много 200 знака.')
    return title


def _clean_access_ids(values):
    if values in (None, ''):
        return []
    if isinstance(values, (str, int)):
        values = [values]
    if not isinstance(values, (list, tuple, set)):
        raise TeamChatValidationError('Невалиден избор за достъп до дискусията.')
    cleaned = []
    for value in values:
        if isinstance(value, bool):
            raise TeamChatValidationError('Невалиден избор за достъп до дискусията.')
        try:
            object_id = int(value)
        except (TypeError, ValueError) as exc:
            raise TeamChatValidationError(
                'Невалиден избор за достъп до дискусията.'
            ) from exc
        if object_id < 1:
            raise TeamChatValidationError('Невалиден избор за достъп до дискусията.')
        if object_id not in cleaned:
            cleaned.append(object_id)
    return cleaned


def clean_discussion_access(
    *,
    visibility=None,
    allowed_user_ids=(),
    allowed_group_ids=(),
    creator_id=None,
):
    visibility = visibility or TeamChatDiscussion.Visibility.TEAM
    if visibility not in TeamChatDiscussion.Visibility.values:
        raise TeamChatValidationError('Изберете валиден режим за достъп.')

    if visibility == TeamChatDiscussion.Visibility.GROUPS:
        group_ids = _clean_access_ids(allowed_group_ids)
        if not group_ids:
            raise TeamChatValidationError('Изберете поне една роля с достъп.')
        groups = list(Group.objects.filter(pk__in=group_ids).order_by('name'))
        if len(groups) != len(group_ids):
            raise TeamChatValidationError('Една или повече избрани роли не съществуват.')
        return visibility, [], groups

    if visibility == TeamChatDiscussion.Visibility.PRIVATE:
        user_ids = [
            user_id for user_id in _clean_access_ids(allowed_user_ids)
            if user_id != creator_id
        ]
        if not user_ids:
            raise TeamChatValidationError(
                'Изберете поне още един служител за частната дискусия.'
            )
        users = list(User.objects.filter(
            pk__in=user_ids,
            is_active=True,
            is_staff=True,
        ).order_by('username'))
        if len(users) != len(user_ids):
            raise TeamChatValidationError(
                'Може да избирате само активни потребители с достъп до админ панела.'
            )
        return visibility, users, []

    return TeamChatDiscussion.Visibility.TEAM, [], []


@transaction.atomic
def create_discussion(
    *,
    author,
    title,
    origin_message_id=None,
    source_object=None,
    visibility=None,
    allowed_user_ids=(),
    allowed_group_ids=(),
):
    title = clean_discussion_title(title)
    visibility, allowed_users, allowed_groups = clean_discussion_access(
        visibility=visibility,
        allowed_user_ids=allowed_user_ids,
        allowed_group_ids=allowed_group_ids,
        creator_id=author.pk,
    )
    if origin_message_id not in (None, '') and source_object is not None:
        raise TeamChatValidationError(
            'Дискусията може да е свързана със съобщение или запис, но не и с двете.'
        )
    origin_message = None
    if origin_message_id not in (None, ''):
        try:
            origin_message = TeamChatMessage.objects.select_for_update().get(
                pk=int(origin_message_id),
                discussion__isnull=True,
            )
        except (TypeError, ValueError, TeamChatMessage.DoesNotExist) as exc:
            raise TeamChatValidationError('Началното съобщение не е намерено.') from exc
        if origin_message.deleted_at is not None:
            raise TeamChatValidationError('Не може да започнете дискусия от изтрито съобщение.')
        if TeamChatDiscussion.objects.filter(origin_message=origin_message).exists():
            raise TeamChatValidationError('За това съобщение вече има дискусия.')

    source_content_type = None
    source_object_id = None
    if source_object is not None:
        source_content_type = ContentType.objects.get_for_model(
            source_object,
            for_concrete_model=False,
        )
        source_object_id = str(source_object.pk)

    discussion = TeamChatDiscussion.objects.create(
        title=title,
        created_by=author,
        created_by_name=staff_display_name(author),
        origin_message=origin_message,
        source_content_type=source_content_type,
        source_object_id=source_object_id,
        visibility=visibility,
    )
    discussion.participants.add(author)
    discussion.allowed_users.set(allowed_users)
    discussion.allowed_groups.set(allowed_groups)
    return discussion


@transaction.atomic
def update_discussion_access(
    *,
    discussion,
    request,
    visibility,
    allowed_user_ids=(),
    allowed_group_ids=(),
):
    if not can_manage_discussion(request, discussion):
        raise TeamChatPermissionError(
            'Нямате право да променяте достъпа до тази дискусия.'
        )
    locked = TeamChatDiscussion.objects.select_for_update().select_related(
        'created_by',
        'source_content_type',
    ).get(pk=discussion.pk)
    visibility, allowed_users, allowed_groups = clean_discussion_access(
        visibility=visibility,
        allowed_user_ids=allowed_user_ids,
        allowed_group_ids=allowed_group_ids,
        creator_id=locked.created_by_id,
    )
    locked.visibility = visibility
    locked.updated_at = timezone.now()
    locked.save(update_fields=('visibility', 'updated_at'))
    locked.allowed_users.set(allowed_users)
    locked.allowed_groups.set(allowed_groups)
    return locked


def accessible_discussions(request, *, status=None, limit=100):
    if (
        not request.user.is_authenticated
        or not request.user.is_active
        or not request.user.is_staff
    ):
        return []
    queryset = TeamChatDiscussion.objects.select_related(
        'created_by',
        'resolved_by',
        'origin_message',
        'source_content_type',
    ).prefetch_related(
        'participants',
        'allowed_groups',
        'allowed_users',
    ).annotate(
        active_message_count=Count(
            'messages',
            filter=Q(messages__deleted_at__isnull=True),
        ),
    )
    if status in TeamChatDiscussion.Status.values:
        queryset = queryset.filter(status=status)
    user = request.user
    if not (
        user.is_superuser
        or user.has_perm(MANAGE_DISCUSSION_ACCESS_PERMISSION)
    ):
        acl_filter = (
            Q(visibility=TeamChatDiscussion.Visibility.TEAM)
            | Q(created_by=user)
            | Q(
                visibility=TeamChatDiscussion.Visibility.PRIVATE,
                allowed_users=user,
            )
        )
        group_ids = list(user.groups.values_list('pk', flat=True))
        if group_ids:
            acl_filter |= Q(
                visibility=TeamChatDiscussion.Visibility.GROUPS,
                allowed_groups__in=group_ids,
            )
        queryset = queryset.filter(acl_filter).distinct()
    rows = []
    for discussion in queryset.order_by('-updated_at', '-pk')[:max(1, limit * 3)]:
        if discussion_is_accessible(request, discussion):
            rows.append(discussion)
        if len(rows) >= limit:
            break
    return rows


def discussion_total_unread_count(request):
    if (
        not request.user.is_authenticated
        or not request.user.is_active
        or not request.user.is_staff
    ):
        return 0
    rows = accessible_discussions(request, limit=200)
    return sum(discussion_unread_counts(rows, request.user).values())


@transaction.atomic
def mark_discussion_read(discussion, user, requested_message_id=None):
    messages = TeamChatMessage.objects.filter(discussion=discussion)
    maximum_id = messages.aggregate(value=Max('pk'))['value'] or 0
    if requested_message_id not in (None, ''):
        try:
            maximum_id = min(maximum_id, max(0, int(requested_message_id)))
        except (TypeError, ValueError):
            raise TeamChatValidationError('Невалиден идентификатор на съобщение.')
    state, _ = TeamChatDiscussionReadState.objects.select_for_update().get_or_create(
        discussion=discussion,
        user=user,
    )
    if maximum_id > state.last_read_message_id:
        state.last_read_message_id = maximum_id
        state.save(update_fields=('last_read_message_id', 'read_at'))
    return state


@transaction.atomic
def set_discussion_resolved(*, discussion, request, resolved):
    if not can_manage_discussion(request, discussion):
        raise TeamChatPermissionError('Нямате право да приключите тази дискусия.')
    discussion = TeamChatDiscussion.objects.select_for_update().get(pk=discussion.pk)
    resolved = bool(resolved)
    discussion.status = (
        TeamChatDiscussion.Status.RESOLVED
        if resolved
        else TeamChatDiscussion.Status.OPEN
    )
    discussion.resolved_at = timezone.now() if resolved else None
    discussion.resolved_by = request.user if resolved else None
    discussion.resolved_by_name = staff_display_name(request.user) if resolved else ''
    discussion.save(update_fields=(
        'status',
        'resolved_at',
        'resolved_by',
        'resolved_by_name',
        'updated_at',
    ))
    return discussion
