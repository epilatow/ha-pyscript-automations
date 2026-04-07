# This is AI generated code
"""Business logic for device health watchdog.

No PyScript runtime dependencies.

Monitors device health across integrations by checking for
unavailable entities and stale state (no state change within
a configurable threshold).
"""

import re
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Config:
    """Configuration parameters (set per-instance)."""

    device_exclude_regex: str
    entity_exclude_regex: str
    monitored_entity_domains: list[str]
    dead_threshold_seconds: int


@dataclass
class EntityInfo:
    """Entity state snapshot for health evaluation."""

    entity_id: str
    state: str
    last_changed: datetime


@dataclass
class DeviceInfo:
    """Device with its entity snapshots."""

    device_id: str
    device_name: str
    device_url: str
    entities: list[EntityInfo] = field(
        default_factory=list,
    )


@dataclass
class DeviceResult:
    """Per-device evaluation result."""

    device_id: str
    device_name: str
    has_issue: bool
    notification_id: str
    notification_title: str
    notification_message: str
    unavailable_entities: list[str]
    is_stale: bool
    newest_entity: str | None
    newest_timestamp: datetime | None
    entities_evaluated: int
    entities_filtered: int


def should_run(
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


def _matches_pattern(
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


def _filter_entities(
    config: Config,
    entities: list[EntityInfo],
) -> tuple[list[EntityInfo], list[EntityInfo]]:
    """Apply domain and regex filters to entities.

    Returns (kept, filtered_out).
    """
    kept: list[EntityInfo] = []
    filtered_out: list[EntityInfo] = []

    domains = [d.lower() for d in config.monitored_entity_domains]
    filter_by_domain = len(domains) > 0

    for entity in entities:
        eid = entity.entity_id
        domain = eid.split(".")[0] if "." in eid else ""

        if filter_by_domain and domain not in domains:
            filtered_out.append(entity)
            continue

        if _matches_pattern(
            eid,
            config.entity_exclude_regex,
        ):
            filtered_out.append(entity)
            continue

        kept.append(entity)

    return kept, filtered_out


def _check_staleness(
    entities: list[EntityInfo],
    threshold_seconds: int,
    current_time: datetime,
) -> tuple[bool, str | None, datetime | None]:
    """Check if all entities are stale.

    Returns (is_stale, newest_entity_id, newest_timestamp).
    A device is stale if no entity has changed state within
    the threshold window. If there are no entities to
    evaluate, staleness is indeterminate — return False.
    """
    if not entities:
        return False, None, None

    newest_entity: str | None = None
    newest_ts: datetime | None = None

    for entity in entities:
        if newest_ts is None or entity.last_changed > newest_ts:
            newest_ts = entity.last_changed
            newest_entity = entity.entity_id

    if newest_ts is None:
        return True, None, None

    age_seconds = (current_time - newest_ts).total_seconds()
    is_stale = age_seconds > threshold_seconds

    return is_stale, newest_entity, newest_ts


def _build_notification_message(
    device: DeviceInfo,
    unavailable: list[EntityInfo],
    is_stale: bool,
    newest_entity: str | None,
    newest_timestamp: datetime | None,
    config: Config,
) -> str:
    """Build the notification body for an unhealthy device."""
    lines: list[str] = []
    lines.append(
        "Device: [" + device.device_name + "](" + device.device_url + ")",
    )

    for entity in unavailable:
        lines.append(
            "Unavailable entity: " + entity.entity_id,
        )

    if is_stale:
        threshold_minutes = config.dead_threshold_seconds // 60
        if newest_timestamp and newest_entity:
            last_seen = newest_timestamp.isoformat()
            lines.append(
                "No entity state change within "
                + str(threshold_minutes)
                + " minutes. Most recent update "
                + last_seen
                + " via "
                + newest_entity
                + ".",
            )
        else:
            lines.append(
                "No entity state change within "
                + str(threshold_minutes)
                + " minutes. No prior updates detected.",
            )

    assert len(lines) > 1, "Expected unavailable or stale content but got none"

    return "\n".join(lines)


def _evaluate_device(
    config: Config,
    device: DeviceInfo,
    current_time: datetime,
) -> DeviceResult:
    """Evaluate health of a single device."""
    kept, filtered_out = _filter_entities(
        config,
        device.entities,
    )

    if _matches_pattern(
        device.device_name,
        config.device_exclude_regex,
    ):
        kept, filtered_out = [], device.entities

    unavailable = [e for e in kept if e.state in ("unavailable", "unknown")]

    is_stale, newest_entity, newest_ts = _check_staleness(
        kept,
        config.dead_threshold_seconds,
        current_time,
    )

    has_issue = bool(unavailable) or is_stale

    notification_id = "device_watchdog_" + device.device_id

    message = ""
    title = ""
    if has_issue:
        title = "Device watchdog: " + device.device_name
        message = _build_notification_message(
            device,
            unavailable,
            is_stale,
            newest_entity,
            newest_ts,
            config,
        )

    return DeviceResult(
        device_id=device.device_id,
        device_name=device.device_name,
        has_issue=has_issue,
        notification_id=notification_id,
        notification_title=title,
        notification_message=message,
        unavailable_entities=[e.entity_id for e in unavailable],
        is_stale=is_stale,
        newest_entity=newest_entity,
        newest_timestamp=newest_ts,
        entities_evaluated=len(kept),
        entities_filtered=len(filtered_out),
    )


def evaluate_devices(
    config: Config,
    devices: list[DeviceInfo],
    current_time: datetime,
) -> list[DeviceResult]:
    """Evaluate health of all devices.

    Main entry point for the logic module.

    The service wrapper triggers every minute via a time
    pattern.  An interval gate checks whether enough time
    has passed since the last evaluation.

    When the gate passes, the wrapper:
    1. Discovers devices across configured integrations
       using the HA entity and device registries
    2. Reads entity state for each device
    3. Calls this function with the device list

    For each device, this function:
    1. Filters entities by domain and exclude regex
    2. Checks for unavailable/unknown entity states
    3. Checks for staleness (no state change within
       threshold)
    4. Returns a DeviceResult per device

    The wrapper then creates persistent notifications
    for unhealthy devices and dismisses them on recovery.
    """
    results: list[DeviceResult] = []
    for device in devices:
        result = _evaluate_device(
            config,
            device,
            current_time,
        )
        results.append(result)
    return results
