import blog.models
import django.db.models.deletion
from django.db import migrations, models


GENERAL_CATEGORY_SLUG = 'default'


def create_general_category(apps, schema_editor):
    Category = apps.get_model('blog', 'Category')
    if not Category.objects.filter(short_name=GENERAL_CATEGORY_SLUG).exists():
        Category.objects.create(
            full_name='Общи',
            short_name=GENERAL_CATEGORY_SLUG,
        )


def copy_category_to_m2m(apps, schema_editor):
    Posts = apps.get_model('blog', 'Posts')
    Category = apps.get_model('blog', 'Category')
    general = Category.objects.filter(short_name=GENERAL_CATEGORY_SLUG).first()
    throughput = Posts.categories.through

    for post in Posts.objects.select_related('category').iterator():
        if post.category_id:
            throughput.objects.get_or_create(
                posts_id=post.pk,
                category_id=post.category_id,
            )
        if general:
            throughput.objects.get_or_create(
                posts_id=post.pk,
                category_id=general.pk,
            )


class Migration(migrations.Migration):

    dependencies = [
        ('blog', '0074_team_chat_discussion_access'),
    ]

    operations = [
        migrations.RunPython(
            create_general_category,
            migrations.RunPython.noop,
        ),
        migrations.AddField(
            model_name='posts',
            name='categories',
            field=models.ManyToManyField(
                blank=True,
                help_text="Категориите, към които принадлежи публикацията. 'Общи' се добавя автоматично. Максимум 3.",
                related_name='posts',
                to='blog.category',
                verbose_name='Категории',
            ),
        ),
        migrations.RunPython(
            copy_category_to_m2m,
            migrations.RunPython.noop,
        ),
        migrations.RemoveField(
            model_name='posts',
            name='category',
        ),
    ]