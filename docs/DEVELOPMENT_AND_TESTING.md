# Разработка, проверки и тестове

## Ежедневен workflow

1. Прочети relevant model/view/serializer/frontend API функция преди change.
2. Направи малък scoped change; не смесвай refactor, feature и dependency
   upgrade без причина.
3. При model change генерирай и прегледай migration.
4. Добави/обнови regression test за behavior, permission и error path.
5. Пусни checks, целеви tests и според риска пълния suite/build.
6. Обнови съответната документация в `docs/`.

## Backend команди

```bash
# От backend repository root
.\.venv\Scripts\python.exe manage.py check              # Windows
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
.\.venv\Scripts\python.exe manage.py test blog.test_search_push
.\.venv\Scripts\python.exe manage.py test
```

Linux equivalent: `source .venv/bin/activate`, после `python manage.py ...`.

Използвай target test module, когато работиш по конкретна област; преди merge
на security/auth/models промяна пусни пълния suite. Не третирай log messages
от tests, които нарочно симулират provider failure, като test failure, ако
процесът завършва с `OK`.

## Frontend команди

```bash
cd ../../Frontend
npm run typecheck
npm run lint
npm run test
npm run test:a11y
npm run build
# при e2e environment:
npm run test:e2e
```

Frontend има Vitest/unit/a11y тестове и Playwright e2e script. Нов UI behavior
трябва да има поне компонентен тест при сложна state/permission логика.

## Тестови сценарии, които често се пропускат

- Anonymous vs logged-in vs other-user access.
- Създаване два пъти (duplicate notification, vote, mission activity).
- Изтрит/скрит source, към който notification/link сочи.
- Session expiration/refresh/revoke.
- Mobile layout, keyboard only и `prefers-reduced-motion`.
- Празен, бавен и failure API state.
- MySQL behavior при unique/index/migration, не само local SQLite.
- Cloudflare/Apache route, който в development работи през Vite proxy, но
  production трябва да достигне Apache/Django.

## Migration правила

```bash
python manage.py makemigrations blog
python manage.py sqlmigrate blog <migration_number>
python manage.py migrate
python manage.py showmigrations blog
```

Преди production migration: backup, review SQL, estimate table size/lock,
staging test и rollback/restore plan. Не редактирай historical migrations,
които вече са приложени на server.

## Документация definition of done

Feature change е готов, когато:

- code/test/build checks са зелени;
- API/type/permission documentation е обновена;
- нови environment settings са в `.env.example` и production template;
- operational action има безопасна команда и rollback note;
- privacy/terms са обновени, ако data purpose/retention се променя.
