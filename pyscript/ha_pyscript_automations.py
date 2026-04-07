# This is AI generated code
"""PyScript service wrappers.

Thin layer bridging Home Assistant and logic modules.
All business logic lives in modules/ and is tested
separately.

IMPORTANT: No sleeping, no waiting. Services are purely
reactive: trigger -> evaluate -> act -> exit.
"""

import json
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from notification_helpers import PersistentNotification  # noqa: F821

if TYPE_CHECKING:
    from collections.abc import Callable

    class _State:
        def get(self, key: str) -> Any: ...
        def getattr(
            self,
            entity_id: str,
        ) -> dict[str, str]: ...
        def set(
            self,
            key: str,
            value: str,
        ) -> None: ...
        def setattr(
            self,
            key: str,
            value: Any,
        ) -> None: ...

    class _HomeAssistant:
        def turn_on(
            self,
            *,
            entity_id: str,
        ) -> None: ...
        def turn_off(
            self,
            *,
            entity_id: str,
        ) -> None: ...

    class _Service:
        def __call__(
            self,
            fn: Callable[..., None],
        ) -> Callable[..., None]: ...
        def call(
            self,
            domain: str,
            svc: str,
            **kwargs: Any,
        ) -> None: ...

    class _Log:
        def warning(
            self,
            msg: str,
            *args: Any,
        ) -> None: ...

    class _PersistentNotification:
        def create(
            self,
            **kwargs: str,
        ) -> None: ...
        def dismiss(
            self,
            **kwargs: str,
        ) -> None: ...

    state: _State
    homeassistant: _HomeAssistant
    service: _Service
    log: _Log
    persistent_notification: _PersistentNotification
    hass: Any

# ── Shared helpers ──────────────────────────────────


def _state_key(instance_id: str) -> str:
    """Build persistence key for an automation instance."""
    safe = instance_id.replace(".", "_")
    return f"pyscript.{safe}_state"


def _save_state(
    key: str,
    now: datetime,
    attrs: dict[str, Any],
) -> None:
    """Persist state and debug attributes.

    Sets the entity state to "ok", writes last_run,
    and writes all attrs as entity attributes.
    """
    state.set(key, "ok")  # noqa: F821
    state.setattr(  # noqa: F821
        key + ".last_run",
        now.isoformat(),
    )
    for name, value in attrs.items():
        state.setattr(  # noqa: F821
            key + "." + name,
            value,
        )


def _process_persistent_notifications(
    notifications: "list[PersistentNotification]",
) -> None:
    """Create or dismiss persistent notifications."""
    for n in notifications:
        if n.active:
            persistent_notification.create(  # noqa: F821
                title=n.title,
                message=n.message,
                notification_id=n.notification_id,
            )
        else:
            persistent_notification.dismiss(  # noqa: F821
                notification_id=n.notification_id,
            )


def _parse_bool(value: object) -> bool:
    """Parse a boolean from a blueprint input.

    Handles bool values and string "true"/"false".
    """
    if isinstance(value, bool):
        return value
    return str(value).lower() == "true"


def _normalize_list(value: object) -> list[str]:
    """Ensure value is a list of strings.

    Blueprint select with multiple: true sends a list,
    but handle comma-separated strings defensively.
    """
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str) and value:
        return [s.strip() for s in value.split(",")]
    return []


def _validate_regex(pattern: str) -> str | None:
    """Return error string if pattern is invalid regex.

    Returns None if pattern is empty or valid.
    Rejects patterns that match the empty string (e.g.,
    "|||||") since those would exclude everything.
    """
    import re

    if not pattern:
        return None
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        return str(exc)
    if compiled.match(""):
        return "pattern matches empty string"
    return None


def _get_integration_entities(
    hass_obj: object,
    integration_id: str,
) -> list[str]:
    """Entity IDs for an integration."""
    import homeassistant.helpers.template as ha_tmpl  # noqa: F821

    return list(
        ha_tmpl.integration_entities(
            hass_obj,
            integration_id,
        ),
    )


def _get_device_for_entity(
    hass_obj: object,
    entity_id: str,
) -> dict[str, str] | None:
    """Return {id, name} for the device owning entity_id.

    Returns None if entity has no device.
    """
    import homeassistant.helpers.device_registry as dr  # noqa: F821
    import homeassistant.helpers.entity_registry as er  # noqa: F821

    ent_reg = er.async_get(hass_obj)
    dev_reg = dr.async_get(hass_obj)
    entry = ent_reg.async_get(entity_id)
    if not entry or not entry.device_id:
        return None
    device = dev_reg.async_get(entry.device_id)
    if not device:
        return None
    name = device.name_by_user or device.name or ""
    return {"id": entry.device_id, "name": name}


def _read_entity_state(
    entity_id: str,
) -> tuple[Any, Any]:
    """Read entity state + last_changed."""
    entity_state = state.get(entity_id)  # noqa: F821
    last_changed = state.get(  # noqa: F821
        entity_id + ".last_changed",
    )
    return entity_state, last_changed


def _automation_name(instance_id: str) -> str:
    """Resolve the user-assigned automation name.

    Falls back to instance_id if unavailable.
    """
    try:
        attrs = state.getattr(instance_id)  # noqa: F821
        name = attrs.get("friendly_name", "")
        if name:
            return name
    except Exception:
        pass
    return instance_id


# Domains that support homeassistant.turn_on/turn_off.
CONTROLLABLE_DOMAINS = frozenset(
    [
        "automation",
        "climate",
        "cover",
        "fan",
        "humidifier",
        "input_boolean",
        "light",
        "lock",
        "media_player",
        "switch",
        "vacuum",
        "water_heater",
    ]
)

# Domains with binary on/off state.
BINARY_DOMAINS = frozenset(
    [
        "binary_sensor",
        "input_boolean",
    ]
)


class EntityType(Enum):
    """Entity type for domain validation."""

    ANY = "any"
    CONTROLLABLE = "controllable"
    BINARY = "binary"


_DOMAIN_MAP = {
    EntityType.CONTROLLABLE: (
        CONTROLLABLE_DOMAINS,
        "does not support on/off",
    ),
    EntityType.BINARY: (
        BINARY_DOMAINS,
        "is not a binary entity",
    ),
}


def _entity_domain(entity_id: str) -> str:
    """Extract domain from an entity ID."""
    return entity_id.split(".")[0] if "." in entity_id else ""


def _validate_entities(
    entities: list[str],
    entity_type: EntityType,
) -> list[str]:
    """Validate entities exist and check domain type.

    Returns list of error strings.

    entities: flat list of entity IDs.
    entity_type: EntityType.ANY for existence-only,
      CONTROLLABLE or BINARY for domain checks.
    """
    errors = []
    for eid in entities:
        try:
            val = state.get(eid)  # noqa: F821
        except NameError:
            val = None
        if val is None:
            errors.append(
                eid + " does not exist",
            )
        elif entity_type in _DOMAIN_MAP:
            allowed, msg = _DOMAIN_MAP[entity_type]
            domain = _entity_domain(eid)
            if domain not in allowed:
                errors.append(
                    eid + " (domain: " + domain + ") " + msg,
                )
    return errors


def _manage_config_error_persistent_notification(
    errors: list[str],
    instance_id: str,
    service_label: str,
) -> None:
    """Create or dismiss a config error notification.

    service_label: label used in the notification ID
      prefix.
    """
    safe_id = instance_id.replace(".", "_")
    prefix = service_label.lower().replace(" ", "_")
    notif_id = prefix + "_config_error_" + safe_id
    name = _automation_name(instance_id)

    message = ""
    if errors:
        message = (
            "Configuration errors:\n\n- "
            + "\n- ".join(errors)
            + "\n\nPlease fix the"
            " automation configuration."
        )

    _process_persistent_notifications(
        [
            PersistentNotification(
                active=bool(errors),
                notification_id=notif_id,
                title=(name + ": Invalid Configuration"),
                message=message,
            ),
        ],
    )


# ── Sensor Threshold Switch Controller ──────────────


def _stsc_debug_dict(
    result: Any,
    now: datetime,
    sensor_value: str,
) -> dict[str, str]:
    """Build debug info dict from a ServiceResult."""
    sv = result.sensor_value
    return {
        "last_action": result.action.name,
        "last_reason": result.reason or "n/a",
        "last_event": result.event_type,
        "last_run": now.isoformat(),
        "last_sensor": str(sv) if sv is not None else "n/a",
    }


# Parameter defaults are defined in the blueprint YAML,
# so don't duplicate them here.
@service  # noqa: F821
def sensor_threshold_switch_controller(
    instance_id: str,
    target_switch_entity: str,
    sensor_value: str,
    switch_state: str,
    trigger_entity: str,
    trigger_threshold_raw: str,
    release_threshold_raw: str,
    sampling_window_seconds_raw: str,
    disable_window_seconds_raw: str,
    auto_off_minutes_raw: str,
    notification_service: str,
    notification_prefix: str,
    notification_suffix: str,
    debug_logging_raw: str,
) -> None:
    """Evaluate sensor threshold switch controller.

    Called by blueprint-generated automation.
    Purely reactive: evaluate -> act -> exit.
    No sleeping, no waiting.
    """
    from sensor_threshold_switch_controller import (  # noqa: F821
        Action,
        handle_service_call,
    )

    now = datetime.now()
    debug_logging = _parse_bool(debug_logging_raw)
    auto_name = _automation_name(instance_id)
    tag = "[STSC: " + auto_name + "]"

    # Validate entities
    errors = _validate_entities(
        [target_switch_entity],
        EntityType.CONTROLLABLE,
    )
    _manage_config_error_persistent_notification(
        errors,
        instance_id,
        "Sensor Threshold Switch Controller",
    )
    if errors:
        if debug_logging:
            log.warning(  # noqa: F821
                "%s invalid config: %s",
                tag,
                errors,
            )
        return

    # Load state from HA entity attribute
    #    (entity state is limited to 255 chars; attributes
    #    have no practical limit)
    key = _state_key(instance_id)
    state_data: dict[str, Any] | None = None
    try:
        attrs = state.getattr(key)  # noqa: F821
        raw = attrs.get("data", "")
        if raw:
            state_data = json.loads(raw)
    except Exception:
        pass

    # Resolve friendly name
    switch_name = target_switch_entity
    try:
        attrs = state.getattr(  # noqa: F821
            target_switch_entity,
        )
        name = attrs.get("friendly_name", "")
        if name:
            switch_name = name
    except Exception:
        pass

    # Evaluate
    result = handle_service_call(
        state_data=state_data,
        switch_name=switch_name,
        current_time=now,
        target_switch_entity=target_switch_entity,
        sensor_value=sensor_value,
        switch_state=switch_state,
        trigger_entity=trigger_entity,
        trigger_threshold=float(trigger_threshold_raw),
        release_threshold=float(release_threshold_raw),
        sampling_window_s=int(sampling_window_seconds_raw),
        disable_window_s=int(disable_window_seconds_raw),
        auto_off_min=int(auto_off_minutes_raw),
        notification_service=notification_service,
        notification_prefix=notification_prefix,
        notification_suffix=notification_suffix,
    )

    # Execute action
    if result.action == Action.TURN_ON:
        homeassistant.turn_on(  # noqa: F821
            entity_id=target_switch_entity,
        )
    elif result.action == Action.TURN_OFF:
        homeassistant.turn_off(  # noqa: F821
            entity_id=target_switch_entity,
        )

    # Send notification
    if result.notification and result.notification_service:
        parts = result.notification_service.split(".")
        service.call(  # noqa: F821
            parts[0],
            parts[1],
            message=result.notification,
        )

    # Save state + debug attributes
    info = _stsc_debug_dict(result, now, sensor_value)
    info["data"] = json.dumps(result.state_dict)
    _save_state(key, now, info)

    # Debug logging (opt-in via blueprint)
    if debug_logging:
        log.warning(  # noqa: F821
            "%s event=%s sw=%s baseline=%s auto_off=%s samples=%s -> %s %r",
            tag,
            info["last_event"],
            switch_state,
            result.state_dict.get("baseline"),
            result.state_dict.get("auto_off_started_at"),
            len(result.state_dict.get("samples", [])),
            info["last_action"],
            info["last_reason"],
        )


# ── Device Watchdog ─────────────────────────────────


# Parameter defaults are defined in the blueprint YAML,
# so don't duplicate them here.
@service  # noqa: F821
def device_watchdog(
    instance_id: str,
    monitored_integrations_raw: object,
    device_exclude_regex_raw: str,
    entity_exclude_regex_raw: str,
    monitored_entity_domains_raw: object,
    check_interval_minutes_raw: str,
    dead_device_threshold_minutes_raw: str,
    check_diagnostic_entities_raw: str,
    debug_logging_raw: str,
    trigger_platform_raw: str,
) -> None:
    """Evaluate device health across integrations.

    Called by blueprint-generated automation.
    Purely reactive: evaluate -> act -> exit.
    No sleeping, no waiting.
    """
    from device_watchdog import (  # noqa: F821
        Config,
        DeviceInfo,
        EntityInfo,
        RegistryEntry,
        evaluate_devices,
        evaluate_diagnostics,
        should_run,
    )

    now = datetime.now(tz=UTC)
    auto_name = _automation_name(instance_id)
    tag = "[DW: " + auto_name + "]"

    # Verify hass is available
    try:
        hass  # noqa: F821, B018
    except NameError:
        _manage_config_error_persistent_notification(
            [
                "pyscript must have hass_is_global"
                " enabled. Add to configuration.yaml:\n"
                "pyscript:\n"
                "  hass_is_global: true\n"
                "  allow_all_imports: true"
            ],
            instance_id,
            "Device Watchdog",
        )
        return

    # Interval gating (skip for manual runs from UI)
    check_interval_minutes = int(
        check_interval_minutes_raw,
    )
    assert check_interval_minutes >= 1, (
        f"check_interval_minutes must be >= 1, got {check_interval_minutes}"
    )
    if str(trigger_platform_raw) == "time_pattern":
        if not should_run(check_interval_minutes, now):
            return

    # Parse config
    monitored_integrations = _normalize_list(
        monitored_integrations_raw,
    )
    monitored_entity_domains = _normalize_list(
        monitored_entity_domains_raw,
    )
    dead_device_threshold_minutes = int(
        dead_device_threshold_minutes_raw,
    )
    assert dead_device_threshold_minutes >= 1, (
        "dead_device_threshold_minutes must be >= 1,"
        f" got {dead_device_threshold_minutes}"
    )
    dead_threshold_seconds = dead_device_threshold_minutes * 60
    check_diagnostics = _parse_bool(
        check_diagnostic_entities_raw,
    )
    debug_logging = _parse_bool(debug_logging_raw)

    device_exclude_regex = str(
        device_exclude_regex_raw or "",
    )
    entity_exclude_regex = str(
        entity_exclude_regex_raw or "",
    )

    # Validate regex patterns
    errors = []
    err = _validate_regex(device_exclude_regex)
    if err:
        errors.append(
            'device_exclude_regex: "' + device_exclude_regex + '": ' + err,
        )
    err = _validate_regex(entity_exclude_regex)
    if err:
        errors.append(
            'entity_exclude_regex: "' + entity_exclude_regex + '": ' + err,
        )
    _manage_config_error_persistent_notification(
        errors,
        instance_id,
        "Device Watchdog",
    )
    if errors:
        return

    config = Config(
        device_exclude_regex=device_exclude_regex,
        entity_exclude_regex=entity_exclude_regex,
        monitored_entity_domains=monitored_entity_domains,
        dead_threshold_seconds=dead_threshold_seconds,
    )

    # Discover devices and their entities from
    # monitored integrations.
    if check_diagnostics:
        import homeassistant.helpers.entity_registry as er  # noqa: F821

        ent_reg = er.async_get(hass)  # noqa: F821

    device_map: dict[str, dict[str, Any]] = {}
    for integration_id in monitored_integrations:
        try:
            entities = _get_integration_entities(
                hass,  # noqa: F821
                integration_id,
            )
        except Exception:
            continue
        for entity_id in entities:
            try:
                info = _get_device_for_entity(
                    hass,  # noqa: F821
                    entity_id,
                )
            except Exception:
                continue
            if not info:
                continue
            dev_id = info["id"]
            if dev_id not in device_map:
                device_map[dev_id] = {
                    "name": info["name"],
                    "entity_ids": [],
                    "integrations": set(),
                }
            device_map[dev_id]["entity_ids"].append(
                entity_id,
            )
            device_map[dev_id]["integrations"].add(integration_id)

    # Build DeviceInfo with state + registry data
    devices = []
    for dev_id, dev_info in device_map.items():
        # Build registry entries if diagnostic check
        # is enabled (requires entity registry access)
        registry_entries: list[RegistryEntry] = []
        if check_diagnostics:
            all_reg_entries = er.async_entries_for_device(
                ent_reg,
                dev_id,
                include_disabled_entities=True,
            )
            registry_entries = [
                RegistryEntry(
                    entity_id=e.entity_id,
                    original_name=(e.original_name or ""),
                    platform=e.platform or "",
                    entity_category=(
                        str(e.entity_category.value)
                        if e.entity_category
                        else None
                    ),
                    disabled=(e.disabled_by is not None),
                )
                for e in all_reg_entries
                if e.platform in dev_info["integrations"]
            ]

        # Read state for enabled entities
        entity_infos = []
        for eid in dev_info["entity_ids"]:
            try:
                ent_state, last_changed = _read_entity_state(eid)
                if ent_state is not None:
                    entity_infos.append(
                        EntityInfo(
                            entity_id=eid,
                            state=str(ent_state),
                            last_changed=last_changed,
                        ),
                    )
            except Exception:
                continue

        url = "/config/devices/device/" + dev_id
        devices.append(
            DeviceInfo(
                device_id=dev_id,
                device_name=dev_info["name"],
                device_url=url,
                integrations=list(
                    dev_info["integrations"],
                ),
                entities=entity_infos,
                registry_entries=registry_entries,
            ),
        )

    # Evaluate health
    results = evaluate_devices(config, devices, now)

    # Check for disabled diagnostic entities
    diag_notifications = []
    if check_diagnostics:
        diag_notifications = evaluate_diagnostics(devices)

    # Process all notifications
    notifications = [r.to_notification() for r in results]
    notifications += diag_notifications
    _process_persistent_notifications(notifications)

    # Write debug attributes
    key = _state_key(instance_id)
    issues = [r for r in results if r.has_issue]
    _save_state(
        key,
        now,
        {
            "devices_checked": len(results),
            "devices_with_issues": len(issues),
            "integrations": json.dumps(
                monitored_integrations,
            ),
        },
    )

    # Debug logging
    if debug_logging:
        issue_names = [r.device_name for r in issues]
        log.warning(  # noqa: F821
            "%s checked=%d issues=%d integrations=%s devices_with_issues=%s",
            tag,
            len(results),
            len(issues),
            monitored_integrations,
            issue_names,
        )


# ── Trigger Entity Controller ────────────────────


# Parameter defaults are defined in the blueprint YAML,
# so don't duplicate them here.
@service  # noqa: F821
def trigger_entity_controller(
    instance_id: str,
    controlled_entities_raw: object,
    trigger_entity_id: str,
    trigger_to_state: str,
    auto_off_minutes_raw: str,
    trigger_entities_raw: object,
    trigger_period_raw: str,
    trigger_forces_on_raw: str,
    trigger_disabling_entities_raw: object,
    trigger_disabling_period_raw: str,
    auto_off_disabling_entities_raw: object,
    notification_service: str,
    notification_prefix_raw: str,
    notification_suffix_raw: str,
    notification_events_raw: object,
    debug_logging_raw: str,
) -> None:
    """Control entities with trigger-based activation.

    Called by blueprint-generated automation.
    Purely reactive: evaluate -> act -> exit.
    No sleeping, no waiting.
    """
    from trigger_entity_controller import (  # noqa: F821
        ActionType,
        Config,
        Inputs,
        determine_event_type,
        evaluate,
        parse_notification_events,
        parse_period,
    )

    now = datetime.now()
    debug_logging = _parse_bool(debug_logging_raw)
    auto_name = _automation_name(instance_id)
    tag = "[TEC: " + auto_name + "]"

    # Parse inputs
    controlled_entities = _normalize_list(
        controlled_entities_raw,
    )
    trigger_entities = _normalize_list(
        trigger_entities_raw,
    )
    trigger_disabling_entities = _normalize_list(
        trigger_disabling_entities_raw,
    )
    auto_off_disabling_entities = _normalize_list(
        auto_off_disabling_entities_raw,
    )
    auto_off_minutes = int(auto_off_minutes_raw)
    assert auto_off_minutes >= 0, "auto_off_minutes must be >= 0, got " + str(
        auto_off_minutes
    )
    notification_events = parse_notification_events(
        _normalize_list(notification_events_raw),
    )

    # Validate entities
    errors = _validate_entities(
        controlled_entities,
        EntityType.CONTROLLABLE,
    )
    all_disabling = trigger_disabling_entities + auto_off_disabling_entities
    errors += _validate_entities(
        trigger_entities + all_disabling,
        EntityType.BINARY,
    )

    # Check for overlapping entity sets
    ctrl_set = set(controlled_entities)
    trig_set = set(trigger_entities)
    dis_set = set(all_disabling)
    for eid in ctrl_set & trig_set:
        errors.append(
            eid + " is in both controlled and trigger entities",
        )
    for eid in ctrl_set & dis_set:
        errors.append(
            eid + " is in both controlled and disabling entities",
        )
    for eid in trig_set & dis_set:
        errors.append(
            eid + " is in both trigger and disabling entities",
        )

    _manage_config_error_persistent_notification(
        errors,
        instance_id,
        "Trigger Entity Controller",
    )
    if errors:
        if debug_logging:
            log.warning(  # noqa: F821
                "%s invalid config: %s",
                tag,
                errors,
            )
        return

    # Determine event type
    event_type = determine_event_type(
        str(trigger_entity_id or ""),
        str(trigger_to_state or ""),
        trigger_entities,
        controlled_entities,
        all_disabling,
    )
    if event_type is None:
        return

    # Read current entity states for evaluation
    triggers_on = False
    for eid in trigger_entities:
        if state.get(eid) == "on":  # noqa: F821
            triggers_on = True
            break

    controlled_on = False
    for eid in controlled_entities:
        if state.get(eid) == "on":  # noqa: F821
            controlled_on = True
            break

    sun_state = state.get("sun.sun")  # noqa: F821
    is_day_time = sun_state == "above_horizon"

    triggers_disabled = False
    for eid in trigger_disabling_entities:
        if state.get(eid) == "on":  # noqa: F821
            triggers_disabled = True
            break

    auto_off_disabled = False
    for eid in auto_off_disabling_entities:
        if state.get(eid) == "on":  # noqa: F821
            auto_off_disabled = True
            break

    # Resolve friendly names for notifications
    friendly_names: dict[str, str] = {}
    for eid in controlled_entities:
        try:
            a = state.getattr(eid)  # noqa: F821
            n = a.get("friendly_name", "")
            if n:
                friendly_names[eid] = n
        except Exception:
            pass

    # Load auto_off_at from entity attribute
    key = _state_key(instance_id)
    auto_off_at: datetime | None = None
    try:
        attrs = state.getattr(key)  # noqa: F821
        stored = attrs.get("auto_off_at", "")
        if stored:
            auto_off_at = datetime.fromisoformat(stored)
    except Exception:
        pass

    # Build config and inputs, evaluate
    trigger_period = parse_period(
        str(trigger_period_raw),
    )
    trigger_forces_on = _parse_bool(trigger_forces_on_raw)
    trigger_disabling_period = parse_period(
        str(trigger_disabling_period_raw),
    )
    notification_prefix = str(
        notification_prefix_raw or "",
    )
    notification_suffix = str(
        notification_suffix_raw or "",
    )
    config = Config(
        controlled_entities=controlled_entities,
        auto_off_minutes=auto_off_minutes,
        auto_off_disabling_entities=(auto_off_disabling_entities),
        trigger_entities=trigger_entities,
        trigger_period=trigger_period,
        trigger_forces_on=trigger_forces_on,
        trigger_disabling_entities=(trigger_disabling_entities),
        trigger_disabling_period=trigger_disabling_period,
        notification_prefix=notification_prefix,
        notification_suffix=notification_suffix,
        notification_events=notification_events,
    )
    inputs = Inputs(
        current_time=now,
        event_type=event_type,
        changed_entity=str(trigger_entity_id or ""),
        triggers_on=triggers_on,
        controlled_on=controlled_on,
        is_day_time=is_day_time,
        triggers_disabled=triggers_disabled,
        auto_off_disabled=auto_off_disabled,
        auto_off_at=auto_off_at,
        friendly_names=friendly_names,
    )
    result = evaluate(config, inputs)

    # Execute action
    if result.action == ActionType.TURN_ON:
        for eid in result.target_entities:
            homeassistant.turn_on(  # noqa: F821
                entity_id=eid,
            )
    elif result.action == ActionType.TURN_OFF:
        for eid in result.target_entities:
            homeassistant.turn_off(  # noqa: F821
                entity_id=eid,
            )

    # Send notification
    if result.notification and notification_service:
        svc = str(notification_service)
        if not svc.startswith("notify."):
            svc = "notify." + svc
        parts = svc.split(".")
        service.call(  # noqa: F821
            parts[0],
            parts[1],
            message=result.notification,
        )

    # Save state + debug attributes
    new_auto_off = ""
    if result.auto_off_at is not None:
        new_auto_off = result.auto_off_at.isoformat()
    _save_state(
        key,
        now,
        {
            "auto_off_at": new_auto_off,
            "last_action": result.action.name,
            "last_reason": result.reason or "n/a",
            "last_event": event_type.name,
        },
    )

    # Debug logging (opt-in via blueprint)
    if debug_logging:
        log.warning(  # noqa: F821
            "%s event=%s action=%s reason=%r"
            " auto_off_at=%s trigger_on=%s"
            " controlled_on=%s is_day_time=%s",
            tag,
            event_type.name,
            result.action.name,
            result.reason,
            new_auto_off or "none",
            triggers_on,
            controlled_on,
            is_day_time,
        )
