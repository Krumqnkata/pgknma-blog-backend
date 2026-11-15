"""Permission-aware Unfold sidebar navigation."""

from django.apps import apps
from django.contrib import admin


def _permissions(app_label, model_name, actions=("view", "add", "change", "delete")):
    return tuple(f"{app_label}.{action}_{model_name}" for action in actions)


def _item(title, link, icon, *permissions, **extra):
    item = {
        "title": title,
        "link": link,
        "icon": icon,
        "_permissions": tuple(permissions),
    }
    item.update(extra)
    return item


def _can_cleanup(request):
    """„Почистване на записи“ needs settings access plus a delete permission."""
    from .models import SiteSettings

    model_admin = admin.site._registry.get(SiteSettings)
    return model_admin is not None and model_admin._can_cleanup(request)


def _model_items(app_label, rows):
    return tuple(
        _item(title, f"/admin/{app_label}/{model}/", icon, *_permissions(app_label, model))
        for title, model, icon in rows
    )


# Daily work first (content, moderation, the weekly poll), open by default.
# Rarely used areas are collapsible. Every page keeps its old address.
ADMIN_NAVIGATION = (
    {
        "items": (
            _item("Начало", "/admin/", "home"),
            _item(
                "Екипен чат",
                "/admin/team-chat/",
                "forum",
                badge="blog.admin_chat.team_chat_unread_badge",
                badge_style="solid",
                badge_variant="danger",
            ),
            _item(
                "Статистика",
                "/admin/#site-statistics",
                "monitoring",
                *_permissions("blog", "pageview"),
            ),
            _item(
                "Изпратени контактни формуляри",
                "/admin/blog/contactsubmission/",
                "mail",
                *_permissions("blog", "contactsubmission"),
                badge="blog.admin_badges.contact_recent", badge_variant="warning",
            ),
            _item("Ръководство", "/admin/help/", "menu_book"),
        ),
    },
    {
        "separator": True,
        "title": "Съдържание",
        "items": (
            _item("Публикации", "/admin/blog/posts/", "article", *_permissions("blog", "posts"), badge="blog.admin_badges.posts_pending", badge_variant="warning"),
            _item("Събития", "/admin/blog/event/", "event", *_permissions("blog", "event")),
            _item("Официални известия", "/admin/blog/notification/", "notifications", *_permissions("blog", "notification")),
            _item("Учебна програма", "/admin/blog/scheduleentry/", "calendar_month", *_permissions("blog", "scheduleentry")),
            _item("Категории", "/admin/blog/category/", "label", *_permissions("blog", "category")),
            _item("Снимки към публикации", "/admin/blog/postimage/", "image", *_permissions("blog", "postimage")),
            _item("Файлове към публикации", "/admin/blog/postdocument/", "book", *_permissions("blog", "postdocument")),
        ),
    },
    {
        "separator": True,
        "title": "Модерация",
        "items": (
            _item("Коментари", "/admin/blog/comments/", "comment", *_permissions("blog", "comments"), badge="blog.admin_badges.comments_flagged", badge_variant="warning"),
            _item("Предложения за песни", "/admin/blog/bellsongsuggestion/", "music_note", *_permissions("blog", "bellsongsuggestion"), badge="blog.admin_badges.songs_pending", badge_variant="warning"),
            _item("Меме на седмицата", "/admin/blog/memeofweek/", "sentiment_very_satisfied", *_permissions("blog", "memeofweek"), badge="blog.admin_badges.memes_pending", badge_variant="warning"),
            _item("Победители по седмици", "/admin/blog/memeweekresult/", "emoji_events", *_permissions("blog", "memeweekresult")),
        ),
    },
    {
        "separator": True,
        "title": "Седмична анкета",
        "items": (
            _item("ИИ чернови за анкети", "/admin/blog/polldraft/?status__exact=pending", "auto_awesome", *_permissions("blog", "polldraft"), badge="blog.admin_badges.poll_drafts_pending", badge_variant="warning"),
            _item("Въпроси за анкети", "/admin/blog/pollquestion/", "poll", *_permissions("blog", "pollquestion")),
            _item("Отговори на анкети", "/admin/blog/pollanswer/", "how_to_vote", *_permissions("blog", "pollanswer")),
        ),
    },
    {
        "separator": True,
        "collapsible": True,
        "title": "ИИ асистент",
        "items": (
            _item("Правила на ИИ асистента", "/admin/blog/assistantrule/", "smart_toy", *_permissions("blog", "assistantrule")),
            _item("Неотговорени въпроси (ИИ)", "/admin/blog/assistantunansweredquestion/", "contact_support", *_permissions("blog", "assistantunansweredquestion"), badge="blog.admin_badges.unanswered_questions", badge_variant="warning"),
        ),
    },
    {
        "separator": True,
        "collapsible": True,
        "title": "Геймификация",
        "items": _model_items("blog", (
            ("Нива", "gamificationlevel", "military_tech"),
            ("Значки", "badge", "workspace_premium"),
            ("Мисии", "mission", "flag"),
            ("Дигитални награди", "digitalreward", "redeem"),
            ("Правила за точки", "gamificationpointrule", "rule"),
            ("Настройки на игрите", "gamesettings", "sports_esports"),
            ("Игрови задачи", "gamepuzzle", "extension"),
        )),
    },
    {
        "collapsible": True,
        "title": "Геймификация: история",
        "items": _model_items("blog", (
            ("Потребителски профили", "userprofile", "account_circle"),
            ("Получени значки", "userbadge", "emoji_events"),
            ("История на точките", "pointtransaction", "stars"),
            ("Прогрес по мисии", "usermissionprogress", "task_alt"),
            ("Активности по мисии", "missionactivity", "timeline"),
            ("Отключени награди", "userdigitalreward", "lock_open"),
            ("Игрови сесии", "gamesession", "history"),
            ("Известия от играта", "gamificationnotification", "notifications_active"),
        )),
    },
    {
        "separator": True,
        "collapsible": True,
        "title": "Валентинки",
        "items": _model_items("valentine", (
            ("Настройки", "settings", "settings"),
            ("Потребители", "profile", "person"),
            ("Регистър на вписаните", "register", "privacy_tip"),
            ("Текущи сигнали", "valenitines", "message"),
            ("Годишни кампании", "valentinecampaign", "calendar_month"),
            ("Архивирани сигнали", "archivedvalentine", "archive"),
        )),
    },
    {
        "separator": True,
        "collapsible": True,
        "title": "Сайт и дневници",
        "items": (
            _item("Настройки на сайта", "/admin/blog/sitesettings/", "settings", *_permissions("blog", "sitesettings")),
            _item(
                "Почистване на записи", "/admin/blog/sitesettings/cleanup/", "delete_sweep",
                *_permissions("blog", "sitesettings", ("change",)), _visible=_can_cleanup,
            ),
            _item("Празнични логота", "/admin/blog/headerlogo/", "celebration", *_permissions("blog", "headerlogo")),
            _item("Регистър с промени", "/admin/blog/changelog/", "update", *_permissions("blog", "changelog")),
            _item("Push абонаменти", "/admin/blog/pushsubscription/", "notifications_active", *_permissions("blog", "pushsubscription")),
            _item("Прегледи на страници", "/admin/blog/pageview/", "visibility", *_permissions("blog", "pageview")),
            _item("Журнал на действията", "/admin/admin/logentry/", "history", *_permissions("admin", "logentry", ("view",))),
        ),
    },
    {
        "collapsible": True,
        "title": "Документи и лични данни",
        "items": (
            _item("Политики за поверителност", "/admin/blog/privacypolicy/", "privacy_tip", *_permissions("blog", "privacypolicy")),
            _item("Условия за ползване", "/admin/blog/termsofservice/", "gavel", *_permissions("blog", "termsofservice")),
            _item("Записи на съгласие за бисквитки", "/admin/blog/consentrecord/", "cookie", *_permissions("blog", "consentrecord")),
            _item("История на почистването", "/admin/blog/personaldatacleanuprun/", "cleaning_services", *_permissions("blog", "personaldatacleanuprun")),
        ),
    },
    {
        "collapsible": True,
        "title": "Потребители и достъп",
        "items": (
            _item("Потребители", "/admin/auth/user/", "person", *_permissions("auth", "user")),
            _item("Активни сесии", "/admin/blog/blogloginsession/?session_status=active", "devices", *_permissions("blog", "blogloginsession")),
            _item("Групи и роли", "/admin/auth/group/", "groups", *_permissions("auth", "group")),
        ),
    },
)


def _registered_model_admin(permissions):
    for permission in permissions:
        try:
            app_label, codename = permission.split(".", 1)
        except ValueError:
            continue
        for action in ("view", "add", "change", "delete"):
            prefix = f"{action}_"
            if not codename.startswith(prefix):
                continue
            try:
                model = apps.get_model(app_label, codename[len(prefix):])
            except LookupError:
                return None
            return admin.site._registry.get(model) if model is not None else None
    return None


def _model_admin_allows_access(request, permissions):
    model_admin = _registered_model_admin(permissions)
    if model_admin is None:
        return True
    if not model_admin.has_module_permission(request):
        return False
    return any(
        (
            model_admin.has_view_permission(request),
            model_admin.has_add_permission(request),
            model_admin.has_change_permission(request),
            model_admin.has_delete_permission(request),
        )
    )


def build_admin_navigation(request):
    """Return only menu entries the current staff user may actually open."""

    user = request.user
    if not user.is_active or not user.is_staff:
        return []

    navigation = []
    for section in ADMIN_NAVIGATION:
        visible_items = []
        for configured_item in section["items"]:
            required = configured_item.get("_permissions", ())
            if required and not user.is_superuser and not any(
                user.has_perm(permission) for permission in required
            ):
                continue
            if required and not _model_admin_allows_access(request, required):
                continue
            # Optional extra check for pages that are not a model list.
            visible = configured_item.get("_visible")
            if visible is not None and not visible(request):
                continue
            visible_items.append(
                {key: value for key, value in configured_item.items() if not key.startswith("_")}
            )

        if not visible_items:
            continue
        visible_section = {key: value for key, value in section.items() if key != "items"}
        visible_section["items"] = visible_items
        navigation.append(visible_section)

    return navigation
