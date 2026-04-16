# This is AI generated code
"""Business logic for entity defaults watchdog.

Does not use PyScript-injected globals.

Detects entity ID and name drift from their defaults.
Entity IDs drift when device names change after entity
creation.  Name overrides become stale when integrations
change their naming conventions and HA auto-preserves
old names.
"""

from dataclasses import dataclass, field

from helpers import (
    DeviceEntry,
    PersistentNotification,
    matches_pattern,
)

DRIFT_CHECK_ENTITY_ID = "entity-id"
DRIFT_CHECK_ENTITY_NAME = "entity-name"


@dataclass
class Config:
    """Configuration parameters (set per-instance)."""

    drift_checks: list[str]
    device_exclude_regex: str
    exclude_entity_ids: list[str]
    entity_id_exclude_regex: str
    entity_name_exclude_regex: str


@dataclass
class EntityDriftInfo:
    """Per-entity drift data computed by service wrapper."""

    entity_id: str
    has_entity_name: bool
    has_name_override: bool
    expected_entity_id: str | None
    current_name: str
    expected_name: str | None


@dataclass
class DeviceInfo:
    """Device with its entity drift snapshots."""

    de: DeviceEntry
    entities: list[EntityDriftInfo] = field(
        default_factory=list,
    )


@dataclass
class DriftDetail:
    """One drift finding for an entity."""

    entity_id: str
    id_drifted: bool
    name_drifted: bool
    current_name: str
    expected_name: str | None
    has_redundant_prefix: bool = False
    recommended_override: str | None = None


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
    drifted_entities: list[DriftDetail]
    entities_checked: int
    entities_excluded: int

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


def _detect_redundant_prefix(
    entry_name: str | None,
    device_name: str,
    has_entity_name: bool,
) -> bool:
    """True if a name override redundantly includes the
    device name.

    Only applies to has_entity_name=True entities where
    HA already prepends the device name automatically.
    """
    if not has_entity_name:
        return False
    if not entry_name or not device_name:
        return False
    return entry_name.startswith(device_name)


def _compute_recommended_override(
    entity_name: str,
    device_default_name: str,
    device_display_name: str,
    has_entity_name: bool,
    multi_integration: bool,
) -> str | None:
    """Compute the correct name override for legacy entities.

    For has_entity_name=False entities whose entity_name
    embeds the device default name, returns the override
    value that produces correct entity IDs.

    Returns None if not applicable (has_entity_name=True,
    entity_name doesn't start with the device default
    name, device hasn't been renamed, or device has
    multiple integrations).
    """
    if has_entity_name:
        return None
    # Multi-integration devices have non-deterministic
    # default_name — skip recommendation to avoid
    # incorrect suggestions.
    if multi_integration:
        return None
    if not entity_name or not device_default_name:
        return None
    if device_default_name == device_display_name:
        return None
    if not entity_name.startswith(
        device_default_name,
    ):
        return None
    suffix = entity_name[len(device_default_name) :].strip()
    if not suffix:
        return device_display_name
    return suffix


def _check_id_enabled(config: Config) -> bool:
    """True if entity-id check is active."""
    return (
        len(config.drift_checks) == 0
        or DRIFT_CHECK_ENTITY_ID in config.drift_checks
    )


def _check_name_enabled(config: Config) -> bool:
    """True if entity-name check is active."""
    return (
        len(config.drift_checks) == 0
        or DRIFT_CHECK_ENTITY_NAME in config.drift_checks
    )


def _is_excluded(
    config: Config,
    entity_id: str,
    friendly_name: str,
) -> bool:
    """True if entity matches any exclusion mechanism."""
    if entity_id in config.exclude_entity_ids:
        return True
    if matches_pattern(
        entity_id,
        config.entity_id_exclude_regex,
    ):
        return True
    if matches_pattern(
        friendly_name,
        config.entity_name_exclude_regex,
    ):
        return True
    return False


def _check_entity_drift(
    config: Config,
    entity: EntityDriftInfo,
    device: DeviceInfo,
) -> DriftDetail | None:
    """Check a single entity for drift.

    Returns DriftDetail if drifted, None if clean or
    excluded. Computes redundant prefix and recommended
    override on the fly using device-level data.
    """
    if _is_excluded(
        config,
        entity.entity_id,
        entity.current_name,
    ):
        return None

    id_drifted = False
    name_drifted = False

    # Compute has_entity_name=False recommendations
    multi = len(device.de.integration_entities) > 1
    recommended = _compute_recommended_override(
        entity.expected_name or "",
        device.de.default_name,
        device.de.name,
        entity.has_entity_name,
        multi,
    )
    redundant = _detect_redundant_prefix(
        entity.current_name if entity.has_name_override else None,
        device.de.name,
        entity.has_entity_name,
    )

    # ID drift check
    if _check_id_enabled(config):
        if (
            entity.expected_entity_id is not None
            and entity.entity_id != entity.expected_entity_id
        ):
            id_drifted = True

    # Name drift check
    if _check_name_enabled(config):
        if not entity.has_entity_name and recommended is not None:
            # has_entity_name=False with extractable
            # device name prefix: compare override
            # against the recommended value. Flag even
            # without an existing override (entity IDs
            # will be broken without the correct
            # override).
            if entity.current_name != recommended:
                name_drifted = True
        elif (
            entity.has_name_override
            and entity.expected_name is not None
            and entity.current_name != entity.expected_name
        ):
            name_drifted = True

    if not id_drifted and not name_drifted:
        return None

    return DriftDetail(
        entity_id=entity.entity_id,
        id_drifted=id_drifted,
        name_drifted=name_drifted,
        current_name=entity.current_name,
        expected_name=entity.expected_name,
        has_redundant_prefix=redundant,
        recommended_override=recommended,
    )


def _build_notification_message(
    device: DeviceInfo,
    drifted: list[DriftDetail],
) -> str:
    """Build the notification body for a device with drift.

    Groups entities into up to four sections:
    - Name overrides to clear (has_entity_name=True stale
      overrides)
    - Name overrides with redundant device name
      (has_entity_name=True with device prefix in override)
    - Name overrides to set (has_entity_name=False with
      recommended override from device name extraction)
    - Non-default entity IDs (ID drift only, no name drift)

    Entities with both name+ID drift appear only in the
    name section — the ID will be addressed after the name
    is fixed.
    """
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

    # Group entities by notification section
    name_clear: list[DriftDetail] = []
    name_redundant: list[DriftDetail] = []
    name_set: list[DriftDetail] = []
    id_only: list[DriftDetail] = []

    for d in drifted:
        if d.name_drifted and d.recommended_override is not None:
            name_set.append(d)
        elif d.name_drifted and d.has_redundant_prefix:
            name_redundant.append(d)
        elif d.name_drifted:
            name_clear.append(d)
        else:
            id_only.append(d)

    # Sort each section by entity_id for consistent
    # output
    name_clear = [
        d
        for _, _, d in sorted(
            [(d.entity_id, i, d) for i, d in enumerate(name_clear)]
        )
    ]
    name_redundant = [
        d
        for _, _, d in sorted(
            [(d.entity_id, i, d) for i, d in enumerate(name_redundant)]
        )
    ]
    name_set = [
        d
        for _, _, d in sorted(
            [(d.entity_id, i, d) for i, d in enumerate(name_set)]
        )
    ]
    id_only = [
        d
        for _, _, d in sorted(
            [(d.entity_id, i, d) for i, d in enumerate(id_only)]
        )
    ]

    has_name_issues = (
        len(name_clear) > 0 or len(name_redundant) > 0 or len(name_set) > 0
    )

    if name_clear:
        lines.append("")
        lines.append("**Name overrides to clear:**")
        for d in name_clear:
            lines.append(
                "- `" + d.entity_id + '`: "' + d.current_name + '"',
            )
        lines.append("")
        lines.append(
            "To keep a custom name, add the entity"
            " to the watchdog's exclusion list.",
        )

    if name_redundant:
        lines.append("")
        lines.append(
            "**Name overrides with redundant device name:**",
        )
        for d in name_redundant:
            lines.append(
                "- `"
                + d.entity_id
                + '`: "'
                + d.current_name
                + '" \u2192 "'
                + (d.expected_name or "")
                + '"',
            )
        lines.append(
            "  The override includes the device name,"
            " which Home Assistant already adds."
            " Edit the override to remove"
            ' "' + device.de.name + ' " or clear it entirely.',
        )

    if name_set:
        lines.append("")
        lines.append("**Name overrides to set:**")
        for d in name_set:
            lines.append(
                "- `"
                + d.entity_id
                + '`: set to "'
                + (d.recommended_override or "")
                + '"',
            )
        lines.append("")
        lines.append(
            "These are legacy entities whose names"
            " embed an old device name. Set the"
            " recommended overrides, then use"
            " Recreate entity IDs.",
        )

    if id_only:
        lines.append("")
        lines.append("**Non-default entity IDs:**")
        for d in id_only:
            lines.append("- `" + d.entity_id + "`")

    # How to fix section
    lines.append("")
    if has_name_issues and id_only:
        lines.append("**How to fix:**")
        lines.append(
            "1. Clear or edit the name overrides"
            " above in each entity's settings.",
        )
        lines.append(
            "2. Use **Recreate entity IDs** on the"
            " device page to fix non-default IDs.",
        )
        lines.append(
            "3. Fix names before recreating IDs"
            ' \u2014 "Recreate entity IDs" uses the'
            " current name to compute the new ID."
            " Clearing a name override may reveal"
            " additional non-default IDs on the"
            " next check.",
        )
    elif has_name_issues:
        lines.append("**How to fix:**")
        lines.append(
            "Clear or edit the name overrides"
            " above in each entity's settings."
            " Clearing a name override may reveal"
            " non-default entity IDs on the next"
            " check.",
        )
    else:
        lines.append(
            "Use **Recreate entity IDs** on the"
            " device page to fix non-default IDs.",
        )

    return "\n".join(lines)


def _evaluate_device(
    config: Config,
    device: DeviceInfo,
) -> DeviceResult:
    """Evaluate drift for a single device."""
    notification_id = "entity_defaults_watchdog_" + device.de.id

    # Skip excluded devices
    if matches_pattern(
        device.de.name,
        config.device_exclude_regex,
    ):
        return DeviceResult(
            device_id=device.de.id,
            device_name=device.de.name,
            has_issue=False,
            device_excluded=True,
            notification_id=notification_id,
            notification_title="",
            notification_message="",
            drifted_entities=[],
            entities_checked=0,
            entities_excluded=0,
        )

    drifted: list[DriftDetail] = []
    excluded = 0
    for entity in device.entities:
        result = _check_entity_drift(config, entity, device)
        if result is None:
            if _is_excluded(
                config,
                entity.entity_id,
                entity.current_name,
            ):
                excluded += 1
        else:
            drifted.append(result)

    has_issue = len(drifted) > 0
    title = ""
    message = ""
    if has_issue:
        title = "Entity defaults watchdog: " + device.de.name
        message = _build_notification_message(
            device,
            drifted,
        )

    return DeviceResult(
        device_id=device.de.id,
        device_name=device.de.name,
        has_issue=has_issue,
        device_excluded=False,
        notification_id=notification_id,
        notification_title=title,
        notification_message=message,
        drifted_entities=drifted,
        entities_checked=len(device.entities) - excluded,
        entities_excluded=excluded,
    )


def evaluate_devices(
    config: Config,
    devices: list[DeviceInfo],
) -> list[DeviceResult]:
    """Evaluate drift for all devices.

    Main entry point for the logic module.

    The service wrapper triggers every minute via a time
    pattern.  An interval gate checks whether enough time
    has passed since the last evaluation.

    When the gate passes, the wrapper:
    - Discovers devices across configured integrations
    - For each entity, computes drift data using the HA
      entity and device registries
    - Calls this function with the device list

    For each device, this function:
    - Filters by device exclusion regex
    - Checks each entity for ID and/or name drift
    - Builds a notification per device with drift details

    The wrapper then creates/dismisses persistent
    notifications per device.
    """
    results: list[DeviceResult] = []
    for device in devices:
        result = _evaluate_device(config, device)
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
    stat_name_issues: int
    stat_id_issues: int


def run_evaluation(
    config: Config,
    devices: list[DeviceInfo],
    all_integrations_count: int,
    max_notifications: int,
) -> EvaluationResult:
    """Run entity defaults evaluation in a worker thread.

    Called via ``@pyscript_executor`` trampoline so the
    event loop stays responsive.
    """
    from helpers import prepare_notifications

    results = evaluate_devices(config, devices)

    notifications = prepare_notifications(
        results,
        max_notifications,
        "entity_defaults_watchdog_cap",
        "Entity defaults watchdog: notification cap reached",
        "devices with drift",
    )

    issues = [r for r in results if r.has_issue]

    return EvaluationResult(
        results=results,
        notifications=notifications,
        all_integrations_count=all_integrations_count,
        stat_entities=sum(
            [
                r.entities_checked + r.entities_excluded
                for r in results
                if not r.device_excluded
            ]
        ),
        stat_devices_excluded=sum([1 for r in results if r.device_excluded]),
        stat_entities_excluded=sum([r.entities_excluded for r in results]),
        issues_count=len(issues),
        stat_entity_issues=sum([len(r.drifted_entities) for r in issues]),
        stat_name_issues=sum(
            [1 for r in issues for d in r.drifted_entities if d.name_drifted]
        ),
        stat_id_issues=sum(
            [1 for r in issues for d in r.drifted_entities if d.id_drifted]
        ),
    )
