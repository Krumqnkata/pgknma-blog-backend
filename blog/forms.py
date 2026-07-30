# blog/forms.py
from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.urls import reverse
from django.utils.html import format_html

from .models import MemeOfWeek, BellSongSuggestion, PollQuestion, Posts, PostDocument # Import PostDocument


MAX_GALLERY_IMAGES = 10
MAX_GALLERY_DOCUMENTS = 10
MAX_IMAGE_SIZE = 10 * 1024 * 1024
MAX_DOCUMENT_SIZE = 25 * 1024 * 1024
ADMIN_SELECTION_LIMIT = 200


def validate_image_size(uploaded_file):
    if uploaded_file.size > MAX_IMAGE_SIZE:
        raise ValidationError("Всяко изображение трябва да е до 10 MB.")


def validate_document_size(uploaded_file):
    if uploaded_file.size > MAX_DOCUMENT_SIZE:
        raise ValidationError("Всеки документ трябва да е до 25 MB.")


class MultipleFileInput(forms.FileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    default_error_messages = {
        **forms.FileField.default_error_messages,
        "too_many_files": "Може да качите най-много %(max_files)s файла наведнъж.",
    }

    def __init__(self, *args, **kwargs):
        self.max_files = kwargs.pop("max_files", None)
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_file_clean = super().clean
        if isinstance(data, (list, tuple)):
            if self.max_files is not None and len(data) > self.max_files:
                raise ValidationError(
                    self.error_messages["too_many_files"],
                    code="too_many_files",
                    params={"max_files": self.max_files},
                )
            result = [single_file_clean(d, initial) for d in data]
        else:
            result = single_file_clean(data, initial)
        return result


class MultipleImageField(forms.ImageField):
    default_error_messages = {
        **forms.ImageField.default_error_messages,
        "too_many_files": "Може да качите най-много %(max_files)s изображения наведнъж.",
    }

    def __init__(self, *args, **kwargs):
        self.max_files = kwargs.pop("max_files", None)
        kwargs.setdefault(
            "widget",
            MultipleFileInput(attrs={"accept": "image/*"}),
        )
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_image_clean = super().clean
        if isinstance(data, (list, tuple)):
            if self.max_files is not None and len(data) > self.max_files:
                raise ValidationError(
                    self.error_messages["too_many_files"],
                    code="too_many_files",
                    params={"max_files": self.max_files},
                )
            return [single_image_clean(image, initial) for image in data]
        return single_image_clean(data, initial)


allowed_document_extensions = ['pdf', 'docx', 'doc', 'xlsx', 'xls', 'pptx', 'ppt', 'zip', 'rar', '7z', 'csv', 'txt', 'json', 'rtf']


class PostAdminForm(forms.ModelForm):
    gallery_images = MultipleImageField(
        required=False,
        label="Качи нови изображения за галерията (до 10 файла, по 10 MB)",
        max_files=MAX_GALLERY_IMAGES,
        validators=[validate_image_size],
    )
    delete_images = forms.MultipleChoiceField(
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Съществуващи изображения"
    )

    gallery_documents = MultipleFileField(
        required=False,
        label="Качи нови документи (до 10 файла, по 25 MB; PDF, DOCX, DOC, XLSX, XLS, PPTX, PPT, ZIP, RAR, 7Z, CSV, TXT, JSON, RTF)",
        max_files=MAX_GALLERY_DOCUMENTS,
        validators=[
            FileExtensionValidator(allowed_extensions=allowed_document_extensions),
            validate_document_size,
        ],
    )
    delete_documents = forms.MultipleChoiceField(
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Съществуващи документи"
    )

    class Meta:
        model = Posts
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            # Populate delete_images for existing images
            existing_images = self.instance.images.all()
            image_choices = []
            for img in existing_images:
                label = format_html(
                    'Изтрий: <img src="{}" width="150" style="margin: 5px;" alt="" />',
                    img.image.url,
                )
                image_choices.append((img.pk, label))
            self.fields['delete_images'].choices = image_choices

            # Populate delete_documents for existing documents
            existing_documents = self.instance.documents.all()
            document_choices = []
            for doc in existing_documents:
                # Display file icon and name for document choices
                file_icon = "📄" # Generic document icon, could be more specific
                if doc.file.name.lower().endswith('.pdf'):
                    file_icon = "PDF"
                elif doc.file.name.lower().endswith('.docx'):
                    file_icon = "Word"
                elif doc.file.name.lower().endswith('.xlsx'):
                    file_icon = "Excel"
                elif doc.file.name.lower().endswith('.zip'):
                    file_icon = "ZIP"

                label_text = doc.file.name.split('/')[-1] if doc.file else "Няма файл"
                label = format_html("{} Изтрий: {}", file_icon, label_text)
                document_choices.append((doc.pk, label))
            self.fields['delete_documents'].choices = document_choices

        else:
            # If it's a new post, hide the delete_images and delete_documents fields
            self.fields['delete_images'].widget = forms.HiddenInput()
            self.fields['delete_documents'].widget = forms.HiddenInput()


class MemeSelectionForm(forms.Form):
    selection_limit = ADMIN_SELECTION_LIMIT

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        memes = (
            MemeOfWeek.objects.select_related("user")
            .order_by("-created_at", "-pk")[:self.selection_limit]
        )

        # Create a checkbox field for each meme
        for meme in memes:
            field_name = f'meme_{meme.id}'
            username = meme.user.username if meme.user else "неизвестен потребител"
            title = meme.title or f"Meme by {username}"
            status_text = "Одобрено" if meme.is_approved else "Неодобрено"
            date_text = meme.created_at.strftime('%d.%m.%Y') if meme.created_at else "Без дата"
            votes_text = f" | Гласове: {meme.votes}" if meme.votes else ""
            status_class = "approved" if meme.is_approved else "not-approved"

            if meme.pk and meme.image:
                image_preview = format_html(
                    '<img src="{}" class="selection-thumbnail" alt="" />',
                    reverse('meme-image', kwargs={'pk': meme.pk}),
                )
            else:
                image_preview = ""

            self.fields[field_name] = forms.BooleanField(
                required=False,
                label=format_html(
                    '{}{} <span class="status-{}">[{}]</span> | {}{}',
                    image_preview,
                    title,
                    status_class,
                    status_text,
                    date_text,
                    votes_text,
                ),
                widget=forms.CheckboxInput(attrs={'data-meme-id': meme.id})
            )

    def get_selected_queryset(self):
        selected_ids = [
            field_name.removeprefix("meme_")
            for field_name, value in self.cleaned_data.items()
            if value and field_name.startswith("meme_")
        ]
        return MemeOfWeek.objects.filter(pk__in=selected_ids)


class SongSuggestionSelectionForm(forms.Form):
    selection_limit = ADMIN_SELECTION_LIMIT

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        suggestions = BellSongSuggestion.objects.order_by(
            "-submitted_at",
            "-pk",
        )[:self.selection_limit]

        # Create a checkbox field for each suggestion
        for suggestion in suggestions:
            field_name = f'suggestion_{suggestion.id}'
            title = suggestion.title
            status_text = suggestion.get_status_display()
            date_text = suggestion.submitted_at.strftime('%d.%m.%Y') if suggestion.submitted_at else "Без дата"
            votes_text = f" | Гласове: {suggestion.votes}" if suggestion.votes else ""
            slot_text = f" | Слот: {suggestion.get_slot_display()}"

            self.fields[field_name] = forms.BooleanField(
                required=False,
                label=f'{title} [{status_text}]{slot_text} | {date_text}{votes_text}',
                widget=forms.CheckboxInput(attrs={'data-suggestion-id': suggestion.id})
            )

    def get_selected_queryset(self):
        selected_ids = [
            field_name.removeprefix("suggestion_")
            for field_name, value in self.cleaned_data.items()
            if value and field_name.startswith("suggestion_")
        ]
        return BellSongSuggestion.objects.filter(pk__in=selected_ids)


class PollQuestionSelectionForm(forms.Form):
    selection_limit = ADMIN_SELECTION_LIMIT

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        polls = PollQuestion.objects.order_by(
            "-created_at",
            "-pk",
        )[:self.selection_limit]

        # Create a checkbox field for each poll
        for poll in polls:
            field_name = f'poll_{poll.id}'
            title = poll.title
            start_date = poll.start_date.strftime('%d.%m.%Y') if poll.start_date else "Без дата"
            end_date = poll.end_date.strftime('%d.%m.%Y') if poll.end_date else "Без дата"

            self.fields[field_name] = forms.BooleanField(
                required=False,
                label=f'{title} | Начало: {start_date} | Край: {end_date}',
                widget=forms.CheckboxInput(attrs={'data-poll-id': poll.id})
            )

    def get_selected_queryset(self):
        selected_ids = [
            field_name.removeprefix("poll_")
            for field_name, value in self.cleaned_data.items()
            if value and field_name.startswith("poll_")
        ]
        return PollQuestion.objects.filter(pk__in=selected_ids)
