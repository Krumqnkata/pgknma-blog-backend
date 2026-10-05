import io
import json
import tempfile
from datetime import timedelta
from pathlib import Path

from PIL import Image
from django.contrib.auth.models import Group, Permission, User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .data_retention import cleanup_personal_data
from .models import (
    Category,
    Notification,
    Posts,
    TeamChatAttachment,
    TeamChatDiscussion,
    TeamChatDiscussionReadState,
    TeamChatMessage,
)
from .team_chat import create_discussion, create_message
from .team_chat_storage import team_chat_protected_storage


def _image_upload(name='photo.jpg', image_format='JPEG', *, animated=False):
    output = io.BytesIO()
    if animated:
        first = Image.new('RGBA', (40, 30), '#2563eb')
        second = Image.new('RGBA', (40, 30), '#16a34a')
        first.save(
            output,
            format='GIF',
            save_all=True,
            append_images=[second],
            duration=[80, 90],
            loop=0,
        )
        content_type = 'image/gif'
    else:
        image = Image.new('RGB', (80, 50), '#2563eb')
        exif = Image.Exif()
        exif[0x010E] = 'private metadata'
        image.save(output, format=image_format, exif=exif)
        content_type = f'image/{image_format.lower()}'
    return SimpleUploadedFile(name, output.getvalue(), content_type=content_type)


class TeamChatPrivateStorageMixin:
    def setUp(self):
        super().setUp()
        self.private_directory = tempfile.TemporaryDirectory()
        self.storage = team_chat_protected_storage
        self.original_location = self.storage._location
        self.storage._location = self.private_directory.name
        for key in ('base_location', 'location'):
            self.storage.__dict__.pop(key, None)

    def tearDown(self):
        self.storage._location = self.original_location
        for key in ('base_location', 'location'):
            self.storage.__dict__.pop(key, None)
        self.private_directory.cleanup()
        super().tearDown()


class TeamChatDiscussionTests(TestCase):
    def setUp(self):
        self.author = User.objects.create_user(
            username='discussion-author',
            password='test-password',
            is_staff=True,
        )
        self.colleague = User.objects.create_user(
            username='discussion-colleague',
            password='test-password',
            is_staff=True,
        )
        self.superuser = User.objects.create_superuser(
            username='discussion-root',
            email='root@example.com',
            password='test-password',
        )

    def test_message_discussion_has_separate_history_and_unread_cursor(self):
        general = create_message(author=self.author, text='Нека уточним задачата')
        self.client.force_login(self.author)
        created = self.client.post(
            reverse('team-chat-discussions-api'),
            data=json.dumps({
                'title': 'Уточнение на задачата',
                'origin_message_id': general.pk,
            }),
            content_type='application/json',
        )
        self.assertEqual(created.status_code, 201)
        discussion = TeamChatDiscussion.objects.get()

        posted = self.client.post(
            reverse('team-chat-discussion-messages', args=(discussion.pk,)),
            {'text': '@discussion-colleague, виж това.'},
        )
        self.assertEqual(posted.status_code, 201)
        discussion_message = TeamChatMessage.objects.get(discussion=discussion)
        self.assertIn(self.colleague, discussion.participants.all())

        general_payload = self.client.get(reverse('team-chat-messages')).json()
        self.assertEqual(
            [row['id'] for row in general_payload['messages']],
            [general.pk],
        )

        self.client.force_login(self.colleague)
        counts = self.client.get(reverse('team-chat-unread')).json()
        self.assertEqual(counts['unread_count'], 1)
        self.assertEqual(counts['discussion_unread_count'], 1)
        self.assertEqual(counts['total_unread_count'], 2)

        marked = self.client.post(
            reverse('team-chat-discussion-read', args=(discussion.pk,)),
            data='{"last_message_id": %d}' % discussion_message.pk,
            content_type='application/json',
        )
        self.assertEqual(marked.status_code, 200)
        self.assertEqual(
            TeamChatDiscussionReadState.objects.get(
                discussion=discussion,
                user=self.colleague,
            ).last_read_message_id,
            discussion_message.pk,
        )
        counts = self.client.get(reverse('team-chat-unread')).json()
        self.assertEqual(counts['discussion_unread_count'], 0)
        self.assertEqual(counts['unread_count'], 1)

    def test_reply_cannot_cross_between_general_chat_and_discussion(self):
        general = create_message(author=self.author, text='Общо съобщение')
        discussion = create_discussion(author=self.author, title='Отделна тема')

        with self.assertRaisesMessage(ValueError, 'друго чат пространство'):
            create_message(
                author=self.author,
                text='Невалиден отговор',
                reply_to_id=general.pk,
                discussion=discussion,
            )

    def test_source_discussion_inherits_exact_admin_object_permissions(self):
        category = Category.objects.create(
            full_name='Дискусии',
            short_name='discussions',
        )
        post = Posts.objects.create(
            title='Ограничена публикация',
            hook='Кратко описание',
            content='Съдържание',
            author=self.superuser,
        )
        post.categories.add(category)
        discussion = create_discussion(
            author=self.superuser,
            title='Редакция на публикацията',
            source_object=post,
            visibility=TeamChatDiscussion.Visibility.PRIVATE,
            allowed_user_ids=(self.colleague.pk,),
        )
        create_message(
            author=self.superuser,
            text='Вътрешна бележка',
            discussion=discussion,
        )

        self.client.force_login(self.colleague)
        detail = self.client.get(
            reverse('team-chat-discussion-detail', args=(discussion.pk,)),
        )
        listing = self.client.get(reverse('team-chat-discussions-api')).json()

        self.assertEqual(detail.status_code, 404)
        self.assertNotIn(
            discussion.pk,
            [row['id'] for row in listing['discussions']],
        )

        self.client.force_login(self.superuser)
        allowed = self.client.get(
            reverse('team-chat-discussion-detail', args=(discussion.pk,)),
        )
        admin_page = self.client.get(
            reverse('admin:blog_posts_change', args=(post.pk,)),
        )
        self.assertEqual(allowed.status_code, 200)
        self.assertContains(admin_page, 'Дискусии за записа')

    def test_only_creator_or_authorized_record_editor_can_resolve(self):
        discussion = create_discussion(author=self.author, title='Решение')
        self.client.force_login(self.colleague)
        forbidden = self.client.post(
            reverse('team-chat-discussion-resolve', args=(discussion.pk,)),
            data='{"resolved": true}',
            content_type='application/json',
        )
        self.assertEqual(forbidden.status_code, 403)

        self.client.force_login(self.author)
        resolved = self.client.post(
            reverse('team-chat-discussion-resolve', args=(discussion.pk,)),
            data='{"resolved": true}',
            content_type='application/json',
        )
        self.assertEqual(resolved.status_code, 200)
        discussion.refresh_from_db()
        self.assertEqual(discussion.status, TeamChatDiscussion.Status.RESOLVED)
        blocked_post = self.client.post(
            reverse('team-chat-discussion-messages', args=(discussion.pk,)),
            {'text': 'Закъсняло съобщение'},
        )
        self.assertEqual(blocked_post.status_code, 400)

    def test_private_and_role_discussions_enforce_separate_acl(self):
        outsider = User.objects.create_user(
            username='discussion-outsider',
            password='test-password',
            is_staff=True,
        )
        private = create_discussion(
            author=self.author,
            title='Частна подготовка',
            visibility=TeamChatDiscussion.Visibility.PRIVATE,
            allowed_user_ids=(self.colleague.pk,),
        )
        self.assertNotIn(self.colleague, private.participants.all())

        self.client.force_login(self.colleague)
        self.assertEqual(
            self.client.get(
                reverse('team-chat-discussion-detail', args=(private.pk,))
            ).status_code,
            200,
        )
        self.client.force_login(outsider)
        self.assertEqual(
            self.client.get(
                reverse('team-chat-discussion-detail', args=(private.pk,))
            ).status_code,
            404,
        )
        self.client.force_login(self.superuser)
        self.assertEqual(
            self.client.get(
                reverse('team-chat-discussion-detail', args=(private.pk,))
            ).status_code,
            200,
        )

        role = Group.objects.create(name='Тестови редактори')
        self.colleague.groups.add(role)
        role_discussion = create_discussion(
            author=self.author,
            title='Тема за роля',
            visibility=TeamChatDiscussion.Visibility.GROUPS,
            allowed_group_ids=(role.pk,),
        )
        self.client.force_login(self.colleague)
        self.assertEqual(
            self.client.get(
                reverse('team-chat-discussion-detail', args=(role_discussion.pk,))
            ).status_code,
            200,
        )
        self.client.force_login(outsider)
        listing = self.client.get(reverse('team-chat-discussions-api')).json()
        self.assertNotIn(
            role_discussion.pk,
            [row['id'] for row in listing['discussions']],
        )

    def test_creator_can_change_access_and_new_members_are_notified(self):
        replacement = User.objects.create_user(
            username='discussion-replacement',
            password='test-password',
            is_staff=True,
        )
        self.client.force_login(self.author)
        created = self.client.post(
            reverse('team-chat-discussions'),
            {
                'title': 'Ограничена редакция',
                'visibility': TeamChatDiscussion.Visibility.PRIVATE,
                'allowed_users': [self.colleague.pk],
            },
        )
        self.assertEqual(created.status_code, 302)
        discussion = TeamChatDiscussion.objects.get(title='Ограничена редакция')
        invite = Notification.objects.get(
            user=self.colleague,
            category='team_chat_invite',
        )
        self.assertEqual(
            invite.action_url,
            reverse('team-chat-discussion-detail', args=(discussion.pk,)),
        )
        self.assertFalse(Notification.objects.filter(
            user=self.superuser,
            category='team_chat_invite',
        ).exists())

        self.client.force_login(self.colleague)
        self.assertEqual(
            self.client.get(
                reverse('team-chat-discussion-access', args=(discussion.pk,))
            ).status_code,
            403,
        )

        self.client.force_login(self.author)
        changed = self.client.post(
            reverse('team-chat-discussion-access', args=(discussion.pk,)),
            {
                'visibility': TeamChatDiscussion.Visibility.PRIVATE,
                'allowed_users': [replacement.pk],
            },
        )
        self.assertEqual(changed.status_code, 302)
        discussion.refresh_from_db()
        self.assertEqual(
            set(discussion.allowed_users.values_list('pk', flat=True)),
            {replacement.pk},
        )
        self.assertFalse(Notification.objects.filter(
            user=self.colleague,
            category='team_chat_invite',
        ).exists())
        self.assertTrue(Notification.objects.filter(
            user=replacement,
            category='team_chat_invite',
        ).exists())

        self.client.force_login(self.colleague)
        self.assertEqual(
            self.client.get(
                reverse('team-chat-discussion-detail', args=(discussion.pk,))
            ).status_code,
            404,
        )
        self.client.force_login(replacement)
        self.assertEqual(
            self.client.get(
                reverse('team-chat-discussion-detail', args=(discussion.pk,))
            ).status_code,
            200,
        )

    def test_discussion_manager_permission_and_mentions_follow_acl(self):
        outsider = User.objects.create_user(
            username='discussion-mentioned-outsider',
            password='test-password',
            is_staff=True,
        )
        manager = User.objects.create_user(
            username='discussion-manager',
            password='test-password',
            is_staff=True,
        )
        manager.user_permissions.add(Permission.objects.get(
            content_type__app_label='blog',
            codename='manage_team_chat_discussion_access',
        ))
        discussion = create_discussion(
            author=self.author,
            title='Контролирани споменавания',
            visibility=TeamChatDiscussion.Visibility.PRIVATE,
            allowed_user_ids=(self.colleague.pk,),
        )
        message = create_message(
            author=self.author,
            text=(
                '@discussion-colleague и '
                '@discussion-mentioned-outsider, проверете това.'
            ),
            discussion=discussion,
        )
        self.assertEqual(
            set(message.mentions.values_list('pk', flat=True)),
            {self.colleague.pk},
        )

        self.client.force_login(self.author)
        suggestions = self.client.get(
            reverse('team-chat-staff'),
            {'discussion': discussion.pk, 'q': 'discussion-'},
        ).json()['results']
        usernames = {row['username'] for row in suggestions}
        self.assertIn(self.colleague.username, usernames)
        self.assertNotIn(outsider.username, usernames)

        self.client.force_login(manager)
        self.assertEqual(
            self.client.get(
                reverse('team-chat-discussion-access', args=(discussion.pk,))
            ).status_code,
            200,
        )

    def test_discussion_creation_page_includes_access_controls_for_chat_origin(self):
        origin = create_message(
            author=self.author,
            text='Нека обсъдим графика отделно.',
        )
        self.client.force_login(self.author)
        page = self.client.get(
            reverse('team-chat-discussions'),
            {'origin_message_id': origin.pk},
        )

        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Нека обсъдим графика отделно.')
        self.assertContains(page, 'Целият екип')
        self.assertContains(page, 'Избрани роли')
        self.assertContains(page, 'Конкретни хора')
        self.assertContains(page, 'team_chat_access.js')


class TeamChatAttachmentTests(TeamChatPrivateStorageMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.staff = User.objects.create_user(
            username='image-staff',
            password='test-password',
            is_staff=True,
        )
        self.other_staff = User.objects.create_user(
            username='image-colleague',
            password='test-password',
            is_staff=True,
        )
        self.public_user = User.objects.create_user(
            username='image-public',
            password='test-password',
        )
        self.client.force_login(self.staff)

    def test_image_only_message_is_sanitized_and_served_only_through_admin(self):
        response = self.client.post(
            reverse('team-chat-messages'),
            {'text': '', 'images': [_image_upload()]},
        )

        self.assertEqual(response.status_code, 201)
        attachment = TeamChatAttachment.objects.get()
        self.assertTrue(attachment.image.name.endswith('/image.webp'))
        self.assertTrue(attachment.thumbnail.name.endswith('/thumbnail.webp'))
        self.assertNotIn('/files/', response.json()['message']['attachments'][0]['url'])
        with attachment.image.open('rb') as handle, Image.open(handle) as image:
            self.assertEqual(image.format, 'WEBP')
            self.assertEqual(len(image.getexif()), 0)

        served = self.client.get(reverse('team-chat-attachment', args=(attachment.pk,)))
        self.assertEqual(served.status_code, 200)
        self.assertEqual(served['Content-Type'], 'image/webp')
        self.assertIn('private', served['Cache-Control'])
        self.assertIn('no-store', served['Cache-Control'])
        served.close()

        self.client.force_login(self.public_user)
        denied = self.client.get(reverse('team-chat-attachment', args=(attachment.pk,)))
        self.assertEqual(denied.status_code, 302)

    def test_animated_gif_is_reencoded_as_animated_webp(self):
        response = self.client.post(
            reverse('team-chat-messages'),
            {'images': [_image_upload('animation.gif', animated=True)]},
        )
        self.assertEqual(response.status_code, 201)
        attachment = TeamChatAttachment.objects.get()
        self.assertTrue(attachment.is_animated)
        self.assertEqual(attachment.original_format, 'GIF')
        with attachment.image.open('rb') as handle, Image.open(handle) as image:
            self.assertEqual(image.format, 'WEBP')
            self.assertGreater(image.n_frames, 1)

    def test_invalid_or_excess_images_roll_back_the_whole_message(self):
        invalid = SimpleUploadedFile(
            'fake.jpg',
            b'<svg onload="alert(1)"></svg>',
            content_type='image/jpeg',
        )
        rejected = self.client.post(
            reverse('team-chat-messages'),
            {'text': 'Опасен файл', 'images': [invalid]},
        )
        self.assertEqual(rejected.status_code, 400)
        self.assertFalse(TeamChatMessage.objects.exists())

        too_many = self.client.post(
            reverse('team-chat-messages'),
            {'images': [_image_upload(f'{index}.jpg') for index in range(4)]},
        )
        self.assertEqual(too_many.status_code, 400)
        self.assertFalse(TeamChatMessage.objects.exists())

    def test_hourly_rate_limit_rejects_the_eleventh_image(self):
        first_message = None
        for batch_size in (3, 3, 3, 1):
            message = create_message(
                author=self.staff,
                text='',
                uploaded_files=tuple(
                    _image_upload(f'allowed-{index}.jpg')
                    for index in range(batch_size)
                ),
            )
            if first_message is None:
                first_message = message

        with self.captureOnCommitCallbacks(execute=True):
            deleted = self.client.post(
                reverse('team-chat-delete', args=(first_message.pk,)),
                data='{}',
                content_type='application/json',
            )
        self.assertEqual(deleted.status_code, 200)

        rejected = self.client.post(
            reverse('team-chat-messages'),
            {'images': [_image_upload('eleventh.jpg')]},
        )
        self.assertEqual(rejected.status_code, 400)
        self.assertIn('10 изображения на час', rejected.json()['detail'])
        self.assertEqual(TeamChatAttachment.objects.count(), 10)

    def test_retention_removes_database_rows_and_private_files(self):
        message = create_message(
            author=self.staff,
            text='',
            uploaded_files=(_image_upload(),),
        )
        attachment = TeamChatAttachment.objects.get(message=message)
        image_path = Path(attachment.image.path)
        thumbnail_path = Path(attachment.thumbnail.path)
        old_time = timezone.now() - timedelta(days=91)
        TeamChatMessage.objects.filter(pk=message.pk).update(
            created_at=old_time,
            updated_at=old_time,
        )

        with self.captureOnCommitCallbacks(execute=True):
            result = cleanup_personal_data(days=90, now=timezone.now())

        self.assertEqual(result['team_chat_messages'], 1)
        self.assertFalse(TeamChatMessage.objects.filter(pk=message.pk).exists())
        self.assertFalse(image_path.exists())
        self.assertFalse(thumbnail_path.exists())

    def test_soft_deleted_message_immediately_deletes_its_attachment_files(self):
        sent = self.client.post(
            reverse('team-chat-messages'),
            {'images': [_image_upload()]},
        ).json()
        message_id = sent['message']['id']
        attachment = TeamChatAttachment.objects.get()
        image_path = Path(attachment.image.path)
        thumbnail_path = Path(attachment.thumbnail.path)

        with self.captureOnCommitCallbacks(execute=True):
            deleted = self.client.post(
                reverse('team-chat-delete', args=(message_id,)),
                data='{}',
                content_type='application/json',
            )

        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json()['message']['attachments'], [])
        self.assertTrue(
            TeamChatAttachment.objects.filter(pk=attachment.pk).exists()
        )
        self.assertFalse(image_path.exists())
        self.assertFalse(thumbnail_path.exists())
        hidden = self.client.get(reverse('team-chat-attachment', args=(attachment.pk,)))
        self.assertEqual(hidden.status_code, 404)

    def test_source_permission_is_also_required_for_attachment(self):
        root = User.objects.create_superuser(
            username='image-root',
            email='image-root@example.com',
            password='test-password',
        )
        category = Category.objects.create(full_name='Снимки', short_name='images')
        post = Posts.objects.create(
            title='Защитен запис',
            hook='Описание',
            content='Съдържание',
            author=root,
        )
        post.categories.add(category)
        discussion = create_discussion(
            author=root,
            title='Защитена дискусия',
            source_object=post,
        )
        create_message(
            author=root,
            text='',
            discussion=discussion,
            uploaded_files=(_image_upload(),),
        )
        attachment = TeamChatAttachment.objects.get()

        self.client.force_login(self.other_staff)
        denied = self.client.get(reverse('team-chat-attachment', args=(attachment.pk,)))
        self.assertEqual(denied.status_code, 404)

        self.client.force_login(root)
        allowed = self.client.get(reverse('team-chat-attachment', args=(attachment.pk,)))
        self.assertEqual(allowed.status_code, 200)
        allowed.close()
