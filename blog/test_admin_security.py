import base64
import tempfile
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib import admin
from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import Permission, User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.forms.models import inlineformset_factory
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .admin import (
    BellSongSuggestionAdmin,
    CommentsAdmin,
    PollQuestionAdmin,
    PostsAdmin,
)
from .forms import (
    ADMIN_SELECTION_LIMIT,
    MemeSelectionForm,
    MultipleImageField,
    PollQuestionSelectionForm,
    SongSuggestionSelectionForm,
)
from .models import (
    BellSongSuggestion,
    Category,
    Comments,
    MemeOfWeek,
    PostDocument,
    PostImage,
    Posts,
    PollOption,
    PollQuestion,
    SiteSettings,
)


class AdminHelpGuideTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff_user = User.objects.create_user(
            username="guide-staff",
            password="test-password",
            is_staff=True,
        )
        cls.superuser = User.objects.create_superuser(
            username="guide-superuser",
            email="guide-superuser@example.com",
            password="test-password",
        )

    def test_admin_index_renders_full_guide_for_all_staff_users(self):
        expected_urls = (
            "admin:blog_posts_add",
            "admin:blog_scheduleentry_import_csv",
            "admin:blog_gamificationpointrule_changelist",
            "admin:blog_mission_changelist",
            "admin:blog_badge_changelist",
            "admin:blog_digitalreward_changelist",
            "admin:blog_pointtransaction_changelist",
            "admin:blog_personaldatacleanuprun_changelist",
            "admin:valentine_profile_changelist",
            "admin:valentine_profile_import_csv",
            "admin:valentine_valentinecampaign_changelist",
            "admin:valentine_valenitines_changelist",
        )

        for user in (self.staff_user, self.superuser):
            with self.subTest(username=user.username):
                self.client.force_login(user)
                response = self.client.get(reverse("admin:index"))

                self.assertEqual(response.status_code, 200)
                self.assertTemplateUsed(response, "admin/index.html")
                self.assertTemplateUsed(response, "admin/_help_guide.html")
                self.assertContains(response, "Администраторско ръководство")
                self.assertContains(response, "Геймификация — точки, мисии, значки")
                self.assertContains(response, "независимо дали е изтрит от потребителя или от администратор")
                self.assertContains(response, "Технически runbook")
                self.assertContains(response, "Valentine сигнали, архив")
                self.assertContains(response, "cleanup_personal_data --days 90 --dry-run")
                for url_name in expected_urls:
                    self.assertContains(response, reverse(url_name))

    def test_admin_index_still_requires_staff_login(self):
        response = self.client.get(reverse("admin:index"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("admin:login"), response.url)


class PostMediaPathTests(TestCase):
    PNG_BYTES = base64.b64decode(
        'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC'
        'AAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII='
    )

    def setUp(self):
        self.media_directory = tempfile.TemporaryDirectory()
        self.media_override = override_settings(
            MEDIA_ROOT=self.media_directory.name,
        )
        self.media_override.enable()
        self.addCleanup(self._cleanup_media)

    def _cleanup_media(self):
        self.media_override.disable()
        self.media_directory.cleanup()

    def _image(self, filename):
        return SimpleUploadedFile(
            filename,
            self.PNG_BYTES,
            content_type='image/png',
        )

    def test_first_save_uses_post_id_for_all_media_directories(self):
        author = User.objects.create_user(
            username='post-author',
            password='test-password',
            is_staff=True,
        )
        category = Category.objects.create(
            full_name='Новини',
            short_name='news',
        )
        request = RequestFactory().post('/admin/blog/posts/add/')
        request.user = author
        post = Posts(
            title='Публикация с файлове',
            category=category,
            banner=self._image('banner.png'),
            hook='Кратко описание',
            content='Съдържание',
        )
        form = SimpleNamespace(
            cleaned_data={
                'delete_images': [],
                'delete_documents': [],
                'gallery_images': [self._image('gallery.png')],
                'gallery_documents': [
                    SimpleUploadedFile(
                        'document.txt',
                        b'test document',
                        content_type='text/plain',
                    )
                ],
            }
        )

        PostsAdmin(Posts, AdminSite()).save_model(
            request,
            post,
            form,
            change=False,
        )

        post.refresh_from_db()
        image = PostImage.objects.get(post=post)
        document = PostDocument.objects.get(post=post)
        self.assertEqual(
            post.banner.name,
            f'posts/{post.pk}/banner/banner.png',
        )
        self.assertEqual(
            image.image.name,
            f'posts/{post.pk}/gallery/gallery.png',
        )
        self.assertEqual(
            document.file.name,
            f'posts/{post.pk}/documents/document.txt',
        )


class CommentAdminIntegrityTests(TestCase):
    def test_authorship_and_content_are_read_only(self):
        model_admin = CommentsAdmin(Comments, AdminSite())

        self.assertTrue(
            {"user", "content", "created_at", "post", "parent"}.issubset(
                set(model_admin.get_readonly_fields(RequestFactory().get("/")))
            )
        )


class SiteSettingsDeletionSecurityTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="settings-staff",
            password="test-password",
            is_staff=True,
        )
        self.owner = User.objects.create_user(
            username="content-owner",
            password="test-password",
        )
        self.settings = SiteSettings.objects.create()
        self.user.user_permissions.add(
            Permission.objects.get(codename="change_sitesettings")
        )
        self.client.force_login(self.user)

    def test_staff_without_related_delete_permission_cannot_delete(self):
        meme = MemeOfWeek.objects.create(
            user=self.owner,
            title="test meme",
        )
        suggestion = BellSongSuggestion.objects.create(
            user=self.owner,
            title="test song",
            link="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        )
        poll_start = timezone.now()
        poll = PollQuestion.objects.create(
            title="test poll",
            start_date=poll_start,
            end_date=poll_start + timedelta(days=1),
        )

        attempts = (
            (
                "admin:blog_sitesettings_delete_memes",
                {f"memes-meme_{meme.pk}": "on"},
                MemeOfWeek,
                meme.pk,
            ),
            (
                "admin:blog_sitesettings_delete_suggestions",
                {f"suggestions-suggestion_{suggestion.pk}": "on"},
                BellSongSuggestion,
                suggestion.pk,
            ),
            (
                "admin:blog_sitesettings_delete_polls",
                {f"polls-poll_{poll.pk}": "on"},
                PollQuestion,
                poll.pk,
            ),
        )

        for url_name, data, model, object_pk in attempts:
            with self.subTest(url_name=url_name):
                response = self.client.post(
                    reverse(url_name, args=(self.settings.pk,)),
                    data,
                )
                self.assertEqual(response.status_code, 403)
                self.assertTrue(model.objects.filter(pk=object_pk).exists())

    def test_staff_with_related_delete_permission_can_delete_selection(self):
        meme = MemeOfWeek.objects.create(
            user=self.owner,
            title="delete me",
        )
        untouched = MemeOfWeek.objects.create(
            user=self.owner,
            title="keep me",
        )
        self.user.user_permissions.add(
            Permission.objects.get(codename="delete_memeofweek")
        )

        response = self.client.post(
            reverse(
                "admin:blog_sitesettings_delete_memes",
                args=(self.settings.pk,),
            ),
            {f"memes-meme_{meme.pk}": "on"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(MemeOfWeek.objects.filter(pk=meme.pk).exists())
        self.assertTrue(MemeOfWeek.objects.filter(pk=untouched.pk).exists())

    def test_change_page_has_separate_action_without_nested_form(self):
        self.user.user_permissions.add(
            Permission.objects.get(codename="delete_memeofweek")
        )
        MemeOfWeek.objects.create(user=self.owner, title="selectable")

        response = self.client.get(
            reverse(
                "admin:blog_sitesettings_change",
                args=(self.settings.pk,),
            )
        )

        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        section_start = html.index(
            '<section class="module aligned related-deletion-section">'
        )
        section_end = html.index("</section>", section_start)
        deletion_section = html[section_start:section_end]
        self.assertNotIn("<form", deletion_section)
        self.assertIn(
            reverse(
                "admin:blog_sitesettings_delete_memes",
                args=(self.settings.pk,),
            ),
            deletion_section,
        )
        self.assertIn(
            f"Показани са най-новите до {ADMIN_SELECTION_LIMIT} записа.",
            deletion_section,
        )
        self.assertIn(
            reverse("admin:blog_memeofweek_changelist"),
            deletion_section,
        )


class AdminEscapingTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="xss-owner",
            password="test-password",
        )

    def test_song_title_is_escaped_in_embedded_media(self):
        suggestion = BellSongSuggestion(
            user=self.owner,
            title='<script>alert("admin-xss")</script>',
            link="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        )
        model_admin = BellSongSuggestionAdmin(
            BellSongSuggestion,
            AdminSite(),
        )

        rendered = str(model_admin.embedded_media_display(suggestion))

        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)

    def test_meme_title_is_escaped_in_selection_label(self):
        meme = MemeOfWeek.objects.create(
            user=self.owner,
            title='<img src=x onerror="alert(1)">',
        )

        label = str(MemeSelectionForm().fields[f"meme_{meme.pk}"].label)

        self.assertNotIn("<img src=x", label)
        self.assertIn("&lt;img", label)


class AdminSelectionLimitTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="selection-owner",
            password="test-password",
        )

    def test_all_site_settings_selection_forms_limit_database_results(self):
        total = ADMIN_SELECTION_LIMIT + 1
        memes = MemeOfWeek.objects.bulk_create(
            [
                MemeOfWeek(user=self.owner, title=f"meme {index}")
                for index in range(total)
            ]
        )
        suggestions = BellSongSuggestion.objects.bulk_create(
            [
                BellSongSuggestion(
                    user=self.owner,
                    title=f"song {index}",
                    link="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                )
                for index in range(total)
            ]
        )
        poll_start = timezone.now()
        polls = PollQuestion.objects.bulk_create(
            [
                PollQuestion(
                    title=f"poll {index}",
                    start_date=poll_start,
                    end_date=poll_start + timedelta(days=1),
                )
                for index in range(total)
            ]
        )

        cases = (
            (MemeSelectionForm, "meme", memes),
            (SongSuggestionSelectionForm, "suggestion", suggestions),
            (PollQuestionSelectionForm, "poll", polls),
        )
        for form_class, field_prefix, objects in cases:
            with self.subTest(form=form_class.__name__):
                form = form_class()
                self.assertEqual(len(form.fields), ADMIN_SELECTION_LIMIT)
                self.assertIn(
                    f"{field_prefix}_{objects[-1].pk}",
                    form.fields,
                )
                self.assertNotIn(
                    f"{field_prefix}_{objects[0].pk}",
                    form.fields,
                )


class BellSongDeletionActionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="song-moderator",
            password="test-password",
            is_staff=True,
        )
        self.user.user_permissions.add(
            Permission.objects.get(codename="delete_bellsongsuggestion")
        )
        self.model_admin = BellSongSuggestionAdmin(
            BellSongSuggestion,
            AdminSite(),
        )

    def _suggestion(self, title, submitted_at):
        suggestion = BellSongSuggestion.objects.create(
            user=self.user,
            title=title,
            link="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        )
        BellSongSuggestion.objects.filter(pk=suggestion.pk).update(
            submitted_at=submitted_at
        )
        suggestion.refresh_from_db()
        return suggestion

    def test_delete_by_date_only_deletes_selected_old_records(self):
        old_time = timezone.now() - timedelta(days=10)
        selected_old = self._suggestion("selected old", old_time)
        unselected_old = self._suggestion("unselected old", old_time)
        selected_new = self._suggestion("selected new", timezone.now())
        request = RequestFactory().post(
            "/admin/blog/bellsongsuggestion/",
            {
                "apply": "yes",
                "delete_before_date": (
                    timezone.localdate() - timedelta(days=1)
                ).isoformat(),
            },
        )
        request.user = self.user
        queryset = BellSongSuggestion.objects.filter(
            pk__in=(selected_old.pk, selected_new.pk)
        )

        with (
            patch.object(self.model_admin, "message_user"),
            patch.object(self.model_admin, "log_deletions"),
        ):
            response = self.model_admin.delete_by_date(request, queryset)

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            BellSongSuggestion.objects.filter(pk=selected_old.pk).exists()
        )
        self.assertTrue(
            BellSongSuggestion.objects.filter(pk=unselected_old.pk).exists()
        )
        self.assertTrue(
            BellSongSuggestion.objects.filter(pk=selected_new.pk).exists()
        )


class PollInlinePersistenceTests(TestCase):
    def test_deleting_middle_option_renumbers_remaining_options(self):
        poll_start = timezone.now()
        question = PollQuestion.objects.create(
            title="renumber",
            start_date=poll_start,
            end_date=poll_start + timedelta(days=1),
        )
        option_a = PollOption.objects.create(
            question=question,
            key="a",
            text="A",
        )
        option_b = PollOption.objects.create(
            question=question,
            key="b",
            text="B",
        )
        option_c = PollOption.objects.create(
            question=question,
            key="c",
            text="C",
        )
        formset_class = inlineformset_factory(
            PollQuestion,
            PollOption,
            fields=("text", "image", "is_correct"),
            can_delete=True,
            extra=0,
        )
        formset = formset_class(
            data={
                "options-TOTAL_FORMS": "3",
                "options-INITIAL_FORMS": "3",
                "options-MIN_NUM_FORMS": "0",
                "options-MAX_NUM_FORMS": "4",
                "options-0-id": str(option_a.pk),
                "options-0-text": "A",
                "options-1-id": str(option_b.pk),
                "options-1-text": "B",
                "options-1-DELETE": "on",
                "options-2-id": str(option_c.pk),
                "options-2-text": "C",
                "options-2-is_correct": "on",
            },
            instance=question,
            prefix="options",
        )
        self.assertTrue(formset.is_valid(), formset.errors)
        model_admin = PollQuestionAdmin(PollQuestion, admin.site)

        model_admin.save_formset(
            RequestFactory().post("/admin/"),
            SimpleNamespace(instance=question),
            formset,
            change=True,
        )

        self.assertEqual(
            list(
                question.options.order_by("key").values_list(
                    "key",
                    "text",
                )
            ),
            [("a", "A"), ("b", "C")],
        )
        self.assertTrue(question.options.get(key="b").is_correct)


class GalleryImageValidationTests(TestCase):
    def test_non_image_with_image_extension_is_rejected(self):
        field = MultipleImageField(required=False)
        fake_image = SimpleUploadedFile(
            "not-really-an-image.jpg",
            b"this is not image data",
            content_type="image/jpeg",
        )

        with self.assertRaises(ValidationError):
            field.clean([fake_image])
