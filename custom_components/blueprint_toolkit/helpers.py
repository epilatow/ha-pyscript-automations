# This is AI generated code
"""Shared helpers for native blueprint_toolkit subpackages.

Counterpart to ``pyscript/modules/helpers.py``: pure-Python
utility surface that subpackage logic + handler modules
can pull from. Lifted incrementally as native ports land
(today: TEC; future: DW, EDW, RW, STSC, ZWRM).

Two flavours of symbol live here:

- **Pure** (no HA imports): ``format_timestamp``,
  ``format_notification``, ``PersistentNotification``,
  ``make_config_error_notification``. Safe to import
  from non-HA test environments.
- **HA-dependent** (use the runtime ``hass`` argument
  but do not import HA modules at module scope):
  ``process_persistent_notifications``,
  ``emit_config_error``. The ``hass`` parameter is
  duck-typed so ``import custom_components.blueprint_toolkit.helpers``
  succeeds outside HA; calling these functions requires
  a real ``HomeAssistant`` instance.

Notification IDs follow the convention
``blueprint_toolkit_{subsystem}__{instance_id}__{kind}``
so each subpackage's notifications stay disambiguated
in the HA persistent-notification namespace.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


# --------------------------------------------------------
# Timestamp + notification text formatting
# --------------------------------------------------------


def format_timestamp(template: str, dt: datetime) -> str:
    """Format timestamp tokens in a template string.

    Supported tokens: YYYY, YY, MM, DD, HH, mm, ss.
    """
    if not template:
        return ""
    # Replace longest tokens first so YYYY is consumed
    # before YY can match.
    result = template
    result = result.replace("YYYY", f"{dt.year:04d}")
    result = result.replace("YY", f"{dt.year % 100:02d}")
    result = result.replace("MM", f"{dt.month:02d}")
    result = result.replace("DD", f"{dt.day:02d}")
    result = result.replace("HH", f"{dt.hour:02d}")
    result = result.replace("mm", f"{dt.minute:02d}")
    result = result.replace("ss", f"{dt.second:02d}")
    return result


def format_notification(
    text: str,
    prefix: str,
    suffix: str,
    current_time: datetime,
) -> str:
    """Format notification with prefix/suffix and timestamp tokens."""
    formatted_prefix = format_timestamp(prefix, current_time)
    formatted_suffix = format_timestamp(suffix, current_time)
    return f"{formatted_prefix}{text}{formatted_suffix}"


# --------------------------------------------------------
# Persistent notification spec + dispatcher
# --------------------------------------------------------


@dataclass
class PersistentNotification:
    """A persistent notification to create or dismiss.

    ``active=True`` means create (or refresh in place);
    ``active=False`` means dismiss. Pure data so logic
    layers can return these without taking an HA
    dependency, and ``process_persistent_notifications``
    can apply them in one batch.
    """

    active: bool
    notification_id: str
    title: str
    message: str


async def process_persistent_notifications(
    hass: HomeAssistant,
    notifications: list[PersistentNotification],
) -> None:
    """Apply a batch of notification specs against HA.

    Each ``active`` entry becomes a
    ``persistent_notification.create`` call; each
    inactive entry becomes a
    ``persistent_notification.dismiss`` call (which is
    a no-op if the notification doesn't exist, so it's
    always safe to fire).
    """
    for n in notifications:
        if n.active:
            await hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "notification_id": n.notification_id,
                    "title": n.title,
                    "message": n.message,
                },
            )
        else:
            await hass.services.async_call(
                "persistent_notification",
                "dismiss",
                {"notification_id": n.notification_id},
            )


# --------------------------------------------------------
# Config-error notification convention
# --------------------------------------------------------


def _config_error_notification_id(subsystem: str, instance_id: str) -> str:
    return f"blueprint_toolkit_{subsystem}__{instance_id}__config_error"


def make_config_error_notification(
    *,
    subsystem: str,
    subsystem_label: str,
    instance_id: str,
    errors: list[str],
) -> PersistentNotification:
    """Build a config-error spec with the standard wire format.

    When ``errors`` is empty, the returned spec has
    ``active=False`` -- pass it straight through to the
    dispatcher and any prior config-error notification
    for this instance is dismissed. This lets handlers
    call ``emit_config_error`` unconditionally on every
    successful argparse without branching.
    """
    notif_id = _config_error_notification_id(subsystem, instance_id)
    if not errors:
        return PersistentNotification(
            active=False,
            notification_id=notif_id,
            title="",
            message="",
        )
    title = (
        f"Blueprint Toolkit -- {subsystem_label} config error: {instance_id}"
    )
    message = "\n".join(f"- {e}" for e in errors)
    return PersistentNotification(
        active=True,
        notification_id=notif_id,
        title=title,
        message=message,
    )


async def emit_config_error(
    hass: HomeAssistant,
    *,
    subsystem: str,
    subsystem_label: str,
    instance_id: str,
    errors: list[str],
) -> None:
    """Build a config-error spec and dispatch it.

    Convenience wrapper -- handlers typically call this
    once per argparse with whatever ``errors`` they
    accumulated (empty list dismisses any prior
    notification for the same instance).
    """
    spec = make_config_error_notification(
        subsystem=subsystem,
        subsystem_label=subsystem_label,
        instance_id=instance_id,
        errors=errors,
    )
    if errors:
        _LOGGER.warning(
            "%s config error for %s: %s",
            subsystem_label,
            instance_id,
            "; ".join(errors),
        )
    await process_persistent_notifications(hass, [spec])
