from valentine.models import Profile
from UserDB.models import Verified
from django.db.models import Q


def migrate_with_cyrillic_fix():
    # Речник за превод на паралелките
    bg_to_en = {
        'А': 'A', 'Б': 'B', 'В': 'V', 'Г': 'G',
        'а': 'A', 'б': 'B', 'в': 'V', 'г': 'G'
    }

    old_profiles = Profile.objects.all()
    created_count = 0

    print(f"Намерени {old_profiles.count()} профила за миграция...")

    for p in old_profiles:
        # 1. Обработка на класа
        old_val = p.clas.strip() if p.clas else ""
        if old_val:
            digit = old_val[:-1]  # Взема цифрите (напр. "8")
            letter = old_val[-1]  # Взема последната буква

            # Сменяме буквата на латиница, ако е българска
            new_letter = bg_to_en.get(letter, letter).upper()
            clean_clas = f"{digit}{new_letter}"
        else:
            clean_clas = ""

        # 2. Създаваме записа в Verified
        # Използваме update_or_create, за да сме сигурни, че няма да има дубликати
        obj, created = Verified.objects.update_or_create(
            code=p.code,
            defaults={
                'first_name': p.first_name,
                'last_name': p.last_name,
                'clas': clean_clas,
            }
        )
        if created:
            created_count += 1

    print(f"✅ Миграцията приключи успешно!")
    print(f"--- Създадени записи в Verified: {created_count}")


def patch_missing_classes():
    # Намираме всички, които нямат клас (NULL или празен стринг)
    missing = Verified.objects.filter(Q(clas__isnull=True) | Q(clas=''))

    bg_to_en = {'А': 'A', 'Б': 'B', 'В': 'V', 'Г': 'G', 'а': 'A', 'б': 'B', 'в': 'V', 'г': 'G'}
    fixed_count = 0

    print(f"Намерени {missing.count()} записа с липсващ клас. Започвам попълване...")

    for v in missing:
        # Търсим същия човек в старата таблица по кода
        old = Profile.objects.filter(code=v.code).first()

        if old and old.clas:
            raw_val = old.clas.strip()

            # 1. Проверка за Учител
            if raw_val.upper() in ['У', 'U', 'УЧИТЕЛ', 'TEACHER']:
                v.clas = 'U'
            # 2. Проверка за нормален клас (превръщаме в латиница)
            elif len(raw_val) >= 2:
                digit = raw_val[:-1]
                letter = raw_val[-1]
                new_letter = bg_to_en.get(letter, letter).upper()
                v.clas = f"{digit}{new_letter}"

            v.save()
            fixed_count += 1

    print(f"✅ Попълването завърши! Обновени записи: {fixed_count}")