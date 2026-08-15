from django.apps import AppConfig
from django.db.models.signals import post_migrate


class BlogConfig(AppConfig):
    name = 'blog'

    def ready(self):
        from .gamification import signals  # noqa: F401
        from . import push_signals  # noqa: F401
        from . import notification_event_signals  # noqa: F401
        from .admin_chat import install_team_chat_admin_sharing
        from .admin_roles import sync_admin_roles_after_migrate

        install_team_chat_admin_sharing()

        post_migrate.connect(
            sync_admin_roles_after_migrate,
            sender=self,
            dispatch_uid="blog.sync_admin_roles_after_migrate",
        )
