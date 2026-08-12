from django.db import migrations, models
from django.utils import timezone


def cancel_deliveries_for_existing_read_states(apps, schema_editor):
    NotificationReadState = apps.get_model('blog', 'NotificationReadState')
    PushDelivery = apps.get_model('blog', 'PushDelivery')
    cancelled_at = timezone.now()
    user_ids = NotificationReadState.objects.order_by().values_list(
        'user_id',
        flat=True,
    ).distinct()
    for user_id in user_ids.iterator(chunk_size=500):
        notification_ids = NotificationReadState.objects.filter(
            user_id=user_id,
        ).values_list('notification_id', flat=True)
        PushDelivery.objects.filter(
            subscription__user_id=user_id,
            notification_id__in=notification_ids,
            status__in=('pending', 'failed'),
        ).update(
            status='cancelled',
            cancelled_at=cancelled_at,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('blog', '0065_login_device_identifier'),
    ]

    operations = [
        migrations.AddField(
            model_name='notificationreadstate',
            name='archived_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Архивирано на'),
        ),
        migrations.AddIndex(
            model_name='notificationreadstate',
            index=models.Index(fields=['user', 'archived_at'], name='notification_archive_user_idx'),
        ),
        migrations.AlterField(
            model_name='pushdelivery',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending', 'Изчаква'),
                    ('sent', 'Изпратено'),
                    ('failed', 'Неуспешно'),
                    ('cancelled', 'Отказано'),
                ],
                default='pending',
                max_length=10,
                verbose_name='Статус',
            ),
        ),
        migrations.AddField(
            model_name='pushdelivery',
            name='cancelled_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Отказано на'),
        ),
        migrations.RunPython(
            cancel_deliveries_for_existing_read_states,
            migrations.RunPython.noop,
        ),
    ]
