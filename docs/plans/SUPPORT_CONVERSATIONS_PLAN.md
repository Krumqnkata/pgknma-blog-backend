# План: запитвания и разговори с екипа

## 1. Цел

Да се превърне сегашната еднократна контактна форма в защитена система за
разговори между посетител/потребител и екипа на сайта.

Системата работи в два режима:

| Изпращач | Къде продължава разговора | Как доказва достъп |
| --- | --- | --- |
| Влязъл потребител | „Моите запитвания“ в профила | Вход в собствения профил |
| Анонимен посетител | Страница за анонимно запитване | Номер на запитването + таен код за възстановяване |

**Важно решение:** функцията не изпраща имейли. Няма email известия, magic
links или отговори по поща. Анонимният посетител вижда нови отговори при
следващо отваряне на сайта със същия браузър или чрез ръчно въвеждане на
номера и тайния код на друго устройство.

## 2. Принципи и граници

### В обхват за първата версия

- Създаване на запитване от контактната форма.
- Двупосочен разговор: посетител/потребител ↔ екип с достъп до админа.
- Статус, приоритет, отговорник и вътрешни бележки за екипа.
- Преглед и отговор от Django admin.
- Профилен екран „Моите запитвания“ за влезли потребители.
- Анонимен достъп чрез таен код, без email.
- Известие в камбанката и Web Push само за влезли потребители, когато получат
  отговор.
- История, защита от IDOR, rate limits, лични данни и срокове за съхранение.

### Извън обхвата на първата версия

- Имейл известия, email reply или magic links.
- Прикачени файлове и изображения. Те изискват отделни антивирусни, MIME,
  storage и retention правила.
- Общ чат между потребители.
- Реално време чрез WebSocket. За начало данните се обновяват при отваряне на
  страницата и след изпращане на съобщение.
- Анонимни browser push известия. Това може да се добави по-късно само с
  изрично opt-in и връзка към конкретно устройство.

## 3. Потребителски сценарии

### 3.1 Влязъл потребител

1. Отваря `/contact`.
2. Формата попълва име и имейл от профила, като имейлът остава редактиуем само
   ако продуктово е необходимо.
3. Изпраща първото съобщение.
4. Получава потвърждение и бутон „Отвори запитването“.
5. В профила вижда таб „Моите запитвания“ с непрочетени отговори и статус.
6. При отговор от екипа получава лично site notification и, ако е позволил,
   Web Push.
7. Може да отговори, докато разговорът е отворен; при затворен разговор вижда
   бутон „Отвори отново“.

### 3.2 Анонимен посетител

1. Отваря `/contact`, въвежда име, незадължителен email за контактна
   информация и първо съобщение.
2. След успешно изпращане получава:
   - публичен номер, например `SUP-2026-000123`;
   - таен recovery code с поне 256 бита ентропия;
   - ясно предупреждение: „Запазете кода. Не можем да го покажем повторно.“
3. Браузърът съхранява номера и кода само локално в `localStorage` под отделен
   key, например `support-guest-conversations`.
4. Веднага се отваря `/support/guest/SUP-2026-000123`; достъпът е през код в
   request header/body, никога в URL.
5. При изчистване на браузърните данни или на друго устройство посетителят
   отваря „Продължи запитване“, въвежда номер и recovery code и възстановява
   достъпа.
6. Няма автоматичен външен канал за уведомяване. Страницата изрично казва, че
   е нужно да се провери отново по-късно.

### 3.3 Администратор/служител

1. В Django admin отваря новото меню „Запитвания“.
2. Вижда филтри по статус, приоритет, причина, отговорник, видимост и дата.
3. Отваря разговор, чете хронологията и отговаря чрез специална форма.
4. Може да добави вътрешна бележка, която никога не се връща към потребителя.
5. Променя статус и отговорник, след което при нужда затваря разговора.
6. Получава dashboard броячи за нови и чакащи запитвания; не се създават
   официални публични известия за всяко ново запитване.

## 4. Данни и модели

Сегашният `ContactSubmission` се запазва като исторически запис. Не се
преименува и не се изтрива, за да не се чупят текущите права, personal-data
export, admin navigation и екипният чат.

### 4.1 Нов модел `SupportConversation`

Предложени полета:

| Поле | Предназначение |
| --- | --- |
| `id` | Вътрешен primary key |
| `public_id` | UUID/непредсказуем публичен идентификатор за API и URL |
| `reference_number` | Четим номер, например `SUP-2026-000123` |
| `legacy_submission` | Незадължителна one-to-one връзка към `ContactSubmission` |
| `requester_user` | Профилът на изпращача, nullable, `SET_NULL` при изтриване |
| `requester_name` | Snapshot на името при създаване |
| `requester_email` | Незадължителен snapshot; не се използва за доставка на съобщения |
| `reason` | Съществуващите причини от контактната форма |
| `status` | `new`, `in_progress`, `waiting_for_user`, `resolved`, `closed` |
| `priority` | `low`, `normal`, `high`, `urgent` |
| `assigned_to` | Отговорник от екипа, nullable, `SET_NULL` |
| `guest_access_token_hash` | Само SHA-256/HMAC hash на recovery code; никога raw код |
| `guest_access_expires_at` | Краен срок за анонимен достъп |
| `last_message_at` / `last_staff_reply_at` | Подредба и dashboard показатели |
| `last_requester_read_at` / `last_staff_read_at` | Непрочетени съобщения |
| `reopened_at`, `closed_at`, `created_at`, `updated_at` | История и lifecycle |
| `privacy_policy_version` | Версията на политиката при създаване |

Индекси: `(status, priority, last_message_at)`, `assigned_to`,
`requester_user`, `reference_number` unique и `public_id` unique. Добавя се
constraint за валиден status/priority чрез `TextChoices`; полетата с кодове са
unique и индексирани.

### 4.2 Нов модел `SupportMessage`

| Поле | Предназначение |
| --- | --- |
| `conversation` | ForeignKey към разговора, `CASCADE` |
| `author_user` | Профилът на автора, nullable за анонимен посетител |
| `author_kind` | `requester`, `staff`, `system` |
| `visibility` | `requester` или `staff_only` |
| `body` | Plain text, 1–5000 символа; без HTML/Markdown HTML rendering |
| `created_at` | Дата на изпращане |
| `read_by_requester_at`, `read_by_staff_at` | Read state |

Подредбата е по `created_at, id`; индексът е `(conversation, created_at)`.
Съобщенията не се редактират и не се изтриват през обичайния интерфейс, за да
остава надеждна история. Изтриването следва retention политика.

### 4.3 Създаване и миграция на старите данни

1. Създават се двата нови модела и migration.
2. Data migration създава по един `SupportConversation` за всеки стар
   `ContactSubmission`, свързан през `legacy_submission`.
3. Старият текст се копира като първо `SupportMessage(author_kind=requester)`.
4. Старите записи запазват първоначалната дата, име, email, причина и user.
5. За старите анонимни записи не се създава recovery code: те остават видими
   само в admin, защото не е коректно да се измисля достъп след факта.
6. Новите контактни заявки създават едновременно стария audit запис и новия
   разговор в една `transaction.atomic()` транзакция. След стабилен период
   може да се направи отделен, внимателен план за retire на стария модел.

## 5. API дизайн

Всички response bodies са JSON; списъците са paginated. Публичните IDs се
използват навсякъде извън admin, никога числовото database ID.

### 5.1 Създаване

`POST /api/contact/` остава като backwards-compatible endpoint, но делегира
към нова service функция `create_support_conversation()`.

Вход: `name`, `email` (optional за guest), `reason`, `message`, при нужда
Turnstile token. За влязъл потребител backend-ът сам свързва `request.user`;
не приема `user_id` от клиента.

Изход за влязъл: `201`, `public_id`, `reference_number`, `conversation_url`.

Изход за guest: `201`, `public_id`, `reference_number`, `recovery_code`.
Recovery code се връща **само при този първи отговор**, не се пази в логове и
не се показва отново от API.

### 5.2 Влезли потребители

| Метод | Път | Правило |
| --- | --- | --- |
| GET | `/api/support/conversations/` | Само разговори с `requester_user=request.user` |
| GET | `/api/support/conversations/<public_id>/` | Само собствен разговор |
| POST | `/api/support/conversations/<public_id>/messages/` | Само собствен и незатворен разговор |
| POST | `/api/support/conversations/<public_id>/reopen/` | Само собствен затворен разговор, с причина |

### 5.3 Анонимен достъп без email

| Метод | Път | Правило |
| --- | --- | --- |
| POST | `/api/support/guest/session/` | Приема `reference_number` + recovery code; създава ограничена guest session |
| GET | `/api/support/guest/conversations/<public_id>/` | Изисква guest session за точно този разговор |
| POST | `/api/support/guest/conversations/<public_id>/messages/` | Същото; само ако разговорът не е затворен |

Recovery code не се предава в URL, query string, analytics параметър или
referer. Фронтендът го изпраща в POST body само към HTTPS API. При успешно
потвърждение backend-ът поставя отделна `HttpOnly`, `Secure`, `SameSite=Lax`
guest-session cookie, ограничена до конкретния разговор и с кратък TTL.

При отваряне на разговор от същия браузър frontend-ът може да използва
запазения код за безшумно създаване/обновяване на guest session. При нов
браузър посетителят попълва формата „Продължи анонимно запитване“.

### 5.4 Права за екипа

Първата версия използва Django admin за staff действия, а не публични staff
API endpoints. Ако по-късно е нужен custom staff frontend, endpoints трябва
да използват `DjangoModelPermissions` и същите service функции, а не да
дублират business logic.

Нужни права:

- `view_supportconversation`, `view_supportmessage` — преглед;
- `change_supportconversation` — статус, приоритет, отговорник;
- `add_supportmessage` — отговор и вътрешна бележка;
- `delete_*` — само ограничени администратори, ако политиката позволява;
- `view_contactsubmission` остава за старите данни.

Преди release се преглеждат `blog/admin_roles.py` и `blog/admin_navigation.py`
и се добавят само нужните permissions към съществуващите роли. Достъпът не се
решава само по `is_staff`, защото ролите вече са permission-based.

## 6. Service слой и транзакции

Добавя се `blog/support_conversations.py` с ясни функции:

- `create_support_conversation()`;
- `add_requester_message()`;
- `add_staff_message()`;
- `change_conversation_status()`;
- `open_guest_session()`;
- `close_inactive_conversations()`;
- `purge_expired_support_data()`.

Създаването, първото съобщение, audit `ContactSubmission` и hash-ът на guest
кода са в една `transaction.atomic()` транзакция. Създаването на site/push
notification за логнат потребител се прави с `transaction.on_commit()`.

Статусът се проверява отново при запис под `select_for_update()` за избягване
на едновременен отговор след затваряне. Backend определя автора и видимостта;
клиентът никога не изпраща `author_kind`, `visibility`, `assigned_to` или
`status` като доверени стойности.

## 7. Django admin

### 7.1 Навигация и списък

Добавя се меню „Запитвания“ в `blog/admin_navigation.py` с брой нови записи.
`SupportConversationAdmin` показва номер, изпращач, причина, статус,
приоритет, отговорник, последна активност и unread indicator.

Филтри: status, priority, reason, assigned staff, има/няма профил, дата.
Търсене: reference number, име, email и текст на съобщение. Използва се
`select_related`/`prefetch_related`, за да няма N+1 заявки.

### 7.2 Детайл и отговор

Страницата на разговора има:

- read-only timeline на всички съобщения;
- ясно различими staff-only бележки;
- форма „Отговор до потребителя“;
- форма „Вътрешна бележка“;
- действия за assign, status и priority;
- защита срещу двойно изпращане;
- audit лог чрез стандартния Django admin log.

Не се показва recovery code, негов hash или guest-session стойности дори на
superuser. Admin има само бутон „Прекрати анонимен достъп“, който invalidира
всички guest сесии и кода при abuse.

## 8. Frontend

### 8.1 Контактна форма

Промени в `Frontend/src/components/ContactForm.tsx` и `src/lib/api.tsx`:

- при login се попълват профилни данни и се показва линк „Моите запитвания“;
- при guest email полето става ясно означено като незадължително, ако ще се
  пази само за контактна информация;
- след guest submit има специален success card с номер, recovery code, copy
  бутон, „Запазих кода“ потвърждение и предупреждение, че кодът няма да се
  показва повторно;
- recovery code не се изпраща към analytics/error reporting;
- съществуващото endpoint име остава, така че други части на frontend-а не се
  чупят.

### 8.2 Маршрути и страници

- `/profile?tab=support` или самостоятелен `/my-support` за влезли потребители;
- `/support/<public_id>` за техен разговор;
- `/support/guest/<public_id>` за guest разговор със session;
- `/support/continue` с форма за номер + recovery code.

Екранът съдържа timeline, поле за ново съобщение, status badge, дата на
последна активност, unread marker, loading/error/empty states и бутон за
повторно отваряне при затворен разговор. На мобилен екран е един-column.

UI не претендира, че guest ще бъде известен автоматично; показва „Проверете
запитването отново тук“. Това избягва подвеждащо обещание при липса на email
и guest push.

### 8.3 Достъпност

- Ясни `aria-label` стойности на copy, show/hide code и send бутони.
- Recovery code е selectable text, не изображение.
- Toast не е единственото място за успех/грешка; има persistent success card.
- Focus се мести към заглавието на новоотворения разговор.
- Статусните промени се съобщават чрез `aria-live`.

## 9. Известия

### Влезли потребители

Добавя се категория, например `support_reply`, към:

- `notification_categories.py`;
- `NotificationPreference` като `support_enabled=True`;
- UI настройките за известия;
- retention mapping (например 30 дни);
- `automatic_notifications.create_notification_once()` с ключ
  `support-reply:<message_id>`.

При staff отговор се създава лично site notification с action URL към
разговора. Съществуващата push система го доставя само на устройства на този
потребител, според preference-а му.

### Анонимни посетители

Не се създават общи notifications и не се праща email. В първата версия
анонимният посетител проверява разговора ръчно. Това е ясно описано в UI.

## 10. Сигурност, anti-spam и поверителност

### Достъп и IDOR

- Няма endpoint, който връща разговор по последователен integer ID.
- Authenticated заявки филтрират едновременно по `public_id` и
  `requester_user=request.user`.
- Guest cookie/session е криптографски свързана с един разговор; не дава
  достъп до друг guest разговор.
- Recovery code се генерира с `secrets.token_urlsafe(32)` или еквивалент,
  съхранява се само като hash с constant-time comparison.
- Кодът не се логва, не влиза в admin, URL, referer, analytics, export или
  exception text.
- Rate limiting: create guest 3/hour, guest code verification 10/hour,
  guest message 15/hour, authenticated message 30/hour. При нужда към guest
  creation се добавя Turnstile.
- Не се разкрива дали номер/кодът е грешен: еднакъв generic response и малко
  забавяне/DRF throttle при многократни опити.

### Валидация и XSS

- Plain text body, trim, 1–5000 знака server-side.
- Съдържанието се escape-ва при rendering; не се използва `dangerouslySetInnerHTML`.
- Строги serializer-и за status/reason/priority и забрана на непознати полета.
- Никакво приемане на файлови пътища, URL fetch или HTML attachments.

### Лични данни и retention

- В `legal/PRIVACY_POLICY.md` се добавят: цел, данни, срокове, кой има достъп и че
  guest recovery code се пази единствено от посетителя.
- В `legal/TERMS_OF_USE.md` се добавя забрана за чувствителни данни, тормоз и spam.
- Данните в conversation/message се включват в personal data export за
  `requester_user`.
- При изтриване на профил `requester_user` става `NULL`; съдържанието се пази
  само до кратък operational retention срок или се анонимизира според одобрена
  политика.
- Препоръка: затваряне след 30 дни без активност; изтриване/анонимизиране 90
  дни след затваряне. Сроковете се правят settings, не magic numbers.
- `guest_access_expires_at` не може да надвишава retention срока.

## 11. Фонови задачи

Съществуващата `dispatch_scheduled_notifications` команда се разширява или се
добавя отделна безопасна management command, изпълнявана от вече наличния
systemd timer. Тя:

1. затваря неактивни разговори;
2. изтича guest access кодове и сесии;
3. премахва/anonymizes данни след retention;
4. отчита само counts, без текстове или recovery codes в логовете.

Не се добавя нов постоянно работещ service и не се добавя Redis.

## 12. Тестове

### Backend

- Guest и authenticated create с коректен `ContactSubmission` audit запис.
- Невалидни/прекалено дълги текстове и невалидна причина.
- Само собственикът вижда authenticated разговор; друг user получава 404/403.
- Guest A не може да отвори Guest B с чужд `public_id` или code.
- Грешен/изтекъл/revoked recovery code не създава session.
- Code не се връща повторно при GET/detail/admin.
- Staff permission matrix: view-only не може да отговори; правилната роля може.
- Staff-only бележка не се връща от public/guest endpoint.
- Затворен разговор не приема съобщение; owner може да го отвори по правилата.
- Notification се създава само за логнатия получател и само след commit.
- Retention/cleanup не засяга активни разговори.
- Migration backfill: старите форми пазят текст, дата, причина и user.
- Query-count тестове за admin списък и conversation detail.

### Frontend

- Login/guest вариант на формата.
- Success card показва recovery code само веднъж и копира правилно.
- Local storage е namespaced, валидиран и се изчиства при изтекъл достъп.
- Error, loading, empty, closed и unread states.
- Guest continuation не слага код в адресната лента.
- Keyboard navigation и mobile layout.

### Ръчни проверки преди release

- Нов гост → admin reply → същият браузър вижда reply.
- Нов гост → друг браузър → възстановяване с номер + code.
- Изчистване на localStorage → без code няма достъп.
- Влязъл user → reply → bell и разрешен push.
- Модератор без change permission не може да пише.
- Проверка на access logs/error tracking: няма recovery code.

## 13. Rollout

1. Добавяне на модели, services, migration и unit tests.
2. Admin интерфейс и permission mapping.
3. Public API, throttles и guest-session механизъм.
4. Frontend contact flow, profile tab и guest recovery page.
5. Notifications само за влезли потребители.
6. Retention команда, privacy/terms промени и documentation.
7. Staging test с отделни guest/user/staff профили.
8. Production migration, `manage.py check`, целеви и пълни тестове.
9. Наблюдение на rate-limit, failed guest verification и нови запитвания без
   записване на съдържание в логовете.

## 14. Критерии за готовност

- Влязъл потребител може сигурно да води разговор от профила си.
- Guest може да продължи разговор без email и без регистрация чрез номер +
  таен код.
- Никой друг потребител, guest или staff без permission не може да прочете или
  пише в чужд разговор.
- Админът може да отговаря, добавя вътрешна бележка, назначава отговорник и
  затваря разговор.
- Кодът никога не се пази или показва в raw вид след първоначалното създаване.
- Няма email изпращане от тази функция.
- Старите контактни форми и съществуващите admin права продължават да работят.
- Backend tests, Django check, migration check, frontend lint и production
  build минават успешно.

## 15. Решения, нужни преди имплементация

1. Дали email полето при guest да остане optional контактна информация или да
   бъде премахнато изцяло.
2. Точните роли, които могат да отговарят: всички с admin access или само
   определени групи.
3. Финалните retention срокове: предложени 30 дни до auto-close и 90 дни след
   close до изтриване/anonymization.
4. Дали в бъдеща версия анонимният посетител да може изрично да включи browser
   push за конкретното си устройство, без email.
