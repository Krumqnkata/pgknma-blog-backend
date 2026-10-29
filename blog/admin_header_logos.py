"""Admin for seasonal and holiday header logos."""

from datetime import date, timedelta

from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.template.response import TemplateResponse
from django.urls import path
from django.utils import timezone
from django.utils.html import format_html
from unfold.admin import ModelAdmin

from .header_logos import header_logo_payload, logo_period_on, logo_periods, logos_on, orthodox_easter
from .models import HeaderLogo

MONTHS = ('ян', 'фев', 'мар', 'апр', 'май', 'юни', 'юли', 'авг', 'сеп', 'окт', 'ное', 'дек')
SCHEDULE_DAYS = 365


def short_date(day):
    return f'{day.day} {MONTHS[day.month - 1]}'


def period_label(logo):
    if logo.date_mode == HeaderLogo.DateMode.ONCE:
        if logo.start_date and logo.end_date:
            return f'{logo.start_date:%d.%m.%Y} – {logo.end_date:%d.%m.%Y}'
        return '—'
    if logo.date_mode == HeaderLogo.DateMode.EASTER:
        start, end = logo.easter_offset_start, logo.easter_offset_end
        offsets = 'Великден' if start == end == 0 else f'Великден {start:+d} … {end:+d} дни'
        year = timezone.localdate().year
        easter = orthodox_easter(year)
        return f'{offsets} (през {year}: {short_date(easter + timedelta(days=start))} – {short_date(easter + timedelta(days=end))})'
    if None in (logo.start_month, logo.start_day, logo.end_month, logo.end_day):
        return '—'
    start = f'{logo.start_day} {MONTHS[logo.start_month - 1]}'
    end = f'{logo.end_day} {MONTHS[logo.end_month - 1]}'
    return start if start == end else f'{start} – {end}'


def overlapping_logos(logo, today):
    """Active same-kind, same-priority logos that share a day with ``logo``."""
    others = (
        HeaderLogo.objects.filter(is_active=True, kind=logo.kind, priority=logo.priority)
        .exclude(pk=logo.pk)
        .exclude(image='')
    )
    clashes = []
    for other in others:
        for start, end in logo_periods(logo, today):
            if end < today - timedelta(days=SCHEDULE_DAYS) or start > today + timedelta(days=2 * SCHEDULE_DAYS):
                continue
            day = start
            while day <= end and not logo_period_on(other, day):
                day += timedelta(days=1)
            if day <= end:
                clashes.append(other)
                break
    return clashes


def build_schedule(first_day, logos):
    """Collapse the daily winners for the next year into readable ranges."""
    schedule = []
    for offset in range(SCHEDULE_DAYS):
        day = first_day + timedelta(days=offset)
        matches = logos_on(day, logos)
        winner = matches[0] if matches else None
        if schedule and schedule[-1]['logo'] == winner:
            schedule[-1]['end'] = day
        else:
            schedule.append({'logo': winner, 'start': day, 'end': day})
    return schedule


@admin.register(HeaderLogo)
class HeaderLogoAdmin(ModelAdmin):
    change_list_template = 'admin/headerlogo/change_list.html'
    _current_pk = None
    list_display = ('thumbnail', 'name', 'kind', 'period_display', 'effect', 'is_active', 'showing_now')
    list_display_links = ('thumbnail', 'name')
    list_filter = ('kind', 'is_active', 'date_mode', 'effect')
    search_fields = ('name', 'greeting')
    readonly_fields = ('header_preview',)
    actions = ('activate', 'deactivate')
    fieldsets = (
        (None, {'fields': ('name', 'kind', 'is_active', 'priority')}),
        ('Лого и поздрав', {'fields': ('image', 'header_preview', 'static_image', 'greeting', 'alt_text', 'effect')}),
        ('Период', {
            'description': 'Попълнете само полетата за избрания начин на задаване.',
            'fields': (
                'date_mode',
                ('start_day', 'start_month'),
                ('end_day', 'end_month'),
                ('easter_offset_start', 'easter_offset_end'),
                ('start_date', 'end_date'),
            ),
        }),
    )

    @admin.display(description='Лого')
    def thumbnail(self, obj):
        if not obj.image:
            return format_html('<span style="color:#9ca3af">{}</span>', 'няма')
        return format_html(
            '<span style="display:inline-flex;padding:4px;border-radius:8px;background:hsl(174 72% 35%)">'
            '<img src="{}" alt="" width="32" height="32" loading="lazy" style="object-fit:contain"></span>',
            obj.image.url,
        )

    @admin.display(description='Период')
    def period_display(self, obj):
        return period_label(obj)

    @admin.display(description='Сега', boolean=True)
    def showing_now(self, obj):
        return obj.pk == self._current_pk

    @admin.display(description='Изглед в хедъра')
    def header_preview(self, obj):
        if not obj or not obj.image:
            return 'Качете лого и запазете, за да видите как изглежда.'
        return format_html(
            '<div style="display:inline-flex;align-items:center;gap:12px;padding:10px 16px;border-radius:12px;'
            'background:hsl(174 72% 35%);color:#fff;font-weight:700;letter-spacing:.12em;text-transform:uppercase">'
            '<img src="{}" alt="" style="width:40px;height:40px;object-fit:contain">'
            '<span>ПГКНМА Блог<small style="display:block;font-size:11px;font-weight:500;letter-spacing:0;text-transform:none;opacity:.85">{}</small></span>'
            '</div>',
            obj.image.url,
            obj.greeting,
        )

    def changelist_view(self, request, extra_context=None):
        matches = logos_on(timezone.localdate())
        self._current_pk = matches[0].pk if matches else None
        return super().changelist_view(request, extra_context)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not obj.is_active or not obj.image:
            if not obj.image:
                self.message_user(request, f'„{obj}“ няма качено лого и няма да се показва.', messages.WARNING)
            return
        clashes = overlapping_logos(obj, timezone.localdate())
        if clashes:
            names = ', '.join(f'„{other}“' for other in clashes)
            self.message_user(
                request,
                f'Периодът се засича с {names} (същия тип и приоритет). Ще се показва по-краткият период – '
                'ако искате друго, сменете приоритета.',
                messages.WARNING,
            )

    @admin.action(description='Активирай избраните', permissions=('change',))
    def activate(self, request, queryset):
        for logo in queryset:
            logo.is_active = True
            logo.save(update_fields=['is_active', 'updated_at'])
        self.message_user(request, f'Активирани: {queryset.count()}.', messages.SUCCESS)

    @admin.action(description='Деактивирай избраните', permissions=('change',))
    def deactivate(self, request, queryset):
        for logo in queryset:
            logo.is_active = False
            logo.save(update_fields=['is_active', 'updated_at'])
        self.message_user(request, f'Деактивирани: {queryset.count()}.', messages.SUCCESS)

    def get_urls(self):
        opts = self.model._meta
        return [
            path(
                'preview/',
                self.admin_site.admin_view(self.preview_view),
                name=f'{opts.app_label}_{opts.model_name}_preview',
            ),
        ] + super().get_urls()

    def preview_view(self, request):
        if not self.has_view_permission(request):
            raise PermissionDenied
        today = timezone.localdate()
        try:
            selected = date.fromisoformat(request.GET.get('date', ''))
        except ValueError:
            selected = today

        covering = []
        visible = logos_on(selected)
        for logo in HeaderLogo.objects.all():
            if not logo_period_on(logo, selected):
                continue
            if not logo.is_active:
                status = 'Изключено'
            elif not logo.image:
                status = 'Няма качено лого'
            elif visible and logo == visible[0]:
                status = 'Показва се'
            else:
                status = 'Засенчено от по-важен запис'
            covering.append({'logo': logo, 'status': status, 'period': period_label(logo)})
        covering.sort(key=lambda row: row['status'] != 'Показва се')

        active = list(HeaderLogo.objects.filter(is_active=True).exclude(image=''))
        context = {
            **self.admin_site.each_context(request),
            'opts': self.model._meta,
            'title': 'Преглед на логото по дата',
            'selected': selected,
            'today': today,
            'winner': visible[0] if visible else None,
            'payload': header_logo_payload(visible[0]) if visible else None,
            'covering': covering,
            'schedule': build_schedule(selected, active),
        }
        return TemplateResponse(request, 'admin/headerlogo/preview.html', context)
