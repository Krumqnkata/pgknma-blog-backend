"""Send a small test email through the configured Django email backend."""

from urllib.parse import urljoin

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.templatetags.static import static
from django.utils.html import strip_tags


class Command(BaseCommand):
    help = 'Изпраща тестов HTML имейл чрез текущите настройки за Resend.'

    def add_arguments(self, parser):
        parser.add_argument(
            'recipient',
            help='Имейл адресът, на който да бъде изпратено тестовото писмо.',
        )

    def handle(self, *args, **options):
        recipient = options['recipient'].strip()
        try:
            validate_email(recipient)
        except ValidationError as exc:
            raise CommandError(f'Невалиден имейл адрес: {recipient}') from exc

        logo_url = urljoin(
            f'{settings.PUBLIC_SITE_URL}/',
            static('images/logo-sm.png').lstrip('/'),
        )
        html_body = f'''\
<!doctype html>
<html lang="bg">
  <body style="margin:0;padding:32px 16px;background:#e8f5e6;font-family:Arial,sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
      <tr>
        <td align="center">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
                 style="max-width:560px;background:#ffffff;border-radius:16px;overflow:hidden;">
            <tr>
              <td align="center" style="padding:32px;background:#243119;">
                <img src="{logo_url}" width="320" alt="ПГКНМА"
                     style="display:block;width:100%;max-width:320px;height:auto;border:0;" />
              </td>
            </tr>
            <tr>
              <td style="padding:36px 32px;color:#243119;">
                <h1 style="margin:0 0 16px;font-size:24px;">Тестовият имейл работи ✅</h1>
                <p style="margin:0;color:#4b5563;line-height:1.6;">
                  Това писмо е изпратено от Django чрез настроения Resend API.
                  Ако го виждате, изпращането на имейли работи правилно.
                </p>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
'''

        message = EmailMultiAlternatives(
            subject='Тестов имейл от ПГКНМА Блог',
            body=strip_tags(html_body).strip(),
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[recipient],
            reply_to=(
                [settings.EMAIL_VERIFICATION_REPLY_TO]
                if settings.EMAIL_VERIFICATION_REPLY_TO
                else None
            ),
        )
        message.attach_alternative(html_body, 'text/html')

        try:
            sent_count = message.send(fail_silently=False)
        except Exception as exc:
            raise CommandError(
                f'Имейлът не беше изпратен: {exc}'
            ) from exc

        if sent_count != 1:
            raise CommandError(
                'Имейл backend-ът не потвърди изпращането на писмото.'
            )

        self.stdout.write(
            self.style.SUCCESS(
                f'Тестовият имейл беше изпратен успешно до {recipient}.'
            )
        )
