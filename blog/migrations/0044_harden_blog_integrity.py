import blog.models
import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import Count, F


def preflight_integrity_and_sync_votes(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor == 'mysql':
        version = connection.mysql_version
        is_mariadb = connection.mysql_is_mariadb
        minimum_version = (10, 6) if is_mariadb else (8, 0, 16)
        if version < minimum_version:
            database_name = 'MariaDB' if is_mariadb else 'MySQL'
            required = '.'.join(str(part) for part in minimum_version)
            detected = '.'.join(str(part) for part in version)
            raise RuntimeError(
                "Migration 0044 requires "
                f"{database_name} >= {required} so its CHECK constraints "
                "are enforced. "
                f"Detected {database_name} {detected}; no DDL was run."
            )

    BellSongSuggestion = apps.get_model('blog', 'BellSongSuggestion')
    Event = apps.get_model('blog', 'Event')
    MemeOfWeek = apps.get_model('blog', 'MemeOfWeek')
    PollAnswer = apps.get_model('blog', 'PollAnswer')
    PollQuestion = apps.get_model('blog', 'PollQuestion')
    SiteSettings = apps.get_model('blog', 'SiteSettings')

    duplicate_answer_groups = (
        PollAnswer.objects.values('user_id', 'question_id')
        .annotate(answer_count=Count('id'))
        .filter(answer_count__gt=1)
        .count()
    )
    invalid_answer_count = sum(
        1
        for question_id, option_question_id
        in PollAnswer.objects.values_list(
            'question_id',
            'selected_option__question_id',
        ).iterator()
        if question_id != option_question_id
    )
    invalid_poll_dates = PollQuestion.objects.filter(
        end_date__lte=F('start_date')
    ).count()
    invalid_event_dates = Event.objects.filter(
        end_datetime__isnull=False,
        end_datetime__lte=F('start_datetime'),
    ).count()
    extra_site_settings_rows = max(SiteSettings.objects.count() - 1, 0)
    miskeyed_site_settings_rows = SiteSettings.objects.exclude(pk=1).count()

    if any((
        duplicate_answer_groups,
        invalid_answer_count,
        invalid_poll_dates,
        invalid_event_dates,
        extra_site_settings_rows,
        miskeyed_site_settings_rows,
    )):
        raise RuntimeError(
            "Migration 0044 preflight failed; no application data was "
            "changed. Create a database backup, resolve the listed records "
            "manually, and retry: "
            f"duplicate poll-answer groups={duplicate_answer_groups}, "
            f"cross-question poll answers={invalid_answer_count}, "
            f"polls with end <= start={invalid_poll_dates}, "
            f"events with end <= start={invalid_event_dates}, "
            f"extra SiteSettings rows={extra_site_settings_rows}, "
            f"SiteSettings rows with pk != 1={miskeyed_site_settings_rows}."
        )

    # The M2M relation is authoritative; repair any old counter drift before
    # constraints and the new signal-based synchronization take effect.
    for song in BellSongSuggestion.objects.annotate(
        actual_votes=Count('voted_by')
    ).only('pk', 'votes'):
        if song.votes != song.actual_votes:
            BellSongSuggestion.objects.filter(pk=song.pk).update(
                votes=song.actual_votes
            )
    for meme in MemeOfWeek.objects.annotate(
        actual_votes=Count('voted_by')
    ).only('pk', 'votes'):
        if meme.votes != meme.actual_votes:
            MemeOfWeek.objects.filter(pk=meme.pk).update(
                votes=meme.actual_votes
            )

class Migration(migrations.Migration):

    dependencies = [
        ('blog', '0043_alter_postdocument_file_alter_posts_hook_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # This must run before any DDL so non-transactional MySQL migrations
        # cannot be left half-applied when legacy conflicts exist.
        migrations.RunPython(
            preflight_integrity_and_sync_votes,
            migrations.RunPython.noop,
        ),
        migrations.CreateModel(
            name='GoogleIdentity',
            fields=[
                (
                    'id',
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name='ID',
                    ),
                ),
                ('issuer', models.CharField(max_length=64)),
                ('subject', models.CharField(max_length=255)),
                ('email_at_link_time', models.EmailField(max_length=254)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Google самоличност',
                'verbose_name_plural': 'Google самоличности',
            },
        ),
        migrations.AlterField(
            model_name='bellsongsuggestion',
            name='user',
            field=models.ForeignKey(
                blank=True,
                help_text='Потребителят, който е направил предложението.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to=settings.AUTH_USER_MODEL,
                verbose_name='Потребител',
            ),
        ),
        migrations.AlterField(
            model_name='comments',
            name='parent',
            field=models.ForeignKey(
                blank=True,
                help_text='Родителският коментар, ако този е отговор.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='replies',
                to='blog.comments',
            ),
        ),
        migrations.AlterField(
            model_name='comments',
            name='user',
            field=models.ForeignKey(
                blank=True,
                help_text='Потребителят, който е написал коментара.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name='memeofweek',
            name='image',
            field=models.ImageField(
                help_text='Файлът с изображението на мемето.',
                null=True,
                upload_to=blog.models.meme_upload_path,
                validators=[blog.models.validate_meme_image_size],
                verbose_name='Изображение',
            ),
        ),
        migrations.AlterField(
            model_name='memeofweek',
            name='title',
            field=models.CharField(
                help_text='Заглавие на мемето.',
                max_length=100,
                null=True,
                verbose_name='Заглавие',
            ),
        ),
        migrations.AlterField(
            model_name='memeofweek',
            name='user',
            field=models.ForeignKey(
                blank=True,
                help_text='Потребителят, който е качил мемето.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to=settings.AUTH_USER_MODEL,
                verbose_name='Потребител',
            ),
        ),
        migrations.AlterField(
            model_name='pollanswer',
            name='selected_option',
            field=models.ForeignKey(
                help_text='Опцията, която потребителят е избрал.',
                on_delete=django.db.models.deletion.RESTRICT,
                to='blog.polloption',
                verbose_name='Избрана опция',
            ),
        ),
        migrations.AlterField(
            model_name='polloption',
            name='image',
            field=models.ImageField(
                blank=True,
                help_text='Изображение, което се показва с тази опция.',
                null=True,
                upload_to=blog.models.poll_media_upload_path,
                validators=[blog.models.validate_image_upload_size],
                verbose_name='Изображение към опция',
            ),
        ),
        migrations.AlterField(
            model_name='pollquestion',
            name='image',
            field=models.ImageField(
                blank=True,
                help_text='Изображение, което се показва с въпроса.',
                null=True,
                upload_to=blog.models.poll_media_upload_path,
                validators=[blog.models.validate_image_upload_size],
                verbose_name='Изображение към въпроса',
            ),
        ),
        migrations.AlterField(
            model_name='pollanswer',
            name='user',
            field=models.ForeignKey(
                blank=True,
                help_text='Потребителят, който е отговорил.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to=settings.AUTH_USER_MODEL,
                verbose_name='Потребител',
            ),
        ),
        migrations.AlterField(
            model_name='pollquestion',
            name='end_date',
            field=models.DateTimeField(
                default=blog.models.default_poll_end_date,
                help_text=(
                    'Дата и час, до които анкетата е активна. '
                    'Формат: YYYY-MM-DD HH:MM:SS.'
                ),
                verbose_name='Крайна дата',
            ),
        ),
        migrations.AlterField(
            model_name='posts',
            name='author',
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    'Авторът на публикацията. '
                    'Полето се попълва автоматично.'
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name='posts',
            name='banner',
            field=models.ImageField(
                blank=True,
                help_text=(
                    'Банер изображение, което ще се показва '
                    'в горната част на публикацията.'
                ),
                null=True,
                upload_to=blog.models.post_banner_upload_path,
                validators=[blog.models.validate_image_upload_size],
            ),
        ),
        migrations.AlterField(
            model_name='postimage',
            name='image',
            field=models.ImageField(
                help_text='Файлът с изображението.',
                upload_to=blog.models.post_gallery_upload_path,
                validators=[blog.models.validate_image_upload_size],
            ),
        ),
        migrations.AlterField(
            model_name='postdocument',
            name='file',
            field=models.FileField(
                help_text=(
                    'Файлът с документа (PDF, DOCX, DOC, XLSX, XLS, '
                    'PPTX, PPT, ZIP, RAR, 7Z, CSV, TXT, JSON, RTF).'
                ),
                upload_to=blog.models.post_document_upload_path,
                validators=[
                    django.core.validators.FileExtensionValidator(
                        allowed_extensions=blog.models.DOCUMENT_EXTENSIONS,
                    ),
                    blog.models.validate_document_upload_size,
                ],
            ),
        ),
        migrations.AlterField(
            model_name='posts',
            name='category',
            field=models.ForeignKey(
                help_text='Категорията, към която принадлежи публикацията.',
                on_delete=django.db.models.deletion.PROTECT,
                to='blog.category',
            ),
        ),
        migrations.AlterField(
            model_name='privacypolicy',
            name='user',
            field=models.ForeignKey(
                blank=True,
                help_text=(
                    'Авторът, който е създал/редактирал политиката.'
                ),
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to=settings.AUTH_USER_MODEL,
                verbose_name='Автор',
            ),
        ),
        migrations.AlterField(
            model_name='termsofservice',
            name='user',
            field=models.ForeignKey(
                blank=True,
                help_text='Авторът, който е създал/редактирал условията.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                to=settings.AUTH_USER_MODEL,
                verbose_name='Автор',
            ),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='singleton_key',
            field=models.PositiveSmallIntegerField(
                default=1,
                editable=False,
                unique=True,
            ),
        ),
        migrations.AddConstraint(
            model_name='bellsongsuggestion',
            constraint=models.CheckConstraint(
                condition=models.Q(('votes__gte', 0)),
                name='blog_song_votes_nonnegative',
            ),
        ),
        migrations.AddConstraint(
            model_name='event',
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ('end_datetime__isnull', True),
                    ('end_datetime__gt', models.F('start_datetime')),
                    _connector='OR',
                ),
                name='blog_event_end_after_start',
            ),
        ),
        migrations.AddConstraint(
            model_name='memeofweek',
            constraint=models.CheckConstraint(
                condition=models.Q(('votes__gte', 0)),
                name='blog_meme_votes_nonnegative',
            ),
        ),
        migrations.AddConstraint(
            model_name='pollanswer',
            constraint=models.UniqueConstraint(
                fields=('user', 'question'),
                name='blog_unique_poll_answer_per_user',
            ),
        ),
        migrations.AddConstraint(
            model_name='pollquestion',
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ('end_date__gt', models.F('start_date'))
                ),
                name='blog_poll_end_after_start',
            ),
        ),
        migrations.AddConstraint(
            model_name='sitesettings',
            constraint=models.CheckConstraint(
                condition=models.Q(('singleton_key', 1)),
                name='blog_site_settings_singleton_key',
            ),
        ),
        migrations.AddField(
            model_name='googleidentity',
            name='user',
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='google_identity',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddConstraint(
            model_name='googleidentity',
            constraint=models.UniqueConstraint(
                fields=('issuer', 'subject'),
                name='blog_unique_google_identity',
            ),
        ),
    ]
