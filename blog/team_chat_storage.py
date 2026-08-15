"""Private file storage used by the staff-only team chat."""

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible


@deconstructible
class TeamChatProtectedStorage(FileSystemStorage):
    """Store chat uploads outside MEDIA_ROOT so Apache cannot serve them."""

    def __init__(self):
        location = getattr(
            settings,
            'TEAM_CHAT_PROTECTED_ROOT',
            settings.BASE_DIR / 'protected_team_chat',
        )
        super().__init__(
            location=location,
            base_url=None,
            file_permissions_mode=0o600,
            directory_permissions_mode=0o700,
        )


team_chat_protected_storage = TeamChatProtectedStorage()
