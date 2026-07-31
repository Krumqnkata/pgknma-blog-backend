from blog.models import Posts, Category, UserProfile, Comments, PollQuestion, PollOption, PollAnswer, ContactSubmission, Notification, TermsOfService, Event, PostImage, BellSongSuggestion, PrivacyPolicy, MemeOfWeek, Cookie, SiteSettings, PostDocument, Changelog, ScheduleEntry
from django.contrib import admin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django import forms
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
    PollQuestionSelectionForm,
    PostAdminForm,
    SongSuggestionSelectionForm,
    ScheduleCsvImportForm,
)
from unfold.admin import ModelAdmin, TabularInline
from django.contrib.auth.models import User, Group
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
from unfold.forms import UserChangeForm, UserCreationForm
from django.contrib import messages
from django.shortcuts import redirect
from django.template.response import TemplateResponse

try:
    admin.site.unregister(Group)
except admin.sites.NotRegistered:
    pass


class DateSelectionForm(forms.Form):
    delete_before_date = forms.DateField(
        label="Изтрий записите преди:",
        widget=forms.DateInput(attrs={'type': 'date'})
    )


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
                    previous_count = ScheduleEntry.objects.count()
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

# 2. Регистрираме Group наново, но с Unfold класа
@admin.register(Group)
class GroupAdmin(BaseGroupAdmin, ModelAdmin):
    # Това прави кутиите с правата хоризонтални (една до друга)
    filter_horizontal = ("permissions",)
    
    # Тук добавяме специален CSS клас "permissions-box", за да го таргетираме по-късно
    fieldsets = (
        (None, {"fields": ("name",)}),
        ("Права", {
            "fields": ("permissions",),
            "classes": ["permissions-box"], 
        }),
    )


admin.site.unregister(User)

@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    form = UserChangeForm
    add_form = UserCreationForm
    # Unfold автоматично ще приложи стиловете си сега
    list_display = ('username', 'email', 'first_name', 'last_name', 'is_staff', 'last_login', 'date_joined')
    ordering = ('-last_login',)  # По подразбиране — последно логвалите се първи
    list_filter = ('is_staff', 'is_superuser', 'is_active', 'last_login')

@admin.register(Posts)
class PostsAdmin(ModelAdmin):
    form = PostAdminForm
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
        images_to_delete = form.cleaned_data.get('delete_images') or []
        documents_to_delete = form.cleaned_data.get('delete_documents') or []
        if images_to_delete and not request.user.has_perm('blog.delete_postimage'):
            raise PermissionDenied
        if documents_to_delete and not request.user.has_perm('blog.delete_postdocument'):
            raise PermissionDenied

        if not obj.pk:
            obj.author = request.user
        super().save_model(request, obj, form, change)

        # Use the form's cleaned values so image/content and size validation
        # cannot be bypassed by reading request.FILES directly.
        images = form.cleaned_data.get('gallery_images') or []
        if not isinstance(images, (list, tuple)):
            images = [images]
        for image in images:
            PostImage.objects.create(post=obj, image=image)

        # Handle image deletion
        if images_to_delete:
            PostImage.objects.filter(pk__in=images_to_delete, post=obj).delete()

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
        updated = queryset.update(status='approved')
        self.message_user(request, f'Успешно одобрихте {updated} предложения за песни.')

    # 3. Функция за масово отхвърляне
    @admin.action(
        description='Отхвърли избраните предложения',
        permissions=['change'],
    )
    def reject_selected(self, request, queryset):
        if not self.has_change_permission(request):
            raise PermissionDenied
        updated = queryset.update(status='rejected')
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
    list_display = ('text', 'enabled', 'created_at')
    list_filter = ('enabled', 'created_at')
    search_fields = ('text',)
    readonly_fields = ('created_at',)

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == 'text':
            kwargs['widget'] = MarkdownWidget(attrs={'rows': 3})
        return super().formfield_for_dbfield(db_field, request, **kwargs)

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
    list_display = ('post', 'image_preview') # Add image_preview to list_display
    list_filter = ('post',)
    readonly_fields = ('image_preview',) # Make image_preview readonly on the change form

    def image_preview(self, obj):
        if obj.image:
            return format_html(
                '<img src="{}" style="max-width: 150px; max-height: 150px;" alt="" />',
                obj.image.url,
            )
        return "Няма изображение"
    image_preview.short_description = "Преглед на изображението"
    def has_add_permission(self, request):
        return False  # Това ще забрани добавянето на нови мемета през админ панела

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

@admin.register(MemeOfWeek)
class MemeOfWeekAdmin(ModelAdmin):
    list_display = ('title', 'user', 'image_preview', 'is_approved', 'votes', 'created_at')
    list_filter = ('is_approved', 'created_at')
    search_fields = ('title', 'user__username')
    readonly_fields = ('user', 'created_at', 'image_preview', 'votes', 'title',)
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

    def user_display(self, obj):
        return obj.user.username if obj.user else "Анонимен"
    user_display.short_description = "Потребител"

    def network_asn_display(self, obj):
        return f"AS{obj.network_asn}" if obj.network_asn else "—"
    network_asn_display.short_description = "ASN"

@admin.register(SiteSettings)
class SiteSettingsAdmin(ModelAdmin):
    change_form_template = 'admin/site_settings_change_form.html'
    list_display = ('__str__', 'maintenance_mode', 'enable_bell_suggestions', 'enable_weekly_poll', 'enable_meme_of_the_week', 'enable_user_registration', 'enable_program_page')

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
