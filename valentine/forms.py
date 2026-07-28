from django import forms

from .models import Profile, Valenitines


class AccessCodeForm(forms.Form):
    code = forms.CharField(
        min_length=6,
        max_length=6,
        label="Код за достъп",
        widget=forms.TextInput(
            attrs={
                "autocomplete": "one-time-code",
                "autocapitalize": "characters",
                "spellcheck": "false",
                "placeholder": "ABC123",
            }
        ),
    )

    def clean_code(self):
        return self.cleaned_data["code"].strip().upper()


class ValentineCreateForm(forms.Form):
    recipient = forms.ModelChoiceField(
        queryset=Profile.objects.none(),
        label="Получател",
        empty_label=None,
    )
    content = forms.CharField(
        min_length=2,
        max_length=500,
        label="Послание",
        widget=forms.Textarea(
            attrs={
                "rows": 6,
                "placeholder": "Напиши нещо мило…",
                "maxlength": 500,
            }
        ),
    )
    theme = forms.ChoiceField(
        choices=Valenitines.Theme.choices,
        initial=Valenitines.Theme.CLASSIC,
        label="Тема на картичката",
    )
    is_anonymous = forms.BooleanField(
        required=False,
        initial=True,
        label="Анонимно за получателя",
    )

    def __init__(self, *args, sender=None, **kwargs):
        super().__init__(*args, **kwargs)
        recipients = Profile.objects.all()
        if sender is not None:
            recipients = recipients.exclude(pk=sender.pk)
        self.fields["recipient"].queryset = recipients

    def clean_content(self):
        content = self.cleaned_data["content"].strip()
        if len(content) < 2:
            raise forms.ValidationError("Посланието е твърде кратко.")
        return content


class ReportValentineForm(forms.Form):
    reason = forms.ChoiceField(
        choices=Valenitines.ReportReason.choices,
        label="Причина",
    )
