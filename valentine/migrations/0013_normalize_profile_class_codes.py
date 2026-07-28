import unicodedata

from django.db import migrations


CLASS_CODE_TRANSLATION = str.maketrans(
    {
        "А": "A",
        "Б": "B",
        "В": "V",
        "Г": "G",
        "У": "U",
        "\u200b": None,
        "\u200c": None,
        "\u200d": None,
        "\ufeff": None,
    }
)


def normalize_class_code(value):
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKC", str(value))
    normalized = normalized.upper().translate(CLASS_CODE_TRANSLATION)
    return "".join(normalized.split())


def normalize_existing_class_codes(apps, schema_editor):
    Profile = apps.get_model("valentine", "Profile")
    Valentine = apps.get_model("valentine", "Valenitines")

    profiles_to_update = []
    for profile in Profile.objects.only("uuid", "clas").iterator(
        chunk_size=500
    ):
        normalized = normalize_class_code(profile.clas)
        if profile.clas != normalized:
            profile.clas = normalized
            profiles_to_update.append(profile)
    if profiles_to_update:
        Profile.objects.bulk_update(profiles_to_update, ("clas",))

    valentines_to_update = []
    for valentine in Valentine.objects.only(
        "id",
        "recipient_class_snapshot",
        "sender_class_snapshot",
    ).iterator(chunk_size=500):
        recipient_class = normalize_class_code(
            valentine.recipient_class_snapshot
        )
        sender_class = normalize_class_code(
            valentine.sender_class_snapshot
        )
        if (
            valentine.recipient_class_snapshot != recipient_class
            or valentine.sender_class_snapshot != sender_class
        ):
            valentine.recipient_class_snapshot = recipient_class
            valentine.sender_class_snapshot = sender_class
            valentines_to_update.append(valentine)
    if valentines_to_update:
        Valentine.objects.bulk_update(
            valentines_to_update,
            (
                "recipient_class_snapshot",
                "sender_class_snapshot",
            ),
        )


class Migration(migrations.Migration):

    dependencies = [
        ("valentine", "0012_profile_lifecycle_and_class_history"),
    ]

    operations = [
        migrations.RunPython(
            normalize_existing_class_codes,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
