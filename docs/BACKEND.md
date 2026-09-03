# Backend: Django структура и конвенции

## Runtime и зависимости

- Python 3.12+, Django 6.0, Django REST Framework и drf-spectacular.
- MySQL чрез `mysqlclient` в production; SQLite по подразбиране само при local
  `DJANGO_DEBUG=true`.
- Waitress/Gunicorn могат да служат WSGI приложението; текущият Apache reverse
  proxy е към localhost application server.
- Anymail/Resend се използва за account email verification; това е отделно от
  бъдещия support-conversation план, който изрично няма email.
- `django-unfold` е admin UI; `django-cleanup` обработва подходящи media files.

Виж точните pinned пакети в `requirements.txt`.

## Приложения

### `blog`

Главното приложение. Съдържа content, accounts, polls, submissions,
notifications, gamification, page analytics, policies, team chat и site
settings.

Важни подмодули:

| Модул | Отговорност |
| --- | --- |
| `auth.py`, `session_auth.py` | Google login/link, JWT session binding, logout/revoke. |
| `serializer.py` | DRF validation и API representations. |
| `permissions.py`, `throttling.py` | Object/site permission-и и trusted-proxy throttles. |
| `automatic_notifications.py` | Deduplicated event/content notifications. |
| `push.py`, `push_endpoints.py`, `push_signals.py` | Push queue snapshot, delivery, retry и SSRF защита. |
| `notification_*` | Категории, source lifecycle, expiry/retention. |
| `gamification/` | Point rules, awards, missions, badges, rewards. |
| `team_chat.py`, `admin_chat.py` | Staff чат, discussion ACL, attachments, signed sharing. |
| `personal_data_export.py`, `data_retention.py` | GDPR export и cleanup. |
| `network.py`, `network_operator.py` | Client IP и network metadata, когато е позволено. |

### `valentine`

Самостоятелно приложение под `/valentines/` със свои models, admin permissions
и кампании. Не добавяй Valentine behavior в `blog` без ясна причина.

## Данни: model карта

### Content и community

- `Category`, `Posts`, `PostImage`, `PostDocument`, `Comments`.
- `Event`, `ScheduleEntry`, `ScheduleImport`.
- `PollQuestion`, `PollOption`, `PollAnswer`.
- `BellSongSuggestion`, `MemeOfWeek`, `ContactSubmission`.
- `TermsOfService`, `PrivacyPolicy`, `Changelog`, `SiteSettings`.

Публикациите имат publish/allow state; публичните querysets трябва да филтрират
само разрешено, публикувано и due съдържание. Не връщай draft fields в public
serializer по подразбиране.

### Identity и profile

- Django `User` е основният account model.
- `UserProfile` пази public profile, avatar, level/points preferences и
  visibility.
- `GoogleIdentity` свързва Google subject с account.
- `BlogLoginSession` пази device/session lifecycle и позволява revoke.
- `EmailVerificationToken` е за account verification.

### Gamification

- `GamificationLevel`, `GamificationPointRule`, `PointTransaction`.
- `Mission`, `UserMissionProgress`, `MissionActivity`.
- `Badge`, `UserBadge`, `DigitalReward`, `UserDigitalReward`.
- `LifetimeProfileActivity` пази lifetime броячи, за да не намаляват при
  moderation/deletion на източника.

Всички точки/награди минават през services, не чрез директна промяна на
потребителски totals в view/admin action.

### Notifications

- `Notification`: canonical user-facing record с category, action URL,
  audience, deduplication и source tracking.
- `NotificationReadState`: read/archive state per user.
- `NotificationPreference`: user category preferences.
- `PushSubscription`: browser endpoint, hashed endpoint/token ownership,
  device preferences и failure state.
- `PushDelivery`: immutable-ish delivery attempts per notification/device.

### Team chat

- `TeamChatMessage`, reactions, revisions, read state и shared object refs.
- `TeamChatDiscussion`, access/read state и protected attachments.

Discussion access е отделен от general chat. Всеки `is_staff` вижда general
chat, но private discussion се проверява през service ACL.

## View и serializer правила

1. `views.py` оркестрира HTTP/auth/permission/queryset, но не трябва да
   съдържа голяма повторяема domain логика.
2. Serializer е границата за untrusted request input: length, type, choices,
   ownership-independent validation.
3. Object ownership се проверява във view/service чрез `request.user`, не от
   client-provided ID.
4. Ползвай `select_related`/`prefetch_related` при list/detail, когато
   serializer докосва relations.
5. За state transition-и с race risk ползвай `transaction.atomic()` и
   `select_for_update()`; виж автоматичните notifications като пример.
6. Използвай standard DRF status codes: 201 create, 200 update/action, 204
   delete без body, 400 validation, 401 no/invalid auth, 403 permission,
   404 inaccessible object, 429 throttle.

## Миграции

- Никога не редактирай приложена migration.
- След model change: `python manage.py makemigrations`, прегледай migration-а,
  после `python manage.py migrate` локално.
- За MySQL не слагай `unique=True` на дълъг `CharField` за browser endpoint.
  `PushSubscription` е пример: пълната стойност е `TextField`, а SHA-256
  digest е unique/indexed.
- При data migration мисли за production volume: `iterator()`, batches,
  reversibility или ясно documented irreversible step.

## Management commands

| Команда | Употреба |
| --- | --- |
| `dispatch_scheduled_notifications` | Due posts/events/polls/missions/changelog, push retry и expiry handling. |
| `cleanup_personal_data` | Retention cleanup; записва `PersonalDataCleanupRun`. |
| `cleanup_blog_sessions` | Премахва стара session history. |
| `repair_notification_sources` | Backfill/sync на notification source lifecycle. |
| `sync_admin_roles` | Създава/синхронизира predefined groups; `--reset` презаписва custom role changes. |
| `create_role_test_users` | Локални test users за permission smoke checks. |
| `assign_random_default_avatars` | Maintenance/backfill за avatars. |
| `send_test_email` | Ръчна проверка на Resend setup. |

Преди command в production прочети `--help`, използвай dry-run ако има и не
пускай data cleanup без backup.
