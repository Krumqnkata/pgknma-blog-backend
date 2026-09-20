import blog.models
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('blog', '0075_post_categories_m2m'),
    ]

    operations = [
        migrations.AddField(
            model_name='postimage',
            name='display_image',
            field=models.ImageField(blank=True, upload_to=blog.models.post_gallery_display_upload_path),
        ),
        migrations.AddField(
            model_name='postimage',
            name='thumbnail_image',
            field=models.ImageField(blank=True, upload_to=blog.models.post_gallery_thumbnail_upload_path),
        ),
    ]
