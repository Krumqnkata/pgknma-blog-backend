from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("valentine", "0013_normalize_profile_class_codes"),
    ]

    operations = [
        migrations.AlterField(
            model_name="profile",
            name="email",
            field=models.EmailField(
                blank=True,
                max_length=254,
                verbose_name="Имейл",
            ),
        ),
    ]
