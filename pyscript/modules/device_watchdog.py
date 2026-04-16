# This is AI generated code
"""Business logic for device health watchdog.

Does not use PyScript-injected globals.

Monitors device health across integrations by checking for
unavailable entities and stale state (no state change within
a configurable threshold).
"""

from dataclasses import dataclass, field
from datetime import datetime

from helpers import (
    DeviceEntry,
    PersistentNotification,
    matches_pattern,
)


@dataclass
class Config:
    """Configuration parameters (set per-instance)."""

    device_exclude_regex: str
    entity_id_exclude_regex: str
    monitored_entity_domains: list[str]
    dead_threshold_seconds: int


@dataclass
class EntityInfo:
    """Entity state snapshot for health evaluation."""

    entity_id: str
    state: str
    last_changed: datetime


@dataclass
class RegistryEntry:
    """Minimal entity registry entry for diagnostics."""

    entity_id: str
    original_name: str
    platform: str
    entity_category: str | None
    disabled: bool


@dataclass
class DeviceInfo:
    """Device with its entity state snapshots."""

    de: DeviceEntry
    entities: list[EntityInfo] = field(
        default_factory=list,
    )
    registry_entries: list[RegistryEntry] = field(
        default_factory=list,
    )


@dataclass
class DeviceResult:
    """Per-device evaluation result."""

    device_id: str
    device_name: str
    has_issue: bool
    device_excluded: bool
    notification_id: str
    notification_title: str
    notification_message: str
    unavailable_entities: list[str]
    is_stale: bool
    newest_entity: str | None
    newest_timestamp: datetime | None
    entities_evaluated: int
    entities_filtered: int

    def to_notification(
        self,
        suppress: bool = False,
    ) -> PersistentNotification:
        return PersistentNotification(
            active=self.has_issue and not suppress,
            notification_id=self.notification_id,
            title=self.notification_title,
            message=self.notification_message,
        )


# Recommended diagnostic entities per integration.
# Matched via original_name on entity_category=diagnostic
# entries. If an entity with the name exists but is
# disabled, the user is notified. If it doesn't exist
# at all, it's silently skipped (device doesn't support
# it).
RECOMMENDED_DIAGNOSTICS: dict[str, list[str]] = {
    "zwave_js": ["Last seen", "Node status"],
    "bthome": ["Signal strength"],
    "shelly": ["RSSI"],
}


def check_disabled_diagnostics(
    integration: str,
    entries: list[RegistryEntry],
) -> list[str]:
    """Check for disabled recommended diagnostic entities.

    Returns list of original_name values for entities
    that exist but are disabled. Entities that don't
    exist at all are silently skipped.
    """
    recommended = RECOMMENDED_DIAGNOSTICS.get(
        integration,
        [],
    )
    if not recommended:
        return []

    # Filter to diagnostic entities for this integration
    diag_entries = [
        e
        for e in entries
        if e.platform == integration and e.entity_category == "diagnostic"
    ]

    disabled: list[str] = []
    for name in recommended:
        for entry in diag_entries:
            if entry.original_name == name:
                if entry.disabled:
                    disabled.append(name)
                break
    return disabled


def evaluate_diagnostics(
    devices: list[DeviceInfo],
) -> list[PersistentNotification]:
    """Check all devices for disabled diagnostics.

    Uses device.de.integration_entities and
    device.registry_entries
    to find disabled recommended diagnostic entities.

    Skips devices with no integrations in
    RECOMMENDED_DIAGNOSTICS. Returns a
    PersistentNotification per device that has at least
    one relevant integration.
    """
    results: list[PersistentNotification] = []
    for device in devices:
        integrations = sorted(
            device.de.integration_entities.keys(),
        )
        has_recommendations = [
            i for i in integrations if i in RECOMMENDED_DIAGNOSTICS
        ]
        if not has_recommendations:
            continue
        disabled: list[str] = []
        for integration in integrations:
            disabled += check_disabled_diagnostics(
                integration,
                device.registry_entries,
            )
        notification_id = "dw_diag_" + device.de.id
        if disabled:
            entity_list = "\n- ".join(disabled)
            message = (
                "Recommended diagnostic entities"
                " are disabled:\n\n- " + entity_list + "\n\nEnable in"
                " [Settings > Devices]("
                + device.de.url
                + ") for better health monitoring."
            )
            results.append(
                PersistentNotification(
                    active=True,
                    notification_id=notification_id,
                    title=(device.de.name + ": Disabled Diagnostics"),
                    message=message,
                ),
            )
        else:
            results.append(
                PersistentNotification(
                    active=False,
                    notification_id=notification_id,
                    title="",
                    message="",
                ),
            )
    return results


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

        if matches_pattern(
            eid,
            config.entity_id_exclude_regex,
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
        "Device: [" + device.de.name + "](" + device.de.url + ")",
    )
    integrations = sorted(
        device.de.integration_entities.keys(),
    )
    if integrations:
        lines.append(
            "Integrations: " + ", ".join(integrations),
        )

    sorted_unavail = [(e.entity_id, i, e) for i, e in enumerate(unavailable)]
    sorted_unavail.sort()
    for _, _, entity in sorted_unavail:
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
    notification_id = "device_watchdog_" + device.de.id

    # Skip excluded devices
    device_excluded = matches_pattern(
        device.de.name,
        config.device_exclude_regex,
    )
    if device_excluded:
        return DeviceResult(
            device_id=device.de.id,
            device_name=device.de.name,
            has_issue=False,
            device_excluded=True,
            notification_id=notification_id,
            notification_title="",
            notification_message="",
            unavailable_entities=[],
            is_stale=False,
            newest_entity=None,
            newest_timestamp=None,
            entities_evaluated=0,
            entities_filtered=0,
        )

    kept, filtered_out = _filter_entities(
        config,
        device.entities,
    )

    unavailable = [e for e in kept if e.state in ("unavailable", "unknown")]

    is_stale, newest_entity, newest_ts = _check_staleness(
        kept,
        config.dead_threshold_seconds,
        current_time,
    )

    has_issue = bool(unavailable) or is_stale

    message = ""
    title = ""
    if has_issue:
        title = "Device watchdog: " + device.de.name
        message = _build_notification_message(
            device,
            unavailable,
            is_stale,
            newest_entity,
            newest_ts,
            config,
        )

    return DeviceResult(
        device_id=device.de.id,
        device_name=device.de.name,
        has_issue=has_issue,
        device_excluded=False,
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


@dataclass
class EvaluationResult:
    """Full evaluation result for the service wrapper."""

    results: list[DeviceResult]
    notifications: list[PersistentNotification]
    all_integrations_count: int
    stat_entities: int
    stat_devices_excluded: int
    stat_entities_excluded: int
    issues_count: int
    stat_entity_issues: int
    stat_stale: int


def run_evaluation(
    config: Config,
    devices: list[DeviceInfo],
    current_time: datetime,
    check_diagnostics: bool,
    all_integrations_count: int,
    max_notifications: int,
) -> EvaluationResult:
    """Run device evaluation in a worker thread.

    Called via ``@pyscript_executor`` trampoline so the
    event loop stays responsive. The service wrapper
    builds the device list on the main thread (requires
    HA registries), then hands it off here.
    """
    from helpers import prepare_notifications

    results = evaluate_devices(config, devices, current_time)

    diag_notifications: list[PersistentNotification] = []
    if check_diagnostics:
        diag_notifications = evaluate_diagnostics(devices)

    notifications = prepare_notifications(
        results,
        max_notifications,
        "device_watchdog_cap",
        "Device watchdog: notification cap reached",
        "devices with issues",
    )
    notifications += diag_notifications

    issues = [r for r in results if r.has_issue]

    return EvaluationResult(
        results=results,
        notifications=notifications,
        all_integrations_count=all_integrations_count,
        stat_entities=sum(
            [
                r.entities_evaluated + r.entities_filtered
                for r in results
                if not r.device_excluded
            ]
        ),
        stat_devices_excluded=sum([1 for r in results if r.device_excluded]),
        stat_entities_excluded=sum([r.entities_filtered for r in results]),
        issues_count=len(issues),
        stat_entity_issues=sum([len(r.unavailable_entities) for r in issues]),
        stat_stale=sum([1 for r in issues if r.is_stale]),
    )
