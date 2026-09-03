import json
from datetime import timedelta

from django.contrib import admin
from django.contrib.auth.models import User
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from .admin_chat import make_share_token
from .admin_navigation import build_admin_navigation
from .models import (
    Category,
    Posts,
    PushSubscription,
    TeamChatDiscussion,
    TeamChatMessage,
    TeamChatMessageRevision,
    TeamChatReaction,
    TeamChatReadState,
)
from .team_chat import (
    MAX_SHARED_OBJECTS,
    TeamChatValidationError,
    create_discussion,
    create_message,
    serialize_message,
)


def _json_post(client, url, payload):
    return client.post(
        url,
        data=json.dumps(payload),
        content_type='application/json',
    )


class TeamChatAccessTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username='chat-staff',
            password='test-password',
            is_staff=True,
        )
        self.non_staff = User.objects.create_user(
            username='chat-public-user',
            password='test-password',
        )

    def test_every_active_staff_user_can_open_chat_without_model_permissions(self):
        self.client.force_login(self.staff)

        response = self.client.get(reverse('team-chat-index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Екипен чат')
        self.assertContains(response, 'team-chat-app')
        self.assertContains(response, 'admin/css/team_chat_floating.css')
        self.assertContains(response, 'admin/js/team_chat_badge.js')

        request = RequestFactory().get('/admin/')
        request.user = self.staff
        visible_titles = {
            item['title']
            for section in build_admin_navigation(request)
            for item in section['items']
        }
        self.assertIn('Екипен чат', visible_titles)

    def test_non_staff_user_cannot_open_or_use_chat(self):
        self.client.force_login(self.non_staff)

        page_response = self.client.get(reverse('team-chat-index'))
        send_response = _json_post(
            self.client,
            reverse('team-chat-messages'),
            {'text': 'Това не трябва да бъде записано.'},
        )

        self.assertEqual(page_response.status_code, 302)
        self.assertEqual(send_response.status_code, 302)
        self.assertFalse(TeamChatMessage.objects.exists())

    def test_chat_post_requires_csrf_token(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.staff)

        response = _json_post(
            csrf_client,
            reverse('team-chat-messages'),
            {'text': 'Без CSRF'},
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(TeamChatMessage.objects.exists())


class TeamChatMessagingTests(TestCase):
    def setUp(self):
        self.author = User.objects.create_user(
            username='chat-author',
            first_name='Анна',
            password='test-password',
            is_staff=True,
        )
        self.colleague = User.objects.create_user(
            username='chat-colleague',
            first_name='Борис',
            password='test-password',
            is_staff=True,
        )
        self.superuser = User.objects.create_superuser(
            username='chat-root',
            email='chat-root@example.com',
            password='test-password',
        )

    def test_send_reply_mentions_and_per_user_unread_state(self):
        self.client.force_login(self.author)
        sent = _json_post(
            self.client,
            reverse('team-chat-messages'),
            {'text': 'Моля, погледни това, @chat-colleague.'},
        )
        self.assertEqual(sent.status_code, 201)
        message = TeamChatMessage.objects.get()
        self.assertEqual(
            list(message.mentions.values_list('username', flat=True)),
            ['chat-colleague'],
        )

        self.client.force_login(self.colleague)
        unread = self.client.get(reverse('team-chat-unread')).json()
        self.assertEqual(unread['unread_count'], 1)

        reply = _json_post(
            self.client,
            reverse('team-chat-messages'),
            {'text': 'Видях го.', 'reply_to_id': message.pk},
        )
        self.assertEqual(reply.status_code, 201)
        self.assertEqual(reply.json()['message']['reply_to']['id'], message.pk)

        marked = _json_post(
            self.client,
            reverse('team-chat-read'),
            {'last_message_id': message.pk},
        )
        self.assertEqual(marked.status_code, 200)
        self.assertEqual(marked.json()['unread_count'], 0)
        state = TeamChatReadState.objects.get(user=self.colleague)
        self.assertEqual(state.last_read_message_id, message.pk)

        # Read progress is monotonic and cannot be moved backwards by an old tab.
        newest_id = TeamChatMessage.objects.latest('pk').pk
        _json_post(
            self.client,
            reverse('team-chat-read'),
            {'last_message_id': newest_id},
        )
        _json_post(
            self.client,
            reverse('team-chat-read'),
            {'last_message_id': message.pk},
        )
        state.refresh_from_db()
        self.assertEqual(state.last_read_message_id, newest_id)

    def test_author_can_edit_for_15_minutes_and_revision_is_recorded(self):
        message = create_message(author=self.author, text='Първи текст')
        self.client.force_login(self.author)

        response = _json_post(
            self.client,
            reverse('team-chat-edit', args=(message.pk,)),
            {'text': 'Поправен текст'},
        )

        self.assertEqual(response.status_code, 200)
        message.refresh_from_db()
        self.assertEqual(message.text, 'Поправен текст')
        revision = TeamChatMessageRevision.objects.get(message=message)
        self.assertEqual(revision.event, TeamChatMessageRevision.Event.EDITED)
        self.assertEqual(revision.previous_text, 'Първи текст')
        self.assertEqual(revision.actor, self.author)

        TeamChatMessage.objects.filter(pk=message.pk).update(
            created_at=timezone.now() - timedelta(minutes=16),
        )
        expired = _json_post(
            self.client,
            reverse('team-chat-edit', args=(message.pk,)),
            {'text': 'Твърде късна промяна'},
        )
        self.assertEqual(expired.status_code, 403)

    def test_only_author_or_superuser_can_soft_delete(self):
        message = create_message(author=self.author, text='За изтриване')
        self.client.force_login(self.colleague)
        forbidden = _json_post(
            self.client,
            reverse('team-chat-delete', args=(message.pk,)),
            {},
        )
        self.assertEqual(forbidden.status_code, 403)

        self.client.force_login(self.superuser)
        deleted = _json_post(
            self.client,
            reverse('team-chat-delete', args=(message.pk,)),
            {},
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertTrue(deleted.json()['message']['deleted'])
        self.assertEqual(deleted.json()['message']['text'], '')

        message.refresh_from_db()
        self.assertIsNotNone(message.deleted_at)
        self.assertEqual(message.deleted_by, self.superuser)
        revision = TeamChatMessageRevision.objects.get(message=message)
        self.assertEqual(revision.event, TeamChatMessageRevision.Event.DELETED)
        self.assertEqual(revision.previous_text, 'За изтриване')

    def test_polling_by_updated_timestamp_does_not_return_unchanged_history(self):
        old = create_message(author=self.author, text='Старо')
        recent = create_message(author=self.colleague, text='Ново')
        old_time = timezone.now() - timedelta(days=1)
        TeamChatMessage.objects.filter(pk=old.pk).update(
            created_at=old_time,
            updated_at=old_time,
        )
        threshold = timezone.now() - timedelta(hours=1)
        self.client.force_login(self.author)

        response = self.client.get(
            reverse('team-chat-messages'),
            {'updated_after': threshold.isoformat()},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [row['id'] for row in response.json()['messages']],
            [recent.pk],
        )

    def test_only_superuser_can_pin_and_unpin_messages(self):
        message = create_message(author=self.author, text='Важна инструкция')
        self.client.force_login(self.colleague)

        forbidden = _json_post(
            self.client,
            reverse('team-chat-pin', args=(message.pk,)),
            {'pinned': True},
        )
        self.assertEqual(forbidden.status_code, 403)

        self.client.force_login(self.superuser)
        pinned = _json_post(
            self.client,
            reverse('team-chat-pin', args=(message.pk,)),
            {'pinned': True},
        )
        self.assertEqual(pinned.status_code, 200)
        self.assertTrue(pinned.json()['message']['is_pinned'])
        self.assertEqual(
            [row['id'] for row in pinned.json()['pinned_messages']],
            [message.pk],
        )
        message.refresh_from_db()
        self.assertTrue(message.is_pinned)
        self.assertEqual(message.pinned_by, self.superuser)
        self.assertEqual(
            TeamChatMessageRevision.objects.get(message=message).event,
            TeamChatMessageRevision.Event.PINNED,
        )

        unpinned = _json_post(
            self.client,
            reverse('team-chat-pin', args=(message.pk,)),
            {'pinned': False},
        )
        self.assertEqual(unpinned.status_code, 200)
        self.assertFalse(unpinned.json()['message']['is_pinned'])
        self.assertEqual(unpinned.json()['pinned_messages'], [])
        self.assertTrue(
            TeamChatMessageRevision.objects.filter(
                message=message,
                event=TeamChatMessageRevision.Event.UNPINNED,
            ).exists()
        )

    def test_reactions_toggle_without_creating_unread_messages(self):
        message = create_message(author=self.author, text='Реакции')
        TeamChatReadState.objects.create(
            user=self.colleague,
            last_read_message_id=message.pk,
        )
        self.client.force_login(self.colleague)

        added = _json_post(
            self.client,
            reverse('team-chat-reaction', args=(message.pk,)),
            {'emoji': '✅'},
        )
        self.assertEqual(added.status_code, 200)
        self.assertTrue(added.json()['active'])
        done = next(
            item
            for item in added.json()['message']['reactions']
            if item['emoji'] == '✅'
        )
        self.assertEqual(done['count'], 1)
        self.assertTrue(done['reacted_by_me'])
        self.assertEqual(
            self.client.get(reverse('team-chat-unread')).json()['unread_count'],
            0,
        )

        removed = _json_post(
            self.client,
            reverse('team-chat-reaction', args=(message.pk,)),
            {'emoji': '✅'},
        )
        self.assertFalse(removed.json()['active'])
        self.assertFalse(TeamChatReaction.objects.exists())

        invalid = _json_post(
            self.client,
            reverse('team-chat-reaction', args=(message.pk,)),
            {'emoji': '🔥'},
        )
        self.assertEqual(invalid.status_code, 400)

    def test_staff_search_excludes_public_and_inactive_accounts(self):
        User.objects.create_user(username='chat-public', is_staff=False)
        User.objects.create_user(username='chat-inactive', is_staff=True, is_active=False)
        self.client.force_login(self.author)

        response = self.client.get(reverse('team-chat-staff'), {'q': 'chat-'})

        usernames = {row['username'] for row in response.json()['results']}
        self.assertIn('chat-author', usernames)
        self.assertIn('chat-colleague', usernames)
        self.assertNotIn('chat-public', usernames)
        self.assertNotIn('chat-inactive', usernames)


class TeamChatSharingTests(TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username='share-root',
            email='share-root@example.com',
            password='test-password',
        )
        self.restricted_staff = User.objects.create_user(
            username='share-restricted',
            password='test-password',
            is_staff=True,
        )
        self.category = Category.objects.create(
            full_name='Екипен чат',
            short_name='team-chat',
        )
        self.post = Posts.objects.create(
            title='Защитено заглавие на публикация',
            hook='Кратко описание',
            content='Съдържание',
            author=self.superuser,
        )
        self.post.categories.add(self.category)

    def _request_for(self, user):
        request = RequestFactory().get('/admin/team-chat/')
        request.user = user
        return request

    def _message_for_sharing(self):
        return create_message(
            author=self.superuser,
            text='Проверете публикацията.',
            shared_objects=(self.post,),
        )

    def test_share_confirmation_creates_structured_reference(self):
        token = make_share_token((('blog', 'posts', self.post.pk),))
        self.client.force_login(self.superuser)

        preview = self.client.get(reverse('team-chat-share'), {'items': token})
        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, self.post.title)

        response = self.client.post(
            reverse('team-chat-share'),
            {'items': token, 'text': 'Моля, редактирайте това.'},
        )
        self.assertEqual(response.status_code, 302)
        message = TeamChatMessage.objects.get()
        shared = message.shared_objects.get()
        self.assertEqual(shared.content_type.model, 'posts')
        self.assertEqual(shared.object_id, str(self.post.pk))

    def test_share_to_discussion_lists_open_topics_and_creates_message(self):
        open_discussion = create_discussion(
            author=self.superuser,
            title='Подготовка на публикацията',
        )
        resolved_discussion = create_discussion(
            author=self.superuser,
            title='Вече приключена тема',
        )
        resolved_discussion.status = TeamChatDiscussion.Status.RESOLVED
        resolved_discussion.save(update_fields=('status',))
        token = make_share_token((('blog', 'posts', self.post.pk),))
        self.client.force_login(self.superuser)

        preview = self.client.get(
            reverse('team-chat-share-to-discussion'),
            {'items': token},
        )
        self.assertEqual(preview.status_code, 200)
        self.assertContains(preview, open_discussion.title)
        self.assertNotContains(preview, resolved_discussion.title)

        response = self.client.post(
            reverse('team-chat-share-to-discussion'),
            {
                'items': token,
                'discussion': open_discussion.pk,
                'text': 'Добавям публикацията към темата.',
            },
        )
        message = TeamChatMessage.objects.get(discussion=open_discussion)
        self.assertRedirects(
            response,
            reverse('team-chat-discussion-detail', args=(open_discussion.pk,))
            + f'?message={message.pk}',
            fetch_redirect_response=False,
        )
        self.assertEqual(message.text, 'Добавям публикацията към темата.')
        shared = message.shared_objects.get()
        self.assertEqual(shared.content_type.model, 'posts')
        self.assertEqual(shared.object_id, str(self.post.pk))

    def test_share_to_discussion_rejects_resolved_or_inaccessible_targets(self):
        discussion = create_discussion(
            author=self.superuser,
            title='Приключена тема',
        )
        discussion.status = TeamChatDiscussion.Status.RESOLVED
        discussion.save(update_fields=('status',))
        token = make_share_token((('blog', 'posts', self.post.pk),))
        self.client.force_login(self.superuser)

        resolved = self.client.post(
            reverse('team-chat-share-to-discussion'),
            {'items': token, 'discussion': discussion.pk},
        )
        self.assertEqual(resolved.status_code, 200)
        self.assertContains(resolved, 'Избраната дискусия вече е приключена.')
        self.assertFalse(TeamChatMessage.objects.exists())

        self.client.force_login(self.restricted_staff)
        inaccessible = self.client.get(
            reverse('team-chat-share-to-discussion'),
            {'items': token},
        )
        self.assertEqual(inaccessible.status_code, 403)

    def test_shared_card_is_resolved_separately_for_each_viewer(self):
        message = self._message_for_sharing()
        message = TeamChatMessage.objects.prefetch_related(
            'mentions',
            'shared_objects__content_type',
        ).select_related('author', 'reply_to').get(pk=message.pk)

        allowed = serialize_message(message, self._request_for(self.superuser))
        allowed_card = allowed['shared_objects'][0]
        self.assertTrue(allowed_card['available'])
        self.assertEqual(allowed_card['title'], self.post.title)
        self.assertIn('/admin/blog/posts/', allowed_card['url'])

        restricted = serialize_message(
            message,
            self._request_for(self.restricted_staff),
        )
        restricted_card = restricted['shared_objects'][0]
        self.assertEqual(
            restricted_card,
            {
                'status': 'restricted',
                'available': False,
                'label': 'Споделен е ограничен административен запис',
            },
        )

    def test_deleted_shared_source_becomes_an_unavailable_card(self):
        message = self._message_for_sharing()
        self.post.delete()
        message = TeamChatMessage.objects.prefetch_related(
            'mentions',
            'shared_objects__content_type',
        ).select_related('author', 'reply_to').get(pk=message.pk)

        payload = serialize_message(message, self._request_for(self.superuser))

        self.assertEqual(
            payload['shared_objects'][0],
            {
                'status': 'missing',
                'available': False,
                'label': 'Записът вече не е наличен',
            },
        )

    def test_share_action_and_detail_button_exist_only_for_allowlisted_models(self):
        request = self._request_for(self.superuser)
        posts_admin = admin.site._registry[Posts]
        push_admin = admin.site._registry[PushSubscription]

        self.assertIn('share_selected_in_team_chat', posts_admin.get_actions(request))
        self.assertIn(
            'share_selected_in_team_chat_discussion',
            posts_admin.get_actions(request),
        )
        self.assertNotIn(
            'share_selected_in_team_chat',
            push_admin.get_actions(request),
        )
        self.assertNotIn(
            'share_selected_in_team_chat_discussion',
            push_admin.get_actions(request),
        )
        self.assertNotIn(
            'share_selected_in_team_chat',
            admin.site._registry[User].get_actions(request),
        )
        self.assertNotIn(
            'share_selected_in_team_chat_discussion',
            admin.site._registry[User].get_actions(request),
        )

        discussion_action = posts_admin.get_actions(request)[
            'share_selected_in_team_chat_discussion'
        ][0]
        bulk_response = discussion_action(
            posts_admin,
            request,
            Posts.objects.filter(pk=self.post.pk),
        )
        self.assertEqual(bulk_response.status_code, 302)
        self.assertTrue(
            bulk_response.url.startswith(reverse('team-chat-share-to-discussion'))
        )

        self.client.force_login(self.superuser)
        response = self.client.get(
            reverse('admin:blog_posts_change', args=(self.post.pk,)),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Сподели в екипния чат')
        self.assertContains(response, 'Сподели в дискусия')

    def test_share_token_rejects_more_than_ten_records(self):
        references = [
            ('blog', 'posts', index)
            for index in range(MAX_SHARED_OBJECTS + 1)
        ]

        with self.assertRaises(TeamChatValidationError):
            make_share_token(references)

    def test_message_text_is_returned_as_data_not_rendered_into_admin_html(self):
        dangerous = '<img src=x onerror=alert(1)>'
        message = create_message(author=self.superuser, text=dangerous)
        self.client.force_login(self.superuser)

        payload = self.client.get(reverse('team-chat-messages')).json()
        page = self.client.get(reverse('team-chat-index'))

        self.assertEqual(payload['messages'][0]['text'], dangerous)
        self.assertNotContains(page, dangerous)
        self.assertContains(page, 'static/admin/js/team_chat.js')
