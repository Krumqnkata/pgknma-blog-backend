# blog/forms.py
from django import forms
from django.utils.safestring import mark_safe
from django.core.validators import FileExtensionValidator
from .models import MemeOfWeek, BellSongSuggestion, PollQuestion, Posts, PostDocument # Import PostDocument


class MultipleFileInput(forms.FileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_file_clean = super().clean
        if isinstance(data, (list, tuple)):
            result = [single_file_clean(d, initial) for d in data]
        else:
            result = single_file_clean(data, initial)
        return result
allowed_document_extensions = ['pdf', 'docx', 'doc', 'xlsx', 'xls', 'pptx', 'ppt', 'zip', 'rar', '7z', 'csv', 'txt', 'json', 'rtf']

class PostAdminForm(forms.ModelForm):
    gallery_images = MultipleFileField(
        required=False,
        label="Качи нови изображения за галерията"
    )
    delete_images = forms.MultipleChoiceField(
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Съществуващи изображения"
    )

    gallery_documents = MultipleFileField(
        required=False,
        label="Качи нови документи (PDF, DOCX, DOC, XLSX, XLS, PPTX, PPT, ZIP, RAR, 7Z, CSV, TXT, JSON, RTF)",
        validators=[FileExtensionValidator(allowed_extensions=allowed_document_extensions)]
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
                label = mark_safe(f'Изтрий: <img src="{img.image.url}" width="150" style="margin: 5px;" />')
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
                label = mark_safe(f'{file_icon} Изтрий: {label_text}')
                document_choices.append((doc.pk, label))
            self.fields['delete_documents'].choices = document_choices

        else:
            # If it's a new post, hide the delete_images and delete_documents fields
            self.fields['delete_images'].widget = forms.HiddenInput()
            self.fields['delete_documents'].widget = forms.HiddenInput()


class MemeSelectionForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Get all memes (or limit to recent ones)
        memes = MemeOfWeek.objects.all().order_by('-created_at')

        # Create a checkbox field for each meme
        for meme in memes:
            field_name = f'meme_{meme.id}'
            image_preview = f'<img src="{meme.image.url}" style="width: 50px; height: 50px; margin-right: 10px; object-fit: cover;" onerror="this.style.display=\'none\'">' if meme.image else ''
            title = meme.title or f"Meme by {meme.user.username}"
            status_text = "Одобрено" if meme.is_approved else "Неодобрено"
            date_text = meme.created_at.strftime('%d.%m.%Y') if meme.created_at else "Без дата"
            votes_text = f" | Гласове: {meme.votes}" if meme.votes else ""

            self.fields[field_name] = forms.BooleanField(
                required=False,
                label=mark_safe(f'{image_preview}{title} <span class="status-{"approved" if meme.is_approved else "not-approved"}">[{status_text}]</span> | {date_text}{votes_text}'),
                widget=forms.CheckboxInput(attrs={'data-meme-id': meme.id})
            )

    def delete_selected_memes(self):
        """Delete the selected memes"""
        deleted_count = 0
        for field_name, value in self.cleaned_data.items():
            if value and field_name.startswith('meme_'):
                meme_id = field_name.replace('meme_', '')
                try:
                    meme = MemeOfWeek.objects.get(id=meme_id)
                    meme.delete()
                    deleted_count += 1
                except MemeOfWeek.DoesNotExist:
                    continue
        return deleted_count


class SongSuggestionSelectionForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Get all song suggestions (or limit to recent ones)
        suggestions = BellSongSuggestion.objects.all().order_by('-submitted_at')

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

    def delete_selected_suggestions(self):
        """Delete the selected song suggestions"""
        deleted_count = 0
        for field_name, value in self.cleaned_data.items():
            if value and field_name.startswith('suggestion_'):
                suggestion_id = field_name.replace('suggestion_', '')
                try:
                    suggestion = BellSongSuggestion.objects.get(id=suggestion_id)
                    suggestion.delete()
                    deleted_count += 1
                except BellSongSuggestion.DoesNotExist:
                    continue
        return deleted_count


class PollQuestionSelectionForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Get all poll questions (or limit to recent ones)
        polls = PollQuestion.objects.all().order_by('-created_at')

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

    def delete_selected_polls(self):
        """Delete the selected poll questions"""
        deleted_count = 0
        for field_name, value in self.cleaned_data.items():
            if value and field_name.startswith('poll_'):
                poll_id = field_name.replace('poll_', '')
                try:
                    poll = PollQuestion.objects.get(id=poll_id)
                    poll.delete()
                    deleted_count += 1
                except PollQuestion.DoesNotExist:
                    continue
        return deleted_count