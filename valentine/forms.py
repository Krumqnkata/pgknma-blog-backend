from django import forms

from .models import Profile, Valenitines, normalize_class_code


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
    target_type = forms.ChoiceField(
        choices=Valenitines.AudienceType.choices,
        initial=Valenitines.AudienceType.INDIVIDUAL,
        required=False,
        label="Изпрати до",
    )
    target_class = forms.CharField(
        max_length=4,
        required=False,
        label="Клас",
    )
    recipient = forms.ModelChoiceField(
        queryset=Profile.objects.none(),
        label="Получател",
        empty_label=None,
        required=False,
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
        self.sender = sender
        recipients = Profile.objects.filter(is_active=True)
        if sender is not None:
            recipients = recipients.exclude(pk=sender.pk)
        self.fields["recipient"].queryset = recipients

    @staticmethod
    def normalize_class_name(value):
        return normalize_class_code(value)

    def clean(self):
        cleaned_data = super().clean()
        target_type = (
            cleaned_data.get("target_type")
            or Valenitines.AudienceType.INDIVIDUAL
        )
        target_class = self.normalize_class_name(
            cleaned_data.get("target_class")
        )
        recipient = cleaned_data.get("recipient")

        if target_type == Valenitines.AudienceType.INDIVIDUAL:
            if recipient is None:
                self.add_error("recipient", "Избери получател.")
                return cleaned_data
            if target_class and recipient.clas != target_class:
                self.add_error(
                    "recipient",
                    "Избраният човек не е в посочения клас.",
                )
                return cleaned_data
            recipients = [recipient]
            target_class = recipient.clas
        elif target_type == Valenitines.AudienceType.CLASS:
            if not target_class:
                self.add_error("target_class", "Избери клас.")
                return cleaned_data
            if target_class == "U":
                self.add_error(
                    "target_class",
                    "За учителите използвай „Всички учители“.",
                )
                return cleaned_data
            recipients = list(
                self.fields["recipient"].queryset.filter(
                    clas=target_class
                )
            )
            if not recipients:
                self.add_error(
                    "target_class",
                    "В този клас няма налични получатели.",
                )
                return cleaned_data
        elif target_type == Valenitines.AudienceType.TEACHERS:
            target_class = "U"
            recipients = list(
                self.fields["recipient"].queryset.filter(clas="U")
            )
            if not recipients:
                self.add_error(
                    "target_type",
                    "Няма налични учители.",
                )
                return cleaned_data
        else:
            self.add_error("target_type", "Невалиден тип получател.")
            return cleaned_data

        cleaned_data["target_type"] = target_type
        cleaned_data["target_class"] = target_class
        cleaned_data["recipients"] = recipients
        return cleaned_data

    def clean_content(self):
        content = self.cleaned_data["content"].strip()
        if len(content) < 2:
            raise forms.ValidationError("Посланието е твърде кратко.")
        return content


class ValentineEditForm(forms.Form):
    content = forms.CharField(
        min_length=2,
        max_length=500,
        label="Послание",
        widget=forms.Textarea(
            attrs={
                "rows": 7,
                "placeholder": "Напиши нещо мило…",
                "maxlength": 500,
            }
        ),
    )
    theme = forms.ChoiceField(
        choices=Valenitines.Theme.choices,
        label="Тема на картичката",
    )
    is_anonymous = forms.BooleanField(
        required=False,
        label="Анонимно за получателя",
    )

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
