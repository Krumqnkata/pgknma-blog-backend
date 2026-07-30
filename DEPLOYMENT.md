# Production deployment

Репото вече не съдържа production тайни. Старите Django/JWT ключ,
MySQL парола и Turnstile secret, които са присъствали в Git историята,
трябва да се считат за компрометирани и да бъдат сменени преди
рестартиране на приложението.

## 1. Предварителни изисквания

Django 6 изисква Python 3.12 или по-нова версия. Провери реалния interpreter
от virtual environment, а не системния `python`:

```bash
cd /var/www/blog/cms/cms
source ../.venv/bin/activate
python --version
python -c "import sys; assert sys.version_info >= (3, 12), sys.version"
```

При Debian/Ubuntu за създаване на нов environment и компилиране на
`mysqlclient` обикновено са нужни:

```bash
sudo apt update
sudo apt install python3.12 python3.12-venv python3.12-dev \
    build-essential pkg-config default-libmysqlclient-dev
```

Не изпълнявай сляпо командата за конкретна дистрибуция. Провери имената на
пакетите и поддържаната Python версия за операционната система на сървъра.

Използвай MySQL **8.0.16 или по-нов** (препоръчително актуална 8.0/8.4
версия) или MariaDB **10.6 или по-нова**. Миграция `blog.0044` спира
преди schema промени на по-стар сървър, защото там нужните `CHECK`
ограничения не се прилагат надеждно.

## 2. Environment файл: first install срещу update

При първа инсталация създай `.env` само ако такъв още не съществува:

```bash
cd /var/www/blog/cms/cms
source ../.venv/bin/activate
test ! -e .env || { echo "STOP: .env already exists"; exit 1; }
install -m 600 .env.production.example .env
```

При update **никога не копирай template-а върху съществуващия `.env`**.
Прегледай новите имена в `.env.production.example` и добави липсващите
настройки ръчно, без да заменяш реалните secrets.

Файлът трябва да може да се чете от същия Unix user, който стартира
Gunicorn/systemd услугата. Ако deploy user-ът и service user-ът са различни,
настрой правилни owner/group и използвай например mode `640`; не оставяй
secrets world-readable.

Генерирай два независими ключа и ги постави съответно в
`DJANGO_SECRET_KEY` и `JWT_SIGNING_KEY`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Попълни всички задължителни стойности:

- `DB_ENGINE` и данните за избраната база;
- новата MySQL парола;
- новия Cloudflare Turnstile secret — production не стартира без него;
- `ENABLE_GOOGLE_AUTH=True` и Google client ID, ако Google входът ще се
  използва; иначе остави флага `False` и client ID празен;
- точните публични hosts и origins;
- trusted-proxy настройките само след изпълнение на изискванията от секция 7.

Булевите стойности приемат само `true/false`, `1/0`, `yes/no` или `on/off`.
Грешно изписана стойност спира приложението вместо тихо да изключи защита.

Не използвай повторно стойност от стар Git commit.

### Временен production SQLite

MySQL остава препоръчителната production база. Ако временно е необходим
SQLite, замени DB настройките в `.env` с изрично съгласие:

```dotenv
DB_ENGINE=sqlite
DB_NAME=/var/www/blog/cms/cms/db.sqlite3
ALLOW_PRODUCTION_SQLITE=True
SQLITE_TIMEOUT_SECONDS=30
CACHE_BACKEND=django.core.cache.backends.locmem.LocMemCache
CACHE_LOCATION=pgknma-single-worker-cache
```

В този режим:

- стартирай точно **един** application worker;
- не използвай `DatabaseCache` в същия SQLite файл;
- LocMemCache е допустим само за този еднопроцесен временен режим;
- спирай услугата преди файлов backup на базата;
- очаквай по-нисък капацитет и върни MySQL/shared cache възможно най-скоро.

`ALLOW_PRODUCTION_SQLITE=True` не прави SQLite еквивалентен на MySQL; то само
предотвратява случайно стартиране с празна SQLite база.

## 3. Смяна на външните credentials

Смени MySQL паролата през административна MySQL сесия и обнови
`DB_PASSWORD` в `.env` в същия maintenance прозорец. Смени Turnstile secret
от Cloudflare dashboard и обнови `TURNSTILE_SECRET_KEY`.

Смяната на Django и JWT ключовете нарочно обезсилва старите подписани данни,
сесии и JWT токени. Не връщай компрометиран ключ при rollback.

## 4. Backup и план за rollback

Преди `migrate`:

1. насочи proxy-то към maintenance страница, после спри **всички** web/app
   workers и background задачи, които могат да пишат в базата; не оставяй
   активни admin/API записи по време на backup и `migrate`;
2. запиши текущия Git commit: `git rev-parse HEAD`;
3. направи проверен backup на базата и директорията `files/`;
4. запази копие на текущия `.env`, достъпно само за администратора;
5. определи предишния работещ commit и командата за рестарт.

Този пълен write freeze е задължителен за миграции `blog.0044` и
`valentine.0015`: при MySQL DDL не се връща надеждно назад, а конкурентен
запис между preflight проверката и добавянето на constraint може да остави
частично приложена schema. Стартирай `migrate` само от отделната deploy shell
след като си потвърдил, че application workers са спрени.

`blog.0044` синхронизира кешираните броячи за гласове по авторитетните
many-to-many записи. Това е очаквана, но необратима data корекция; при rollback
възстанови предварителния backup, вместо да разчиташ на reverse migration.

Пример за MySQL и media backup; избери защитена директория и не записвай
паролата в shell history:

```bash
DEPLOY_TAG="$(date -u +%Y%m%dT%H%M%SZ)"
sudo install -d -o "$(id -un)" -g "$(id -gn)" -m 700 \
    /var/backups/pgknma
mysqldump -u django_prod -p --single-transaction --routines --triggers \
    pgknma_blog | gzip > \
    "/var/backups/pgknma/database-${DEPLOY_TAG}.sql.gz"
tar -C /var/www/blog/cms/cms -czf \
    "/var/backups/pgknma/files-${DEPLOY_TAG}.tar.gz" files
```

Провери, че архивите не са празни и могат да бъдат прочетени. При rollback
спри приложението, върни предишния code/dependencies/static files и възстанови
database/media backup-а. Не изпълнявай reverse migration в production, освен
ако е била изрично тествана върху копие на базата.

## 5. Инсталация, миграции и cache

```bash
python -m pip install -r requirements.txt
python -m pip check
python manage.py check
python manage.py migrate --plan
python manage.py migrate
python manage.py collectstatic --noinput
```

Ако `CACHE_BACKEND` е `django.core.cache.backends.db.DatabaseCache`, създай
таблицата след миграциите:

```bash
python manage.py createcachetable
```

DatabaseCache е временна shared-cache опция. Глобалните DRF throttles четат
и записват cache при почти всяка API заявка, затова production стойността
`CACHE_MAX_ENTRIES` е значително по-голяма от Django default-а. За по-висок
трафик използвай Redis; преди превключването добави и pin-ни съвместим Redis
Python client в `requirements.txt`, тествай връзката и чак тогава промени
`CACHE_BACKEND`/`CACHE_LOCATION`.

Провери cache-а от същия environment и user като услугата:

```bash
python manage.py shell -c \
    "from django.core.cache import cache; cache.set('deploy-smoke', 'ok', 30); assert cache.get('deploy-smoke') == 'ok'"
```

Накрая:

```bash
python manage.py check --deploy
```

Предупрежденията за HSTS subdomains/preload не трябва да се „поправят“
сляпо. Включи ги само ако всеки поддомейн е окончателно HTTPS-only.
Рестартирай WSGI услугата едва след успешните проверки, като използваш
реалното service име от сървъра.

## 6. Почистване на изтекли JWT записи

SimpleJWT пази outstanding/blacklisted refresh tokens в базата. Добави
ежедневен systemd timer. В примера замени `www-data` със същия user/group,
който изпълнява приложението.

`/etc/systemd/system/pgknma-flush-expired-tokens.service`:

```ini
[Unit]
Description=Remove expired PGKNMA JWT records
After=network.target

[Service]
Type=oneshot
User=www-data
Group=www-data
WorkingDirectory=/var/www/blog/cms/cms
ExecStart=/var/www/blog/cms/.venv/bin/python manage.py flushexpiredtokens
```

`/etc/systemd/system/pgknma-flush-expired-tokens.timer`:

```ini
[Unit]
Description=Daily cleanup of expired PGKNMA JWT records

[Timer]
OnCalendar=daily
Persistent=true
RandomizedDelaySec=15m

[Install]
WantedBy=timers.target
```

Активиране и проверка:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now pgknma-flush-expired-tokens.timer
sudo systemctl list-timers pgknma-flush-expired-tokens.timer
```

## 7. Reverse proxy и trusted client IP

Gunicorn трябва да слуша само на Unix socket или loopback адрес, защитен с
firewall. Никога не излагай application порта директно в интернет, когато
`DJANGO_TRUST_X_FORWARDED_PROTO=True` или `TRUSTED_PROXY_IPS` не е празен.
Custom IP/throttle логиката изисква `REMOTE_ADDR` да съдържа непосредствения
proxy IP. Провери това изрично, ако използваш Unix socket; при липсващ
`REMOTE_ADDR` използвай защитена loopback връзка вместо да допускаш всички
заявки под общия IP идентификатор.

Reverse proxy конфигурацията трябва:

- да пренасочва HTTP към HTTPS;
- да задава `Host` към оригиналния проверен host;
- да **презаписва**, а не да допълва, `X-Forwarded-Proto` и
  `X-Forwarded-For`;
- да поставя в `X-Forwarded-For` точно проверения client IP;
- при Cloudflare да приема `CF-Connecting-IP` само от официалните Cloudflare
  proxy ranges и да не го доверява от директен клиент;
- да маршрутизира `/api/`, `/admin/`, `/valentines/`, `/sitemap/` и другите
  Django endpoints към application server-а;
- да изпраща frontend routes като `/`, `/post/...`, `/news` и `/events` към
  SPA fallback-а, за да не съдържа sitemap-ът 404 адреси;
- да сервира `/static/` от `staticfiles_collected/`;
- да сервира `/files/` като неизпълними потребителски файлове;
- да забранява MIME sniffing и изпълнение на скриптове от качена медия;
- да задава location-specific request body limits, описани по-долу.

Меметата са изключение от общото публично `/files/` правило. Те се
визуализират само през `/api/memes/<id>/image/`, където Django проверява
статуса, feature flag-а, собственика и maintenance режима. Постави следния
Nginx блок **преди** generic `/files/` location-а:

```nginx
location ^~ /files/memes/ {
    return 404;
}

location /files/ {
    alias /var/www/blog/cms/cms/files/;
    add_header X-Content-Type-Options "nosniff" always;
}
```

Не заменяй първия блок с `alias` или `try_files`: старите директни meme URL-и
трябва винаги да връщат 404. След deploy/reload изчисти евентуално кеширани
`/files/memes/` от CDN/reverse proxy.

При един sanitizing proxy използвай:

```dotenv
DJANGO_TRUST_X_FORWARDED_PROTO=True
TRUSTED_PROXY_IPS=127.0.0.1,::1
VALENTINE_TRUSTED_PROXY_IPS=127.0.0.1,::1
```

`TRUSTED_PROXY_IPS` и `VALENTINE_TRUSTED_PROXY_IPS` са comma-separated
allowlists само на непосредствените peers, които могат директно да се свържат
към Django/Gunicorn. Не добавяй публични client IP адреси и не добавяй
Cloudflare ranges, ако Cloudflare не е непосредственият application peer.
Loopback стойностите са правилни само когато `REMOTE_ADDR` действително е
`127.0.0.1` или `::1`. Custom DRF throttles приемат forwarded client IP само
когато `REMOTE_ADDR` е в този allowlist; иначе игнорират forwarded headers.

Избери каноничен публичен host. Препоръчително е `www.pgknma.space` да
пренасочва към `https://pgknma.space`. Докато и двата hosts се обслужват,
и двата трябва да присъстват в `DJANGO_ALLOWED_HOSTS`,
`CORS_ALLOWED_ORIGINS` и `CSRF_TRUSTED_ORIGINS`.

### Upload лимити

Production template-ът задава:

- 5 MiB за публично качено меме;
- 10 MiB за едно изображение;
- 25 MiB за един документ;
- максимум 25 file parts в една multipart заявка.

`FILE_UPLOAD_MAX_MEMORY_SIZE` е прагът, след който Django записва файла
временно на диска; той не е лимит за размера на заявката.
`DATA_UPLOAD_MAX_MEMORY_SIZE=10 MiB` защитава non-file form полетата и
умишлено не е увеличен до сбора на всички файлове. Самият multipart parser
не използва тази стойност като общ file-body лимит.

Затова reverse proxy трябва да има собствен `client_max_body_size` или
еквивалент. Използвай малък лимит за публичните `/api/` endpoints и отделен
лимит за `/admin/blog/posts/`. Теоретичният максимум на една admin заявка с
10 изображения по 10 MiB и 10 документа по 25 MiB е около 350 MiB плюс
multipart overhead. Ако не искаш толкова голяма единична заявка, задай
по-нисък proxy лимит и качвай файловете на по-малки партиди. Провери също
свободното място и правата на системната temporary директория.

## 8. Smoke tests след рестарт

Провери публичния status endpoint и pagination envelope-а:

```bash
curl -fsS https://pgknma.space/api/site-status/

for endpoint in posts events notifications changelog approved-songs memes
do
    curl -fsS "https://pgknma.space/api/${endpoint}/?page=1" |
        python -c "import json,sys; d=json.load(sys.stdin); assert {'count','next','previous','results'} <= set(d)"
done
```

Провери и sitemap/frontend routing:

```bash
curl -fsS https://pgknma.space/sitemap/ > /dev/null
curl -fsSI https://pgknma.space/
curl -fsSI https://pgknma.space/news
```

Тъй като login/register изискват реален Turnstile token, направи следните
проверки през production frontend, а не с фиктивен CAPTCHA:

1. password login връща access и refresh token;
2. refresh endpoint връща нов refresh token и старият вече е отхвърлен;
3. logout blacklist-ва последния refresh token;
4. регистрация работи само когато е разрешена от SiteSettings;
5. Google входът работи, ако `ENABLE_GOOGLE_AUTH=True`;
6. posts/events/notifications/changelog/songs/memes се визуализират от
   pagination полето `results`, а не като стария директен array.

Провери логовете след smoke tests и едва тогава изключи maintenance mode.

## 9. Git история

Първо задължително се сменят credentials. След това Git историята може да
бъде пренаписана, но това изисква координиран force-push и нов clone от всеки
сътрудник. Пренаписването на историята не замества смяната на ключовете.
