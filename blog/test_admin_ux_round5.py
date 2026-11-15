"""Fifth round of admin UX: titles, „+ Нова …“, names, menu, events, tabs."""

import re
from datetime import timedelta

from django.contrib import admin
from django.contrib.admin.models import ADDITION, CHANGE, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from valentine.models import Profile

from .admin_titles import add_label, list_title
from .forms import PostAdminForm
from .models import (
    Event,
    GameSettings,
    MissionActivity,
    PollDraft,
    PostImage,
    Posts,
    PushSubscription,
    SiteSettings,
    UserProfile,
)
from .test_admin_ux_round3 import SuperuserTestCase


class AddLabelTests(SimpleTestCase):
    def test_gender_and_capitals(self):
        self.assertEqual(add_label(Posts._meta), 'Нова публикация')
        self.assertEqual(add_label(Event._meta), 'Ново събитие')
        self.assertEqual(add_label(PollDraft._meta), 'Нова ИИ чернова за анкета')
        self.assertEqual(add_label(MissionActivity._meta), 'Нова активност по мисия')
        self.assertEqual(add_label(UserProfile._meta), 'Нов потребителски профил')
        self.assertEqual(add_label(PushSubscription._meta), 'Нов Push абонамент')
        self.assertEqual(add_label(GameSettings._meta), 'Нови настройки на игрите')
        self.assertEqual(list_title(Posts._meta), 'Публикации')

    def test_no_mixed_valentine_names(self):
        self.assertEqual(str(Profile._meta.verbose_name_plural), 'Профили (валентинки)')
        self.assertEqual(str(UserProfile._meta.verbose_name_plural), 'Потребителски профили')


class TitleAndButtonTests(SuperuserTestCase):
    def title(self, url):
        content = self.client.get(url).content.decode()
        return re.search(r'<title>\s*(.*?)\s*</title>', content, re.S).group(1)

    def test_page_titles(self):
        post = Posts.objects.create(title='Статия', hook='Кука', content='Текст', author=self.admin)
        self.assertTrue(self.title(reverse('admin:blog_posts_changelist')).startswith('Публикации |'))
        self.assertTrue(self.title(reverse('admin:blog_posts_add')).startswith('Нова публикация |'))
        self.assertTrue(
            self.title(reverse('admin:blog_posts_change', args=(post.pk,))).startswith('Статия | Публикация |')
        )

    def test_settings_title_is_not_repeated(self):
        settings_obj = SiteSettings.objects.create()
        title = self.title(reverse('admin:blog_sitesettings_change', args=(settings_obj.pk,)))
        self.assertEqual(title.count('Настройки на сайта'), 1)

    def test_add_button_has_text(self):
        content = self.client.get(reverse('admin:blog_posts_changelist')).content.decode()
        self.assertIn('ux-add-button-text">Нова публикация<', content)

    def test_reset_filters_only_when_filtered(self):
        url = reverse('admin:blog_notification_changelist')
        self.assertNotIn('filter_list_off', self.client.get(url).content.decode())
        self.assertIn('filter_list_off', self.client.get(url + '?q=няма-такова').content.decode())


class NamesTests(SuperuserTestCase):
    def test_action_log_in_bulgarian(self):
        LogEntry.objects.log_actions(
            user_id=self.admin.pk,
            queryset=[self.admin],
            action_flag=ADDITION,
            change_message='',
        )
        self.assertIsInstance(admin.site._registry[LogEntry], admin.ModelAdmin)
        content = self.client.get(reverse('admin:admin_logentry_changelist')).content.decode()
        for header in ('Кога', 'Потребител', 'Действие', 'Вид запис', 'Запис'):
            self.assertIn(header, content)
        for english in ('>User<', '>Action<', '>Object<'):
            self.assertNotIn(english, content)

    def test_user_list_labels(self):
        content = self.client.get(reverse('admin:auth_user_changelist')).content.decode()
        self.assertIn('Достъп до админ панела', content)
        self.assertNotIn('Статус на персонал', content)
        self.assertNotIn('Собствено име', content)

    def test_menu_names_and_single_active_item(self):
        content = self.client.get(reverse('admin:index')).content.decode()
        self.assertIn('Контактни съобщения', content)
        self.assertNotIn('Изпратени контактни формуляри', content)
        active = re.findall(r'<a[^>]+href="(/admin/[^"]*)"[^>]*class="[^"]*\bactive"', content)
        self.assertEqual(active, ['/admin/'])

    def test_site_subtitle_is_not_cut(self):
        content = self.client.get(reverse('admin:index')).content.decode()
        self.assertIn('ux-site-subheader', content)


class EventsAndFormsTests(SuperuserTestCase):
    def test_event_date_and_relative_time_in_one_column(self):
        Event.objects.create(
            title='Събитие', description='Описание', location='Зала',
            start_datetime=timezone.now() + timedelta(days=2, hours=1),
        )
        content = self.client.get(reverse('admin:blog_event_changelist')).content.decode()
        self.assertIn('ux-when', content)
        header = re.search(r'<thead.*?</thead>', content, re.S).group(0)
        self.assertNotIn('Начална дата и час', header)  # (the filter keeps it)

    def test_empty_cells_look_the_same(self):
        self.assertEqual(admin.site.empty_value_display, '—')

    def test_post_form_has_tabs(self):
        content = self.client.get(reverse('admin:blog_posts_add')).content.decode()
        for tab in ('fieldset-основно', 'fieldset-снимки-и-файлове', 'fieldset-публикуване', 'fieldset-ии-резюме'):
            self.assertIn(tab, content)
        self.assertIn('ux-into-tab', content)
        self.assertNotIn('>Други<', content)


class WordingTests(SuperuserTestCase):
    def test_delete_button_and_new_gallery_row(self):
        post = Posts.objects.create(title='Статия', hook='Кука', content='Текст', author=self.admin)
        content = self.client.get(reverse('admin:blog_posts_change', args=(post.pk,))).content.decode()
        self.assertIn('Изтрий публикация', content)
        self.assertNotIn('Изтрий Публикация', content)
        self.assertIn('Нова снимка в галерията', content)
        self.assertNotIn('Добави друг', content)
        self.assertIn('ux-site-subheader', content)

    def test_gallery_image_name_is_bulgarian(self):
        image = PostImage(post=Posts(title='Статия'), title='Сцена')
        self.assertEqual(str(image), 'Снимка: Сцена')
        self.assertNotIn('Image for post', str(PostImage(post=Posts(title='Статия'))))

    def test_empty_upload_is_not_a_change(self):
        form = PostAdminForm(data={}, files={})
        self.assertFalse(form.fields['gallery_images'].has_changed(None, []))
        self.assertFalse(form.fields['gallery_documents'].has_changed(None, []))
        self.assertTrue(form.fields['gallery_documents'].has_changed(None, ['file']))

    def test_log_entry_page_is_readable(self):
        post = Posts.objects.create(title='Статия', hook='Кука', content='Текст', author=self.admin)
        entry = LogEntry.objects.log_actions(
            user_id=self.admin.pk,
            queryset=[post],
            action_flag=CHANGE,
            change_message=[{'changed': {'fields': ['Заглавие']}}],
            single_object=True,
        )
        content = self.client.get(reverse('admin:admin_logentry_change', args=(entry.pk,))).content.decode()
        self.assertIn('Променени Заглавие', content)
        self.assertIn('Промяна', content)
        self.assertNotIn('[{&quot;changed&quot;', content)  # no raw JSON
        self.assertNotIn('[{"changed"', content)
        self.assertNotIn('Repr на обекта', content)
