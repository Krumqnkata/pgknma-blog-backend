# Известия и Web Push

## Канали и категории

`Notification` е canonical user-facing record. Frontend разделя записите на
official/admin, automatic/system и gamification визуално; backend category
определя preference, retention и push eligibility.

Основните category constants са в `blog/notification_categories.py`:

- `general` — official/admin;
- `content_post`, `content_event`, `event_reminder`, `content_poll`,
  `content_meme`, `content_changelog` — automatic content (`content_meme` е
  обявяването на победителя в „Меме на седмицата“, настройка `memes_enabled`);
- `comment_reply`, `submission_status`, `mission_available` — personal/event;
- `security_login`, `security_account` — personal security;
- `gamification_*` — game channel;
- `team_chat_invite` — staff invite.

Добавянето на нова category изисква едновременно preference mapping, UI label,
retention behavior, test и decision дали може да стига до anonymous device.

## Notification lifecycle

```text
Domain change / command
  → create_notification_once(deduplication key)
  → post-save signal after transaction commit
  → snapshot eligible PushDelivery rows
  → immediate safe delivery attempt
  → scheduler retries failed pending rows (< 3 attempts)
  → read/archive/expiry cancels unsent deliveries
  → retention cleanup removes aged records
```

`deduplication_key` гарантира, че publish/edit/retry не правят duplicate
notification. `source_content_type/source_object_id/source_policy` свързват
notification с origin content; когато source стане недостъпен, policy може да
деактивира target или да пази detached history.

## Read, archive и expiry

Read/archive са per-user в `NotificationReadState`. Архивирането не е веднага
hard delete. `notification_retention.py` определя default expiry по category и
source; изтеклите записи първо стават auto-archived, а cleanup следва final
retention policy. При read/archive се отменят pending/failed deliveries към
подходящия user/device, за да не получи вече видяна нотификация на второ
устройство.

Important notification без expiry остава active, докато не се промени ръчно.

## Preferences

`NotificationPreference` е per user. `USER_PREFERENCE_FIELD_BY_CATEGORY`
свързва category с preference field. `PushSubscription` има отделни device
preferences за public topics: general/posts/events/polls/changelog.

Разлика:

- user preference: дали account като цяло иска даден тип;
- device preference: дали точно browser device иска public pushes;
- subscription active/failure state: дали endpoint технически е usable.

Анонимен browser може да получава само explicit `PUBLIC_PUSH_CATEGORIES`.
Personal/security/gamification content изисква свързан user account.

## Push subscription ownership

Subscription съдържа full endpoint (`TextField`) и unique SHA-256
`endpoint_hash`. Browser endpoint може да е по-дълъг от MySQL safe indexed
varchar, затова не сменяй този design с unique `CharField`.

При first subscribe server връща `management_token`. Frontend го пази локално
за anonymous device lifecycle. Последващият manage/transfer operation трябва
да е от current account owner или с valid management token. Друг logged-in
user не може да claim-не чужд endpoint само защото знае URL-а.

## Push dispatch и безопасност

`push.py` работи по вече snapshot-нати `PushDelivery` записи. Това означава,
че устройство, добавено след notification create, не получава исторически
pending notification. Retry също не създава delivery към ново устройство.

При изпращане:

1. endpoint се валидира от `push_endpoints.py`;
2. разрешени са HTTPS allowlisted provider hosts (configurable);
3. DNS трябва да върне само public address;
4. custom port, userinfo, fragment, redirect и env proxy са забранени;
5. заявката е с bounded `WEB_PUSH_REQUEST_TIMEOUT_SECONDS`;
6. 404/410 премахва dead subscription; други failures увеличават count и след
   праг деактивират subscription;
7. unsafe legacy endpoint се cancel-ва/деактивира без network request.

Това не е място за generic outbound HTTP. Не добавяй arbitrary endpoint/provider
само за да „проработи“ тест или една неизвестна subscription URL.

## Scheduler и диагностика

`dispatch_scheduled_notifications` се стартира от systemd timer на 5 минути.
Той обработва scheduled publication, event reminder, retry, expiration и
summary counts. Логът трябва да съдържа IDs/counts/status, не endpoint URLs,
management tokens или notification body с лични данни.

Полезни проверки:

```bash
python manage.py dispatch_scheduled_notifications --help
python manage.py test blog.test_search_push
curl -i https://pgknma.space/api/push/config/
```

Cloudflare трябва да bypass-ва browser JS challenge/cache за `/api/push/*`, но
Django throttling и subscription validation остават включени.

## При промяна

- Добави category → preferences, retention, frontend label/filter/tests.
- Добави public push type → explicit device preference и abuse/rate review.
- Промени source policy → тест за source deletion/unpublish и target route.
- Промени provider config → production `.env` template + `check` + real browser
  smoke test, без да отпускаш arbitrary hostname.
