# ПГКНМА Блог — Django backend

[![CI](https://github.com/Krumqnkata/pgknma-valentine/actions/workflows/ci.yml/badge.svg)](https://github.com/Krumqnkata/pgknma-valentine/actions/workflows/ci.yml)
[![CodeQL](https://github.com/Krumqnkata/pgknma-valentine/actions/workflows/codeql.yml/badge.svg)](https://github.com/Krumqnkata/pgknma-valentine/actions/workflows/codeql.yml)
[![License: AGPL-3.0-or-later](https://img.shields.io/badge/license-AGPL--3.0--or--later-blue.svg)](LICENSE)
![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![Django 6](https://img.shields.io/badge/Django-6-092E20?logo=django&logoColor=white)

Това repository съдържа Django REST API, Django Unfold admin и оперативните
скриптове за ПГКНМА Блог — дигиталната платформа на Професионална гимназия по
компютърно моделиране и компютърни анализи „Проф. Минко Балкански“.

React/Vite frontend-ът е в отделно repository:
[Krumqnkata/PGKNMA-Blog](https://github.com/Krumqnkata/PGKNMA-Blog).

## Какво прави

- **REST API** за новини, коментари, анкети, мемета, предложения за звънец,
  календар, галерия и публични профили (Django REST Framework + OpenAPI схема
  чрез drf-spectacular).
- **Акаунти и сесии** — JWT със следими login сесии, Google вход, имейл
  потвърждение през Resend и Cloudflare Turnstile срещу spam.
- **Admin панел** (Django Unfold) с роли за учители, модератори и администратори.
- **Нотификации** — вътрешни известия и Web Push към браузъра.
- **Геймификация и игри**, екипен чат и валентинки.
- **Поверителност** — експорт на лични данни и автоматично изтриване на стари
  данни по график.

## Технологии

Python 3.12 · Django 6 · Django REST Framework · SimpleJWT · Django Unfold ·
MySQL (SQLite локално) · Waitress/Gunicorn · Apache + Cloudflare в production.

## Документация

Започни от [docs/README.md](docs/README.md). Най-важните документи са:

- [Архитектура](docs/ARCHITECTURE.md)
- [Backend модули и модели](docs/BACKEND.md)
- [REST API](docs/API.md)
- [Admin и роли](docs/ADMIN_AND_ROLES.md)
- [Operations/deployment](docs/OPERATIONS.md)
- [Security и данни](docs/SECURITY_AND_DATA.md)
- [Разработка и тестове](docs/DEVELOPMENT_AND_TESTING.md)

## Бърз local старт

```bash
python -m venv .venv
# Windows: .\.venv\Scripts\Activate.ps1
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env  # Linux/macOS: cp .env.example .env
python manage.py migrate
python manage.py runserver 8000
```

`.env.example` включва `DJANGO_DEBUG=True` за локална работа. Без тази
стойност Django тръгва в production режим и изисква всички тайни ключове.

## Тестове

```bash
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
```

Същите проверки се пускат автоматично в GitHub Actions при всеки push и pull
request.

За production и пълните правила не използвай този кратък блок като runbook;
прочети [подробния deployment runbook](docs/operations/DEPLOYMENT.md) и
[docs/OPERATIONS.md](docs/OPERATIONS.md).

## Сигурност

Ако откриеш уязвимост, не отваряй публичен issue — виж [SECURITY.md](SECURITY.md).

## Автори

- [Krumqnkata](https://github.com/Krumqnkata)
- ad0312
- [lkbonev72](https://github.com/lkbonev72)

Проектът е създаден за ПГКНМА „Проф. Минко Балкански“.

## Лиценз

Кодът е © 2025–2026 Krumqnkata, ad0312 и lkbonev72 и е лицензиран под
[GNU AGPL v3.0 или по-нова версия](LICENSE). Ако го ползваш или пуснеш като
сайт или API, трябва да запазиш авторството и да публикуваш промените си под
същия лиценз.

Името, логата, снимките, документите и правните текстове на ПГКНМА са
собственост на училището и **не са** част от лиценза. Подробности:
[NOTICE.md](NOTICE.md).
