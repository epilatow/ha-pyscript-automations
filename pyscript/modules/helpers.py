# This is AI generated code
"""Shared helpers for automation logic modules.

No PyScript runtime dependencies.

Provides dataclasses, persistent notification support,
timestamp formatting, interval gating, and regex
matching.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class PersistentNotification:
    """A persistent notification to create or dismiss."""

    active: bool
    notification_id: str
    title: str
    message: str


@dataclass
class EntityRegistryInfo:
    """Entity registry fields for drift detection."""

    entity_id: str
    name: str | None
    # Default entity name (HA entry.original_name).
    # Used when entry.name is not set.
    original_name: str | None
    has_entity_name: bool
    device_id: str | None


@dataclass
class DeviceEntry:
    """Device discovered during integration scan."""

    id: str
    url: str

    # Current device name. HA device registry
    # device.name_by_user (if set) or device.name
    # (set by integration).
    name: str

    # Integration default name. HA device registry
    # device.name. Non-deterministic for
    # multi-integration devices.
    default_name: str

    # Map integrations to the entity ids they provide.
    integration_entities: dict[str, set[str]] = field(
        default_factory=dict,
    )


def format_timestamp(template: str, dt: datetime) -> str:
    """Format timestamp tokens in a template string.

    Supported tokens: YYYY, YY, MM, DD, HH, mm, ss.
    """
    if not template:
        return ""
    # Replace longest tokens first so YYYY is consumed
    # before YY can match.  Uses str.replace instead of
    # re.sub + lambda because PyScript's AST evaluator
    # cannot resolve local variables in lambda closures.
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
    """Format notification with prefix/suffix and
    timestamp tokens."""
    formatted_prefix = format_timestamp(
        prefix,
        current_time,
    )
    formatted_suffix = format_timestamp(
        suffix,
        current_time,
    )
    return f"{formatted_prefix}{text}{formatted_suffix}"


def on_interval(
    check_interval_minutes: int,
    current_time: datetime,
) -> bool:
    """Return True if this tick should run evaluation.

    Uses modulo arithmetic on the minute-of-epoch to gate
    execution to every N minutes without persistent state.
    """
    if check_interval_minutes <= 0:
        return True
    minutes_since_epoch = int(
        current_time.timestamp() // 60,
    )
    return (minutes_since_epoch % check_interval_minutes) == 0


def matches_pattern(
    text: str,
    pattern: str,
) -> bool:
    """Return True if text matches regex pattern.

    Returns False if pattern is empty or invalid.
    """
    if not pattern:
        return False
    try:
        return bool(
            re.search(pattern, text, re.IGNORECASE),
        )
    except re.error:
        return False
