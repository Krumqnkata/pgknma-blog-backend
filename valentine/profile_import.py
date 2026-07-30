import csv
import io
import re
from collections import defaultdict

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction

from .models import ACCESS_CODE_ALPHABET, Profile, normalize_class_code


MAX_CSV_BYTES = 2 * 1024 * 1024
MAX_CSV_ROWS = 2000
STUDENT_CLASS_PATTERN = re.compile(r"^(?:[1-9]|1[0-2])[ABVG]$")

HEADER_ALIASES = {
    "first_name": {
        "first name",
        "firstname",
        "име",
        "първо име",
        "собствено име",
    },
    "last_name": {
        "last name",
        "lastname",
        "фамилия",
        "фамилно име",
    },
    "clas": {
        "class",
        "clas",
        "клас",
        "паралелка",
    },
    "email": {
        "email",
        "e mail",
        "имейл",
        "електронна поща",
    },
    "code": {
        "code",
        "access code",
        "код",
        "код за достъп",
    },
}


class ProfileImportError(Exception):
    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("\n".join(self.errors))


def _normalize_header(value):
    value = str(value or "").strip().lower()
    value = value.replace("_", " ").replace("-", " ")
    return " ".join(value.split())


def _canonical_headers(fieldnames):
    aliases = {
        alias: canonical
        for canonical, values in HEADER_ALIASES.items()
        for alias in values
    }
    header_map = {}
    found = set()
    for raw_header in fieldnames or ():
        canonical = aliases.get(_normalize_header(raw_header))
        if not canonical:
            continue
        if canonical in found:
            raise ProfileImportError(
                [f"Колоната „{canonical}“ присъства повече от веднъж."]
            )
        header_map[raw_header] = canonical
        found.add(canonical)

    missing = {
        "first_name": "first_name / Име",
        "last_name": "last_name / Фамилия",
        "clas": "class / Клас",
    }.keys() - found
    if missing:
        labels = {
            "first_name": "first_name / Име",
            "last_name": "last_name / Фамилия",
            "clas": "class / Клас",
        }
        raise ProfileImportError(
            [
                "Липсват задължителни колони: "
                + ", ".join(labels[name] for name in sorted(missing))
                + "."
            ]
        )
    return header_map


def _decode_csv(uploaded_file):
    raw = uploaded_file.read(MAX_CSV_BYTES + 1)
    if len(raw) > MAX_CSV_BYTES:
        raise ProfileImportError(["CSV файлът не може да е по-голям от 2 MB."])
    if not raw:
        raise ProfileImportError(["CSV файлът е празен."])
    if b"\x00" in raw:
        raise ProfileImportError(["CSV файлът съдържа невалидни символи."])

    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ProfileImportError(
        ["Файлът трябва да бъде записан като UTF-8 или Windows-1251."]
    )


def _detect_delimiter(text):
    sample = text[:8192]
    try:
        return csv.Sniffer().sniff(
            sample,
            delimiters=";,\t",
        ).delimiter
    except csv.Error:
        first_line = sample.splitlines()[0] if sample.splitlines() else ""
        counts = {
            delimiter: first_line.count(delimiter)
            for delimiter in (";", ",", "\t")
        }
        delimiter = max(counts, key=counts.get)
        if counts[delimiter] == 0:
            raise ProfileImportError(
                ["Не е открит CSV разделител (; или ,)."]
            )
        return delimiter


def _clean_name(value):
    return " ".join(str(value or "").split())


def parse_profile_csv(uploaded_file):
    text = _decode_csv(uploaded_file)
    delimiter = _detect_delimiter(text)
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    header_map = _canonical_headers(reader.fieldnames)

    rows = []
    errors = []
    seen_codes = {}
    for row_number, raw_row in enumerate(reader, start=2):
        values = {
            canonical: str(raw_row.get(raw_header) or "").strip()
            for raw_header, canonical in header_map.items()
        }
        if not any(values.values()):
            continue
        if len(rows) >= MAX_CSV_ROWS:
            errors.append(
                f"Файлът съдържа повече от {MAX_CSV_ROWS} записа."
            )
            break

        first_name = _clean_name(values.get("first_name"))
        last_name = _clean_name(values.get("last_name"))
        class_code = normalize_class_code(values.get("clas"))
        email = values.get("email", "").strip().lower()
        code = values.get("code", "").strip().upper()
        row_errors = []

        if not first_name:
            row_errors.append("липсва име")
        elif len(first_name) > 100:
            row_errors.append("името е по-дълго от 100 знака")
        if not last_name:
            row_errors.append("липсва фамилия")
        elif len(last_name) > 100:
            row_errors.append("фамилията е по-дълга от 100 знака")
        if not STUDENT_CLASS_PATTERN.fullmatch(class_code):
            row_errors.append(
                "класът трябва да е между 1А и 12Г"
            )
        if email:
            try:
                validate_email(email)
            except ValidationError:
                row_errors.append("имейлът е невалиден")
        if code and (
            len(code) != 6
            or any(character not in ACCESS_CODE_ALPHABET for character in code)
        ):
            row_errors.append(
                "кодът трябва да съдържа точно 6 латински букви/цифри"
            )
        if code:
            if code in seen_codes:
                row_errors.append(
                    f"кодът се повтаря и на ред {seen_codes[code]}"
                )
            else:
                seen_codes[code] = row_number

        if row_errors:
            errors.append(
                f"Ред {row_number}: " + "; ".join(row_errors) + "."
            )
            continue
        rows.append(
            {
                "row_number": row_number,
                "first_name": first_name,
                "last_name": last_name,
                "clas": class_code,
                "email": email,
                "code": code,
            }
        )

    if not rows and not errors:
        errors.append("CSV файлът не съдържа ученици.")
    return rows, errors


def _profile_lookup(profiles):
    by_code = {}
    by_email = defaultdict(list)
    for profile in profiles:
        by_code[profile.code.upper()] = profile
        if profile.email:
            by_email[profile.email.strip().casefold()].append(profile)
    return by_code, by_email


def build_profile_import_plan(rows, *, lock=False):
    queryset = Profile.objects.all()
    if lock:
        queryset = queryset.select_for_update()
    profiles = list(queryset)
    by_code, by_email = _profile_lookup(profiles)
    plans = []
    errors = []
    targeted_profiles = {}
    new_email_rows = {}

    for row in rows:
        code = row["code"]
        email_key = row["email"].casefold()
        profile = by_code.get(code) if code else None

        if profile is None and email_key:
            candidates = by_email.get(email_key, ())
            if len(candidates) == 1:
                profile = candidates[0]
                if code and profile.code.upper() != code:
                    errors.append(
                        f"Ред {row['row_number']}: имейлът принадлежи на "
                        "съществуващ профил, но подаденият код не съвпада. "
                        "Използвай точния код или остави кода празен."
                    )
                    continue
            elif len(candidates) > 1:
                errors.append(
                    f"Ред {row['row_number']}: имейлът се използва от "
                    "няколко профила; добави точния код за достъп."
                )
                continue

        if profile is not None:
            profile_key = str(profile.pk)
            if profile_key in targeted_profiles:
                errors.append(
                    f"Ред {row['row_number']}: същият профил вече е посочен "
                    f"на ред {targeted_profiles[profile_key]}."
                )
                continue
            targeted_profiles[profile_key] = row["row_number"]
            action = "reactivate" if not profile.is_active else "update"
        else:
            if email_key and email_key in new_email_rows:
                errors.append(
                    f"Ред {row['row_number']}: имейлът се повтаря и на ред "
                    f"{new_email_rows[email_key]}."
                )
                continue
            if email_key:
                new_email_rows[email_key] = row["row_number"]
            action = "create"

        plans.append(
            {
                "row": row,
                "action": action,
                "profile": profile,
                "display_code": profile.code if profile else (code or "автоматичен"),
                "display_email": (
                    row["email"]
                    or (profile.email if profile is not None else "")
                ),
            }
        )
    return plans, errors


def apply_profile_import(
    rows,
    academic_year_start,
    *,
    allow_existing_updates=True,
):
    with transaction.atomic():
        plans, errors = build_profile_import_plan(rows, lock=True)
        if not allow_existing_updates:
            protected_rows = [
                plan["row"]["row_number"]
                for plan in plans
                if plan["action"] != "create"
            ]
            if protected_rows:
                errors.append(
                    "Нямаш право да обновяваш или възстановяваш "
                    "съществуващи профили (редове: "
                    + ", ".join(str(row) for row in protected_rows)
                    + ")."
                )
        if errors:
            raise ProfileImportError(errors)

        created = 0
        updated = 0
        reactivated = 0
        for plan in plans:
            row = plan["row"]
            profile = plan["profile"]
            if profile is None:
                profile = Profile(
                    code=row["code"],
                    first_name=row["first_name"],
                    last_name=row["last_name"],
                    clas=row["clas"],
                    email=row["email"],
                    academic_year_start=academic_year_start,
                    is_active=True,
                )
                profile.save()
                created += 1
                continue

            was_inactive = not profile.is_active
            profile.first_name = row["first_name"]
            profile.last_name = row["last_name"]
            profile.clas = row["clas"]
            if row["email"]:
                profile.email = row["email"]
            profile.academic_year_start = academic_year_start
            profile.is_active = True
            profile.save(
                update_fields=(
                    "first_name",
                    "last_name",
                    "clas",
                    "email",
                    "academic_year_start",
                    "is_active",
                )
            )
            updated += 1
            if was_inactive:
                reactivated += 1

    return {
        "created": created,
        "updated": updated,
        "reactivated": reactivated,
    }
