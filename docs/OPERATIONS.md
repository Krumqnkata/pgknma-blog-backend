# Операции и deployment

Подробният production runbook е [operations/DEPLOYMENT.md](operations/DEPLOYMENT.md). Този
документ е съкратен operational index и checklist.

## Локално стартиране

### Backend

```bash
cd "backend backup 27.07.2026/cms"
python -m venv .venv
# Windows
.\.venv\Scripts\Activate.ps1
# Linux/macOS
source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env  # Windows; на Linux: cp .env.example .env
python manage.py migrate
python manage.py runserver 8000
```

Local DEBUG default е SQLite, ако `DB_ENGINE` не е зададен. Production не
стартира с implicit DB/secret настройки.

### Frontend

```bash
cd Frontend
npm install
npm run dev
```

Vite е на `http://127.0.0.1:5173`; `VITE_DEV_BACKEND_URL` по default е
`http://127.0.0.1:8000` и proxy-ва API/media paths.

## Production topology

- Apache terminate-ва TLS и доставя React `dist`.
- `/api/`, `/admin`, `/valentines/`, `/hqs/` и `/sitemap` се proxy-ват към
  Django app server.
- `/files/` е Alias към media storage с byte-range support за HLS.
- `/files/memes/` не трябва да bypass-ва Django protected route.
- Cloudflare е пред Apache; API routes, особено `/api/push/*`, не трябва да
  получават JavaScript/managed challenge, защото browser service worker/API
  няма как да го изпълни.

## Стандартен release

1. Направи MySQL backup и запиши текущия deployed revision.
2. Pull/копирай backend и frontend source без `.env` replacement.
3. В backend virtualenv: `pip install -r requirements.txt` само при dependency
   change.
4. `python manage.py migrate`.
5. `python manage.py check`; при security/config review и
   `python manage.py check --deploy` с production env.
6. Build frontend: `npm ci`/`npm install`, `npm run typecheck`, `npm run lint`,
   `npm run build`; deploy `Frontend/dist` към configured Apache DocumentRoot.
7. Restart application service (Waitress/Gunicorn според server unit), после
   `sudo apachectl configtest` и `sudo systemctl reload apache2` само при
   Apache config change.
8. Smoke test: home, login, API JSON, admin, protected file, push config,
   calendar и one staff permission role.

Не прави `git reset --hard` върху server worktree и не копирай production
secrets обратно в repository.

## systemd timer-и

### Notifications

Файлове: `deployment/systemd/pgknma-notifications.service` и `.timer`.
Timer-ът е на всеки 5 минути и изпълнява `dispatch_scheduled_notifications`.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now pgknma-notifications.timer
systemctl list-timers pgknma-notifications.timer
journalctl -u pgknma-notifications.service -n 100 --no-pager
```

### Data retention

Файлове: `deploy/systemd/pgknma-data-retention.service` и `.timer`.
Работи дневно около 03:15 с random delay.

```bash
sudo systemctl enable --now pgknma-data-retention.timer
systemctl list-timers pgknma-data-retention.timer
journalctl -u pgknma-data-retention.service -n 100 --no-pager
```

Проверявай `WorkingDirectory`, Python virtualenv path, `User`, `Group` и file
permissions спрямо реалния сървър преди copy на unit файловете.

## Backup и възстановяване

Backup минимумът е MySQL + user uploads + protected team chat uploads +
production `.env` в защитено място. Тествай restore периодично на отделна
машина/DB. При data cleanup или голяма migration първо backup, после запис на
точна команда, час и резултат.

## Полезни diagnostics

```bash
python manage.py check
python manage.py showmigrations
python manage.py test
python manage.py dispatch_scheduled_notifications --help
python manage.py cleanup_personal_data --help
curl -i https://pgknma.space/api/site-status/
curl -i https://pgknma.space/api/push/config/
```

`curl` към Cloudflare може да получи challenge според WAF policy. JSON API
endpoint не трябва да е зад browser-only challenge, но WAF rules трябва да
останат ограничени до точно нужните API routes.
