from django.db.models import Count, Max, Q
from django.utils import timezone

from .models import (
    Register,
    ValentineModerationEvent,
    Valenitines,
)


def _iso(value):
    return value.isoformat() if value is not None else None


def _session_status(session, *, current_session_id, now):
    if session.pk == current_session_id:
        return "current"
    if session.revoked:
        return "revoked"
    if session.expires_at <= now:
        return "expired"
    return "active"


def _sender_details(valentine):
    if valentine.is_anonymous:
        return {"type": "anonymous", "display_name": None}
    if valentine.sender_profile_id:
        return {
            "type": "named",
            "display_name": valentine.sender_profile.full_name,
        }
    return {"type": "system", "display_name": "Valentine екипа"}


def build_valentine_personal_data_export(
    profile,
    platform_settings,
    *,
    current_session_id,
    now=None,
):
    """Build a privacy-safe export for one authenticated Valentine profile."""
    now = now or timezone.now()
    active_campaign = platform_settings.active_campaign
    reveal_at = platform_settings.get_reveal_at()
    is_revealed = now >= reveal_at

    sessions = [
        {
            "ip_address": session.ip_address,
            "network_operator": session.network_operator,
            "network_asn": session.network_asn,
            "created_at": _iso(session.created_at),
            "expires_at": _iso(session.expires_at),
            "revoked": session.revoked,
            "status": _session_status(
                session,
                current_session_id=current_session_id,
                now=now,
            ),
            "is_current": session.pk == current_session_id,
        }
        for session in Register.objects.filter(profile=profile).order_by(
            "created_at",
            "pk",
        )
    ]

    audience_labels = dict(Valenitines.AudienceType.choices)
    theme_labels = dict(Valenitines.Theme.choices)
    sent_dispatches = []
    sent_rows = (
        Valenitines.objects.filter(sender_profile=profile)
        .values(
            "dispatch_id",
            "campaign_id",
            "campaign__year",
            "campaign__name",
            "campaign__is_archived",
            "audience_type",
            "audience_label",
            "content",
            "theme",
            "is_anonymous",
            "sender_class_snapshot",
        )
        .annotate(
            sent_at=Max("date"),
            edited_at=Max("edited_at"),
            recipient_count=Count("id"),
            moderated_count=Count(
                "id",
                filter=~Q(
                    moderation_status=Valenitines.ModerationStatus.CLEAR
                ),
            ),
        )
        .order_by("campaign__year", "sent_at", "dispatch_id")
    )
    for dispatch in sent_rows:
        sent_dispatches.append(
            {
                "dispatch_id": str(dispatch["dispatch_id"]),
                "campaign": {
                    "id": dispatch["campaign_id"],
                    "year": dispatch["campaign__year"],
                    "name": dispatch["campaign__name"],
                    "is_archived": dispatch["campaign__is_archived"],
                },
                "audience_type": dispatch["audience_type"],
                "audience_type_display": audience_labels.get(
                    dispatch["audience_type"],
                    dispatch["audience_type"],
                ),
                "audience_label": dispatch["audience_label"],
                "recipient_count": dispatch["recipient_count"],
                "content": dispatch["content"],
                "theme": dispatch["theme"],
                "theme_display": theme_labels.get(
                    dispatch["theme"],
                    dispatch["theme"],
                ),
                "is_anonymous": dispatch["is_anonymous"],
                "sender_class_at_send_time": dispatch[
                    "sender_class_snapshot"
                ],
                "sent_at": _iso(dispatch["sent_at"]),
                "edited_at": _iso(dispatch["edited_at"]),
                "is_moderated": dispatch["moderated_count"] > 0,
            }
        )

    visible_received = Valenitines.objects.filter(
        profile=profile,
        is_reported=False,
    ).exclude(
        moderation_status=Valenitines.ModerationStatus.HIDDEN,
    )
    if is_revealed:
        visible_received = visible_received.filter(
            Q(campaign__is_archived=True) | Q(campaign=active_campaign)
        )
    else:
        visible_received = visible_received.filter(campaign__is_archived=True)

    received_valentines = [
        {
            "id": valentine.pk,
            "dispatch_id": str(valentine.dispatch_id),
            "campaign": {
                "id": valentine.campaign_id,
                "year": valentine.campaign.year,
                "name": valentine.campaign.name,
                "is_archived": valentine.campaign.is_archived,
            },
            "content": valentine.content,
            "theme": valentine.theme,
            "theme_display": valentine.get_theme_display(),
            "received_at": _iso(valentine.date),
            "recipient_class_at_send_time": (
                valentine.recipient_class_snapshot
            ),
            "is_anonymous": valentine.is_anonymous,
            "sender": _sender_details(valentine),
            "moderation_status": valentine.moderation_status,
            "moderation_status_display": (
                valentine.get_moderation_status_display()
            ),
        }
        for valentine in (
            visible_received.select_related("campaign", "sender_profile")
            .order_by("campaign__year", "date", "pk")
        )
    ]

    sealed_current_campaign = None
    if active_campaign and not is_revealed:
        sealed_received = Valenitines.objects.filter(
            profile=profile,
            campaign=active_campaign,
            is_reported=False,
        ).exclude(
            moderation_status=Valenitines.ModerationStatus.HIDDEN,
        )
        sealed_current_campaign = {
            "campaign": {
                "id": active_campaign.pk,
                "year": active_campaign.year,
                "name": active_campaign.name,
            },
            "reveal_at": _iso(reveal_at),
            "received_count_is_visible": (
                platform_settings.show_received_count
            ),
            "received_count": (
                sealed_received.count()
                if platform_settings.show_received_count
                else None
            ),
        }

    report_deliveries = {
        (delivery.campaign_id, delivery.dispatch_id): delivery
        for delivery in (
            Valenitines.objects.filter(profile=profile)
            .select_related("campaign")
            .order_by("campaign_id", "dispatch_id", "pk")
        )
    }
    submitted_reports = []
    report_events = (
        ValentineModerationEvent.objects.filter(
            reporter_profile=profile,
            event_type=ValentineModerationEvent.EventType.REPORTED,
        )
        .select_related("campaign")
        .order_by("created_at", "pk")
    )
    for event in report_events:
        delivery = report_deliveries.get(
            (event.campaign_id, event.dispatch_id)
        )
        submitted_reports.append(
            {
                "dispatch_id": str(event.dispatch_id),
                "campaign": {
                    "id": event.campaign_id,
                    "year": event.campaign.year,
                    "name": event.campaign.name,
                },
                "reason": event.reason,
                "reason_display": event.get_reason_display(),
                "reported_at": _iso(event.created_at),
                "current_moderation_status": (
                    delivery.moderation_status if delivery else None
                ),
                "current_moderation_status_display": (
                    delivery.get_moderation_status_display()
                    if delivery
                    else None
                ),
            }
        )

    return {
        "format_version": "1.0",
        "scope": "pgknma-valentines",
        "generated_at": _iso(now),
        "profile": {
            "id": str(profile.pk),
            "access_code": profile.code,
            "first_name": profile.first_name,
            "last_name": profile.last_name,
            "full_name": profile.full_name,
            "class_code": profile.clas,
            "class_display": profile.clas_bg,
            "email": profile.email,
            "is_active": profile.is_active,
            "academic_year_start": profile.academic_year_start,
            "academic_year": profile.academic_year_label,
            "pin_security": {
                "has_pin": profile.has_pin,
                "pin_set_at": _iso(profile.pin_set_at),
                "failed_attempts": profile.pin_failed_attempts,
                "locked_until": _iso(profile.pin_locked_until),
            },
        },
        "sessions": sessions,
        "sent_dispatches": sent_dispatches,
        "received_valentines": received_valentines,
        "sealed_current_campaign": sealed_current_campaign,
        "submitted_reports": submitted_reports,
        "excluded_for_security": [
            "PIN and PIN hash",
            "Valentine session UUID and authentication cookie",
            "anonymous sender identity",
            "moderator identity and internal moderation notes",
            "individual recipient list for group dispatches",
            "hidden or pending-report received message content",
        ],
        "notes": [
            "Получените послания от активната кампания не се разкриват преди зададената дата.",
            "Сесиите присъстват само докато се пазят според срока за съхранение.",
        ],
    }
