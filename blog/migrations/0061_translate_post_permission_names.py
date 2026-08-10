from django.db import migrations


PERMISSION_NAMES = {
    "view_only_posts": "Може да вижда собствените си публикации",
    "can_view_all_posts": "Може да вижда всички публикувани публикации",
    "can_view_all_pnp_posts": "Може да вижда всички публикации, включително чернови",
    "can_allow_posts": "Може да одобрява публикации",
    "can_edit_users_post": "Може да редактира публикации на други потребители",
}

OLD_PERMISSION_NAMES = {
    "view_only_posts": "View only users posts",
    "can_view_all_posts": "Can view all posts",
    "can_view_all_pnp_posts": "Can view all post if they not published",
    "can_allow_posts": "Can allow posts",
    "can_edit_users_post": "Can edit users posts",
}


def _rename_permissions(apps, names, using):
    Permission = apps.get_model("auth", "Permission")
    for codename, name in names.items():
        Permission.objects.using(using).filter(
            content_type__app_label="blog",
            content_type__model="posts",
            codename=codename,
        ).update(name=name)


def rename_permissions(apps, schema_editor):
    _rename_permissions(apps, PERMISSION_NAMES, schema_editor.connection.alias)


def restore_permission_names(apps, schema_editor):
    _rename_permissions(
        apps,
        OLD_PERMISSION_NAMES,
        schema_editor.connection.alias,
    )


class Migration(migrations.Migration):
    dependencies = [
        ("blog", "0060_notificationpreference_notificationreadstate_and_more"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="posts",
            options={
                "permissions": [
                    (codename, name) for codename, name in PERMISSION_NAMES.items()
                ],
                "verbose_name": "Публикация",
                "verbose_name_plural": "Публикации",
            },
        ),
        migrations.RunPython(rename_permissions, restore_permission_names),
    ]
