# Сигурност, файлове и данни

## Secrets и environment

Secrets са само в процес environment или неcommit-нат `.env`. Шаблони:
`.env.example` и `.env.production.example`. Никога не commit-вай реални
стойности за:

- `DJANGO_SECRET_KEY`, `JWT_SIGNING_KEY`;
- MySQL password;
- `TURNSTILE_SECRET_KEY`, `RESEND_API_KEY`, Google client secrets;
- VAPID private key и token-и;
- Cloudflare/API credentials.

Production settings fail closed за ключови празни стойности: `DJANGO_DEBUG`
false изисква secret, DB configuration, Turnstile secret и JWT signing key.

## Authentication и sessions

- JWT access lifetime е 30 минути; refresh lifetime 7 дни и се ротира.
- `CHECK_REVOKE_TOKEN` обвързва token validity с password hash; revoke/logout
  blacklists refresh token-а и `BlogLoginSession` пази device lifecycle.
- Frontend пази токените в localStorage и добавя Bearer header. Това означава,
  че XSS prevention е критично; не добавяй unsafe HTML rendering.
- Ново устройство и важни account промени създават лични security
  notifications.
- Google login е feature-flag-нат и backend-ът е авторитет за token verify.

## Authorization

- Default DRF permission е `IsAuthenticated`; public routes избират `AllowAny`
  експлицитно.
- За owner content използвай queryset filter + object check, не само client
  parameter.
- `is_staff` разрешава влизане в admin; реалните възможности са Django
  permissions/model admin checks.
- OpenAPI schema е само за active superuser.
- Protected meme/team-chat media не трябва да получава Apache `Alias` обход.

## CORS, CSRF и proxy

- CORS е ограничен до `CORS_ALLOWED_ORIGINS`; `CORS_ALLOW_ALL_ORIGINS=False`.
- `CORS_ALLOW_CREDENTIALS` е environment controlled и по default false.
- CSRF trusted origins са отделна allowlist.
- `X-Forwarded-Proto` и host се доверяват само при explicit production proxy
  configuration. Apache/Cloudflare трябва да са единственият път до backend-а.
- Rate-limit key използва trusted-proxy-aware throttling; не заменяй с raw
  unchecked header.

## Rate limits и anti-abuse

`REST_FRAMEWORK.DEFAULT_THROTTLE_RATES` в `cms/settings.py` съдържа отделни
scope-ове за login, register, Google auth, contact, consent, username/password
validation, submissions, vote, session management, export, search, push,
avatar, verification resend, gamification и analytics.

При нов public/action endpoint:

1. реши дали anonymous/user/scoped throttle е нужен;
2. добави scope и environment override;
3. добави тест за 429 или поне scope binding;
4. не връщай различна грешка, която позволява account/email enumeration.

## Input, rendering и uploads

- Serializers ограничават дължина, type и choices; React/Zod е UX, не security.
- `NotificationSerializer` escape-ва plaintext; не използвай markdown като
  trust boundary.
- Markdown rendering трябва да остане sanitised; нов `rehype-raw` usage се
  разглежда внимателно.
- Upload limits са settings (`MAX_*_UPLOAD_SIZE`, data upload limits).
- Avatar, meme, image и document validators са отделни; извиквай правилния
  validator, не само content type от browser.
- Документи и images се пазят в media; protected team-chat files са отделно.

## Web Push

Push subscription endpoint е untrusted input. `push_endpoints.py` налага:

- HTTPS (debug localhost exception само локално);
- allowlist на browser push provider hosts;
- без username/password, fragment и custom port;
- DNS resolution към публични IP адреси;
- no redirects, no environment proxy и bounded timeout.

`PushSubscription.endpoint_hash` и management-token hash пазят уникалност и
ownership без MySQL long-index проблем. Authenticated user може да управлява
само своя endpoint; transfer/recovery изисква valid management token според
view policy. Legacy unsafe endpoint се деактивира вместо да се contact-ва.

## Данни, privacy и retention

- `ConsentRecord` пази cookie consent audit.
- `PersonalDataExportView` и `personal_data_export.py` събират данните на
  logged-in user; при нов личен model добави export mapping и tests.
- `cleanup_personal_data` следва `PERSONAL_DATA_RETENTION_DAYS`; notifications
  използват category/source expiry и архивиране преди final cleanup.
- `BlogLoginSession` има отделен retention период.
- `PageView` е предназначен за минимална/anonymized analytics; не добавяй IP
  или fingerprinting без privacy review.

При промяна на лични данни, purpose или външен processor обнови
`legal/PRIVACY_POLICY.md`, `legal/TERMS_OF_USE.md` при нужда, export и cleanup behavior.

## Security review checklist

- [ ] Новият route има правилен permission + ownership check.
- [ ] Няма IDOR чрез integer/object ID или direct media URL.
- [ ] Request data минава serializer/валидатор.
- [ ] HTML, URLs, uploads и external fetch са обработени defensively.
- [ ] Няма secret/token/PII в logs, exception или client response.
- [ ] Throttle и Turnstile са обмислени за public action.
- [ ] Database migration има constraint/index и не чупи MySQL.
- [ ] Има tests за unauthorized, cross-user и invalid input сценарии.
