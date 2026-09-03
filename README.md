# ПГКНМА Блог — Django backend

Това repository съдържа Django REST API, Django Unfold admin, данните и
оперативните скриптове за ПГКНМА Блог. React/Vite frontend-ът е отделното
repository `../../Frontend`.

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

За production и пълните правила не използвай този кратък блок като runbook;
прочети [подробния deployment runbook](docs/operations/DEPLOYMENT.md) и
[docs/OPERATIONS.md](docs/OPERATIONS.md).
