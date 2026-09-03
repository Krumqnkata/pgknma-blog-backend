# Геймификация

## Цел и принцип

Геймификацията награждава реални действия в блога, без да е source of truth за
съдържанието. Например публикация/коментар/глас остава domain object, а
points/badges/missions са записани отделно като history. Не променяй total
points директно от view или frontend.

Основният service слой е в `blog/gamification/`. Views, signals и admin actions
извикват services, които валидират rule-и, deduplicate-ват активност и създават
audit записи.

## Данни

| Model | Предназначение |
| --- | --- |
| `GamificationLevel` | Праг от точки, номер, title и icon. |
| `GamificationPointRule` | Конфигурируема action → points rule. |
| `PointTransaction` | Неизменима история на начислени/коригирани точки. |
| `Badge` | Дефиниция на значка, период, secret/active condition и criteria. |
| `UserBadge` | Коя значка е получена от кой user и кога. |
| `Mission` | Active window, requirements и reward configuration. |
| `UserMissionProgress` | Progress per user/mission. |
| `MissionActivity` | Deduplicated action history за mission logic. |
| `DigitalReward` | Конфигурируема визуална/дигитална награда. |
| `UserDigitalReward` | Unlocked/select state на reward. |
| `LifetimeProfileActivity` | Historical counts за profile stats, независими от по-късно изтрит source content. |

## Основен flow

```text
Потребителско действие
  → конкретен domain view/signal
  → award_activity() / award_tracked_activity()
  → PointTransaction + текущи totals
  → level evaluation
  → mission progress evaluation
  → badge/reward evaluation
  → personal gamification notification (ако е приложимо)
```

### Защо има отделни транзакции и activity записи

- `PointTransaction` обяснява откъде идват точките и позволява audit.
- `MissionActivity`/unique keys пазят от двойно начисляване при повторен POST,
  page refresh, retry или duplicate signal.
- `LifetimeProfileActivity` пази профилната статистика, когато администратор
  изтрие одобрено меме/песен или потребител изтрие content след валидна action.

## Нива, титли и награди

Level се определя от minimum point threshold. Frontend показва current level,
следващ праг и unlocked presentation в profile/gamification components.

Digital rewards са отделни от badges: badge е achievement, reward е елемент,
който user може да отключи и избере за публично представяне. Selection limit и
active state се валидират server-side. Не приемай selected reward list като
доверен frontend input.

Профил може да показва повече от една избрана титла/награда според текущата
selection policy. Public profile serializer връща само active, разрешени и
public-safe данни.

## Мисии

Mission requirements се редактират през подобрената admin форма, а не чрез
ръчно писане на суров JSON. Конфигурацията описва action key, readable label и
target count. При създаване/редакция:

1. валидирай schema/allowed action keys;
2. избери start/end window и `active` state;
3. провери дали award е points, badge, digital reward или комбинация;
4. не прави mission retroactive без изрично решение и backfill plan;
5. ако mission има ограничен срок, scheduler може да създаде one-time
   `mission_available` notification.

Преди да изтриеш mission/reward/badge, провери получените `User*` записи.
Deactivation е предпочитана пред delete, когато историята трябва да остане
обяснима.

## Значки

Badge периодът управлява **кога може да бъде раздавана**, не кога става
невалидна вече получената значка. Secret badge скрива details/prompt преди
отключване; не дава допълнителна security защита. Публичното показване зависи
от profile visibility и active/award rules.

За масово раздаване (например early adopters) ползвай idempotent management
script/service с стабилен key, а не ad-hoc shell loop без audit. Скриптът
трябва да може да се пусне втори път без duplicate `UserBadge`.

## Notifications

Game уведомления са отделни category prefixes (`gamification_points`, level,
badge, mission, reward). Те не трябва да се смесват с official/system/security
feed. User preference fields контролират visibility; delivery следва standard
notification lifecycle.

## Admin responsibilities

Ролята „Мениджъри на геймификацията“ може да управлява definitions и да вижда
историята според permissions в `admin_roles.py`. Тя не трябва автоматично да
получава права за posts/users/groups само защото работи с game data.

Преди промяна на point rule:

- провери дали action вече се track-ва;
- реши дали промяната е само за бъдещи actions;
- не коригирай исторически `PointTransaction` без отделен migration/audit;
- тествай duplicate action, delete source и profile visibility.

## API и frontend

Профилният frontend използва `/api/gamification/profile/`, transactions,
activity, preferences, leaderboard и reward selection endpoints. Разделът е
в `MyProfilePage`/`GamificationProgress` и свързаните profile компоненти.
При response change обнови едновременно `GamificationProfileResponseSerializer`
и frontend types/functions в `Frontend/src/lib/api.tsx`.

## Regression checklist

- [ ] Една action носи точки точно веднъж.
- [ ] Concurrent/retry action не създава duplicate transaction.
- [ ] Mission progress спира при end date/active false.
- [ ] Badge/reward не се раздава два пъти.
- [ ] Deleting moderated source не отнема lifetime historical count.
- [ ] Друг user не може да чете/избира чужда reward data.
- [ ] Gamification notification не излиза като общо official известие.
