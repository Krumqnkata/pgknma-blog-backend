# Диагностика и чести operational проблеми

Започни с най-тесния слой, който може да е повреден: browser/Vite, Apache,
Django process, database, или външен provider. Не изключвай security защити
като трайно „решение“ на единична грешка.

## Базова диагностика

```bash
python manage.py check
python manage.py showmigrations
python manage.py test blog
sudo apachectl configtest
systemctl status <django-service-name> --no-pager
```

В production преглеждай application и Apache error log около точния час на
грешката. Не публикувай Authorization headers, cookies, JWT, VAPID capability
tokens или реални endpoint URL-и от push subscription.

## Симптом → вероятен слой → безопасна проверка

| Симптом | Най-вероятен слой | Проверка/следваща стъпка |
| --- | --- | --- |
| React route връща 404 след browser refresh | Apache SPA rewrite / DocumentRoot | Провери, че реалните assets се обслужват като файлове, а останалите SPA paths отиват към `index.html`. API/admin paths трябва да са изключени от този fallback. |
| `/api/...` връща HTML challenge вместо JSON | Cloudflare WAF | Провери rule-а за точно този API path. Browser-only managed challenge не работи за service worker, curl и API client. Не изключвай защита за цялото `/api/`. |
| Login работи локално, но има redirect/CSRF/protocol проблем в production | Trusted proxy или public URL config | Свери Apache `X-Forwarded-Proto`, `TRUSTED_PROXY_IPS`, `DJANGO_TRUST_X_FORWARDED_PROTO`, `PUBLIC_SITE_URL`, `FRONTEND_URL`, CORS/CSRF origin-и. |
| `migrate` отказва с MySQL key/index error | Database/version или migration | Виж `python manage.py sqlmigrate blog <номер>`, MySQL/MariaDB версията и charset/index lengths. Не маркирай migration като applied на ръка. Backup преди повторен опит. |
| Push permission е дадено, но няма запис/известие | Browser subscription POST, API, VAPID или WAF | В browser Network провери `POST /api/push/subscription/`; после server traceback/log и `GET /api/push/config/`. Увери се, че endpoint не получава Cloudflare challenge. |
| Scheduled reminder не се изпраща | systemd timer или dispatch data | `systemctl list-timers`, `journalctl -u pgknma-notifications.service`, после ръчно `python manage.py dispatch_scheduled_notifications` само в контролирана среда. |
| Cleanup/archiving не се случва | data-retention timer | Провери `pgknma-data-retention.timer`, journal и environment/DB достъпа на service user. Не стартирай destructive cleanup без backup и dry-run, ако командата го поддържа. |
| Image/chat attachment дава 403/404 | Protected file route или ownership | Не заменяй protected URL с публичен `/files/` Alias. Провери user access, database record и Django route first. |
| Нов deployment показва стар frontend | Build/cache/service worker | Потвърди, че правилният `dist` е поставен в Apache DocumentRoot, после тествай инкогнито/hard refresh. Не изтривай browser cache като единствено доказателство за fix. |
| Site status/feature flags блокират page | Backend settings response/cache | Провери `/api/site-status/`, admin setting и дали frontend е получил успешен, а не failure response. App не бива да приема transient API failure като maintenance. |

## Локално срещу production

Vite proxy-то прави local API заявки да изглеждат same-origin. В production
browser-ът вижда Cloudflare/Apache, което добавя TLS, rewrites, headers, WAF и
static/media rules. Затова regression, която се проявява само production,
се проверява с production-like request path, а не само с `npm run dev`.

## Кога да ескалираш

- При authentication, permission, private upload или secret incident: спри
  риска, запази минимални безопасни logs и не прави „временно public“ решение.
- При database schema/data problem: backup, exact migration/DB version и
  read-only inspection преди change.
- При външен provider (Google, Resend, push): запиши HTTP status и provider
  error code без credential-и; не споделяй цял request payload.

Свързани документи: [OPERATIONS.md](OPERATIONS.md),
[SECURITY_AND_DATA.md](SECURITY_AND_DATA.md),
[NOTIFICATIONS_AND_PUSH.md](NOTIFICATIONS_AND_PUSH.md) и
[operations/DEPLOYMENT.md](operations/DEPLOYMENT.md).
