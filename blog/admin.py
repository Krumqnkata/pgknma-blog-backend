from blog.models import Posts, Category, UserProfile, Comments, PollQuestion, PollOption, PollAnswer, ContactSubmission, Notification, GamificationNotification, NotificationPreference, NotificationReadState, PushSubscription, PushDelivery, PageView, TermsOfService, Event, PostImage, BellSongSuggestion, PrivacyPolicy, MemeOfWeek, Cookie, SiteSettings, PostDocument, Changelog, ScheduleEntry, ScheduleImport, BlogLoginSession, PersonalDataCleanupRun, Badge, DigitalReward, GamificationLevel, GamificationPointRule, Mission, MissionActivity, PointTransaction, UserBadge, UserDigitalReward, UserMissionProgress, calculate_file_sha256
from django.contrib import admin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django import forms
from django.forms.models import BaseInlineFormSet
from django.http import Http404, HttpResponseNotAllowed
from django.urls import path, reverse
from django.utils.html import format_html
from django.utils.text import Truncator
from django.utils import timezone
import re
from unfold_markdown.widgets import MarkdownWidget
from unfold.widgets import UnfoldBooleanSwitchWidget
from .forms import (
    MemeSelectionForm,
    BadgeAdminForm,
    DigitalRewardAdminForm,
    MissionAdminForm,
    PollQuestionSelectionForm,
    PostAdminForm,
    SongSuggestionSelectionForm,
    ScheduleCsvImportForm,
)
from unfold.admin import ModelAdmin, StackedInline, TabularInline
from django.contrib.auth.models import User, Group
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from unfold.forms import UserChangeForm, UserCreationForm
from django.contrib import messages
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from io import StringIO
import csv
from .session_auth import revoke_login_session
from .avatars import reset_profile_avatar
from .gamification.services import award_badge, change_points, evaluate_badge_for_all_users
from .admin_roles import ADMIN_ROLE_BY_NAME

try:
    admin.site.unregister(Group)
except admin.sites.NotRegistered:
    pass


class DateSelectionForm(forms.Form):
    delete_before_date = forms.DateField(
        label="Изтрий записите преди:",
        widget=forms.DateInput(attrs={'type': 'date'})
    )


class PointAdjustmentForm(forms.Form):
    points = forms.IntegerField(
        label='Промяна в точки',
        min_value=-10000,
        max_value=10000,
        help_text='Използвайте отрицателна стойност за отнемане.',
    )
    reason = forms.CharField(
        label='Причина',
        max_length=255,
        widget=forms.Textarea(attrs={'rows': 3}),
    )


class BadgeAwardForm(forms.Form):
    badge = forms.ModelChoiceField(
        label='Значка',
        queryset=Badge.objects.filter(active=True).order_by('title'),
    )
    reason = forms.CharField(
        label='Причина',
        max_length=255,
        required=False,
    )


class BlogSessionStatusFilter(admin.SimpleListFilter):
    title = 'Статус'
    parameter_name = 'session_status'

    def lookups(self, request, model_admin):
        return (
            ('active', 'Активна'),
            ('revoked', 'Прекратена'),
            ('expired', 'Изтекла'),
        )

    def queryset(self, request, queryset):
        now = timezone.now()
        if self.value() == 'active':
            return queryset.filter(revoked_at__isnull=True, expires_at__gt=now)
        if self.value() == 'revoked':
            return queryset.filter(revoked_at__isnull=False)
        if self.value() == 'expired':
            return queryset.filter(revoked_at__isnull=True, expires_at__lte=now)
        return queryset


@admin.register(BlogLoginSession)
class BlogLoginSessionAdmin(ModelAdmin):
    list_display = (
        'user',
        'auth_method',
        'session_status',
        'ip_address',
        'network_operator',
        'device_label',
        'created_at',
        'last_seen_at',
    )
    list_filter = (
        BlogSessionStatusFilter,
        'auth_method',
        'created_at',
    )
    search_fields = (
        'user__username',
        'user__email',
        'ip_address',
        'network_operator',
    )
    ordering = ('-created_at',)
    readonly_fields = (
        'id',
        'user',
        'auth_method',
        'ip_address',
        'network_operator',
        'network_asn',
        'device_label',
        'created_at',
        'last_seen_at',
        'expires_at',
        'revoked_at',
        'revoked_reason',
    )
    exclude = ('refresh_jti',)
    actions = ('revoke_selected_sessions',)

    @admin.display(description='Статус')
    def session_status(self, obj):
        if obj.revoked_at is not None:
            return 'Прекратена'
        if obj.expires_at <= timezone.now():
            return 'Изтекла'
        return 'Активна'

    @admin.action(
        description='Прекрати избраните сесии',
        permissions=('change',),
    )
    def revoke_selected_sessions(self, request, queryset):
        if not request.user.is_superuser:
            raise PermissionDenied
        changed = 0
        with transaction.atomic():
            sessions = BlogLoginSession.objects.select_for_update().filter(
                pk__in=queryset.values('pk')
            )
            for login_session in sessions:
                if revoke_login_session(
                    login_session,
                    BlogLoginSession.REVOKED_ADMIN,
                ):
                    changed += 1
        self.message_user(
            request,
            f'Прекратени сесии: {changed}.',
            messages.SUCCESS,
        )

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not request.user.is_superuser:
            actions.pop('revoke_selected_sessions', None)
        return actions

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PersonalDataCleanupRun)
class PersonalDataCleanupRunAdmin(ModelAdmin):
    list_display = (
        'started_at',
        'status',
        'dry_run',
        'retention_days',
        'affected_records_display',
        'finished_at',
        'error_preview',
    )
    list_filter = ('status', 'dry_run', 'started_at')
    ordering = ('-started_at',)
    date_hierarchy = 'started_at'
    readonly_fields = (
        'id',
        'started_at',
        'finished_at',
        'retention_days',
        'cutoff_at',
        'dry_run',
        'status',
        'affected_records_display',
        'result_summary',
        'result_counts',
        'error_message',
    )
    fields = readonly_fields

    RESULT_LABELS = {
        'consent_records': 'Съгласия за бисквитки',
        'blog_login_sessions': 'Сесии в блога',
        'valentine_sessions': 'Сесии във валентинките',
        'valentine_login_attempts': 'Опити за вход във валентинките',
        'expired_django_sessions': 'Изтекли Django сесии',
        'expired_jwt_tokens': 'Изтекли JWT токени',
    }

    @admin.display(description='Общо записи')
    def affected_records_display(self, obj):
        return obj.affected_records

    @admin.display(description='Резултат')
    def result_summary(self, obj):
        if not obj.result_counts:
            return '—'
        return '\n'.join(
            f'{self.RESULT_LABELS.get(key, key)}: {value}'
            for key, value in sorted(obj.result_counts.items())
        )

    @admin.display(description='Грешка')
    def error_preview(self, obj):
        return Truncator(obj.error_message).chars(80) if obj.error_message else '—'

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ScheduleEntry)
class ScheduleEntryAdmin(ModelAdmin):
    change_list_template = 'admin/scheduleentry/change_list.html'
    list_display = ('class_name', 'lesson_number', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday')
    list_filter = ('class_name',)
    search_fields = ('class_name', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday')
    ordering = ('class_name', 'lesson_number')

    def get_urls(self):
        opts = self.model._meta
        custom_urls = [
            path(
                'import-csv/',
                self.admin_site.admin_view(self.import_csv_view),
                name=f'{opts.app_label}_{opts.model_name}_import_csv',
            ),
        ]
        return custom_urls + super().get_urls()

    def import_csv_view(self, request):
        if not self.has_change_permission(request):
            raise PermissionDenied

        if request.method == 'POST':
            form = ScheduleCsvImportForm(request.POST, request.FILES)
            if form.is_valid():
                entries = form.entries
                with transaction.atomic():
                    self._create_current_snapshot_if_missing(request)
                    previous_count = ScheduleEntry.objects.count()
                    ScheduleImport.objects.filter(is_current=True).update(is_current=False)
                    ScheduleImport.objects.create(
                        source_file=form.cleaned_data['csv_file'],
                        imported_by=request.user,
                        entry_count=len(entries),
                        is_current=True,
                    )
                    ScheduleEntry.objects.all().delete()
                    ScheduleEntry.objects.bulk_create(entries)
                self.message_user(
                    request,
                    f'Програмата е обновена: добавени са {len(entries)} реда и са заменени {previous_count} стари реда.',
                    messages.SUCCESS,
                )
                return redirect('admin:blog_scheduleentry_changelist')
        else:
            form = ScheduleCsvImportForm()

        context = {
            **self.admin_site.each_context(request),
            'opts': self.model._meta,
            'title': 'Импортиране на учебна програма от CSV',
            'form': form,
        }
        return TemplateResponse(request, 'admin/scheduleentry/import_csv.html', context)

    def _create_current_snapshot_if_missing(self, request):
        """Preserve the schedule that existed before history support was enabled."""
        if ScheduleImport.objects.filter(is_current=True).exists():
            return

        existing_entries = list(ScheduleEntry.objects.all())
        if not existing_entries:
            return

        csv_buffer = StringIO()
        writer = csv.writer(csv_buffer, delimiter=';')
        writer.writerow(['Клас', 'Час', 'Понеделник', 'Вторник', 'Сряда', 'Четвъртък', 'Петък'])
        for entry in existing_entries:
            writer.writerow([
                entry.class_name,
                entry.lesson_number,
                entry.monday,
                entry.tuesday,
                entry.wednesday,
                entry.thursday,
                entry.friday,
            ])

        snapshot = ScheduleImport(
            imported_by=request.user,
            entry_count=len(existing_entries),
            is_current=True,
        )
        snapshot.source_file.save(
            'schedule_before_history.csv',
            ContentFile(csv_buffer.getvalue().encode('utf-8-sig')),
            save=False,
        )
        snapshot.save()


@admin.register(ScheduleImport)
class ScheduleImportAdmin(ModelAdmin):
    list_display = ('__str__', 'imported_by', 'entry_count', 'is_current', 'restore_link')
    list_filter = ('is_current', 'imported_at')
    readonly_fields = ('source_file', 'imported_at', 'imported_by', 'entry_count', 'is_current')
    ordering = ('-imported_at',)

    def get_urls(self):
        opts = self.model._meta
        custom_urls = [
            path(
                '<int:pk>/restore/',
                self.admin_site.admin_view(self.restore_view),
                name=f'{opts.app_label}_{opts.model_name}_restore',
            ),
        ]
        return custom_urls + super().get_urls()

    @admin.display(description='Действие')
    def restore_link(self, obj):
        if obj.is_current:
            return 'Текуща версия'
        url = reverse('admin:blog_scheduleimport_restore', args=(obj.pk,))
        return format_html('<a class="button" href="{}">Върни тази версия</a>', url)

    def restore_view(self, request, pk):
        schedule_import = self.get_object(request, pk)
        if schedule_import is None:
            raise Http404
        if not self.has_change_permission(request, schedule_import):
            raise PermissionDenied

        if request.method == 'POST':
            with schedule_import.source_file.open('rb') as source_file:
                uploaded_file = SimpleUploadedFile(
                    schedule_import.source_file.name.rsplit('/', 1)[-1],
                    source_file.read(),
                    content_type='text/csv',
                )
            form = ScheduleCsvImportForm({}, {'csv_file': uploaded_file})
            if not form.is_valid():
                self.message_user(
                    request,
                    'Версията не може да бъде възстановена, защото CSV файлът е невалиден: '
                    + '; '.join(form.errors.get('csv_file', [])),
                    messages.ERROR,
                )
                return redirect('admin:blog_scheduleimport_changelist')

            with transaction.atomic():
                ScheduleImport.objects.filter(is_current=True).update(is_current=False)
                schedule_import.is_current = True
                schedule_import.save(update_fields=['is_current'])
                ScheduleEntry.objects.all().delete()
                ScheduleEntry.objects.bulk_create(form.entries)
            self.message_user(request, 'Старата версия на програмата е възстановена успешно.', messages.SUCCESS)
            return redirect('admin:blog_scheduleentry_changelist')

        context = {
            **self.admin_site.each_context(request),
            'opts': self.model._meta,
            'title': 'Възстановяване на учебна програма',
            'schedule_import': schedule_import,
        }
        return TemplateResponse(request, 'admin/scheduleimport/restore_confirm.html', context)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

# 2. Регистрираме Group наново, но с Unfold класа
@admin.register(Group)
class GroupAdmin(BaseGroupAdmin, ModelAdmin):
    list_display = ('name', 'role_description', 'permission_count', 'member_count')
    search_fields = ('name',)
    filter_horizontal = ("permissions",)
    fieldsets = (
        (None, {"fields": ("name",)}),
        ("Права", {
            "fields": ("permissions",),
            "classes": ["permissions-box"],
        }),
    )

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related('permissions', 'user_set')

    @admin.display(description='Предназначение')
    def role_description(self, obj):
        role = ADMIN_ROLE_BY_NAME.get(obj.name)
        return role.description if role else 'Персонализирана роля'

    @admin.display(description='Права')
    def permission_count(self, obj):
        return len(obj.permissions.all())

    @admin.display(description='Потребители')
    def member_count(self, obj):
        return len(obj.user_set.all())

    def has_add_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser


admin.site.unregister(User)


@admin.register(UserProfile)
class UserProfileAdmin(ModelAdmin):
    list_display = (
        'user',
        'gamification_points',
        'public_profile_enabled',
        'email_verified',
        'avatar_preview',
        'avatar_source',
        'avatar_preset',
        'avatar_updated_at',
    )
    list_filter = (
        'public_profile_enabled', 'show_email_on_profile',
        'show_comments_on_profile', 'leaderboard_opt_in', 'email_verified', 'avatar_source',
        'avatar_updated_at',
    )
    search_fields = ('user__username', 'user__email')
    ordering = ('-gamification_points', 'user__username')
    readonly_fields = (
        'user',
        'avatar_preview',
        'avatar_source',
        'avatar_preset',
        'avatar_user_selected',
        'avatar_updated_at',
        'email_verified',
        'email_verified_at',
        'gamification_points',
        'leaderboard_opt_in',
    )
    exclude = ('avatar',)
    actions = ('reset_selected_avatars', 'adjust_points', 'award_selected_badge')

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('user')

    def get_readonly_fields(self, request, obj=None):
        fields = set(super().get_readonly_fields(request, obj))
        if not request.user.is_superuser:
            fields.update(field.name for field in self.model._meta.fields)
        return tuple(fields)

    def get_search_fields(self, request):
        if not request.user.is_superuser:
            return ('user__username',)
        return super().get_search_fields(request)

    def _gamification_action_response(self, request, queryset, form, title, action):
        return TemplateResponse(
            request,
            'admin/gamification_action.html',
            {
                'form': form,
                'title': title,
                'queryset': queryset,
                'action': action,
                'opts': self.model._meta,
                'selected_count': queryset.count(),
            },
        )

    @admin.action(
        description='Дай или отнеми точки с причина',
        permissions=('change',),
    )
    def adjust_points(self, request, queryset):
        if not self.has_change_permission(request):
            raise PermissionDenied
        if 'apply' in request.POST:
            form = PointAdjustmentForm(request.POST)
            if form.is_valid():
                changed = 0
                with transaction.atomic():
                    for profile in queryset.select_for_update().select_related('user'):
                        point_transaction, created = change_points(
                            user=profile.user,
                            points=form.cleaned_data['points'],
                            action='admin_adjustment',
                            description=form.cleaned_data['reason'],
                            source=PointTransaction.Source.ADMIN,
                            created_by=request.user,
                            evaluate_badges=False,
                        )
                        changed += int(created)
                self.message_user(
                    request,
                    f'Коригирани профили: {changed}.',
                    messages.SUCCESS,
                )
                return redirect(request.get_full_path())
        else:
            form = PointAdjustmentForm()
        return self._gamification_action_response(
            request, queryset, form, 'Корекция на точки', 'adjust_points'
        )

    @admin.action(
        description='Дай значка на избраните потребители',
        permissions=('change',),
    )
    def award_selected_badge(self, request, queryset):
        if not self.has_change_permission(request):
            raise PermissionDenied
        if 'apply' in request.POST:
            form = BadgeAwardForm(request.POST)
            if form.is_valid():
                awarded = 0
                for profile in queryset.select_related('user'):
                    _, created = award_badge(
                        profile.user,
                        form.cleaned_data['badge'],
                        awarded_by=request.user,
                        reason=form.cleaned_data['reason'],
                    )
                    awarded += int(created)
                self.message_user(
                    request,
                    f'Дадени нови значки: {awarded}.',
                    messages.SUCCESS,
                )
                return redirect(request.get_full_path())
        else:
            form = BadgeAwardForm()
        return self._gamification_action_response(
            request, queryset, form, 'Даване на значка', 'award_selected_badge'
        )

    @admin.display(description='Преглед')
    def avatar_preview(self, obj):
        if obj.avatar_url:
            return format_html(
                '<img src="{}" alt="" width="56" height="56" '
                'style="border-radius:50%;object-fit:cover;" />',
                obj.avatar_url,
            )
        initial = (obj.user.username or '?')[:1].upper()
        return format_html(
            '<span style="display:inline-flex;width:56px;height:56px;'
            'align-items:center;justify-content:center;border-radius:50%;'
            'background:#e2e8f0;color:#334155;font-weight:700;">{}</span>',
            initial,
        )

    @admin.action(
        description='Върни избраните профили към инициали',
        permissions=('change',),
    )
    def reset_selected_avatars(self, request, queryset):
        if not self.has_change_permission(request):
            raise PermissionDenied
        changed = 0
        with transaction.atomic():
            profiles = UserProfile.objects.select_for_update().filter(
                pk__in=queryset.values('pk')
            )
            for profile in profiles:
                reset_profile_avatar(profile, user_selected=True)
                self.log_change(
                    request,
                    profile,
                    'Профилната снимка е върната към инициали.',
                )
                changed += 1
        self.message_user(
            request,
            f'Нулирани профилни снимки: {changed}.',
            messages.SUCCESS,
        )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    list_display = (
        'username', 'email', 'first_name', 'last_name', 'assigned_roles',
        'is_staff', 'last_login', 'date_joined',
    )
    ordering = ('-last_login',)  # По подразбиране — последно логвалите се първи
    list_filter = ('is_staff', 'is_superuser', 'is_active', 'groups', 'last_login')
    search_fields = ('username', 'first_name', 'last_name', 'email', 'groups__name')

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related('groups')

    def get_list_display(self, request):
        if not request.user.is_superuser:
            return (
                'username', 'first_name', 'last_name', 'is_active', 'last_login',
            )
        return super().get_list_display(request)

    def get_list_filter(self, request):
        if not request.user.is_superuser:
            return ('is_active', 'last_login')
        return super().get_list_filter(request)

    def get_search_fields(self, request):
        if not request.user.is_superuser:
            return ('username', 'first_name', 'last_name')
        return super().get_search_fields(request)

    @admin.display(description='Роли')
    def assigned_roles(self, obj):
        names = [group.name for group in obj.groups.all()]
        return ', '.join(names) if names else 'Без роля'

    def get_fieldsets(self, request, obj=None):
        if not request.user.is_superuser and obj is not None:
            return (
                (None, {
                    'fields': (
                        'username', 'first_name', 'last_name', 'is_active',
                        'last_login', 'date_joined',
                    ),
                }),
            )
        return super().get_fieldsets(request, obj)

    def has_add_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser


class PostImageInlineFormSet(BaseInlineFormSet):
    """Validate gallery-wide rules before any inline image is saved."""

    def clean(self):
        super().clean()
        if any(self.errors):
            return

        featured_count = 0
        known_hashes = {}
        for form in self.forms:
            cleaned_data = getattr(form, 'cleaned_data', {})
            if not cleaned_data or cleaned_data.get('DELETE'):
                continue

            if cleaned_data.get('is_featured'):
                featured_count += 1

            image = cleaned_data.get('image')
            if not image:
                continue
            digest = form.instance.file_hash
            if not digest or 'image' in form.changed_data:
                try:
                    digest = calculate_file_sha256(image)
                except (FileNotFoundError, OSError, ValueError):
                    continue
                image._post_image_sha256 = digest
                uploaded_file = getattr(image, 'file', None)
                if uploaded_file is not None:
                    uploaded_file._post_image_sha256 = digest
                form.instance.file_hash = digest
                if form.instance.pk and not form.changed_data:
                    form.instance._gallery_hash_needs_backfill = True

            duplicate_name = known_hashes.get(digest)
            if duplicate_name:
                raise ValidationError(
                    'Галерията съдържа дублирани снимки: '
                    f'„{duplicate_name}“ и „{image.name.rsplit("/", 1)[-1]}“.'
                )
            known_hashes[digest] = image.name.rsplit('/', 1)[-1]

        if featured_count > 1:
            raise ValidationError(
                'Изберете само една водеща снимка за галерията.'
            )

    def save(self, commit=True):
        instances = super().save(commit=commit)
        if commit:
            for form in self.initial_forms:
                instance = form.instance
                if getattr(instance, '_gallery_hash_needs_backfill', False):
                    PostImage.objects.filter(
                        pk=instance.pk,
                        file_hash__isnull=True,
                    ).update(file_hash=instance.file_hash)
        return instances


class PostImageInline(StackedInline):
    model = PostImage
    formset = PostImageInlineFormSet
    extra = 0
    fields = (
        'image_preview',
        'image',
        'title',
        'alt_text',
        'is_featured',
        'focus_x',
        'focus_y',
        'position',
    )
    readonly_fields = ('image_preview',)
    ordering_field = 'position'
    hide_ordering_field = True
    # Unfold keeps the drag handle in the header while the detailed fields stay
    # collapsed.  An inline with validation errors opens automatically.
    collapsible = True
    show_change_link = True
    verbose_name = 'Снимка в галерията'
    verbose_name_plural = (
        'Галерия — влачете дръжката, за да промените реда; натиснете заглавието за детайли'
    )

    @admin.display(description='Преглед на кадрирането')
    def image_preview(self, obj):
        if not obj or not obj.image:
            return 'Ще се появи след записване.'
        return format_html(
            '<img src="{}" alt="{}" '
            'style="width:220px;height:150px;object-fit:cover;'
            'object-position:{}% {}%;border-radius:10px;" />',
            obj.image.url,
            obj.alt_text or obj.title or '',
            obj.focus_x,
            obj.focus_y,
        )

@admin.register(Posts)
class PostsAdmin(ModelAdmin):
    form = PostAdminForm
    inlines = (PostImageInline,)
    readonly_fields_base = ('author', 'created_at')
    list_display = ('title', 'author', 'publication_status', 'published_at', 'created_at')
    list_filter = ('published', 'allowed', 'category', 'published_at', 'created_at')
    search_fields = ('title', 'content')
    exclude = ('author',)

    @admin.display(description='Статус', ordering='published_at')
    def publication_status(self, obj):
        if not obj.published:
            return 'Чернова'
        if not obj.allowed:
            return 'Чака одобрение'
        if obj.published_at and obj.published_at > timezone.now():
            return f'Планирана за {obj.published_at.strftime("%d.%m.%Y %H:%M")}'
        return 'Публикувана'

    # V V V ДОБАВЯНЕ НА MARKDOWN ВИДЖЕТ V V V
    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == "content":
            kwargs["widget"] = MarkdownWidget(attrs={"rows": 25})
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    # V V V КОРЕКЦИЯ НА READONLY_FIELDS V V V
    def get_readonly_fields(self, request, obj=None):
        user = request.user
        base_fields = list(self.readonly_fields_base)
        if not user.has_perm('blog.can_allow_posts'):
            base_fields.append('allowed')
        return tuple(base_fields)

    def save_model(self, request, obj, form, change):
        documents_to_delete = form.cleaned_data.get('delete_documents') or []
        if documents_to_delete and not request.user.has_perm('blog.delete_postdocument'):
            raise PermissionDenied

        is_new_post = obj.pk is None
        pending_banner = obj.banner if is_new_post and obj.banner else None

        # ImageField calculates upload_to before an AutoField primary key is
        # assigned.  Save a new post without the banner first, then commit the
        # banner below so it always lands in posts/<post id>/banner/.
        if pending_banner is not None:
            obj.banner = None

        if is_new_post:
            obj.author = request.user
        super().save_model(request, obj, form, change)

        if pending_banner is not None:
            obj.banner = pending_banner
            obj.save(update_fields=('banner',))

        # Use the form's cleaned values so image/content and size validation
        # cannot be bypassed by reading request.FILES directly.
        images = form.cleaned_data.get('gallery_images') or []
        if not isinstance(images, (list, tuple)):
            images = [images]
        last_position = obj.images.aggregate(
            max_position=models.Max('position')
        )['max_position']
        next_position = 0 if last_position is None else last_position + 1
        has_featured_image = obj.images.filter(is_featured=True).exists()
        for offset, image in enumerate(images):
            PostImage.objects.create(
                post=obj,
                image=image,
                position=next_position + offset,
                is_featured=not has_featured_image and offset == 0,
                file_hash=getattr(image, '_post_image_sha256', None),
            )

        # Handle multiple document uploads
        documents = form.cleaned_data.get('gallery_documents') or []
        if not isinstance(documents, (list, tuple)):
            documents = [documents]
        for document in documents:
            PostDocument.objects.create(post=obj, file=document)

        # Handle document deletion
        if documents_to_delete:
            PostDocument.objects.filter(pk__in=documents_to_delete, post=obj).delete()


    # V V V КОРЕКЦИЯ НА GET_QUERYSET V V V
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        user = request.user
        if user.is_superuser:
            return qs
        if user.has_perm('blog.can_view_all_pnp_posts'):
            return qs
        if user.has_perm('blog.can_view_all_posts'):
            return qs.filter(published=True)
        if user.has_perm('blog.view_only_posts'):
            return qs.filter(author=user)
        return qs.filter(author=user, published=True)

    def has_change_permission(self, request, obj=None):
        if not super().has_change_permission(request, obj):
            return False
        if obj is None or request.user.is_superuser or obj.author_id == request.user.pk:
            return True
        return request.user.has_perm('blog.can_edit_users_post')

    def has_delete_permission(self, request, obj=None):
        if not super().has_delete_permission(request, obj):
            return False
        if obj is None or request.user.is_superuser or obj.author_id == request.user.pk:
            return True
        return request.user.has_perm('blog.can_edit_users_post')

@admin.register(Category)
class CategoryAdmin(ModelAdmin):
    list_display = ('full_name', 'short_name')
    search_fields = ('full_name',)

@admin.register(Comments)
class CommentsAdmin(ModelAdmin):
    readonly_fields = ('user', 'content', 'created_at', 'post', 'parent')
    list_display = ('user', 'content', 'created_at', 'post')
    search_fields = ('user__username', 'content')
    def has_add_permission(self, request):
        return False

class PollOptionInline(admin.TabularInline):
    model = PollOption
    extra = 4
    max_num = 4
    verbose_name = "Опция"
    verbose_name_plural = "Опции (Системата автоматично ще запази само един маркиран като 'верен' отговор и максимум до 4 отговора на анкета!)"
    fields = ('key', 'text', 'image', 'image_preview', 'is_correct')
    readonly_fields = ('key', 'image_preview')

    def image_preview(self, obj):
        if obj.image:
            return format_html(
                '<img src="{}" style="max-width: 100px; max-height: 100px;" alt="" />',
                obj.image.url,
            )
        return "Няма"
    image_preview.short_description = "Преглед"

@admin.register(PollQuestion)
class PollQuestionAdmin(ModelAdmin):
    list_display = ('title', 'start_date', 'end_date', 'created_at')
    list_filter = ('start_date', 'end_date')
    search_fields = ('title', 'subtitle', 'task_description')
    inlines = [PollOptionInline]
    fields = ('title', 'subtitle', 'task_description', 'image', 'image_preview', 'start_date', 'end_date')
    readonly_fields = ('image_preview',)

    def image_preview(self, obj):
        if obj.image:
            return format_html(
                '<img src="{}" style="max-width: 200px; max-height: 200px;" alt="" />',
                obj.image.url,
            )
        return "Няма изображение"
    image_preview.short_description = "Преглед на изображението"

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == "task_description":
            kwargs["widget"] = MarkdownWidget(attrs={"rows": 10})
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    def save_formset(self, request, form, formset, change):
        # Populate deleted_objects/save_m2m without persisting partial changes.
        formset.save(commit=False)
        active_forms = [
            inline_form
            for inline_form in formset.forms
            if inline_form.cleaned_data
            and not inline_form.cleaned_data.get("DELETE")
            and (inline_form.instance.pk or inline_form.has_changed())
        ]
        letters = ("a", "b", "c", "d")

        if len(active_forms) > len(letters):
            raise ValidationError("Анкетата може да има най-много 4 опции.")

        with transaction.atomic():
            for deleted_object in formset.deleted_objects:
                deleted_object.delete()

            # Move persisted keys out of the a-d range first. This avoids a
            # transient unique(question, key) collision while gaps are closed.
            persisted_options = list(
                form.instance.options.order_by("key", "pk")
            )
            temporary_keys = ("W", "X", "Y", "Z")
            for index, option in enumerate(persisted_options):
                PollOption.objects.filter(pk=option.pk).update(
                    key=temporary_keys[index]
                )

            saved_options = []
            for index, inline_form in enumerate(active_forms):
                option = inline_form.save(commit=False)
                option.question = form.instance
                option.key = letters[index]
                option.save()
                saved_options.append(option)

            formset.save_m2m()

            # Preserve the previous behaviour: if several answers are marked,
            # the last visible one wins.
            correct_options = [
                option for option in saved_options if option.is_correct
            ]
            if len(correct_options) > 1:
                correct_option = correct_options[-1]
                form.instance.options.exclude(pk=correct_option.pk).update(
                    is_correct=False
                )

@admin.register(PollAnswer)
class PollAnswerAdmin(ModelAdmin):
    list_display = ('user', 'question', 'selected_option', 'created_at')
    list_filter = ('created_at', 'question')
    search_fields = ('user__username', 'question__title')
    readonly_fields = ('user', 'question', 'selected_option', 'created_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

@admin.register(BellSongSuggestion)
class BellSongSuggestionAdmin(ModelAdmin):
    list_display = ('title', 'user', 'slot', 'status', 'votes', 'submitted_at', 'embedded_media_display')
    list_filter = ('status', 'slot')
    search_fields = ('title', 'link', 'user__username')
    readonly_fields = ('user', 'submitted_at', 'title', 'link', 'embedded_media_display', 'votes', 'slot', 'note',)
    
    # 1. Регистрираме двата екшъна
    actions = ['approve_selected', 'reject_selected', 'delete_by_date']

    
    @admin.action(
        description='Изтрий избраните записи по дата (преди...)',
        permissions=['delete'],
    )
    def delete_by_date(self, request, queryset):
        if not self.has_delete_permission(request):
            raise PermissionDenied

        if 'apply' in request.POST:
            form = DateSelectionForm(request.POST)
            if form.is_valid():
                date = form.cleaned_data['delete_before_date']
                selected_for_deletion = queryset.filter(
                    submitted_at__date__lt=date
                )
                selected_objects = list(selected_for_deletion)

                with transaction.atomic():
                    self.log_deletions(request, selected_for_deletion)
                    selected_for_deletion.delete()
                
                self.message_user(
                    request,
                    (
                        f'Успешно бяха изтрити {len(selected_objects)} от '
                        f'избраните записи преди дата {date}.'
                    ),
                    messages.SUCCESS
                )
                return redirect(request.get_full_path())
        else:
            form = DateSelectionForm()

        # Показваме междинна страница с формата
        # Използваме 'admin/forms/default.html' или персонализиран шаблон
        return TemplateResponse(
            request,
            "admin/delete_confirmation_by_date.html", # Трябва да създадеш този файл
            {
                'form': form,
                'title': "Избор на дата за изтриване",
                'opts': self.model._meta,
                'queryset': queryset,
                'action': 'delete_by_date',
                'selected_count': queryset.count(),
            }
        )

    # 2. Функция за масово одобряване
    @admin.action(
        description='Одобри избраните предложения',
        permissions=['change'],
    )
    def approve_selected(self, request, queryset):
        if not self.has_change_permission(request):
            raise PermissionDenied
        updated = 0
        with transaction.atomic():
            for suggestion in queryset.select_for_update():
                if suggestion.status != 'approved':
                    suggestion.status = 'approved'
                    suggestion.save(update_fields=('status',))
                    updated += 1
        self.message_user(request, f'Успешно одобрихте {updated} предложения за песни.')

    # 3. Функция за масово отхвърляне
    @admin.action(
        description='Отхвърли избраните предложения',
        permissions=['change'],
    )
    def reject_selected(self, request, queryset):
        if not self.has_change_permission(request):
            raise PermissionDenied
        updated = 0
        with transaction.atomic():
            for suggestion in queryset.select_for_update():
                if suggestion.status != 'rejected':
                    suggestion.status = 'rejected'
                    suggestion.save(update_fields=('status',))
                    updated += 1
        self.message_user(request, f'Успешно отхвърлихте {updated} предложения за песни.')

    fieldsets = (
        (None, {
            'fields': ('title', 'link', 'embedded_media_display', 'user', 'submitted_at')
        }),
        ('Настройки на предложението', {
            'fields': ('slot', 'note', 'status', 'votes')
        }),
    )

    def embedded_media_display(self, obj):
        if not obj.link:
            return "Няма линк"

        youtube_match = re.search(r'(?:https?://)?(?:www\.)?(?:m\.)?(?:youtube\.com|youtu\.be)/(?:watch\?v=|embed/|v/|)([a-zA-Z0-9_-]{11})(?:\S+)?', obj.link)
        spotify_match = re.search(r'(?:https?://)?(?:www\.)?(?:open\.spotify\.com)/(?:track|episode)/([a-zA-Z0-9]{22})(?:\S+)?', obj.link)

        if youtube_match:
            video_id = youtube_match.group(1)
            youtube_direct_url = f"https://www.youtube.com/watch?v={video_id}"
            return format_html(
                '<a href="{}" target="_blank" rel="noopener noreferrer">'
                'Гледай в YouTube: {}</a>',
                youtube_direct_url,
                obj.title,
            )
        elif spotify_match:
            track_id = spotify_match.group(1)
            return format_html(
                '<iframe src="https://open.spotify.com/embed/track/{}" '
                'width="300" height="152" frameborder="0" '
                'allowtransparency="true" allow="encrypted-media" '
                'loading="lazy" title="Spotify: {}"></iframe>',
                track_id,
                obj.title,
            )
        else:
            return format_html(
                '<a href="{}" target="_blank" rel="noopener noreferrer">{}</a> '
                '(Неподдържан формат)',
                obj.link,
                obj.link,
            )

    embedded_media_display.short_description = "Медия"

    def has_add_permission(self, request):
        return False

@admin.register(Notification)
class NotificationAdmin(ModelAdmin):
    list_display = ('text', 'recipient_display', 'category', 'enabled', 'created_at')
    list_filter = ('enabled', 'audience', 'category', 'created_at')
    search_fields = ('text', 'user__username')
    readonly_fields = ('created_at',)
    fields = ('text', 'user', 'audience', 'action_url', 'enabled', 'created_at')

    def get_queryset(self, request):
        return super().get_queryset(request).exclude(
            category__startswith='gamification_',
        )

    def save_model(self, request, obj, form, change):
        obj.category = 'general'
        super().save_model(request, obj, form, change)

    @admin.display(description='Получател', ordering='user__username')
    def recipient_display(self, obj):
        if obj.user:
            return obj.user
        if obj.audience == Notification.Audience.AUTHENTICATED:
            return 'Регистрирани потребители'
        return 'Всички посетители'

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == 'text':
            kwargs['widget'] = MarkdownWidget(attrs={'rows': 3})
        return super().formfield_for_dbfield(db_field, request, **kwargs)


@admin.register(GamificationNotification)
class GamificationNotificationAdmin(ModelAdmin):
    list_display = (
        'text_preview',
        'recipient_display',
        'event_display',
        'created_at',
    )
    list_filter = ('category', 'created_at')
    search_fields = ('text', 'user__username')
    readonly_fields = (
        'text',
        'user',
        'event_display',
        'action_url',
        'enabled',
        'created_at',
    )
    fields = (
        'text',
        'user',
        'event_display',
        'action_url',
        'enabled',
        'created_at',
    )
    actions = None

    CATEGORY_LABELS = {
        'gamification_points': 'Точки',
        'gamification_level': 'Ново ниво',
        'gamification_badge': 'Значка',
        'gamification_reward': 'Дигитална награда',
        'gamification_mission': 'Мисия',
    }

    def get_queryset(self, request):
        return super().get_queryset(request).filter(
            category__startswith='gamification_',
        ).select_related('user')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return (
            request.user.has_perm('blog.view_notification')
            or super().has_view_permission(request, obj)
        )

    @admin.display(description='Съобщение')
    def text_preview(self, obj):
        return Truncator(obj.text).chars(90)

    @admin.display(description='Получател', ordering='user__username')
    def recipient_display(self, obj):
        return obj.user or 'Всички потребители'

    @admin.display(description='Събитие', ordering='category')
    def event_display(self, obj):
        return self.CATEGORY_LABELS.get(obj.category, obj.category)


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(ModelAdmin):
    list_display = (
        'user',
        'general_enabled',
        'points_enabled',
        'badges_enabled',
        'missions_enabled',
        'posts_enabled',
        'events_enabled',
        'polls_enabled',
        'updated_at',
    )
    list_filter = (
        'general_enabled',
        'points_enabled',
        'levels_enabled',
        'posts_enabled',
        'events_enabled',
        'polls_enabled',
        'badges_enabled',
        'missions_enabled',
        'rewards_enabled',
    )
    search_fields = ('user__username', 'user__email')
    readonly_fields = ('updated_at',)


@admin.register(NotificationReadState)
class NotificationReadStateAdmin(ModelAdmin):
    list_display = ('user', 'notification', 'read_at', 'archived_at')
    list_filter = ('read_at', 'archived_at')
    search_fields = ('user__username', 'notification__text')
    readonly_fields = ('user', 'notification', 'read_at', 'archived_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PushSubscription)
class PushSubscriptionAdmin(ModelAdmin):
    list_display = ('owner_display', 'browser_display', 'active', 'general_enabled', 'posts_enabled', 'events_enabled', 'polls_enabled', 'failure_count', 'last_success_at', 'updated_at')
    list_filter = ('active', 'general_enabled', 'posts_enabled', 'events_enabled', 'polls_enabled', 'updated_at', 'last_success_at')
    search_fields = ('user__username', 'user__email', 'endpoint', 'user_agent')
    readonly_fields = (
        'user', 'endpoint', 'endpoint_hash', 'management_token_hash', 'p256dh', 'auth', 'user_agent', 'active',
        'general_enabled', 'posts_enabled', 'events_enabled', 'polls_enabled',
        'failure_count', 'created_at', 'updated_at', 'last_success_at',
    )
    fields = readonly_fields

    @admin.display(description='Браузър')
    def browser_display(self, obj):
        return Truncator(obj.user_agent or 'Неизвестен браузър').chars(70)

    @admin.display(description='Собственик', ordering='user__username')
    def owner_display(self, obj):
        return obj.user or 'Анонимно устройство'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(PushDelivery)
class PushDeliveryAdmin(ModelAdmin):
    list_display = ('notification', 'subscription', 'status', 'attempts', 'last_attempt_at', 'sent_at', 'cancelled_at')
    list_filter = ('status', 'last_attempt_at', 'sent_at', 'cancelled_at')
    search_fields = ('notification__text', 'subscription__user__username', 'subscription__endpoint')
    readonly_fields = (
        'subscription', 'notification', 'status', 'attempts',
        'last_attempt_at', 'sent_at', 'cancelled_at', 'created_at',
    )
    fields = readonly_fields

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

@admin.register(PageView)
class PageViewAdmin(ModelAdmin):
    list_display = ('path', 'post', 'user', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('path', 'post__title', 'user__username')
    readonly_fields = ('path', 'post', 'user', 'created_at')
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ContactSubmission)
class ContactSubmissionAdmin(ModelAdmin):
    list_display = ('name', 'email', 'submitted_at')
    list_filter = ('submitted_at',)
    search_fields = ('name', 'email', 'message')
    readonly_fields = ('name', 'email', 'message', 'submitted_at', 'user')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

@admin.register(TermsOfService)
class TermsOfServiceAdmin(ModelAdmin):
    list_display = ('user', 'content_preview', 'date')
    list_filter = ('date',)
    search_fields = ('user__username', 'content')

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == "content":
            kwargs["widget"] = MarkdownWidget()
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        if not hasattr(obj, 'user') or not obj.user:
            obj.user = request.user
        super().save_model(request, obj, form, change)

    def content_preview(self, obj):
        if obj.content:
            return format_html("{}", Truncator(obj.content).chars(150))
        return "-"
    content_preview.short_description = 'Преглед'

    def has_delete_permission(self, request, obj=None):
        return False

@admin.register(PrivacyPolicy)
class PrivacyPolicyAdmin(ModelAdmin):
    list_display = ('user', 'content_preview', 'date')
    list_filter = ('date',)
    search_fields = ('user__username', 'content')

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == "content":
            kwargs["widget"] = MarkdownWidget()
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    def save_model(self, request, obj, form, change):
        if not hasattr(obj, 'user') or not obj.user:
            obj.user = request.user
        super().save_model(request, obj, form, change)

    def content_preview(self, obj):
        if obj.content:
            return format_html("{}", Truncator(obj.content).chars(150))
        return "-"
    content_preview.short_description = 'Преглед'

    def has_delete_permission(self, request, obj=None):
        return False

@admin.register(Event)
class EventAdmin(ModelAdmin):
    list_display = ('title', 'start_datetime', 'location', 'category', 'attendees_text', 'published', 'created_at')
    list_filter = ('published', 'category', 'start_datetime')
    search_fields = ('title', 'description', 'location', 'attendees_text')
    readonly_fields = ('created_at',)
    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == "description":
            kwargs["widget"] = MarkdownWidget(attrs={"rows": 10})
        return super().formfield_for_dbfield(db_field, request, **kwargs)

@admin.register(Changelog)
class ChangelogAdmin(ModelAdmin):
    list_display = ('__str__', 'is_active', 'created_at', 'updated_at')
    list_filter = ('is_active', 'updated_at')
    search_fields = ('content',)
    readonly_fields = ('created_at', 'updated_at')

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == "content":
            kwargs["widget"] = MarkdownWidget(attrs={"rows": 15})
        return super().formfield_for_dbfield(db_field, request, **kwargs)

@admin.register(PostImage)
class PostImageAdmin(ModelAdmin):
    list_display = (
        'image_preview',
        'title',
        'post',
        'position',
        'is_featured',
    )
    list_filter = ('is_featured', 'post')
    search_fields = ('title', 'alt_text', 'post__title')
    ordering = ('post', 'position', 'pk')
    readonly_fields = ('post', 'image_preview', 'file_hash')
    fields = (
        'post',
        'image_preview',
        'image',
        'title',
        'alt_text',
        'is_featured',
        'focus_x',
        'focus_y',
        'position',
        'file_hash',
    )

    @admin.display(description='Преглед')
    def image_preview(self, obj):
        if obj.image:
            return format_html(
                '<img src="{}" alt="{}" '
                'style="width:150px;height:110px;object-fit:cover;'
                'object-position:{}% {}%;border-radius:8px;" />',
                obj.image.url,
                obj.alt_text or obj.title or '',
                obj.focus_x,
                obj.focus_y,
            )
        return "Няма изображение"

    def has_add_permission(self, request):
        return False  # Images are created from the parent post form.

    def get_queryset(self, request):
        queryset = super().get_queryset(request).select_related('post', 'post__author')
        if request.user.is_superuser or request.user.has_perm('blog.can_view_all_pnp_posts'):
            return queryset
        return queryset.filter(post__author=request.user)

    def get_list_filter(self, request):
        if request.user.is_superuser or request.user.has_perm('blog.can_view_all_pnp_posts'):
            return super().get_list_filter(request)
        return ('is_featured',)

@admin.register(PostDocument)
class PostDocumentAdmin(ModelAdmin):
    list_display = ('post', 'get_file_name')
    list_filter = ('post',)
    search_fields = ('file', 'post__title')
    readonly_fields = ('post', 'file')

    def get_file_name(self, obj):
        if obj.file:
            return obj.file.name.split('/')[-1]
        return "N/A"
    get_file_name.short_description = 'File Name'

    def has_add_permission(self, request):
        # Documents are added via the Post admin, not directly
        return False

    def get_queryset(self, request):
        queryset = super().get_queryset(request).select_related('post', 'post__author')
        if request.user.is_superuser or request.user.has_perm('blog.can_view_all_pnp_posts'):
            return queryset
        return queryset.filter(post__author=request.user)

    def get_list_filter(self, request):
        if request.user.is_superuser or request.user.has_perm('blog.can_view_all_pnp_posts'):
            return super().get_list_filter(request)
        return ()

@admin.register(MemeOfWeek)
class MemeOfWeekAdmin(ModelAdmin):
    list_display = ('title', 'user', 'image_preview', 'status', 'votes', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('title', 'user__username')
    readonly_fields = (
        'user', 'created_at', 'image', 'image_preview', 'votes', 'title',
    )
    exclude = ('voted_by',)

    def image_preview(self, obj):
        if obj.pk and obj.image:
            return format_html(
                '<img src="{}" style="max-width: 150px; max-height: 150px;" alt="" />',
                reverse('meme-image', kwargs={'pk': obj.pk}),
            )
        return "Няма изображение"
    image_preview.short_description = "Изображение"

    def has_add_permission(self, request):
        return False  # Това ще забрани добавянето на нов мемета през админ панела


@admin.register(GamificationLevel)
class GamificationLevelAdmin(ModelAdmin):
    list_display = ('number', 'title', 'minimum_points', 'icon')
    ordering = ('minimum_points',)
    search_fields = ('title',)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Badge)
class BadgeAdmin(ModelAdmin):
    form = BadgeAdminForm
    list_display = (
        'title', 'rarity', 'active', 'hidden', 'repeatable', 'points_reward',
    )
    list_filter = ('active', 'hidden', 'repeatable', 'rarity')
    search_fields = ('title', 'slug', 'description')
    filter_horizontal = ('digital_rewards',)
    fieldsets = (
        ('Основна информация', {'fields': ('slug', 'title', 'description', 'icon', 'rarity')}),
        ('Автоматично отключване', {'fields': ('condition_type', 'condition_action', 'condition_target')}),
        ('Награди', {'fields': ('points_reward', 'digital_rewards')}),
        ('Достъпност и период', {'fields': ('active', 'hidden', 'repeatable', 'starts_at', 'ends_at')}),
    )

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        awarded = evaluate_badge_for_all_users(obj)
        if awarded:
            self.message_user(
                request,
                f'Значката беше дадена автоматично на {awarded} потребители.',
                messages.SUCCESS,
            )

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(UserBadge)
class UserBadgeAdmin(ModelAdmin):
    list_display = ('user', 'badge', 'awarded_at', 'awarded_by', 'reason')
    list_filter = ('badge', 'awarded_at')
    search_fields = ('user__username', 'badge__title', 'reason')
    readonly_fields = (
        'user', 'badge', 'award_key', 'awarded_at', 'awarded_by', 'reason',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PointTransaction)
class PointTransactionAdmin(ModelAdmin):
    list_display = (
        'user', 'points', 'balance_after', 'action', 'source', 'created_at',
    )
    list_filter = ('source', 'action', 'created_at')
    search_fields = ('user__username', 'description', 'idempotency_key')
    readonly_fields = (
        'user', 'action', 'points', 'balance_after', 'description', 'source',
        'reference_type', 'reference_id', 'idempotency_key', 'created_by',
        'reversal_of', 'metadata', 'created_at',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(GamificationPointRule)
class GamificationPointRuleAdmin(ModelAdmin):
    list_display = ('action', 'points', 'daily_limit', 'active')
    list_editable = ('points', 'daily_limit', 'active')
    list_filter = ('active',)
    ordering = ('action',)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Mission)
class MissionAdmin(ModelAdmin):
    form = MissionAdminForm
    list_display = (
        'title', 'start_date', 'end_date', 'reward_points', 'active',
    )
    list_filter = ('active', 'start_date', 'end_date')
    search_fields = ('title', 'slug', 'description')

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(UserMissionProgress)
class UserMissionProgressAdmin(ModelAdmin):
    list_display = (
        'user', 'mission', 'completed_at', 'rewarded_at', 'updated_at',
    )
    list_filter = ('mission', 'completed_at', 'rewarded_at')
    search_fields = ('user__username', 'mission__title')
    readonly_fields = (
        'user', 'mission', 'progress', 'completed_at', 'rewarded_at',
        'updated_at',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(MissionActivity)
class MissionActivityAdmin(ModelAdmin):
    list_display = ('user', 'mission', 'action', 'reference_id', 'created_at')
    list_filter = ('mission', 'action', 'created_at')
    search_fields = ('user__username', 'reference_id')
    readonly_fields = ('user', 'mission', 'action', 'reference_id', 'created_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DigitalReward)
class DigitalRewardAdmin(ModelAdmin):
    form = DigitalRewardAdminForm
    list_display = ('title', 'kind', 'key', 'active')
    list_filter = ('kind', 'active')
    search_fields = ('title', 'key')
    fieldsets = (
        ('Основна информация', {'fields': ('key', 'title', 'kind', 'active')}),
        ('Визуален дизайн', {'fields': ('visual_color', 'secondary_color', 'effect_style', 'display_text', 'display_icon')}),
    )

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(UserDigitalReward)
class UserDigitalRewardAdmin(ModelAdmin):
    list_display = ('user', 'reward', 'unlocked_at', 'source_badge', 'selected')
    list_filter = ('reward__kind', 'selected', 'unlocked_at')
    search_fields = ('user__username', 'reward__title')
    readonly_fields = ('unlocked_at',)
    autocomplete_fields = ('user', 'reward', 'source_badge')

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj is not None:
            fields.extend(('user', 'reward', 'source_badge'))
        return tuple(fields)

    def save_model(self, request, obj, form, change):
        if obj.selected:
            UserDigitalReward.objects.filter(
                user=obj.user,
                reward__kind=obj.reward.kind,
            ).exclude(pk=obj.pk).update(selected=False)
        super().save_model(request, obj, form, change)

    def has_delete_permission(self, request, obj=None):
        return False

@admin.register(Cookie.ConsentRecord)
class ConsentRecordAdmin(ModelAdmin):
    list_display = ('user_display', 'ip_address', 'network_operator', 'network_asn_display', 'consent_status', 'timestamp', 'policy_version')
    list_filter = ('consent_status', 'policy_version')
    search_fields = ('ip_address', 'network_operator', 'user__username')
    readonly_fields = ('user', 'ip_address', 'network_operator', 'network_asn', 'consent_status', 'timestamp', 'policy_version')

    def has_add_permission(self, request):
        return False # Records are created via API, not admin

    def has_change_permission(self, request, obj=None):
        return False # Records should not be changeable

    def has_delete_permission(self, request, obj=None):
        return False

    def user_display(self, obj):
        return obj.user.username if obj.user else "Анонимен"
    user_display.short_description = "Потребител"

    def network_asn_display(self, obj):
        return f"AS{obj.network_asn}" if obj.network_asn else "—"
    network_asn_display.short_description = "ASN"

@admin.register(SiteSettings)
class SiteSettingsAdmin(ModelAdmin):
    change_form_template = 'admin/site_settings_change_form.html'
    list_display = ('__str__', 'maintenance_mode', 'enable_bell_suggestions', 'enable_weekly_poll', 'enable_meme_of_the_week', 'enable_user_registration', 'auto_verify_on_email_failure', 'enable_program_page')

    class Media:
        css = {
            'all': ('admin/css/meme_deletion.css',)
        }
        js = ('admin/js/meme_deletion.js',)

    def get_urls(self):
        opts = self.model._meta
        custom_urls = [
            path(
                '<path:object_id>/delete-memes/',
                self.admin_site.admin_view(self.delete_memes_view),
                name=f'{opts.app_label}_{opts.model_name}_delete_memes',
            ),
            path(
                '<path:object_id>/delete-suggestions/',
                self.admin_site.admin_view(self.delete_suggestions_view),
                name=f'{opts.app_label}_{opts.model_name}_delete_suggestions',
            ),
            path(
                '<path:object_id>/delete-polls/',
                self.admin_site.admin_view(self.delete_polls_view),
                name=f'{opts.app_label}_{opts.model_name}_delete_polls',
            ),
        ]
        return custom_urls + super().get_urls()

    def _has_related_delete_permission(self, request, model):
        model_admin = self.admin_site._registry.get(model)
        if model_admin is not None:
            return model_admin.has_delete_permission(request)
        opts = model._meta
        return request.user.has_perm(f'{opts.app_label}.delete_{opts.model_name}')

    def _get_change_object(self, request, object_id):
        obj = self.get_object(request, object_id)
        if obj is None:
            raise Http404
        if not self.has_change_permission(request, obj):
            raise PermissionDenied
        return obj

    def _delete_selected_view(
        self,
        request,
        object_id,
        *,
        form_class,
        prefix,
        model,
        object_label,
    ):
        if request.method != 'POST':
            return HttpResponseNotAllowed(['POST'])

        obj = self._get_change_object(request, object_id)
        if not self._has_related_delete_permission(request, model):
            raise PermissionDenied

        form = form_class(request.POST, prefix=prefix)
        if not form.is_valid():
            self.message_user(
                request,
                'Избраните записи не са валидни. Нищо не беше изтрито.',
                messages.ERROR,
            )
            return redirect(
                reverse(
                    'admin:blog_sitesettings_change',
                    args=(obj.pk,),
                )
            )

        selected_objects = list(form.get_selected_queryset())
        if not selected_objects:
            self.message_user(
                request,
                'Не сте избрали записи за изтриване.',
                messages.WARNING,
            )
            return redirect(
                reverse(
                    'admin:blog_sitesettings_change',
                    args=(obj.pk,),
                )
            )

        selected_ids = [
            selected_object.pk for selected_object in selected_objects
        ]
        with transaction.atomic():
            selected_queryset = model.objects.filter(pk__in=selected_ids)
            related_admin = self.admin_site._registry.get(model)
            if related_admin is not None:
                related_admin.log_deletions(request, selected_queryset)
            selected_queryset.delete()
        self.message_user(
            request,
            f'Изтрити са {len(selected_objects)} {object_label}.',
            messages.SUCCESS,
        )
        return redirect(
            reverse(
                'admin:blog_sitesettings_change',
                args=(obj.pk,),
            )
        )

    def delete_memes_view(self, request, object_id):
        return self._delete_selected_view(
            request,
            object_id,
            form_class=MemeSelectionForm,
            prefix='memes',
            model=MemeOfWeek,
            object_label='мем(а)',
        )

    def delete_suggestions_view(self, request, object_id):
        return self._delete_selected_view(
            request,
            object_id,
            form_class=SongSuggestionSelectionForm,
            prefix='suggestions',
            model=BellSongSuggestion,
            object_label='предложени(я) за песни',
        )

    def delete_polls_view(self, request, object_id):
        return self._delete_selected_view(
            request,
            object_id,
            form_class=PollQuestionSelectionForm,
            prefix='polls',
            model=PollQuestion,
            object_label='анкет(и)',
        )

    def render_change_form(
        self,
        request,
        context,
        add=False,
        change=False,
        form_url='',
        obj=None,
    ):
        # Django reaches render_change_form only after its normal object-level
        # view/change checks. Destructive controls are shown solely to users
        # who also hold delete permission for the related model.
        context['can_delete_memes'] = self._has_related_delete_permission(
            request,
            MemeOfWeek,
        )
        context['can_delete_suggestions'] = (
            self._has_related_delete_permission(request, BellSongSuggestion)
        )
        context['can_delete_polls'] = self._has_related_delete_permission(
            request,
            PollQuestion,
        )
        context['meme_selection_form'] = (
            MemeSelectionForm(prefix='memes')
            if context['can_delete_memes']
            else None
        )
        context['suggestion_selection_form'] = (
            SongSuggestionSelectionForm(prefix='suggestions')
            if context['can_delete_suggestions']
            else None
        )
        context['poll_selection_form'] = (
            PollQuestionSelectionForm(prefix='polls')
            if context['can_delete_polls']
            else None
        )
        return super().render_change_form(
            request,
            context,
            add=add,
            change=change,
            form_url=form_url,
            obj=obj,
        )

    def __str__(self):
        return "Настройки на сайта"

    def changelist_view(self, request, extra_context=None):
        if not self.has_view_or_change_permission(request):
            raise PermissionDenied

        obj = SiteSettings.objects.order_by('pk').first()
        if obj is None:
            if not super().has_add_permission(request):
                raise PermissionDenied
            obj = SiteSettings.objects.create()
        return redirect(
            reverse('admin:blog_sitesettings_change', args=(obj.pk,))
        )

    def has_add_permission(self, request):
        return (
            not SiteSettings.objects.exists()
            and super().has_add_permission(request)
        )

    def has_delete_permission(self, request, obj=None):
        return False

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if isinstance(db_field, models.BooleanField):
            kwargs["widget"] = UnfoldBooleanSwitchWidget()
        return super().formfield_for_dbfield(db_field, request, **kwargs)
