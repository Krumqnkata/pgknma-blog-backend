# Архитектура на платформата

## Компоненти

```text
Браузър
  │ HTTPS / JSON / static assets
  ▼
Cloudflare ──► Apache :8443
                 ├─ /api/, /admin, /valentines, /hqs, /sitemap → Django/Waitress :8000
                 ├─ /files/ → Apache static media (с изключения за memes)
                 └─ останалите пътища → React/Vite build: dist/index.html
                                              │
                                              ▼
                                        Django REST API
                                              │
                         ┌────────────────────┼────────────────────┐
                         ▼                    ▼                    ▼
                       MySQL              файлове            Resend / Google /
                    (SQLite local)      protected chat       Turnstile / Push services
```

Frontend repository: `../Frontend`. Backend repository: тази директория.
Локалният Vite dev server е на `5173` и proxy-ва `/api`, `/files` и
`/valentines` към Django на `8000`.

## Request flow

### Публична страница

1. Apache връща React `dist` за SPA route, например `/news`.
2. `BrowserRouter` избира lazy-loaded React page.
3. Page/component използва `src/lib/api.tsx` и `apiRequest()`.
4. Django route от `blog/urls.py` достига view/viewset/serializer/queryset.
5. JSON отговорът се кешира/управлява от TanStack Query във frontend-а.

### Влязъл потребител

1. Frontend пази access и refresh JWT в `localStorage`.
2. `api_auth.ts` добавя `Authorization: Bearer <access>`.
3. При 401 един заявен refresh към `/api/auth/token/refresh/` обновява
   токените и опашката от чакащи заявки; при неуспех сесията се изчиства.
4. `SessionJWTAuthentication` проверява access token и свързаната
   `BlogLoginSession`.
5. View permission-ите филтрират по `request.user`; client ID/username никога
   не е достатъчно доказателство за собственост.

### Нотификация и Web Push

1. Content action или management command създава `Notification` чрез
   `automatic_notifications.create_notification_once()`.
2. Model signal snapshot-ва подходящите `PushDelivery` записи след commit.
3. `push.py` изпраща по delivery лог с retry/failure state.
4. Endpoint-ът се проверява за доверен доставчик, публичен DNS/IP, HTTPS,
   redirect забрана и bounded timeout.
5. Browser service worker показва системната нотификация и при click води към
   `/notification/<id>`.

### Scheduled work

- `pgknma-notifications.timer`: на 5 минути стартира
  `dispatch_scheduled_notifications`; публикува due content notifications,
  event reminders, retry-ва push и отменя изтекли deliveries.
- `pgknma-data-retention.timer`: дневно стартира
  `cleanup_personal_data`; изпълнява retention/purge процеси.

И двете са oneshot systemd услуги, не постоянно работещи Python процеси.

## Основни domain области

| Област | Основни backend части | Frontend входни точки |
| --- | --- | --- |
| Публикации | `Posts`, `Category`, `PostImage`, `PostDocument`, `PostViewSet` | `Index`, `News`, `Post`, `NewsGrid` |
| Коментари | `Comments`, comment views/serializers | `Comments` в публикация |
| Календар | `Event`, `ScheduleEntry`, `ScheduleImport` | `SchoolCalendar`, `Program` |
| Анкети | `PollQuestion`, `PollOption`, `PollAnswer` | `WeeklyPoll`, `PollStatistics` |
| Предложения | `BellSongSuggestion`, `MemeOfWeek` | `BellSuggest`, `MemeOfTheWeek` |
| Профили | `UserProfile`, `BlogLoginSession`, `GoogleIdentity` | `MyProfilePage`, `AccountSettings` |
| Геймификация | нива, точки, мисии, значки, награди | `GamificationProgress`, profile tabs |
| Известия | `Notification*`, `PushSubscription`, `PushDelivery` | bell, preferences, Push settings |
| Екипен чат | `TeamChat*`, `admin_chat.py`, protected uploads | Django admin само |
| Валентинки | отделно Django app `valentine` | `/valentines/` |

## Граници, които не трябва да се смесват

- `Notification` е user-facing система; `GamificationNotification` е отделна
  admin/audit категория. Не показвай security или game записи като официално
  общо съобщение.
- Team chat е само за `is_staff` и permission-aware discussions; не е
  публичен потребителски чат.
- Meme файловете са authorization-aware endpoint-и и не трябва да се сервират
  директно от `/files/`.
- Protected team-chat files са извън `MEDIA_ROOT` и се връщат само през admin
  views.
- Production MySQL е очакваният storage; local SQLite е development fallback.

## Къде се намира какво

| Път | Роля |
| --- | --- |
| `cms/settings.py` | Environment parsing, security, DB, REST, cache, JWT, uploads. |
| `cms/urls.py` | Root routes, JWT, admin/team chat, sitemap, nested API. |
| `blog/urls.py` | Публичен и authenticated REST API. |
| `blog/models.py` | Данни и част от model validation. |
| `blog/views.py` | API orchestration и permissions. |
| `blog/serializer.py` | Request validation и response shape. |
| `blog/admin*.py` | Django Unfold admin, menu, roles, team chat. |
| `blog/gamification/` | Rules/services за точки, мисии, rewards. |
| `blog/push*.py` | Registration, dispatch, endpoint safety. |
| `deployment/`, `deploy/` | systemd unit/template файлове. |
