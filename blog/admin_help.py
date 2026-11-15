"""The admin guide on its own page, so the start page stays short.

`help_url_for_path` gives the „?“ button in the top bar a link straight to the
part of the guide about the current screen.
"""

from django.contrib import admin
from django.template.response import TemplateResponse
from django.urls import reverse


def admin_help_page(request):
    """Wrapped in admin_site.admin_view in urls.py: staff only."""
    request.current_app = admin.site.name
    context = {
        **admin.site.each_context(request),
        'title': 'Ръководство',
    }
    return TemplateResponse(request, 'admin/help.html', context)


# (app label, model name) of a list/form → id of a section in _help_guide.html.
_SECTIONS = {
    'guide-posts': ('blog', ('posts', 'category', 'postimage', 'postdocument')),
    'guide-events': ('blog', ('event', 'notification', 'changelog')),
    'guide-polls': ('blog', ('pollquestion', 'pollanswer', 'polldraft')),
    'guide-moderation': ('blog', ('comments', 'contactsubmission', 'bellsongsuggestion', 'memeofweek', 'memeweekresult')),
    'guide-schedule': ('blog', ('scheduleentry', 'scheduleimport')),
    'guide-users': ('blog', ('blogloginsession',)),
    'guide-gamification': ('blog', (
        'gamificationlevel', 'badge', 'mission', 'digitalreward', 'gamificationpointrule', 'gamesettings',
        'gamepuzzle', 'userprofile', 'userbadge', 'pointtransaction', 'usermissionprogress', 'missionactivity',
        'userdigitalreward', 'gamesession', 'gamificationnotification',
    )),
    'guide-governance': ('blog', ('sitesettings', 'privacypolicy', 'termsofservice', 'consentrecord', 'headerlogo')),
    'guide-cleanup': ('blog', ('personaldatacleanuprun',)),
}

HELP_ANCHORS = {
    (app_label, model): anchor
    for anchor, (app_label, models) in _SECTIONS.items()
    for model in models
}
HELP_ANCHORS.update({
    ('auth', 'user'): 'guide-users',
    ('auth', 'group'): 'guide-users',
    ('valentine', 'profile'): 'guide-valentine-profiles',
    ('valentine', 'register'): 'guide-valentine-profiles',
    ('valentine', 'settings'): 'guide-valentine-campaigns',
    ('valentine', 'valentinecampaign'): 'guide-valentine-campaigns',
    ('valentine', 'valenitines'): 'guide-valentine-moderation',
    ('valentine', 'archivedvalentine'): 'guide-valentine-moderation',
})


def help_url_for_path(path):
    """Link to the guide section for an admin page, or None on the guide itself."""
    help_url = reverse('admin-help')
    if path.startswith(help_url):
        return None
    admin_root = reverse('admin:index')
    if not path.startswith(admin_root):
        return help_url
    rest = path[len(admin_root):].strip('/')
    if not rest:
        return f'{help_url}#guide-start'
    if rest.startswith('team-chat'):
        return f'{help_url}#guide-team-chat'
    parts = rest.split('/')
    anchor = HELP_ANCHORS.get(tuple(parts[:2])) if len(parts) >= 2 else None
    return f'{help_url}#{anchor}' if anchor else help_url
