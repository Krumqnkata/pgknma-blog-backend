# Екипен чат и дискусии

## Обхват

Team chat е internal инструмент в Django admin, достъпен само за активни staff
потребители. Не е публичен чат и не използва frontend React repository.
Основният UI е `/admin/team-chat/` и custom templates/JavaScript под Django
admin context.

## Режими

| Режим | Кой вижда | Употреба |
| --- | --- | --- |
| General chat | Всеки active `is_staff` | Бързи вътрешни съобщения и координация. |
| Discussion | Създател + избрани users/groups + superuser според ACL | Отделна тема/задача със controlled access. |

Discussion status е open/resolved. Resolved discussion не приема ново content,
докато не бъде върната към open през разрешеното действие.

## Models

- `TeamChatMessage`: text, author, reply relation, discussion/origin relation,
  soft delete, pinned state, edit metadata.
- `TeamChatMessageRevision`: audit на редакциите.
- `TeamChatReaction`: reaction per user/message.
- `TeamChatReadState`: unread state за general messages.
- `TeamChatDiscussion`: title, source relation, access/status, creator.
- `TeamChatDiscussionReadState`: unread state за discussion members.
- `TeamChatSharedObject`: safe references към споделени admin objects.
- `TeamChatAttachment`: image/thumbnail metadata и protected filesystem path.

## HTTP surface

`cms/urls.py` регистрира всички `/admin/team-chat/...` actions и ги обгръща с
`admin.site.admin_view`. `admin_chat.py` добавя secondary `_require_staff()` и
delegира domain checks на `team_chat.py`.

Основни действия: list/create messages, mark read, edit/delete, pin,
reaction, staff list, discussions list/detail/access/messages/read/resolve,
protected attachment/thumbnail, share и share-to-discussion.

Използвай само existing JSON payload helpers и POST decorators. Не прави state
change чрез GET.

## Permissions и ACL

- Staff requirement е minimum gate.
- General chat не означава auto access до private discussion.
- `accessible_discussions`, `get_accessible_discussion` и
  `discussion_is_accessible_to_user` са source of truth за object access.
- При недостъпна discussion предпочитай 404 вместо да разкриваш existence.
- Group membership update трябва да recalculates/проверява discussion access.

Не предполагай, че наличието на admin role автоматично дава право за всяка
discussion; explicit discussion audience е отделна policy.

## Attachments

Team chat images се валидират от `team_chat_images.py` и се пазят в
`TEAM_CHAT_PROTECTED_ROOT`, извън Apache public media. Retrieval endpoint
проверява staff и discussion access преди `FileResponse`. Thumbnail е отделен
protected endpoint.

При delete на message провери attachment cleanup; никога не подменяй protected
storage с `/files/` Alias за удобство. Нов file type в първа стъпка не се
добавя без размер/MIME/content inspection/retention design.

## Sharing от admin

`SHAREABLE_ADMIN_MODELS` е explicit allowlist. ModelAdmin action/button
генерира краткоживеещ signed share token (salt + max age), след което server
отново проверява дали requester има право за всеки object. Това предотвратява
подправен query parameter да сподели недостъпен запис.

Преди да добавиш model към allowlist:

1. провери `can_share_object` и display serializer;
2. увери се, че няма secret/PII, което не трябва да се копира в чат;
3. тествай model action с role, която има view на model, и с role без него;
4. тествай sharing към private discussion без membership.

## UI поведение

Floating/admin launcher не трябва да блокира Save/Submit controls. На малки
екрани се използва side tab/drawer, а не постоянен large overlay. Chat poll
ползва pagination/boundaries, за да не зарежда неограничена история при всяко
обновяване.

## Regression checklist

- [ ] Non-staff получава отказ за всяка chat route.
- [ ] Staff A не вижда private discussion на Staff B.
- [ ] Mention/reaction/edit/delete проверяват access към original message.
- [ ] Pinned limit се налага server-side.
- [ ] Attachment URL без staff ACL връща 404/403 и не изтича файл.
- [ ] Share token с изтекъл/подправен payload не работи.
- [ ] Deleted message не остава във list/unread/attachment response.
- [ ] Mobile launcher не покрива основни admin бутони.
