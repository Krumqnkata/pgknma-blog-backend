# Django admin, роли и екипен чат

## Admin UI

`/admin/` използва Django Unfold. Sidebar-ът не е статичен: `blog/admin_navigation.py`
показва item само ако активен staff user има поне един изискан Django permission
и съответният `ModelAdmin` допуска достъп. Това предотвратява меню с линкове,
които водят до 403.

Admin access не е равно на superuser. Superuser има всички права; останалите
staff users получават права чрез Django Groups и explicit permissions.

## Стандартни роли

`blog/admin_roles.py` дефинира baseline групи. `sync_admin_roles` ги създава
след migration и може да се пусне ръчно.

| Роля | Основни възможности |
| --- | --- |
| Автори | Създават/редактират собствени публикации и техните файлове. |
| Редактори | Публикации, категории, календар, програма, официални известия, changelog и политики. |
| Модератори | Comments, poll answers, песни, memes и контактни формуляри. |
| Мениджъри на геймификацията | Levels, badges, missions, point rules, rewards и свързани read-only записи. |
| Мениджъри на Валентинки | Valentine кампании, профили, сигнали и архив. |
| Достъп до Valentine кодове | Допълнителна чувствителна роля за кодове. |
| Управление на сайта | Site settings, policy текстове, consent, sessions, push records и admin log. |

### Синхронизация

```bash
python manage.py sync_admin_roles
python manage.py sync_admin_roles --help
```

Normal sync добавя само липсващи стандартни групи и запазва последващи custom
permission промени. Режимът `--reset` възстановява точно baseline набора и
може да премахне ръчни промени — използвай го само след преглед и backup.

При нов модел, който трябва да се вижда от дадена роля:

1. добави model permissions към правилната константа в `admin_roles.py`;
2. добави navigation item с `_permissions(...)` в `admin_navigation.py`;
3. прегледай custom `ModelAdmin.has_*_permission` overrides;
4. migration + `sync_admin_roles` в staging;
5. тествай с реален non-superuser account.

## Content workflow

### Новини

- Автор създава post и свързани images/documents.
- Редактор може да управлява publish/allow state и официалното представяне.
- Public API връща само allowed/published/due content.
- При първо публикуване notification service маркира `publication_notified_at`;
  редакцията не трябва да спами с duplicate notification.

### Community content

- Песни и memes минават през moderation status и owner actions.
- Vote routes са throttled; game point/activity effects минават през services.
- Изтриването/скриването на content може да промени публичната видимост, но
  lifetime gamification counters пазят историческата earned activity.

### Policies и feature flags

- `TermsOfService` и `PrivacyPolicy` са versioned content модели, управлявани
  от редактор/site-manager permissions.
- `SiteSettings` управлява maintenance и feature switches. Frontend read-ва
  site status и не трябва да предполага, че feature винаги е включен.

## Team chat

Маршрути: `/admin/team-chat/` и subordinate admin views от `cms/urls.py`.
Достъпът е `is_active && is_staff`; sensitive discussion access е допълнително
проверен от `blog/team_chat.py`.

Функции:

- general staff chat;
- private discussions с explicit members/allowed staff;
- replies, mentions, edit history, soft-delete;
- pin до зададения лимит;
- reactions;
- protected image attachments и thumbnails;
- unread markers;
- споделяне на разрешени admin objects към chat или discussion.

### Важни security свойства

- Attachments живеят в `TEAM_CHAT_PROTECTED_ROOT`, не в публичния media
  directory.
- Сервират се от `admin.site.admin_view`, след staff/discussion permission
  проверка.
- Share link-овете използват Django signed token, ограничен по възраст; при
  share се проверява всеки object permission отново.
- Не добавяй model към `SHAREABLE_ADMIN_MODELS`, преди да провериш дали всяка
  роля, която може да го избере, действително има право да го вижда.

## Административни проверки

Преди production change:

1. Тествай с superuser, с user от само една роля и с user без role.
2. Провери sidebar, директен URL и POST action — sidebar hidden не е security.
3. Провери audit log за destructive/moderation actions.
4. За team chat провери general chat, private discussion и attachment access
   през два различни staff user-а.
5. Изпълни `python manage.py test` и приложи migration-и в staging.
