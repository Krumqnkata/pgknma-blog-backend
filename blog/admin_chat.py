"""Staff-only Django admin views and integration for the team chat."""

import json
from urllib.parse import urlencode

from django.contrib import admin, messages
from django.contrib.auth.models import Group
from django.contrib.contenttypes.models import ContentType
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import (
    FileResponse,
    Http404,
    HttpResponseBadRequest,
    HttpResponseNotAllowed,
    HttpResponseRedirect,
    JsonResponse,
)
from django.utils.cache import patch_vary_headers
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.text import Truncator
from django.views.decorators.http import require_GET, require_POST

from .automatic_notifications import create_notification_once
from .models import (
    Notification,
    TeamChatAttachment,
    TeamChatDiscussion,
    TeamChatMessage,
)
from .notification_categories import TEAM_CHAT_INVITE
from .team_chat_images import TeamChatImageValidationError
from .team_chat import (
    MAX_SHARED_OBJECTS,
    MAX_PINNED_MESSAGES,
    SHAREABLE_ADMIN_MODELS,
    TeamChatPermissionError,
    TeamChatValidationError,
    accessible_discussions,
    can_share_object,
    create_discussion,
    create_message,
    can_manage_discussion,
    delete_message,
    discussion_is_accessible_to_user,
    discussion_total_unread_count,
    discussion_unread_counts,
    edit_message,
    get_accessible_discussion,
    get_shareable_object,
    is_shareable_model,
    mark_discussion_read,
    mark_messages_read,
    safe_object_title,
    serialize_message,
    serialize_discussion,
    set_discussion_resolved,
    set_message_pinned,
    share_reference_for_object,
    staff_display_name,
    toggle_message_reaction,
    unread_message_count,
    update_discussion_access,
)


SHARE_TOKEN_SALT = 'blog.team-chat.share-v1'
SHARE_TOKEN_MAX_AGE_SECONDS = 60 * 60
MESSAGE_PAGE_SIZE = 50
POLL_RESULT_LIMIT = 200


def _require_staff(request):
    user = request.user
    if not user.is_authenticated or not user.is_active or not user.is_staff:
        raise PermissionDenied


def _json_payload(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TeamChatValidationError('Невалидни данни в заявката.') from exc


def _request_payload(request):
    if request.content_type and request.content_type.startswith('multipart/form-data'):
        return request.POST
    return _json_payload(request)


def _json_error(detail, status_code):
    return JsonResponse({'detail': str(detail)}, status=status_code)


def _serialize_discussion_rows(rows, request):
    rows = list(rows)
    unread = discussion_unread_counts(rows, request.user)
    return [
        serialize_discussion(
            row,
            request,
            unread_count=unread.get(row.pk, 0),
        )
        for row in rows
    ]


def _message_queryset():
    return TeamChatMessage.objects.select_related(
        'author',
        'discussion',
        'discussion__source_content_type',
        'origin_discussion',
        'reply_to',
        'reply_to__author',
        'pinned_by',
    ).prefetch_related(
        'mentions',
        'shared_objects__content_type',
        'reactions',
        'attachments',
    )


def _general_message_queryset():
    return _message_queryset().filter(discussion__isnull=True)


def _message_for_request(request, message_id):
    message = _message_queryset().get(pk=message_id)
    if message.discussion_id:
        try:
            get_accessible_discussion(request, message.discussion_id)
        except TeamChatPermissionError as exc:
            raise Http404 from exc
    return message


def _serialize_one(message_id, request):
    message = _message_for_request(request, message_id)
    return serialize_message(message, request)


def _serialize_pinned(request, *, discussion=None, now=None):
    rows = _message_queryset().filter(
        discussion=discussion,
        is_pinned=True,
        deleted_at__isnull=True,
    ).order_by('-pinned_at', '-pk')[:MAX_PINNED_MESSAGES]
    return [serialize_message(row, request, now=now) for row in rows]


def team_chat_page(request):
    _require_staff(request)
    request.current_app = admin.site.name
    context = {
        **admin.site.each_context(request),
        'title': 'Екипен чат',
        'subtitle': 'Вътрешно пространство за всички потребители с достъп до админ панела.',
        'staff_display_name': staff_display_name(request.user),
        'messages_url': reverse('team-chat-messages'),
        'read_url': reverse('team-chat-read'),
        'staff_url': reverse('team-chat-staff'),
        'edit_url_template': reverse('team-chat-edit', args=(0,)),
        'delete_url_template': reverse('team-chat-delete', args=(0,)),
        'pin_url_template': reverse('team-chat-pin', args=(0,)),
        'reaction_url_template': reverse('team-chat-reaction', args=(0,)),
        'discussions_url': reverse('team-chat-discussions'),
        'discussions_api_url': reverse('team-chat-discussions-api'),
    }
    return TemplateResponse(request, 'admin/team_chat/index.html', context)


def team_chat_messages(request):
    _require_staff(request)
    if request.method == 'POST':
        try:
            payload = _request_payload(request)
            message = create_message(
                author=request.user,
                text=payload.get('text', ''),
                reply_to_id=payload.get('reply_to_id'),
                uploaded_files=request.FILES.getlist('images'),
                request=request,
            )
        except (TeamChatValidationError, TeamChatImageValidationError) as exc:
            return _json_error(exc, 400)
        return JsonResponse(
            {'message': _serialize_one(message.pk, request)},
            status=201,
        )
    if request.method != 'GET':
        return HttpResponseNotAllowed(('GET', 'POST'))

    queryset = _general_message_queryset()
    before_id = request.GET.get('before_id')
    after_id = request.GET.get('after_id')
    updated_after = request.GET.get('updated_after')
    has_more = False

    if before_id:
        try:
            boundary = max(1, int(before_id))
        except (TypeError, ValueError):
            return _json_error('Невалиден before_id.', 400)
        rows = list(queryset.filter(pk__lt=boundary).order_by('-pk')[:MESSAGE_PAGE_SIZE])
        rows.reverse()
        if rows:
            has_more = TeamChatMessage.objects.filter(
                discussion__isnull=True,
                pk__lt=rows[0].pk,
            ).exists()
    elif after_id or updated_after:
        changes = Q()
        if after_id:
            try:
                changes |= Q(pk__gt=max(0, int(after_id)))
            except (TypeError, ValueError):
                return _json_error('Невалиден after_id.', 400)
        if updated_after:
            parsed = parse_datetime(updated_after)
            if parsed is None:
                return _json_error('Невалиден updated_after.', 400)
            if timezone.is_naive(parsed):
                parsed = timezone.make_aware(parsed)
            changes |= Q(updated_at__gt=parsed)
        rows = list(queryset.filter(changes).order_by('updated_at', 'pk')[:POLL_RESULT_LIMIT])
    else:
        rows = list(queryset.order_by('-pk')[:MESSAGE_PAGE_SIZE])
        rows.reverse()
        if rows:
            has_more = TeamChatMessage.objects.filter(
                discussion__isnull=True,
                pk__lt=rows[0].pk,
            ).exists()

    now = timezone.now()
    return JsonResponse({
        'messages': [serialize_message(row, request, now=now) for row in rows],
        'pinned_messages': _serialize_pinned(request, now=now),
        'has_more': has_more,
        'server_time': now.isoformat(),
        'unread_count': unread_message_count(request.user),
    })


@require_POST
def team_chat_edit(request, pk):
    _require_staff(request)
    try:
        _message_for_request(request, pk)
        payload = _json_payload(request)
        message = edit_message(
            message_id=pk,
            actor=request.user,
            text=payload.get('text', ''),
            request=request,
        )
    except TeamChatMessage.DoesNotExist:
        return _json_error('Съобщението не е намерено.', 404)
    except TeamChatValidationError as exc:
        return _json_error(exc, 400)
    except TeamChatPermissionError as exc:
        return _json_error(exc, 403)
    return JsonResponse({'message': _serialize_one(message.pk, request)})


@require_POST
def team_chat_delete(request, pk):
    _require_staff(request)
    try:
        _message_for_request(request, pk)
        message = delete_message(message_id=pk, actor=request.user)
    except TeamChatMessage.DoesNotExist:
        return _json_error('Съобщението не е намерено.', 404)
    except TeamChatPermissionError as exc:
        return _json_error(exc, 403)
    return JsonResponse({'message': _serialize_one(message.pk, request)})


@require_POST
def team_chat_pin(request, pk):
    _require_staff(request)
    try:
        existing = _message_for_request(request, pk)
        payload = _json_payload(request)
        pinned = payload.get('pinned')
        if not isinstance(pinned, bool):
            raise TeamChatValidationError('Полето pinned трябва да е булева стойност.')
        message = set_message_pinned(
            message_id=pk,
            actor=request.user,
            pinned=pinned,
        )
    except TeamChatMessage.DoesNotExist:
        return _json_error('Съобщението не е намерено.', 404)
    except TeamChatValidationError as exc:
        return _json_error(exc, 400)
    except TeamChatPermissionError as exc:
        return _json_error(exc, 403)
    now = timezone.now()
    return JsonResponse({
        'message': _serialize_one(message.pk, request),
        'pinned_messages': _serialize_pinned(
            request,
            discussion=existing.discussion,
            now=now,
        ),
    })


@require_POST
def team_chat_reaction(request, pk):
    _require_staff(request)
    try:
        _message_for_request(request, pk)
        payload = _json_payload(request)
        message, active = toggle_message_reaction(
            message_id=pk,
            actor=request.user,
            emoji=payload.get('emoji', ''),
        )
    except TeamChatMessage.DoesNotExist:
        return _json_error('Съобщението не е намерено.', 404)
    except TeamChatValidationError as exc:
        return _json_error(exc, 400)
    return JsonResponse({
        'message': _serialize_one(message.pk, request),
        'active': active,
    })


@require_POST
def team_chat_mark_read(request):
    _require_staff(request)
    try:
        payload = _json_payload(request)
        state = mark_messages_read(
            request.user,
            payload.get('last_message_id'),
        )
    except TeamChatValidationError as exc:
        return _json_error(exc, 400)
    return JsonResponse({
        'last_read_message_id': state.last_read_message_id,
        'unread_count': unread_message_count(request.user),
    })


@require_GET
def team_chat_unread(request):
    _require_staff(request)
    general_count = unread_message_count(request.user)
    discussion_count = discussion_total_unread_count(request)
    return JsonResponse({
        'unread_count': general_count,
        'discussion_unread_count': discussion_count,
        'total_unread_count': general_count + discussion_count,
    })


@require_GET
def team_chat_staff(request):
    _require_staff(request)
    query = request.GET.get('q', '').strip()[:50]
    users = request.user.__class__.objects.filter(is_active=True, is_staff=True)
    if query:
        users = users.filter(
            Q(username__icontains=query)
            | Q(first_name__icontains=query)
            | Q(last_name__icontains=query)
        )
    discussion = None
    discussion_id = request.GET.get('discussion', '').strip()
    if discussion_id:
        try:
            discussion = get_accessible_discussion(request, discussion_id)
        except (TeamChatDiscussion.DoesNotExist, TeamChatPermissionError, ValueError):
            raise Http404
    results = []
    for user in users.order_by('username'):
        if (
            discussion is not None
            and not discussion_is_accessible_to_user(request, discussion, user)
        ):
            continue
        results.append({
            'username': user.get_username(),
            'display_name': staff_display_name(user),
        })
        if len(results) >= 8:
            break
    return JsonResponse({
        'results': results,
    })


def _source_from_token(request, token):
    if not token:
        return None
    objects = _objects_from_share_token(request, token)
    if len(objects) != 1:
        raise TeamChatValidationError('Изберете точно един административен запис.')
    return objects[0]


def _origin_message_for_creation(value):
    if value in (None, ''):
        return None
    try:
        message_id = int(value)
    except (TypeError, ValueError) as exc:
        raise TeamChatValidationError('Невалидно начално съобщение.') from exc
    try:
        return TeamChatMessage.objects.select_related('author').get(
            pk=message_id,
            discussion__isnull=True,
            deleted_at__isnull=True,
            origin_discussion__isnull=True,
        )
    except TeamChatMessage.DoesNotExist as exc:
        raise TeamChatValidationError(
            'Началното съобщение липсва или вече има дискусия.'
        ) from exc


def _selected_id_strings(values):
    if values in (None, ''):
        return set()
    if isinstance(values, (str, int)):
        values = (values,)
    return {str(value) for value in values}


def _discussion_access_form_context(
    request,
    *,
    visibility=None,
    allowed_user_ids=(),
    allowed_group_ids=(),
    creator_id=None,
):
    visibility = visibility or TeamChatDiscussion.Visibility.TEAM
    selected_users = _selected_id_strings(allowed_user_ids)
    selected_groups = _selected_id_strings(allowed_group_ids)
    staff = request.user.__class__.objects.filter(
        is_active=True,
        is_staff=True,
    )
    if creator_id:
        staff = staff.exclude(pk=creator_id)
    return {
        'access_visibility': visibility,
        'access_modes': [
            {
                'value': TeamChatDiscussion.Visibility.TEAM,
                'label': 'Целият екип',
                'description': 'Всички активни хора с достъп до админ панела.',
                'icon': 'groups',
                'selected': visibility == TeamChatDiscussion.Visibility.TEAM,
            },
            {
                'value': TeamChatDiscussion.Visibility.GROUPS,
                'label': 'Избрани роли',
                'description': 'Само членовете на една или повече админ роли.',
                'icon': 'group_work',
                'selected': visibility == TeamChatDiscussion.Visibility.GROUPS,
            },
            {
                'value': TeamChatDiscussion.Visibility.PRIVATE,
                'label': 'Конкретни хора',
                'description': 'Частна тема за изрично избрани служители.',
                'icon': 'lock',
                'selected': visibility == TeamChatDiscussion.Visibility.PRIVATE,
            },
        ],
        'access_groups': [
            {
                'id': group.pk,
                'name': group.name,
                'selected': str(group.pk) in selected_groups,
            }
            for group in Group.objects.order_by('name')
        ],
        'access_users': [
            {
                'id': user.pk,
                'username': user.get_username(),
                'name': staff_display_name(user),
                'selected': str(user.pk) in selected_users,
            }
            for user in staff.order_by('first_name', 'last_name', 'username')
        ],
    }


def _staff_ids_with_discussion_access(request, discussion):
    return {
        user.pk
        for user in request.user.__class__.objects.filter(
            is_active=True,
            is_staff=True,
        )
        if discussion_is_accessible_to_user(request, discussion, user)
    }


def _explicit_staff_ids_with_discussion_access(request, discussion):
    if discussion.visibility == TeamChatDiscussion.Visibility.PRIVATE:
        candidates = discussion.allowed_users.filter(is_active=True, is_staff=True)
    elif discussion.visibility == TeamChatDiscussion.Visibility.GROUPS:
        candidates = request.user.__class__.objects.filter(
            is_active=True,
            is_staff=True,
            groups__in=discussion.allowed_groups.all(),
        ).distinct()
    else:
        return set()
    return {
        user.pk
        for user in candidates
        if discussion_is_accessible_to_user(request, discussion, user)
    }


def _sync_discussion_access_notifications(
    request,
    discussion,
    *,
    added_user_ids=(),
    removed_user_ids=(),
):
    content_type = ContentType.objects.get_for_model(
        discussion,
        for_concrete_model=False,
    )
    removed_user_ids = set(removed_user_ids)
    if removed_user_ids:
        Notification.objects.filter(
            user_id__in=removed_user_ids,
            category=TEAM_CHAT_INVITE,
            source_content_type=content_type,
            source_object_id=str(discussion.pk),
        ).delete()

    if discussion.visibility == TeamChatDiscussion.Visibility.TEAM:
        return
    recipients = request.user.__class__.objects.filter(
        pk__in=set(added_user_ids) - {request.user.pk},
        is_active=True,
        is_staff=True,
    )
    revision = int(timezone.now().timestamp() * 1_000_000)
    for recipient in recipients:
        create_notification_once(
            key=(
                f'team-chat-access:{discussion.pk}:'
                f'{recipient.pk}:{revision}'
            ),
            text=(
                f'{staff_display_name(request.user)} Ви даде достъп до '
                f'екипната дискусия „{discussion.title}“.'
            ),
            category=TEAM_CHAT_INVITE,
            action_url=reverse(
                'team-chat-discussion-detail',
                args=(discussion.pk,),
            ),
            user=recipient,
            source=discussion,
            source_policy=Notification.SourcePolicy.DEACTIVATE,
        )


def discussion_creation_url(reference):
    token = make_share_token((reference,))
    return f"{reverse('team-chat-discussions')}?{urlencode({'source': token})}"


def team_chat_discussions_page(request):
    _require_staff(request)
    source_token = (
        request.POST.get('source')
        if request.method == 'POST'
        else request.GET.get('source', '')
    )
    origin_message_id = (
        request.POST.get('origin_message_id')
        if request.method == 'POST'
        else request.GET.get('origin_message_id', '')
    )
    try:
        source_object = _source_from_token(request, source_token)
        origin_message = _origin_message_for_creation(origin_message_id)
        if source_object is not None and origin_message is not None:
            raise TeamChatValidationError(
                'Дискусията може да започне от запис или съобщение, но не и от двете.'
            )
    except TeamChatValidationError as exc:
        return HttpResponseBadRequest(str(exc))
    except TeamChatPermissionError as exc:
        raise PermissionDenied(str(exc)) from exc

    visibility = (
        request.POST.get('visibility')
        if request.method == 'POST'
        else TeamChatDiscussion.Visibility.TEAM
    )
    allowed_user_ids = (
        request.POST.getlist('allowed_users')
        if request.method == 'POST' else ()
    )
    allowed_group_ids = (
        request.POST.getlist('allowed_groups')
        if request.method == 'POST' else ()
    )

    if request.method == 'POST':
        try:
            discussion = create_discussion(
                author=request.user,
                title=request.POST.get('title', ''),
                origin_message_id=(origin_message.pk if origin_message else None),
                source_object=source_object,
                visibility=visibility,
                allowed_user_ids=allowed_user_ids,
                allowed_group_ids=allowed_group_ids,
            )
        except TeamChatValidationError as exc:
            messages.error(request, str(exc))
        else:
            discussion = get_accessible_discussion(request, discussion.pk)
            _sync_discussion_access_notifications(
                request,
                discussion,
                added_user_ids=_explicit_staff_ids_with_discussion_access(
                    request,
                    discussion,
                ),
            )
            messages.success(request, 'Дискусията беше създадена.')
            return HttpResponseRedirect(
                reverse('team-chat-discussion-detail', args=(discussion.pk,))
            )
    elif request.method != 'GET':
        return HttpResponseNotAllowed(('GET', 'POST'))

    rows = accessible_discussions(request, limit=100)
    if source_object is not None:
        source_type = source_object._meta.label_lower
        rows = [
            row for row in rows
            if row.source_content_type
            and row.source_content_type.model_class()
            and row.source_content_type.model_class()._meta.label_lower == source_type
            and row.source_object_id == str(source_object.pk)
        ]
    request.current_app = admin.site.name
    context = {
        **admin.site.each_context(request),
        'title': 'Екипни дискусии',
        'subtitle': 'Отделни разговори по задача, съобщение или административен запис.',
        'discussions': _serialize_discussion_rows(rows, request),
        'source_token': source_token,
        'origin_message_id': origin_message.pk if origin_message else '',
        'source': (
            {
                'label': str(source_object._meta.verbose_name).strip().capitalize(),
                'title': safe_object_title(source_object),
            }
            if source_object is not None else None
        ),
        'origin_message': (
            {
                'author_name': origin_message.author_name,
                'preview': Truncator(
                    origin_message.text.strip()
                    or 'Съобщение с прикачено съдържание'
                ).chars(220),
            }
            if origin_message else None
        ),
        'suggested_title': (
            request.POST.get('title', '')
            if request.method == 'POST'
            else (
                f'{str(source_object._meta.verbose_name).strip().capitalize()}: '
                f'{safe_object_title(source_object)}'
                if source_object is not None
                else (
                    Truncator(
                        origin_message.text.strip()
                        or f'Съобщение от {origin_message.author_name}'
                    ).chars(100)
                    if origin_message else ''
                )
            )
        ),
        **_discussion_access_form_context(
            request,
            visibility=visibility,
            allowed_user_ids=allowed_user_ids,
            allowed_group_ids=allowed_group_ids,
            creator_id=request.user.pk,
        ),
    }
    return TemplateResponse(request, 'admin/team_chat/discussions.html', context)


def team_chat_discussions_api(request):
    _require_staff(request)
    if request.method == 'POST':
        try:
            payload = _request_payload(request)
            source_object = _source_from_token(request, payload.get('source_token', ''))
            discussion = create_discussion(
                author=request.user,
                title=payload.get('title', ''),
                origin_message_id=payload.get('origin_message_id'),
                source_object=source_object,
                visibility=payload.get('visibility'),
                allowed_user_ids=payload.get('allowed_user_ids', ()),
                allowed_group_ids=payload.get('allowed_group_ids', ()),
            )
        except TeamChatValidationError as exc:
            return _json_error(exc, 400)
        except TeamChatPermissionError as exc:
            return _json_error(exc, 403)
        discussion = get_accessible_discussion(request, discussion.pk)
        _sync_discussion_access_notifications(
            request,
            discussion,
            added_user_ids=_explicit_staff_ids_with_discussion_access(
                request,
                discussion,
            ),
        )
        return JsonResponse(
            {'discussion': serialize_discussion(discussion, request)},
            status=201,
        )
    if request.method != 'GET':
        return HttpResponseNotAllowed(('GET', 'POST'))
    rows = accessible_discussions(
        request,
        status=request.GET.get('status'),
        limit=100,
    )
    serialized = _serialize_discussion_rows(rows, request)
    return JsonResponse({
        'discussions': serialized,
        'unread_count': sum(row['unread_count'] for row in serialized),
    })


def team_chat_discussion_page(request, pk):
    _require_staff(request)
    try:
        discussion = get_accessible_discussion(request, pk)
    except TeamChatDiscussion.DoesNotExist as exc:
        raise Http404 from exc
    except TeamChatPermissionError as exc:
        raise Http404 from exc
    request.current_app = admin.site.name
    context = {
        **admin.site.each_context(request),
        'title': discussion.title,
        'subtitle': 'Екипна дискусия',
        'discussion': serialize_discussion(discussion, request),
        'staff_display_name': staff_display_name(request.user),
        'messages_url': reverse('team-chat-discussion-messages', args=(pk,)),
        'read_url': reverse('team-chat-discussion-read', args=(pk,)),
        'resolve_url': reverse('team-chat-discussion-resolve', args=(pk,)),
        'access_url': reverse('team-chat-discussion-access', args=(pk,)),
        'staff_url': (
            f"{reverse('team-chat-staff')}?"
            f"{urlencode({'discussion': pk})}"
        ),
        'edit_url_template': reverse('team-chat-edit', args=(0,)),
        'delete_url_template': reverse('team-chat-delete', args=(0,)),
        'pin_url_template': reverse('team-chat-pin', args=(0,)),
        'reaction_url_template': reverse('team-chat-reaction', args=(0,)),
        'chat_url': reverse('team-chat-index'),
        'discussions_url': reverse('team-chat-discussions'),
        'discussions_api_url': reverse('team-chat-discussions-api'),
    }
    return TemplateResponse(request, 'admin/team_chat/discussion_detail.html', context)


def team_chat_discussion_access(request, pk):
    _require_staff(request)
    try:
        discussion = get_accessible_discussion(request, pk)
    except (TeamChatDiscussion.DoesNotExist, TeamChatPermissionError) as exc:
        raise Http404 from exc
    if not can_manage_discussion(request, discussion):
        raise PermissionDenied(
            'Нямате право да променяте достъпа до тази дискусия.'
        )

    visibility = discussion.visibility
    allowed_user_ids = list(
        discussion.allowed_users.values_list('pk', flat=True)
    )
    allowed_group_ids = list(
        discussion.allowed_groups.values_list('pk', flat=True)
    )
    if request.method == 'POST':
        visibility = request.POST.get('visibility', '')
        allowed_user_ids = request.POST.getlist('allowed_users')
        allowed_group_ids = request.POST.getlist('allowed_groups')
        before_user_ids = _staff_ids_with_discussion_access(request, discussion)
        try:
            update_discussion_access(
                discussion=discussion,
                request=request,
                visibility=visibility,
                allowed_user_ids=allowed_user_ids,
                allowed_group_ids=allowed_group_ids,
            )
        except TeamChatValidationError as exc:
            messages.error(request, str(exc))
        except TeamChatPermissionError as exc:
            raise PermissionDenied(str(exc)) from exc
        else:
            discussion = get_accessible_discussion(request, pk)
            after_user_ids = _staff_ids_with_discussion_access(request, discussion)
            _sync_discussion_access_notifications(
                request,
                discussion,
                added_user_ids=after_user_ids - before_user_ids,
                removed_user_ids=before_user_ids - after_user_ids,
            )
            messages.success(request, 'Достъпът до дискусията беше обновен.')
            return HttpResponseRedirect(
                reverse('team-chat-discussion-detail', args=(pk,))
            )
    elif request.method != 'GET':
        return HttpResponseNotAllowed(('GET', 'POST'))

    serialized = serialize_discussion(discussion, request)
    request.current_app = admin.site.name
    context = {
        **admin.site.each_context(request),
        'title': f'Достъп: {discussion.title}',
        'subtitle': 'Управление на достъпа до екипна дискусия',
        'discussion': serialized,
        **_discussion_access_form_context(
            request,
            visibility=visibility,
            allowed_user_ids=allowed_user_ids,
            allowed_group_ids=allowed_group_ids,
            creator_id=discussion.created_by_id,
        ),
    }
    return TemplateResponse(
        request,
        'admin/team_chat/discussion_access.html',
        context,
    )


def team_chat_discussion_messages(request, pk):
    _require_staff(request)
    try:
        discussion = get_accessible_discussion(request, pk)
    except TeamChatDiscussion.DoesNotExist as exc:
        raise Http404 from exc
    except TeamChatPermissionError as exc:
        raise Http404 from exc

    if request.method == 'POST':
        try:
            payload = _request_payload(request)
            message = create_message(
                author=request.user,
                text=payload.get('text', ''),
                reply_to_id=payload.get('reply_to_id'),
                discussion=discussion,
                uploaded_files=request.FILES.getlist('images'),
                request=request,
            )
        except (TeamChatValidationError, TeamChatImageValidationError) as exc:
            return _json_error(exc, 400)
        return JsonResponse(
            {'message': _serialize_one(message.pk, request)},
            status=201,
        )
    if request.method != 'GET':
        return HttpResponseNotAllowed(('GET', 'POST'))

    queryset = _message_queryset().filter(discussion=discussion)
    before_id = request.GET.get('before_id')
    after_id = request.GET.get('after_id')
    updated_after = request.GET.get('updated_after')
    has_more = False
    if before_id:
        try:
            boundary = max(1, int(before_id))
        except (TypeError, ValueError):
            return _json_error('Невалиден before_id.', 400)
        rows = list(queryset.filter(pk__lt=boundary).order_by('-pk')[:MESSAGE_PAGE_SIZE])
        rows.reverse()
        if rows:
            has_more = queryset.filter(pk__lt=rows[0].pk).exists()
    elif after_id or updated_after:
        changes = Q()
        if after_id:
            try:
                changes |= Q(pk__gt=max(0, int(after_id)))
            except (TypeError, ValueError):
                return _json_error('Невалиден after_id.', 400)
        if updated_after:
            parsed = parse_datetime(updated_after)
            if parsed is None:
                return _json_error('Невалиден updated_after.', 400)
            if timezone.is_naive(parsed):
                parsed = timezone.make_aware(parsed)
            changes |= Q(updated_at__gt=parsed)
        rows = list(queryset.filter(changes).order_by('updated_at', 'pk')[:POLL_RESULT_LIMIT])
    else:
        rows = list(queryset.order_by('-pk')[:MESSAGE_PAGE_SIZE])
        rows.reverse()
        if rows:
            has_more = queryset.filter(pk__lt=rows[0].pk).exists()

    now = timezone.now()
    serialized_discussion = serialize_discussion(discussion, request)
    return JsonResponse({
        'messages': [serialize_message(row, request, now=now) for row in rows],
        'pinned_messages': _serialize_pinned(
            request,
            discussion=discussion,
            now=now,
        ),
        'has_more': has_more,
        'server_time': now.isoformat(),
        'unread_count': serialized_discussion['unread_count'],
        'discussion': serialized_discussion,
    })


@require_POST
def team_chat_discussion_mark_read(request, pk):
    _require_staff(request)
    try:
        discussion = get_accessible_discussion(request, pk)
        payload = _json_payload(request)
        state = mark_discussion_read(
            discussion,
            request.user,
            payload.get('last_message_id'),
        )
    except TeamChatDiscussion.DoesNotExist as exc:
        raise Http404 from exc
    except TeamChatPermissionError as exc:
        raise Http404 from exc
    except TeamChatValidationError as exc:
        return _json_error(exc, 400)
    return JsonResponse({
        'last_read_message_id': state.last_read_message_id,
        'unread_count': 0,
    })


@require_POST
def team_chat_discussion_resolve(request, pk):
    _require_staff(request)
    try:
        discussion = get_accessible_discussion(request, pk)
        payload = _json_payload(request)
        resolved = payload.get('resolved')
        if not isinstance(resolved, bool):
            raise TeamChatValidationError('Полето resolved трябва да е булева стойност.')
        discussion = set_discussion_resolved(
            discussion=discussion,
            request=request,
            resolved=resolved,
        )
        discussion = get_accessible_discussion(request, discussion.pk)
    except TeamChatDiscussion.DoesNotExist as exc:
        raise Http404 from exc
    except TeamChatValidationError as exc:
        return _json_error(exc, 400)
    except TeamChatPermissionError as exc:
        return _json_error(exc, 403)
    return JsonResponse({'discussion': serialize_discussion(discussion, request)})


def _serve_team_chat_attachment(request, pk, *, thumbnail=False):
    _require_staff(request)
    try:
        attachment = TeamChatAttachment.objects.select_related(
            'message',
            'message__discussion',
            'message__discussion__source_content_type',
        ).get(pk=pk, message__deleted_at__isnull=True)
    except (TeamChatAttachment.DoesNotExist, ValueError) as exc:
        raise Http404 from exc
    if attachment.message.discussion_id:
        try:
            get_accessible_discussion(request, attachment.message.discussion_id)
        except (TeamChatDiscussion.DoesNotExist, TeamChatPermissionError) as exc:
            raise Http404 from exc
    field = attachment.thumbnail if thumbnail else attachment.image
    try:
        file_handle = field.open('rb')
    except (FileNotFoundError, OSError, ValueError) as exc:
        raise Http404 from exc
    response = FileResponse(
        file_handle,
        as_attachment=False,
        filename='thumbnail.webp' if thumbnail else 'image.webp',
        content_type='image/webp',
    )
    response['Cache-Control'] = 'private, no-store'
    response['Pragma'] = 'no-cache'
    response['X-Content-Type-Options'] = 'nosniff'
    patch_vary_headers(response, ('Cookie',))
    return response


@require_GET
def team_chat_attachment(request, pk):
    return _serve_team_chat_attachment(request, pk)


@require_GET
def team_chat_attachment_thumbnail(request, pk):
    return _serve_team_chat_attachment(request, pk, thumbnail=True)


def make_share_token(references):
    normalized = [tuple(str(part) for part in reference) for reference in references]
    if not normalized or len(normalized) > MAX_SHARED_OBJECTS:
        raise TeamChatValidationError(
            f'Изберете между 1 и {MAX_SHARED_OBJECTS} записа.'
        )
    return signing.dumps(normalized, salt=SHARE_TOKEN_SALT, compress=True)


def share_confirmation_url(references):
    token = make_share_token(references)
    return f"{reverse('team-chat-share')}?{urlencode({'items': token})}"


def share_to_discussion_confirmation_url(references):
    token = make_share_token(references)
    return (
        f"{reverse('team-chat-share-to-discussion')}?"
        f"{urlencode({'items': token})}"
    )


def _objects_from_share_token(request, token):
    try:
        references = signing.loads(
            token,
            salt=SHARE_TOKEN_SALT,
            max_age=SHARE_TOKEN_MAX_AGE_SECONDS,
        )
    except signing.BadSignature as exc:
        raise TeamChatValidationError(
            'Връзката за споделяне е невалидна или е изтекла.'
        ) from exc
    if (
        not isinstance(references, list)
        or not references
        or len(references) > MAX_SHARED_OBJECTS
    ):
        raise TeamChatValidationError('Невалиден списък със записи.')

    objects = []
    for reference in references:
        if not isinstance(reference, (list, tuple)) or len(reference) != 3:
            raise TeamChatValidationError('Невалиден запис за споделяне.')
        obj = get_shareable_object(request, *reference)
        if obj is None:
            raise TeamChatPermissionError(
                'Нямате достъп до един или повече от избраните записи.'
            )
        objects.append(obj)
    return objects


def team_chat_share(request):
    _require_staff(request)
    token = request.POST.get('items') if request.method == 'POST' else request.GET.get('items')
    if not token:
        return HttpResponseBadRequest('Липсва списък със записи за споделяне.')
    try:
        objects = _objects_from_share_token(request, token)
    except TeamChatValidationError as exc:
        return HttpResponseBadRequest(str(exc))
    except TeamChatPermissionError as exc:
        raise PermissionDenied(str(exc)) from exc

    if request.method == 'POST':
        try:
            message = create_message(
                author=request.user,
                text=request.POST.get('text', ''),
                shared_objects=objects,
                request=request,
            )
        except TeamChatValidationError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, 'Записът беше споделен в екипния чат.')
            return HttpResponseRedirect(
                f"{reverse('team-chat-index')}?{urlencode({'message': message.pk})}"
            )
    elif request.method != 'GET':
        return HttpResponseNotAllowed(('GET', 'POST'))

    request.current_app = admin.site.name
    context = {
        **admin.site.each_context(request),
        'title': 'Споделяне в екипния чат',
        'items_token': token,
        'objects': [
            {
                'type': str(obj._meta.verbose_name).strip().capitalize(),
                'title': safe_object_title(obj),
            }
            for obj in objects
        ],
        'max_shared_objects': MAX_SHARED_OBJECTS,
    }
    return TemplateResponse(request, 'admin/team_chat/share_confirm.html', context)


def team_chat_share_to_discussion(request):
    _require_staff(request)
    token = (
        request.POST.get('items')
        if request.method == 'POST'
        else request.GET.get('items')
    )
    if not token:
        return HttpResponseBadRequest('Липсва списък със записи за споделяне.')
    try:
        objects = _objects_from_share_token(request, token)
    except TeamChatValidationError as exc:
        return HttpResponseBadRequest(str(exc))
    except TeamChatPermissionError as exc:
        raise PermissionDenied(str(exc)) from exc

    form_error = ''
    selected_discussion_id = ''
    if request.method == 'POST':
        selected_discussion_id = request.POST.get('discussion', '').strip()
        if not selected_discussion_id:
            form_error = 'Изберете дискусия.'
        else:
            try:
                discussion = get_accessible_discussion(
                    request,
                    selected_discussion_id,
                )
            except (
                TeamChatDiscussion.DoesNotExist,
                TeamChatPermissionError,
                TypeError,
                ValueError,
            ) as exc:
                raise Http404 from exc
            if discussion.status != TeamChatDiscussion.Status.OPEN:
                form_error = 'Избраната дискусия вече е приключена.'
            else:
                try:
                    message = create_message(
                        author=request.user,
                        text=request.POST.get('text', ''),
                        shared_objects=objects,
                        discussion=discussion,
                        request=request,
                    )
                except TeamChatValidationError as exc:
                    form_error = str(exc)
                else:
                    messages.success(
                        request,
                        f'Записът беше споделен в дискусия „{discussion.title}“.',
                    )
                    return HttpResponseRedirect(
                        f"{reverse('team-chat-discussion-detail', args=(discussion.pk,))}"
                        f"?{urlencode({'message': message.pk})}"
                    )
    elif request.method != 'GET':
        return HttpResponseNotAllowed(('GET', 'POST'))

    rows = accessible_discussions(
        request,
        status=TeamChatDiscussion.Status.OPEN,
        limit=100,
    )
    serialized_rows = _serialize_discussion_rows(rows, request)
    for row in serialized_rows:
        row['selected'] = str(row['id']) == selected_discussion_id

    request.current_app = admin.site.name
    context = {
        **admin.site.each_context(request),
        'title': 'Споделяне в дискусия',
        'items_token': token,
        'objects': [
            {
                'type': str(obj._meta.verbose_name).strip().capitalize(),
                'title': safe_object_title(obj),
            }
            for obj in objects
        ],
        'discussions': serialized_rows,
        'form_error': form_error,
        'text_value': request.POST.get('text', '') if request.method == 'POST' else '',
        'max_shared_objects': MAX_SHARED_OBJECTS,
    }
    return TemplateResponse(
        request,
        'admin/team_chat/share_to_discussion_confirm.html',
        context,
    )


@admin.action(
    permissions=('view',),
    description='Сподели избраните записи в екипния чат',
)
def share_selected_in_team_chat(model_admin, request, queryset):
    if not is_shareable_model(model_admin.model):
        raise PermissionDenied
    count = queryset.count()
    if count < 1 or count > MAX_SHARED_OBJECTS:
        messages.error(
            request,
            f'Изберете между 1 и {MAX_SHARED_OBJECTS} записа за споделяне.',
        )
        return None
    objects = list(queryset)
    if any(not can_share_object(request, obj) for obj in objects):
        raise PermissionDenied
    return HttpResponseRedirect(
        share_confirmation_url(share_reference_for_object(obj) for obj in objects)
    )


@admin.action(
    permissions=('view',),
    description='Сподели избраните записи в дискусия',
)
def share_selected_in_team_chat_discussion(model_admin, request, queryset):
    if not is_shareable_model(model_admin.model):
        raise PermissionDenied
    count = queryset.count()
    if count < 1 or count > MAX_SHARED_OBJECTS:
        messages.error(
            request,
            f'Изберете между 1 и {MAX_SHARED_OBJECTS} записа за споделяне.',
        )
        return None
    objects = list(queryset)
    if any(not can_share_object(request, obj) for obj in objects):
        raise PermissionDenied
    return HttpResponseRedirect(
        share_to_discussion_confirmation_url(
            share_reference_for_object(obj) for obj in objects
        )
    )


def install_team_chat_admin_sharing():
    """Attach the share hook only to the explicit, safe ModelAdmin allowlist."""

    for model, model_admin in admin.site._registry.items():
        if model._meta.label_lower not in SHAREABLE_ADMIN_MODELS:
            continue
        if getattr(model_admin, '_team_chat_sharing_installed', False):
            continue

        original_get_actions = model_admin.get_actions

        def get_actions(request, *, _original=original_get_actions, _admin=model_admin):
            actions = dict(_original(request))
            user = request.user
            if (
                user.is_active
                and user.is_staff
                and _admin.has_module_permission(request)
                and _admin.has_view_permission(request)
            ):
                actions['share_selected_in_team_chat'] = (
                    share_selected_in_team_chat,
                    'share_selected_in_team_chat',
                    share_selected_in_team_chat.short_description,
                )
                actions['share_selected_in_team_chat_discussion'] = (
                    share_selected_in_team_chat_discussion,
                    'share_selected_in_team_chat_discussion',
                    share_selected_in_team_chat_discussion.short_description,
                )
            return actions

        model_admin.get_actions = get_actions
        model_admin.change_form_outer_before_template = (
            'admin/team_chat/share_button.html'
        )
        model_admin._team_chat_sharing_installed = True


def team_chat_unread_badge(request):
    count = unread_message_count(request.user) + discussion_total_unread_count(request)
    if count <= 0:
        return None
    return '99+' if count > 99 else str(count)
