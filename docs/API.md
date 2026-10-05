# REST API карта

Base path: `/api/`. Всички отговори са JSON. Default DRF policy е
authenticated; конкретните public endpoints изрично използват `AllowAny`.
Swagger/OpenAPI: `/api/schema/`, `/api/docs/`, `/api/redoc/` — само active
superuser, защото endpoint картата не е публична документация.

## Authentication

| Метод и път | Цел |
| --- | --- |
| `POST /api/auth/token/` | Login; връща access/refresh и current user payload. |
| `POST /api/auth/token/refresh/` | Ротира/опреснява refresh session. |
| `POST /api/auth/logout/` | Blacklist/revoke на текущата refresh session. |
| `POST /api/auth/register/` | Регистрация с validation/Turnstile, когато feature е включен. |
| `POST /api/auth/google/` | Google OAuth credential login/registration. |
| `POST /api/auth/google/link/` | Свързване на Google account към logged-in profile. |
| `POST /api/auth/verify-email/` | Потвърждение на email token. |
| `POST /api/auth/verify-email/resend/` | Controlled resend. |

Authenticated request използва `Authorization: Bearer <access>`. Не поставяй
токени в query parameters, logs или screenshots. Access token е кратък;
frontend `api_auth.ts` управлява refresh rotation.

## Публикации и коментари

| Метод и път | Достъп | Бележка |
| --- | --- | --- |
| `GET /api/posts/` | public | Paginated публични публикации. |
| `GET /api/posts/<id>/` | public според visibility | Detail. |
| `POST/PATCH/DELETE /api/posts/...` | permission-based | ViewSet/admin role policy. |
| `GET /api/posts/<post_pk>/comments/` | public | Списък с видими comments. |
| `POST /api/posts/<post_pk>/comments/add/` | authenticated | Създава comment; reply може да генерира personal notification. |

Ползвай `PostViewSet` и `PostSerializer` като източник за точни fields и
filters; не разчитай на frontend тип само като server validation.

## Профил, sessions и personal data

| Метод и път | Цел |
| --- | --- |
| `GET /api/profile/` | Current user + profile payload. |
| `PATCH /api/profile/public-settings/` | Public profile visibility/preferences. |
| `GET /api/users/<username>/profile/` | Public profile, ако owner го допуска. |
| `GET /api/gamification/profile/` | Level, points, badges, rewards, missions. |
| `GET /api/gamification/transactions/` | Paginated points history. |
| `GET /api/gamification/activity/` | Activity snapshot. |
| `POST /api/gamification/rewards/<key>/select/` | Equip/select reward. |
| `PATCH /api/gamification/preferences/` | Leaderboard preference. |
| `GET /api/gamification/leaderboard/` | Public/filtered leaderboard. |
| `POST /api/profile/avatar/` | Upload/select/reset avatar according to payload. |
| `GET /api/profile/sessions/` | Current account device sessions. |
| `POST /api/profile/sessions/<uuid>/revoke/` | Revoke one session. |
| `POST /api/profile/sessions/revoke-others/` | Revoke other sessions. |
| `GET /api/profile/export/` | Personal-data export. |
| `POST /api/auth/profile/change-password/` | Password update and session security flow. |
| `POST /api/auth/profile/change-username/` | Username update. |
| `POST /api/auth/profile/deactivate/` | Account deactivation. |

## User content and engagement

| Метод и път | Цел |
| --- | --- |
| `GET /api/my-content/songs/` | Собствени предложения за песен. |
| `DELETE /api/my-content/songs/<id>/delete/` | Изтриване по owner policy. |
| `GET /api/my-content/memes/` | Собствени memes. |
| `DELETE /api/my-content/memes/<id>/delete/` | Изтриване по owner policy. |
| `GET /api/my-content/comments/` | Собствени comments. |
| `DELETE /api/my-content/comments/<id>/delete/` | Собствен comment. |
| `POST /api/bell-song-suggestions/submit/` | Ново song suggestion; throttled. |
| `GET /api/approved-songs/` | Публичен approved list. |
| `POST /api/songs/<id>/vote/` | Vote; throttled. |
| `GET/POST /api/memes/...` | Meme ViewSet/create/list според status. |
| `POST /api/memes/<id>/vote/` | Meme vote. |
| `GET/POST /api/poll/...` | Poll ViewSet actions, status и answers. |

## Notifications и Push

| Метод и път | Цел |
| --- | --- |
| `GET /api/notifications/` | Notification feed; channel/filter според UI. |
| `GET /api/notifications/<id>/target/` | Проверява target преди redirect/open. |
| `POST /api/notifications/<id>/read/` | Mark one read. |
| `POST /api/notifications/read-all/` | Mark all applicable read. |
| `POST /api/notifications/<id>/archive/` | Archive one. |
| `POST /api/notifications/archive-read/` | Archive read items in channel. |
| `GET/PATCH /api/notifications/preferences/` | User category preferences. |
| `GET /api/push/config/` | Public VAPID public key + enabled state. |
| `GET/POST/DELETE /api/push/subscription/` | Device subscription, preferences, detach/manage token. |

Push management token е capability за anonymous device ownership. Никога не го
показвай, логвай или споделяй. Backend-ът приема само allowlisted public push
providers; виж `SECURITY_AND_DATA.md`.

## Public site data

| Метод и път | Цел |
| --- | --- |
| `GET /api/events/` | Публикувани събития. |
| `GET /api/program/` | Учебна програма. |
| `GET /api/changelog/` | Активни changelog записи. |
| `GET /api/terms-of-service/` | Активен текст на условията. |
| `GET /api/privacy-policy/` | Активна политика. |
| `POST /api/consent/` | Cookie consent record; throttled. |
| `GET /api/site-status/` | Maintenance/feature flags. |
| `GET /api/search/?q=` | Public search. |
| `POST /api/analytics/page-view/` | Anonymized page-view metric. |
| `POST /api/contact/` | Contact form; `AllowAny`, throttled. |
| `GET /api/memes/<id>/image/` | Permission-aware meme binary. |

## API change правила

Миниигрите използват `/api/games/`, `/api/games/sessions/` и
`/api/games/sessions/{uuid}/actions/`. Каталогът е публичен, играенето
изисква вход и собствена сесия. Пълният договор е в [GAMES.md](GAMES.md#api).

1. Не променяй response shape на използван endpoint без frontend промяна и
   regression tests.
2. Добавяй optional field вместо да преименуваш/премахваш field, когато има
   deployed frontend.
3. Backend винаги валидира независимо от Zod/form validation във frontend.
4. Documentирай нов endpoint тук и добави OpenAPI schema/example, когато е
   user/staff facing.
5. За destructive action използвай explicit POST/DELETE route, owner check,
   throttle при нужда и audit/notification effects.
