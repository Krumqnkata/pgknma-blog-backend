# ПГКНМА Блог — backend и platform документация

Тази папка е главната техническа документация за Django частта и за
оперативната архитектура на платформата. Frontend документацията е в отделното
repository `../../Frontend/docs/`, защото frontend и backend се version-ват
самостоятелно.

## Бърза ориентация

1. Нов разработчик: [ARCHITECTURE.md](ARCHITECTURE.md) →
   [DEVELOPMENT_AND_TESTING.md](DEVELOPMENT_AND_TESTING.md) →
   [BACKEND.md](BACKEND.md).
2. Работа по API: [API.md](API.md) и защитената Swagger страница
   `/api/docs/` (само active superuser).
3. Работа по админ панела/роли: [ADMIN_AND_ROLES.md](ADMIN_AND_ROLES.md).
4. Production, timer-и и диагностика: [OPERATIONS.md](OPERATIONS.md).
5. Данни, auth, файлове и security правила:
   [SECURITY_AND_DATA.md](SECURITY_AND_DATA.md).
6. Нов production/local environment: [CONFIGURATION.md](CONFIGURATION.md).

## Документи

| Документ | За какво служи |
| --- | --- |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Компоненти, request flows и граници между frontend, Django, MySQL, Apache и Cloudflare. |
| [BACKEND.md](BACKEND.md) | Django приложения, модели, services, signals, management commands и важни конвенции. |
| [API.md](API.md) | Карта на REST API-то, authentication, статуси и групи endpoint-и. |
| [ADMIN_AND_ROLES.md](ADMIN_AND_ROLES.md) | Unfold admin, permission-based sidebar, роли, team chat и content workflow. |
| [OPERATIONS.md](OPERATIONS.md) | Локално стартиране, deployment, static/media, timers, backup и безопасни проверки. |
| [SECURITY_AND_DATA.md](SECURITY_AND_DATA.md) | Токени, CORS/CSRF, rate limits, upload-и, push, retention и лични данни. |
| [DEVELOPMENT_AND_TESTING.md](DEVELOPMENT_AND_TESTING.md) | Работен процес, миграции, backend проверки и test suite. |
| [GAMIFICATION.md](GAMIFICATION.md) | Точки, нива, мисии, значки, награди и lifetime статистики. |
| [NOTIFICATIONS_AND_PUSH.md](NOTIFICATIONS_AND_PUSH.md) | Канали, delivery lifecycle, preferences, push и scheduler. |
| [TEAM_CHAT.md](TEAM_CHAT.md) | Staff chat, discussions, attachments, sharing и access control. |
| [CONFIGURATION.md](CONFIGURATION.md) | Всички групи environment настройки, критични production стойности и безопасен upgrade процес. |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Диагностика на routing, WAF, Push, timers, uploads, migrations и cache проблеми. |
| [operations/DEPLOYMENT.md](operations/DEPLOYMENT.md) | Подробен production deployment runbook. |
| [operations/PUSH_NOTIFICATIONS_DEPLOYMENT.md](operations/PUSH_NOTIFICATIONS_DEPLOYMENT.md) | Deployment и проверка на Web Push инфраструктурата. |
| [plans/SUPPORT_CONVERSATIONS_PLAN.md](plans/SUPPORT_CONVERSATIONS_PLAN.md) | План за бъдеща система за разговори със/без профил, без email. |
| [../legal/PRIVACY_POLICY.md](../legal/PRIVACY_POLICY.md) | Repository reference версия на Политиката за поверителност. |
| [../legal/TERMS_OF_USE.md](../legal/TERMS_OF_USE.md) | Repository reference версия на Условията за ползване. |

## Източник на истина

Документите описват текущия код, но при разминаване кодът и тестовете са
авторитетни. При промяна на архитектура, endpoint, permission, environment
variable или systemd unit, обнови съответния документ в същия change.

Не записвай реални пароли, VAPID private key, JWT/Google/Turnstile/Resend
тайни или production URL с credentials в тази папка.
