# Конфигурация и environment променливи

Този документ е карта на настройките, които се четат от `cms/settings.py`.
Шаблоните [../.env.example](../.env.example) и
[../.env.production.example](../.env.production.example) остават практичният
източник за имената и примерните стойности. Не записвай истински ключове в
документация, Git или ticket-и.

## Правило за `.env`

- Local development: копирай `.env.example` като `.env` и използвай само
  стойности, нужни за функцията, по която работиш.
- Production: създай `.env` от `.env.production.example` **само при първа
  инсталация**. При update добавяй липсващите ключове ръчно; не презаписвай
  съществуващ файл със secrets.
- `DJANGO_DEBUG` е `False` по подразбиране. Локално трябва изрично
  `DJANGO_DEBUG=True` (вече е в `.env.example`).
- `DJANGO_DEBUG=False` не допуска празен `DJANGO_SECRET_KEY`, production
  database или некоректни security boolean-и. Това е умишлен fail-fast
  механизъм.
- Vite `VITE_*` настройки са отделни и принадлежат на frontend repository;
  те никога не са място за backend secret.

## Основни групи

| Група | Променливи | Какво контролира |
| --- | --- | --- |
| Django и proxy | `DJANGO_DEBUG`, `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_TRUST_X_FORWARDED_PROTO`, `DJANGO_USE_X_FORWARDED_HOST`, `TRUSTED_PROXY_IPS` | Debug, trusted host/proxy и generated HTTPS URLs. |
| Database | `DB_ENGINE`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, `DB_CONN_MAX_AGE` | MySQL/MariaDB connection. SQLite е само изрично допустим временен режим чрез `ALLOW_PRODUCTION_SQLITE`. |
| Public URLs | `PUBLIC_SITE_URL`, `FRONTEND_URL`, `SITEMAP_DOMAIN`, `SITEMAP_PROTOCOL`, `FRONTEND_INDEX_PATH` | Canonical links, email return links, sitemap и server-side social preview fallback. |
| Login и anti-bot | `ENABLE_GOOGLE_AUTH`, `GOOGLE_CLIENT_ID`, `TURNSTILE_SECRET_KEY`, email verification settings | OAuth, Turnstile и lifecycle на email verification. |
| Email | `RESEND_API_KEY`, `DEFAULT_FROM_EMAIL`, `EMAIL_VERIFICATION_REPLY_TO` | Resend API sender и reply-to адрес. |
| ИИ (по избор) | `CF_ACCOUNT_ID`, `CF_API_TOKEN`, `AI_REQUEST_TIMEOUT_SECONDS`, `AI_BATCH_SIZE`, `AI_ASSISTANT_DAILY_LIMIT`, `AI_SITE_KNOWLEDGE_PATH`, `AI_SITE_KNOWLEDGE_URL`, `THROTTLE_AI_ASSISTANT_*` | Cloudflare Workers AI. Копчетата и моделите са в админа. Виж [AI.md](AI.md). |
| Web Push | `WEB_PUSH_ENABLED`, `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT`, `WEB_PUSH_*` | Push capability, allowlist, rate limit, timeout и event reminders. |
| Upload-и | `FILE_UPLOAD_MAX_MEMORY_SIZE`, `DATA_UPLOAD_*`, `MAX_*_UPLOAD_SIZE`, `TEAM_CHAT_MAX_IMAGE_UPLOAD_SIZE` | Глобални и domain-specific upload лимити. |
| Browser security | `CORS_ALLOWED_ORIGINS`, `CORS_ALLOW_CREDENTIALS`, `CSRF_TRUSTED_ORIGINS`, `SECURE_*` | Allowed browser origins, CSRF и HTTPS/HSTS cookies. |
| Cache | `CACHE_BACKEND`, `CACHE_LOCATION`, `CACHE_DEFAULT_TIMEOUT`, `CACHE_MAX_ENTRIES`, `CACHE_CULL_FREQUENCY` | Django cache backend и eviction behavior. |
| Abuse control | `THROTTLE_*` | DRF rate limits за anonymous/user/login/register/contact/search/push и други рискови actions. |
| Retention | `BLOG_SESSION_LAST_SEEN_UPDATE_SECONDS`, `BLOG_SESSION_RETENTION_DAYS`, `PERSONAL_DATA_RETENTION_DAYS` | Session activity writes и cleanup срокове. |
| Optional network data | `MAXMIND_ASN_DATABASE` | Локален MaxMind ASN database за network/operator functions, когато е конфигуриран. |
| Valentine module | `VALENTINE_TRUSTED_PROXY_IPS`, `VALENTINE_LOGIN_*`, `VALENTINE_PIN_*` | Отделни proxy и brute-force ограничения за Valentine module. |

## Критични production стойности

### Ключове, които трябва да са различни

`DJANGO_SECRET_KEY`, `JWT_SIGNING_KEY` и `VAPID_PRIVATE_KEY` са различни
секрети с различна цел. Не преизползвай един ключ между тях. При съмнение, че
ключ е попаднал в Git history, log или screenshot, ротирай го и прекрати
старите сесии/credentials според засегнатия механизъм.

### Database избор

Нормалният production избор е MySQL/MariaDB. `DB_ENGINE=sqlite` в production
изисква изрично `ALLOW_PRODUCTION_SQLITE=True` и еднопроцесен worker; иначе
приложението умишлено отказва да стартира. Виж подробностите и backup
изискванията в [OPERATIONS.md](OPERATIONS.md).

### Proxy, Cloudflare и HTTPS

`TRUSTED_PROXY_IPS` съдържа само IP адресите на действителния reverse proxy.
Не включвай широки мрежи „за да проработи“. `DJANGO_TRUST_X_FORWARDED_PROTO`
и `DJANGO_USE_X_FORWARDED_HOST` се включват само когато Apache надеждно
задава тези headers след TLS termination.

`CORS_ALLOWED_ORIGINS` и `CSRF_TRUSTED_ORIGINS` са списъци на точни origins
със схема, например `https://pgknma.space`; това не са URL paths и не трябва
да съдържат wildcard за production.

### Push

За включен Push са нужни `WEB_PUSH_ENABLED=True`, public/private VAPID key
pair и валиден `VAPID_SUBJECT` (`mailto:` или HTTPS URL). `VAPID_PRIVATE_KEY`
може да е PEM съдържание или абсолютен path, поддържан от `pywebpush`.
Остави `WEB_PUSH_ALLOWED_ENDPOINT_HOSTS` ограничен до истинските push
провайдъри; не го разширявай, за да „оправиш“ една неуспешна subscription.

### Boolean и числови стойности

Boolean parser-ът приема `true/false`, `1/0`, `yes/no` и `on/off`.
Невалидна стойност трябва да се поправи, не да се заобикаля. За размери,
timeouts, retention и throttles използвай стойности с ясна единица според
коментара в `.env.production.example`; след промяна направи smoke test на
засегнатия flow.

## Процедура при добавяне на нова настройка

1. Дай безопасен default за local development или fail-fast при задължителен
   production secret.
2. Добави ключа и коментар в двата environment template-а, ако е приложимо.
3. Групирай и опиши ключа тук, включително кой го притежава и риск от грешна
   стойност.
4. Добави тест за parser/behavior, когато настройката променя security,
   authentication, permission или data retention.
5. Добави upgrade note в [operations/DEPLOYMENT.md](operations/DEPLOYMENT.md), ако deployed
   `.env` трябва да се промени.

## Безопасна проверка

```bash
python manage.py check
python manage.py check --deploy
```

Втората команда се изпълнява със същата production `.env` и runtime
потребител, с които ще стартира приложението. Тя не замества Apache/Cloudflare
smoke test или проверка на конкретна външна услуга.
