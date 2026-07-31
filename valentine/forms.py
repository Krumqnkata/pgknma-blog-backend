from django import forms
from django.utils import timezone

from .models import (
    Profile,
    ValentineCampaign,
    Valenitines,
    default_academic_year_start,
    normalize_class_code,
)


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


class PinAuthenticationForm(forms.Form):
    pin = forms.CharField(
        min_length=6,
        max_length=6,
        label="6-цифрен PIN",
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "current-password",
                "inputmode": "numeric",
                "pattern": "[0-9]*",
                "placeholder": "••••••",
            }
        ),
    )

    def clean_pin(self):
        pin = self.cleaned_data["pin"]
        if not pin.isascii() or not pin.isdigit() or len(pin) != 6:
            raise forms.ValidationError("PIN кодът трябва да е точно 6 цифри.")
        return pin


class PinSetupForm(PinAuthenticationForm):
    pin_confirmation = forms.CharField(
        min_length=6,
        max_length=6,
        label="Повтори PIN кода",
        widget=forms.PasswordInput(
            attrs={
                "autocomplete": "new-password",
                "inputmode": "numeric",
                "pattern": "[0-9]*",
                "placeholder": "••••••",
            }
        ),
    )

    def clean_pin_confirmation(self):
        pin = self.cleaned_data["pin_confirmation"]
        if not pin.isascii() or not pin.isdigit() or len(pin) != 6:
            raise forms.ValidationError("PIN кодът трябва да е точно 6 цифри.")
        return pin

    def clean(self):
        cleaned_data = super().clean()
        if (
            cleaned_data.get("pin")
            and cleaned_data.get("pin_confirmation")
            and cleaned_data["pin"] != cleaned_data["pin_confirmation"]
        ):
            self.add_error("pin_confirmation", "Двата PIN кода не съвпадат.")
        return cleaned_data


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


class ProfileCsvImportForm(forms.Form):
    csv_file = forms.FileField(
        label="CSV файл",
        help_text=(
            "Приемат се UTF-8 и Windows-1251 файлове с разделител "
            "точка и запетая или запетая."
        ),
        widget=forms.ClearableFileInput(
            attrs={"accept": ".csv,text/csv"}
        ),
    )
    academic_year_start = forms.IntegerField(
        label="Начало на учебната година",
        min_value=2000,
        max_value=2100,
        initial=default_academic_year_start,
        help_text="Например 2026 означава учебната 2026/2027 година.",
    )


class ProfilePromotionForm(forms.Form):
    target_academic_year_start = forms.IntegerField(
        label="Нова учебна година",
        min_value=2000,
        max_value=2100,
        help_text="Например 2026 означава учебната 2026/2027 година.",
    )


class CampaignActivationForm(forms.Form):
    sending_starts_at = forms.DateTimeField(
        label="Начало на изпращането",
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local"},
            format="%Y-%m-%dT%H:%M",
        ),
        input_formats=("%Y-%m-%dT%H:%M",),
    )
    sending_ends_at = forms.DateTimeField(
        label="Край на изпращането",
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local"},
            format="%Y-%m-%dT%H:%M",
        ),
        input_formats=("%Y-%m-%dT%H:%M",),
    )
    reveal_at = forms.DateTimeField(
        label="Разкриване",
        widget=forms.DateTimeInput(
            attrs={"type": "datetime-local"},
            format="%Y-%m-%dT%H:%M",
        ),
        input_formats=("%Y-%m-%dT%H:%M",),
    )

    def __init__(self, *args, campaign: ValentineCampaign, **kwargs):
        self.campaign = campaign
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()
        starts_at = cleaned_data.get("sending_starts_at")
        ends_at = cleaned_data.get("sending_ends_at")
        reveal_at = cleaned_data.get("reveal_at")
        values = {
            "sending_starts_at": starts_at,
            "sending_ends_at": ends_at,
            "reveal_at": reveal_at,
        }
        labels = {
            "sending_starts_at": "Началото",
            "sending_ends_at": "Краят",
            "reveal_at": "Разкриването",
        }
        for field_name, value in values.items():
            if (
                value
                and timezone.localtime(value).year != self.campaign.year
            ):
                self.add_error(
                    field_name,
                    f"{labels[field_name]} трябва да е през "
                    f"{self.campaign.year} година.",
                )
        if starts_at and ends_at and starts_at >= ends_at:
            self.add_error(
                "sending_ends_at",
                "Краят трябва да бъде след началото.",
            )
        if ends_at and reveal_at and ends_at > reveal_at:
            self.add_error(
                "reveal_at",
                "Разкриването трябва да бъде след края на изпращането.",
            )
        if starts_at and reveal_at and starts_at >= reveal_at:
            self.add_error(
                "reveal_at",
                "Разкриването трябва да бъде след началото.",
            )
        return cleaned_data
