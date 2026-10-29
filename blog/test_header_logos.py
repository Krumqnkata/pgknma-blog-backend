import tempfile
from datetime import date, datetime
from io import BytesIO
from unittest.mock import patch

from PIL import Image
from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from .header_logos import logos_on, orthodox_easter
from .models import HeaderLogo


def image_upload(name='logo.png', image_format='PNG', frames=1, size=(64, 64)):
    output = BytesIO()
    images = [Image.new('RGBA', size, (index * 80, 120, 200, 255)) for index in range(frames)]
    if frames > 1:
        images[0].save(output, format=image_format, save_all=True, append_images=images[1:], duration=100, loop=0)
    else:
        images[0].save(output, format=image_format)
    return SimpleUploadedFile(name, output.getvalue(), content_type=f'image/{image_format.lower()}')


class HeaderLogoTestMixin:
    def setUp(self):
        super().setUp()
        self.media_directory = tempfile.TemporaryDirectory()
        self.media_override = override_settings(MEDIA_ROOT=self.media_directory.name)
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)
        self.addCleanup(self.media_directory.cleanup)
        # Start from an empty table: the seed migration adds image-less records.
        HeaderLogo.objects.all().delete()
        cache.clear()

    def make_logo(self, name, start, end, kind=HeaderLogo.Kind.HOLIDAY, **extra):
        start_day, start_month = start
        end_day, end_month = end
        extra.setdefault('image', image_upload(f'{name}.png'))
        return HeaderLogo.objects.create(
            name=name,
            kind=kind,
            start_day=start_day,
            start_month=start_month,
            end_day=end_day,
            end_month=end_month,
            **extra,
        )


class OrthodoxEasterTests(TestCase):
    def test_known_dates(self):
        self.assertEqual(orthodox_easter(2024), date(2024, 5, 5))
        self.assertEqual(orthodox_easter(2025), date(2025, 4, 20))
        self.assertEqual(orthodox_easter(2026), date(2026, 4, 12))
        self.assertEqual(orthodox_easter(2027), date(2027, 5, 2))


class HeaderLogoResolverTests(HeaderLogoTestMixin, TestCase):
    def test_yearly_period_wraps_over_new_year(self):
        winter = self.make_logo('Зима', (1, 12), (29, 2), kind=HeaderLogo.Kind.SEASON)
        self.assertEqual(logos_on(date(2026, 12, 15)), [winter])
        self.assertEqual(logos_on(date(2027, 1, 10)), [winter])
        self.assertEqual(logos_on(date(2027, 2, 28)), [winter])
        self.assertEqual(logos_on(date(2027, 3, 1)), [])
        self.assertEqual(logos_on(date(2028, 2, 29)), [winter])

    def test_holiday_beats_season_and_shorter_period_wins(self):
        autumn = self.make_logo('Есен', (1, 9), (30, 11), kind=HeaderLogo.Kind.SEASON)
        week = self.make_logo('Седмица', (28, 10), (3, 11))
        day = self.make_logo('Будители', (1, 11), (1, 11))
        self.assertEqual(logos_on(date(2026, 11, 1)), [day, week, autumn])
        self.assertEqual(logos_on(date(2026, 10, 29))[0], week)

    def test_priority_beats_shorter_period(self):
        long = self.make_logo('Дълъг', (1, 10), (30, 11), priority=5)
        short = self.make_logo('Кратък', (1, 11), (1, 11))
        self.assertEqual(logos_on(date(2026, 11, 1)), [long, short])

    def test_inactive_and_imageless_logos_are_skipped(self):
        self.make_logo('Изключено', (1, 1), (31, 12), is_active=False)
        self.make_logo('Без лого', (1, 1), (31, 12), image='')
        self.assertEqual(logos_on(date(2026, 6, 1)), [])

    def test_easter_and_once_modes(self):
        easter = HeaderLogo.objects.create(
            name='Великден',
            date_mode=HeaderLogo.DateMode.EASTER,
            easter_offset_start=-1,
            easter_offset_end=1,
            image=image_upload(),
        )
        once = HeaderLogo.objects.create(
            name='Юбилей',
            date_mode=HeaderLogo.DateMode.ONCE,
            start_date=date(2027, 5, 10),
            end_date=date(2027, 5, 12),
            image=image_upload(),
        )
        self.assertEqual(logos_on(date(2027, 5, 1)), [easter])
        self.assertEqual(logos_on(date(2027, 5, 3)), [easter])
        self.assertEqual(logos_on(date(2027, 5, 4)), [])
        self.assertEqual(logos_on(date(2027, 5, 11)), [once])
        self.assertEqual(logos_on(date(2028, 5, 11)), [])


class HeaderLogoModelTests(HeaderLogoTestMixin, TestCase):
    def test_animated_gif_gets_a_still_frame(self):
        logo = self.make_logo('GIF', (1, 1), (2, 1), image=image_upload('logo.gif', 'GIF', frames=3))
        self.assertTrue(logo.image_still.name.endswith('.png'))
        with Image.open(logo.image_still.path) as still:
            self.assertFalse(getattr(still, 'is_animated', False))

        logo.image = image_upload('static.png')
        logo.save()
        self.assertFalse(logo.image_still)

    def test_clean_rejects_incomplete_or_impossible_periods(self):
        with self.assertRaises(ValidationError) as missing:
            HeaderLogo(name='X', date_mode=HeaderLogo.DateMode.YEARLY).full_clean()
        self.assertIn('start_month', missing.exception.message_dict)

        with self.assertRaises(ValidationError) as impossible:
            HeaderLogo(name='X', start_day=31, start_month=4, end_day=1, end_month=5).full_clean()
        self.assertIn('start_day', impossible.exception.message_dict)

        with self.assertRaises(ValidationError) as backwards:
            HeaderLogo(
                name='X',
                date_mode=HeaderLogo.DateMode.ONCE,
                start_date=date(2026, 5, 2),
                end_date=date(2026, 5, 1),
            ).full_clean()
        self.assertIn('end_date', backwards.exception.message_dict)

        HeaderLogo(name='Зима', start_day=1, start_month=12, end_day=29, end_month=2).full_clean()

    def test_clean_rejects_oversized_images(self):
        logo = HeaderLogo(
            name='Голямо',
            start_day=1, start_month=1, end_day=1, end_month=1,
            image=image_upload(size=(1500, 200)),
        )
        with self.assertRaises(ValidationError) as error:
            logo.full_clean()
        self.assertIn('image', error.exception.message_dict)


class SiteStatusHeaderLogoTests(HeaderLogoTestMixin, TestCase):
    def test_site_status_returns_null_without_logo(self):
        response = APIClient().get('/api/site-status/')
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['header_logo'])

    def test_site_status_returns_todays_logo_and_cache_is_cleared_on_save(self):
        now = timezone.make_aware(datetime(2026, 11, 1, 10, 0))
        with patch('blog.header_logos.timezone.localtime', return_value=now), \
                patch('blog.header_logos.timezone.localdate', return_value=now.date()):
            self.assertIsNone(APIClient().get('/api/site-status/').json()['header_logo'])

            with self.captureOnCommitCallbacks(execute=True):
                logo = self.make_logo(
                    'Будители', (1, 11), (1, 11),
                    image=image_upload('budit.gif', 'GIF', frames=2),
                    greeting='Честит Ден на народните будители!',
                    effect=HeaderLogo.Effect.LEAVES,
                )
            payload = APIClient().get('/api/site-status/').json()['header_logo']

        self.assertEqual(payload['image_url'], logo.image.url)
        self.assertEqual(payload['static_image_url'], logo.image_still.url)
        self.assertEqual(payload['greeting'], 'Честит Ден на народните будители!')
        self.assertEqual(payload['alt'], 'Честит Ден на народните будители!')
        self.assertEqual(payload['effect'], 'leaves')


class HeaderLogoAdminTests(HeaderLogoTestMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.superuser = User.objects.create_superuser('logo-admin', 'logo@example.com', 'test-password')
        self.client.force_login(self.superuser)

    def test_changelist_and_preview_render(self):
        autumn = self.make_logo('Есен', (1, 9), (30, 11), kind=HeaderLogo.Kind.SEASON)
        self.make_logo('Будители', (1, 11), (1, 11), greeting='Честит 1 ноември!')

        self.assertEqual(self.client.get(reverse('admin:blog_headerlogo_changelist')).status_code, 200)
        response = self.client.get(reverse('admin:blog_headerlogo_preview'), {'date': '2026-11-01'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['winner'].name, 'Будители')
        statuses = {row['logo'].name: row['status'] for row in response.context['covering']}
        self.assertEqual(statuses, {'Будители': 'Показва се', 'Есен': 'Засенчено от по-важен запис'})
        schedule = response.context['schedule']
        self.assertEqual((schedule[0]['logo'].name, schedule[1]['logo']), ('Будители', autumn))
        self.assertContains(response, 'Честит 1 ноември!')

    def test_pages_render_records_without_an_image(self):
        logo = self.make_logo('Без лого', (1, 11), (1, 11), image='')

        response = self.client.get(reverse('admin:blog_headerlogo_changelist'))
        self.assertContains(response, 'няма')
        self.assertEqual(self.client.get(reverse('admin:blog_headerlogo_change', args=[logo.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse('admin:blog_headerlogo_add')).status_code, 200)
        response = self.client.get(reverse('admin:blog_headerlogo_preview'), {'date': '2026-11-01'})
        self.assertContains(response, 'Няма качено лого')

    def test_preview_falls_back_to_today_on_bad_date(self):
        response = self.client.get(reverse('admin:blog_headerlogo_preview'), {'date': 'nope'})
        self.assertEqual(response.status_code, 200)


class SeededHeaderLogoAdminTests(TestCase):
    def test_admin_pages_render_with_the_seeded_holidays(self):
        self.assertEqual(HeaderLogo.objects.filter(image='').count(), 22)
        self.client.force_login(User.objects.create_superuser('seed-admin', 'seed@example.com', 'test-password'))

        self.assertEqual(self.client.get(reverse('admin:blog_headerlogo_changelist')).status_code, 200)
        for day in ('2026-11-01', '2027-05-02', '2026-12-31'):
            with self.subTest(day=day):
                response = self.client.get(reverse('admin:blog_headerlogo_preview'), {'date': day})
                self.assertEqual(response.status_code, 200)
                self.assertIsNone(response.context['winner'])
        for logo in HeaderLogo.objects.all():
            with self.subTest(logo=logo.name):
                url = reverse('admin:blog_headerlogo_change', args=[logo.pk])
                self.assertEqual(self.client.get(url).status_code, 200)
