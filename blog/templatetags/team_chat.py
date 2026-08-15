from django import template

from blog.admin_chat import (
    discussion_creation_url,
    share_confirmation_url,
    share_to_discussion_confirmation_url,
)
from blog.team_chat import can_share_object, share_reference_for_object


register = template.Library()


@register.simple_tag
def team_chat_share_url(request, obj):
    if obj is None or not can_share_object(request, obj):
        return ''
    return share_confirmation_url((share_reference_for_object(obj),))


@register.simple_tag
def team_chat_share_to_discussion_url(request, obj):
    if obj is None or not can_share_object(request, obj):
        return ''
    return share_to_discussion_confirmation_url((share_reference_for_object(obj),))


@register.simple_tag
def team_chat_discussion_url(request, obj):
    if obj is None or not can_share_object(request, obj):
        return ''
    return discussion_creation_url(share_reference_for_object(obj))
