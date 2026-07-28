from blog.models import Posts, Category, UserProfile, Comments, PollQuestion, PollOption, PollAnswer, ContactSubmission, Notification, TermsOfService, Event, PostImage, BellSongSuggestion, PrivacyPolicy, MemeOfWeek, Cookie, SiteSettings, PostDocument, Changelog
from django.contrib import admin
from django.utils.safestring import mark_safe
from django.db import models
from django import forms
from django.http import HttpResponseRedirect
from django.urls import reverse
import markdown
import re # Import regex module
from unfold_markdown.widgets import MarkdownWidget
from unfold.widgets import UnfoldBooleanSwitchWidget
from .forms import MemeSelectionForm, PostAdminForm
from unfold.admin import ModelAdmin
from django.contrib.auth.models import User
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from unfold.forms import UserChangeForm, UserCreationForm
# Може да ти потрябва и TabularInline от Unfold, ако правиш Inlines
from unfold.admin import ModelAdmin, TabularInline
from django.contrib.auth.models import User, Group 
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin
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
    readonly_fields_base = ('author',)
    list_display = ('title', 'author', 'published', 'created_at')
    list_filter = ('published', 'category', 'created_at')
    search_fields = ('title', 'content')
    exclude = ('author',)

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
        if not obj.pk:
            obj.author = request.user
        super().save_model(request, obj, form, change)

        # Handle multiple image uploads
        images = request.FILES.getlist('gallery_images')
        for image in images:
            PostImage.objects.create(post=obj, image=image)

        # Handle image deletion
        if 'delete_images' in form.cleaned_data:
            images_to_delete = form.cleaned_data['delete_images']
            PostImage.objects.filter(pk__in=images_to_delete, post=obj).delete()

        # Handle multiple document uploads
        documents = request.FILES.getlist('gallery_documents')
        for document in documents:
            PostDocument.objects.create(post=obj, file=document)

        # Handle document deletion
        if 'delete_documents' in form.cleaned_data:
            documents_to_delete = form.cleaned_data['delete_documents']
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
    readonly_fields_base = ('user', 'content' ,'created_at', 'post')
    list_display = ('user', 'content', 'created_at', 'post')
    search_fields = ('user', 'content')
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
            return mark_safe(f'<img src="{obj.image.url}" style="max-width: 100px; max-height: 100px;" />')
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
            return mark_safe(f'<img src="{obj.image.url}" style="max-width: 200px; max-height: 200px;" />')
        return "Няма изображение"
    image_preview.short_description = "Преглед на изображението"

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == "task_description":
            kwargs["widget"] = MarkdownWidget(attrs={"rows": 10})
        return super().formfield_for_dbfield(db_field, request, **kwargs)

    def save_formset(self, request, form, formset, change):
        # Получаваме инстанциите, без да записваме в базата
        instances = formset.save(commit=False)

        # Задаваме ключовете на опциите преди да запишем в базата
        for i, instance in enumerate(instances):
            if i < 4:  # Максимален брой опции
                letters = ['a', 'b', 'c', 'd']
                instance.key = letters[i]
            instance.question = form.instance  # Уверяваме се, че въпросът е зададен
            instance.save()

        # Изтриваме останалите връзки
        formset.save_m2m()

        # Enforce single correct answer
        if form.instance.pk:
            correct_options = form.instance.options.filter(is_correct=True)
            if correct_options.count() > 1:
                last_correct_option = correct_options.order_by('-id').first()
                form.instance.options.exclude(pk=last_correct_option.pk).update(is_correct=False)

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

    
    @admin.action(description='Изтрий записи по дата (преди...)')
    def delete_by_date(self, request, queryset):
        # Ако формата е изпратена
        if 'apply' in request.POST:
            form = DateSelectionForm(request.POST)
            if form.is_valid():
                date = form.cleaned_data['delete_before_date']
                # Изтриваме всички записи преди тази дата (независимо от селекцията в списъка)
                deleted_count, _ = self.model.objects.filter(submitted_at__lt=date).delete()
                
                self.message_user(
                    request, 
                    f'Успешно бяха изтрити {deleted_count} записа преди дата {date}.',
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
            }
        )

    # 2. Функция за масово одобряване
    @admin.action(description='Одобри избраните предложения')
    def approve_selected(self, request, queryset):
        updated = queryset.update(status='approved')
        self.message_user(request, f'Успешно одобрихте {updated} предложения за песни.')

    # 3. Функция за масово отхвърляне
    @admin.action(description='Отхвърли избраните предложения')
    def reject_selected(self, request, queryset):
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
            return mark_safe(f'<a href="{youtube_direct_url}" target="_blank">Гледай в YouTube: {obj.title}</a>')
        elif spotify_match:
            track_id = spotify_match.group(1)
            return mark_safe(f'<iframe src="https://open.spotify.com/embed/track/{track_id}" width="300" height="152" frameborder="0" allowtransparency="true" allow="encrypted-media"></iframe>')
        else:
            return mark_safe(f'<a href="{obj.link}" target="_blank">{obj.link}</a> (Неподдържан формат)')

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
            html = markdown.markdown(obj.content)
            return mark_safe(html[:150] + "..." if len(html) > 150 else html)
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
            html = markdown.markdown(obj.content)
            return mark_safe(html[:150] + "..." if len(html) > 150 else html)
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
            # Assuming MEDIA_URL is correctly configured, adjust width/height as needed
            return mark_safe(f'<img src="{obj.image.url}" style="max-width: 150px; max-height: 150px;" />')
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
        if obj.image:
            return mark_safe(f'<img src="{obj.image.url}" style="max-width: 150px; max-height: 150px;" />')
        return "Няма изображение"
    image_preview.short_description = "Изображение"

    def has_add_permission(self, request):
        return False  # Това ще забрани добавянето на нов мемета през админ панела

@admin.register(Cookie.ConsentRecord)
class ConsentRecordAdmin(ModelAdmin):
    list_display = ('user_display', 'ip_address', 'consent_status', 'timestamp', 'policy_version')
    list_filter = ('consent_status', 'policy_version')
    search_fields = ('ip_address', 'user__username')
    readonly_fields = ('user', 'ip_address', 'consent_status', 'timestamp', 'policy_version')

    def has_add_permission(self, request):
        return False # Records are created via API, not admin

    def has_change_permission(self, request, obj=None):
        return False # Records should not be changeable

    def user_display(self, obj):
        return obj.user.username if obj.user else "Анонимен"
    user_display.short_description = "Потребител"

@admin.register(SiteSettings)
class SiteSettingsAdmin(ModelAdmin):
    change_form_template = 'admin/site_settings_change_form.html'  # Custom template
    list_display = ('__str__', 'maintenance_mode', 'enable_bell_suggestions', 'enable_weekly_poll', 'enable_meme_of_the_week', 'enable_user_registration', 'enable_program_page')

    class Media:
        css = {
            'all': ('admin/css/meme_deletion.css',)
        }
        js = ('admin/js/meme_deletion.js',)

    def changeform_view(self, request, object_id=None, form_url='', extra_context=None):
        from .forms import SongSuggestionSelectionForm, PollQuestionSelectionForm  # Import here to avoid circular import
        extra_context = extra_context or {}

        # Handle meme deletion
        if request.method == 'POST' and 'delete_selected_memes' in request.POST:
            meme_form = MemeSelectionForm(request.POST, prefix='memes')
            if meme_form.is_valid():
                deleted_count = meme_form.delete_selected_memes()
                self.message_user(request, f'Изтрити са {deleted_count} мем(а).')

                # Redirect to avoid re-submission
                return HttpResponseRedirect(request.get_full_path())

        # Handle song suggestion deletion
        if request.method == 'POST' and 'delete_selected_suggestions' in request.POST:
            suggestion_form = SongSuggestionSelectionForm(request.POST, prefix='suggestions')
            if suggestion_form.is_valid():
                deleted_count = suggestion_form.delete_selected_suggestions()
                self.message_user(request, f'Изтрити са {deleted_count} предложени(е) за песни.')

                # Redirect to avoid re-submission
                return HttpResponseRedirect(request.get_full_path())

        # Handle poll question deletion
        if request.method == 'POST' and 'delete_selected_polls' in request.POST:
            poll_form = PollQuestionSelectionForm(request.POST, prefix='polls')
            if poll_form.is_valid():
                deleted_count = poll_form.delete_selected_polls()
                self.message_user(request, f'Изтрити са {deleted_count} анкета(и).')

                # Redirect to avoid re-submission
                return HttpResponseRedirect(request.get_full_path())

        # Pass the forms to the template
        extra_context['meme_selection_form'] = MemeSelectionForm(prefix='memes')
        extra_context['suggestion_selection_form'] = SongSuggestionSelectionForm(prefix='suggestions')
        extra_context['poll_selection_form'] = PollQuestionSelectionForm(prefix='polls')

        return super().changeform_view(request, object_id, form_url, extra_context)

    def __str__(self):
        return "Настройки на сайта"

    def changelist_view(self, request, extra_context=None):
        from django.http import HttpResponseRedirect
        from django.urls import reverse

        obj, created = SiteSettings.objects.get_or_create(pk=1)
        return HttpResponseRedirect(reverse('admin:blog_sitesettings_change', args=(obj.pk,)))

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if isinstance(db_field, models.BooleanField):
            kwargs["widget"] = UnfoldBooleanSwitchWidget()
        return super().formfield_for_dbfield(db_field, request, **kwargs)