from django.db import migrations, models
from django.db.models.functions import Length


def drop_too_long(apps, schema_editor):
    # MySQL warns about unique CharFields over 255 characters. The table only
    # holds anonymous logs, so any longer key is simply dropped before shrinking.
    question_model = apps.get_model('blog', 'AssistantUnansweredQuestion')
    question_model.objects.annotate(key_length=Length('normalized')).filter(key_length__gt=255).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('blog', '0084_ai_assistant_v2'),
    ]

    operations = [
        migrations.RunPython(drop_too_long, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='assistantunansweredquestion',
            name='normalized',
            field=models.CharField(editable=False, max_length=255, unique=True),
        ),
    ]
