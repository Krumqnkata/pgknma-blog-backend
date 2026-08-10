from django.db import migrations


NEW_NAME = "Може да вижда, експортира и регенерира Valentine кодове"
OLD_NAME = "Може да вижда и експортира Valentine кодове"


def _rename_permission(apps, name, using):
    Permission = apps.get_model("auth", "Permission")
    Permission.objects.using(using).filter(
        content_type__app_label="valentine",
        content_type__model="profile",
        codename="view_profile_codes",
    ).update(name=name)


def rename_permission(apps, schema_editor):
    _rename_permission(apps, NEW_NAME, schema_editor.connection.alias)


def restore_permission_name(apps, schema_editor):
    _rename_permission(apps, OLD_NAME, schema_editor.connection.alias)


class Migration(migrations.Migration):
    dependencies = [
        ("valentine", "0018_alter_valenitines_theme"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="profile",
            options={
                "ordering": ("clas", "first_name", "last_name"),
                "permissions": (("view_profile_codes", NEW_NAME),),
                "verbose_name": "Valentine профил",
                "verbose_name_plural": "Valentine профили",
            },
        ),
        migrations.RunPython(rename_permission, restore_permission_name),
    ]
