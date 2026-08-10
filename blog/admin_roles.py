"""Predefined, permission-based roles for the Django administration."""

from dataclasses import dataclass

from django.contrib.auth.models import Group, Permission
from django.db import DEFAULT_DB_ALIAS, transaction


@dataclass(frozen=True)
class AdminRole:
    name: str
    description: str
    permissions: frozenset[str]


def _model_permissions(app_label, model_names, actions=("view", "add", "change", "delete")):
    return {
        f"{app_label}.{action}_{model_name}"
        for model_name in model_names
        for action in actions
    }


AUTHOR_PERMISSIONS = (
    _model_permissions("blog", ("posts",), ("view", "add", "change"))
    | _model_permissions("blog", ("category",), ("view",))
    | _model_permissions(
        "blog",
        ("postimage", "postdocument"),
        ("view", "add", "change", "delete"),
    )
    | {"blog.view_only_posts"}
)

EDITOR_PERMISSIONS = (
    _model_permissions(
        "blog",
        (
            "posts",
            "category",
            "postimage",
            "postdocument",
            "event",
            "scheduleentry",
            "scheduleimport",
            "notification",
            "changelog",
            "termsofservice",
            "privacypolicy",
        ),
    )
    | {
        "blog.can_allow_posts",
        "blog.can_edit_users_post",
        "blog.can_view_all_pnp_posts",
        "blog.can_view_all_posts",
    }
)

MODERATOR_PERMISSIONS = _model_permissions(
    "blog",
    (
        "comments",
        "pollquestion",
        "pollanswer",
        "bellsongsuggestion",
        "memeofweek",
        "contactsubmission",
    ),
)

GAMIFICATION_PERMISSIONS = (
    _model_permissions(
        "blog",
        (
            "userprofile",
            "gamificationlevel",
            "badge",
            "userbadge",
            "pointtransaction",
            "gamificationpointrule",
            "mission",
            "usermissionprogress",
            "missionactivity",
            "digitalreward",
            "userdigitalreward",
        ),
    )
    | _model_permissions("blog", ("gamificationnotification",), ("view",))
    | _model_permissions("blog", ("notification",), ("view",))
    | _model_permissions("auth", ("user",), ("view",))
)

VALENTINE_PERMISSIONS = (
    _model_permissions(
        "valentine",
        (
            "settings",
            "profile",
            "register",
            "valenitines",
            "valentinecampaign",
            "archivedvalentine",
            "valentinemoderationevent",
        ),
    )
    | {"valentine.view_profile_codes"}
)

SITE_MANAGER_PERMISSIONS = (
    _model_permissions(
        "blog",
        (
            "sitesettings",
            "changelog",
            "privacypolicy",
            "termsofservice",
            "consentrecord",
            "personaldatacleanuprun",
            "blogloginsession",
            "pageview",
        ),
    )
    | _model_permissions("admin", ("logentry",), ("view",))
)


ADMIN_ROLES = (
    AdminRole(
        "Автори",
        "Създават и редактират собствените си публикации и прикачени файлове.",
        frozenset(AUTHOR_PERMISSIONS),
    ),
    AdminRole(
        "Редактори",
        "Управляват публикации, календар, програма, официални известия и страници с текстово съдържание.",
        frozenset(EDITOR_PERMISSIONS),
    ),
    AdminRole(
        "Модератори",
        "Обработват коментари, анкети, предложения за песни, мемета и контактни формуляри.",
        frozenset(MODERATOR_PERMISSIONS),
    ),
    AdminRole(
        "Мениджъри на геймификацията",
        "Управляват точки, нива, мисии, значки и дигитални награди.",
        frozenset(GAMIFICATION_PERMISSIONS),
    ),
    AdminRole(
        "Мениджъри на Валентинки",
        "Управляват Valentine профили, кампании, сигнали и архив.",
        frozenset(VALENTINE_PERMISSIONS),
    ),
    AdminRole(
        "Управление на сайта",
        "Управлява настройки, политики, статистика, сесии и технически регистри, без права за роли и потребители.",
        frozenset(SITE_MANAGER_PERMISSIONS),
    ),
)

ADMIN_ROLE_BY_NAME = {role.name: role for role in ADMIN_ROLES}


def sync_admin_roles(*, using=DEFAULT_DB_ALIAS, reset=False):
    """Create/update the predefined groups and return a short result summary.

    Normal synchronization adds missing baseline permissions and preserves any
    extra permissions assigned by a superuser. ``reset=True`` restores the
    exact predefined permission set.
    """

    permission_lookup = {
        f"{permission.content_type.app_label}.{permission.codename}": permission
        for permission in Permission.objects.using(using).select_related("content_type")
    }
    result = {"created": 0, "updated": 0, "missing": set()}

    with transaction.atomic(using=using):
        for role in ADMIN_ROLES:
            group, created = Group.objects.using(using).get_or_create(name=role.name)
            resolved = []
            for permission_name in role.permissions:
                permission = permission_lookup.get(permission_name)
                if permission is None:
                    result["missing"].add(permission_name)
                else:
                    resolved.append(permission)

            before = set(group.permissions.values_list("pk", flat=True))
            desired = {permission.pk for permission in resolved}
            if reset:
                group.permissions.set(resolved)
                after = desired
            else:
                group.permissions.add(*resolved)
                after = before | desired

            result["created"] += int(created)
            result["updated"] += int(before != after and not created)

    return result


def sync_admin_roles_after_migrate(sender, using, **kwargs):
    """Install the role baseline after Django creates model permissions."""

    return sync_admin_roles(using=using)
