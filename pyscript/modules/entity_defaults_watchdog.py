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
    md_escape,
)

# Check identifiers surfaced as blueprint options. Adding
# a new check = one new constant, add it to ``CHECK_ALL``,
# and test ``in config.drift_checks`` at the use site.
DRIFT_CHECK_DEVICE_ENTITY_ID = "device-entity-id"
DRIFT_CHECK_DEVICE_ENTITY_NAME = "device-entity-name"
DRIFT_CHECK_ENTITY_ID = "entity-id"

CHECK_ALL: frozenset[str] = frozenset(
    {
        DRIFT_CHECK_DEVICE_ENTITY_ID,
        DRIFT_CHECK_DEVICE_ENTITY_NAME,
        DRIFT_CHECK_ENTITY_ID,
    },
)

# Domains eligible for the deviceless entity_id drift
# check. Limited to user-named domains whose entity_ids
# are derived from a user-supplied name (and therefore
# drift when the user renames the name).  Integration-
# entity domains (media_player, camera, climate, etc.)
# are excluded because their entity_ids are derived from
# device + integration names -- the device_entity_id
# check already covers those.
DEVICELESS_DOMAINS: frozenset[str] = frozenset(
    {
        "automation",
        "script",
        "scene",
        "group",
        "schedule",
        "timer",
        "counter",
        "input_boolean",
        "input_number",
        "input_text",
        "input_select",
        "input_datetime",
        "input_button",
        "sensor",
        "binary_sensor",
        "switch",
        "light",
    },
)


@dataclass
class Config:
    """Configuration parameters (set per-instance)."""

    drift_checks: frozenset[str]
    device_exclude_regex: str
    exclude_entity_ids: list[str]
    entity_id_exclude_regex: str
    entity_name_exclude_regex: str
    # Per-instance notification ID prefix, ending with
    # the canonical ``__`` separator. Every notification
    # this module mints must start with this string so
    # the service wrapper's orphan sweep can safely scope
    # dismissals to one instance.
    notification_prefix: str = ""


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
class DevicelessEntityInfo:
    """Deviceless entity snapshot for drift evaluation."""

    entity_id: str
    # HA's effective display name for the entity.  For
    # registry entries, ``entry.name or entry.original_name``;
    # for state-only entities, ``attributes.friendly_name``.
    # Empty string when no name is set.
    effective_name: str
    # Registry ``entry.platform`` -- the integration that
    # supplied the entity.  None for state-only entities.
    platform: str | None
    # Registry ``entry.unique_id`` -- used to build the
    # automation edit link.  None for state-only entities.
    unique_id: str | None
    # True if this entity has a registry entry.  False for
    # state-only entities (YAML-defined without unique_id).
    from_registry: bool
    # Registry ``entry.config_entry_id`` -- set when the
    # entity was registered via a UI-created config entry,
    # ``None`` when it came through a YAML platform setup
    # (e.g. legacy ``sensor:`` YAML, ``template:`` YAML).
    # Discriminates "integration page is useful" from
    # "integration page doesn't show this entity, point
    # the user at the YAML instead".
    config_entry_id: str | None = None


@dataclass
class DevicelessDriftDetail:
    """One deviceless entity drift finding."""

    entity_id: str
    expected_object_id: str
    friendly_name: str
    stale_suffix: bool
    platform: str | None
    unique_id: str | None
    from_registry: bool
    config_entry_id: str | None = None


@dataclass
class DevicelessResult:
    """Aggregated deviceless drift result.

    Unlike DeviceResult (one per device), this is a single
    aggregate covering every drifted deviceless entity --
    deviceless entities have no natural grouping, so we
    emit one bucket notification instead of one per entity.
    """

    has_issue: bool
    notification_id: str
    notification_title: str
    notification_message: str
    drifted: list[DevicelessDriftDetail]
    entities_checked: int
    entities_excluded: int


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
    # default_name -- skip recommendation to avoid
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
    """True if device-entity-id check is active."""
    return DRIFT_CHECK_DEVICE_ENTITY_ID in config.drift_checks


def _check_name_enabled(config: Config) -> bool:
    """True if device-entity-name check is active."""
    return DRIFT_CHECK_DEVICE_ENTITY_NAME in config.drift_checks


def _check_deviceless_enabled(config: Config) -> bool:
    """True if deviceless entity-id check is active."""
    return DRIFT_CHECK_ENTITY_ID in config.drift_checks


def _matches_with_collision_suffix(
    obj_id: str,
    expected: str,
    peers: set[str],
) -> tuple[bool, bool]:
    """Decide whether ``obj_id`` is a match for ``expected``.

    Returns ``(matches, stale_suffix)``:

    - ``obj_id == expected`` -> ``(True, False)``.
    - ``obj_id`` equals ``<expected>_N`` for integer
      ``N >= 2`` AND ``expected`` is in ``peers``
      -> ``(True, False)`` -- a valid HA collision suffix.
    - ``obj_id`` equals ``<expected>_N`` for ``N >= 2``,
      ``expected`` is not in ``peers``, but a higher
      ``<expected>_M`` (``M > N``) is in ``peers``
      -> ``(True, False)`` -- not flagged; the highest
      entry in the chain is flagged instead so renaming
      it to ``expected`` resolves the whole chain.
    - ``obj_id`` equals ``<expected>_N`` for ``N >= 2``,
      no base peer, and no higher chain peer
      -> ``(False, True)`` -- a stale suffix.
    - Otherwise -> ``(False, False)`` -- plain drift.
    """
    if not expected:
        return (False, False)
    if obj_id == expected:
        return (True, False)
    if not obj_id.startswith(f"{expected}_"):
        return (False, False)
    rest = obj_id[len(expected) + 1 :]
    if not rest.isdigit():
        return (False, False)
    # Reject leading-zero forms ("01", "0") so "_0"
    # and "_01" aren't mistaken for HA suffixes; HA
    # uses "_2", "_3", ... starting at 2.
    if rest.startswith("0"):
        return (False, False)
    n = int(rest)
    if n < 2:
        return (False, False)
    if expected in peers:
        return (True, False)
    # No base peer. Scan for any higher-numbered chain
    # peer; if present, defer flagging to it so the user
    # fixes the chain in one rename.
    prefix = f"{expected}_"
    for p in peers:
        if not p.startswith(prefix):
            continue
        rest_p = p[len(prefix) :]
        if not rest_p.isdigit() or rest_p.startswith("0"):
            continue
        if int(rest_p) > n:
            return (True, False)
    return (False, True)


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
    name section -- the ID will be addressed after the name
    is fixed.
    """
    lines: list[str] = []
    lines.append(
        f"Device: [{md_escape(device.de.name)}]({device.de.url})",
    )
    integrations = sorted(
        device.de.integration_entities.keys(),
    )
    if integrations:
        lines.append(
            f"Integrations: {', '.join(integrations)}",
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
                f'- `{d.entity_id}`: "{d.current_name}"',
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
            expected = d.expected_name or ""
            lines.append(
                f'- `{d.entity_id}`: "{d.current_name}" \u2192 "{expected}"',
            )
        lines.append(
            "  The override includes the device name,"
            " which Home Assistant already adds."
            " Edit the override to remove"
            f' "{device.de.name} " or clear it entirely.',
        )

    if name_set:
        lines.append("")
        lines.append("**Name overrides to set:**")
        for d in name_set:
            override = d.recommended_override or ""
            lines.append(
                f'- `{d.entity_id}`: set to "{override}"',
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
            lines.append(f"- `{d.entity_id}`")

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
    notification_id = f"{config.notification_prefix}device_{device.de.id}"

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
        title = f"Entity defaults watchdog: {device.de.name}"
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


def _deviceless_line_suffix(
    entity_id: str,
    friendly_name: str,
    platform: str | None,
    unique_id: str | None,
    from_registry: bool,
    config_entry_id: str | None = None,
) -> str:
    """Build the indented second line of a deviceless
    drift bullet.

    The exact layout varies by entity kind:

    - ``automation`` / ``script``: the friendly name is
      itself the link to that entity's editor.
    - registry-backed, UI-configured (``config_entry_id``
      set): plain friendly name followed by
      `` -  integration [<platform>](...)`` so the user can
      click through to the integration's config page.
    - registry-backed, YAML-configured (``config_entry_id``
      is ``None``): same but the integration name is plain
      text with a `` -  YAML-configuration`` note. The
      integration page doesn't show YAML-defined entities,
      so a link there would mislead -- the user should edit
      the YAML instead.
    - otherwise (state-only YAML without ``unique_id:``):
      plain friendly name followed by a nudge to add a
      ``unique_id:`` -- no per-entity exclusion suggestion.

    The friendly name is markdown-escaped in every branch
    so brackets or backslashes in the name can't break the
    surrounding link markdown.

    See docs/entity_defaults_watchdog.md for rationale.
    """
    dom, obj_id = entity_id.split(".", 1)
    name = md_escape(friendly_name)
    if dom == "automation" and unique_id:
        return f"[{name}](/config/automation/edit/{unique_id})"
    if dom == "script":
        return f"[{name}](/config/script/edit/{obj_id})"
    if from_registry and platform:
        if config_entry_id:
            url = f"/config/integrations/integration/{platform}"
            return f"{name}  -  integration [{platform}]({url})"
        return f"{name}  -  integration {platform}  -  YAML-configuration"
    return f"{name}  -  add `unique_id:` to make this entity manageable"


def _build_deviceless_notification_message(
    drift_items: list[DevicelessDriftDetail],
    stale_items: list[DevicelessDriftDetail],
) -> str:
    """Build the deviceless-bucket notification body.

    Two sections -- generic drift and stale collision
    suffixes -- each shown only when non-empty. Bullets
    carry current/expected entity_id, friendly name, and
    a per-domain pointer (edit link or integration page).
    """
    sections: list[str] = []

    if drift_items:
        lines = [
            f"Entity IDs do not match their names ({len(drift_items)}):",
        ]
        sorted_drift = [(d.entity_id, i, d) for i, d in enumerate(drift_items)]
        sorted_drift.sort()
        for _, _, d in sorted_drift:
            dom = d.entity_id.split(".", 1)[0]
            suffix = _deviceless_line_suffix(
                d.entity_id,
                d.friendly_name,
                d.platform,
                d.unique_id,
                d.from_registry,
                d.config_entry_id,
            )
            lines.append(
                f"- `{d.entity_id}` -> expected `{dom}.{d.expected_object_id}`",
            )
            lines.append(f"  {suffix}")
        sections.append("\n".join(lines))

    if stale_items:
        lines = [
            "Stale collision suffixes"
            " (original peer removed, rename recommended):",
        ]
        sorted_stale = [(d.entity_id, i, d) for i, d in enumerate(stale_items)]
        sorted_stale.sort()
        for _, _, d in sorted_stale:
            dom = d.entity_id.split(".", 1)[0]
            suffix = _deviceless_line_suffix(
                d.entity_id,
                d.friendly_name,
                d.platform,
                d.unique_id,
                d.from_registry,
                d.config_entry_id,
            )
            lines.append(
                f"- `{d.entity_id}` -> rename to"
                f" `{dom}.{d.expected_object_id}`",
            )
            lines.append(f"  {suffix}")
        sections.append("\n".join(lines))

    return "\n\n".join(sections)


def _evaluate_deviceless(
    config: Config,
    entities: list[DevicelessEntityInfo],
    peers_by_domain: dict[str, set[str]],
) -> DevicelessResult:
    """Evaluate entity_id drift for deviceless entities.

    Classifies each entity into ok, drift, or stale
    suffix, then builds a single bucket notification
    covering all flagged entities.
    """
    from helpers import slugify

    drift_items: list[DevicelessDriftDetail] = []
    stale_items: list[DevicelessDriftDetail] = []
    excluded = 0

    for entity in entities:
        if _is_excluded(
            config,
            entity.entity_id,
            entity.effective_name,
        ):
            excluded += 1
            continue

        if not entity.effective_name:
            continue

        expected = slugify(entity.effective_name)
        if not expected:
            continue

        dom, obj_id = entity.entity_id.split(".", 1)
        peers = peers_by_domain.get(dom, set())
        matches, stale = _matches_with_collision_suffix(
            obj_id,
            expected,
            peers,
        )
        if matches:
            continue

        detail = DevicelessDriftDetail(
            entity_id=entity.entity_id,
            expected_object_id=expected,
            friendly_name=entity.effective_name,
            stale_suffix=stale,
            platform=entity.platform,
            unique_id=entity.unique_id,
            from_registry=entity.from_registry,
            config_entry_id=entity.config_entry_id,
        )
        if stale:
            stale_items.append(detail)
        else:
            drift_items.append(detail)

    has_issue = bool(drift_items) or bool(stale_items)
    title = ""
    message = ""
    if has_issue:
        title = "Entity defaults watchdog: deviceless entity drift"
        message = _build_deviceless_notification_message(
            drift_items,
            stale_items,
        )

    all_drifted: list[DevicelessDriftDetail] = []
    all_drifted.extend(drift_items)
    all_drifted.extend(stale_items)

    return DevicelessResult(
        has_issue=has_issue,
        notification_id=f"{config.notification_prefix}deviceless",
        notification_title=title,
        notification_message=message,
        drifted=all_drifted,
        entities_checked=len(entities) - excluded,
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
    stat_deviceless_entities: int
    stat_deviceless_excluded: int
    stat_deviceless_drift: int
    stat_deviceless_stale: int


def run_evaluation(
    config: Config,
    devices: list[DeviceInfo],
    deviceless_entities: list[DevicelessEntityInfo],
    peers_by_domain: dict[str, set[str]],
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
        f"{config.notification_prefix}cap",
        "Entity defaults watchdog: notification cap reached",
        "devices with drift",
    )

    if _check_deviceless_enabled(config):
        deviceless = _evaluate_deviceless(
            config,
            deviceless_entities,
            peers_by_domain,
        )
    else:
        deviceless = DevicelessResult(
            has_issue=False,
            notification_id=f"{config.notification_prefix}deviceless",
            notification_title="",
            notification_message="",
            drifted=[],
            entities_checked=0,
            entities_excluded=0,
        )
    notifications.append(
        PersistentNotification(
            active=deviceless.has_issue,
            notification_id=deviceless.notification_id,
            title=deviceless.notification_title,
            message=deviceless.notification_message,
        ),
    )

    issues = [r for r in results if r.has_issue]
    stat_deviceless_stale = sum(
        [1 for d in deviceless.drifted if d.stale_suffix]
    )
    stat_deviceless_drift = len(deviceless.drifted) - stat_deviceless_stale

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
        stat_deviceless_entities=deviceless.entities_checked,
        stat_deviceless_excluded=deviceless.entities_excluded,
        stat_deviceless_drift=stat_deviceless_drift,
        stat_deviceless_stale=stat_deviceless_stale,
    )
