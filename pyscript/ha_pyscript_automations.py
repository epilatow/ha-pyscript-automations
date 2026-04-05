# This is AI generated code
"""PyScript service wrappers.

Thin layer bridging Home Assistant and pure logic modules.
All business logic lives in modules/ and is tested
separately.

IMPORTANT: No sleeping, no waiting. Services are purely
reactive: trigger -> evaluate -> act -> exit.
"""

import json
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

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


def _update_persistent_error_notifications(
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

    if errors:
        name = _automation_name(instance_id)
        persistent_notification.create(  # noqa: F821
            title=(name + ": Invalid Configuration"),
            message=(
                "Configuration errors:\n\n- "
                + "\n- ".join(errors)
                + "\n\nPlease fix the automation"
                " configuration."
            ),
            notification_id=notif_id,
        )
    else:
        persistent_notification.dismiss(  # noqa: F821
            notification_id=notif_id,
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
    trigger_threshold: str,
    release_threshold: str,
    sampling_window_s: str,
    disable_window_s: str,
    auto_off_min: str,
    notification_service: str,
    notification_prefix: str,
    notification_suffix: str,
    debug: str,
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
    auto_name = _automation_name(instance_id)
    tag = "[STSC: " + auto_name + "]"

    # Validate entities
    errors = _validate_entities(
        [target_switch_entity],
        EntityType.CONTROLLABLE,
    )
    _update_persistent_error_notifications(
        errors,
        instance_id,
        "Sensor Threshold Switch Controller",
    )
    if errors:
        if str(debug).lower() == "true":
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

    # Evaluate (pure logic)
    result = handle_service_call(
        state_data=state_data,
        switch_name=switch_name,
        current_time=now,
        target_switch_entity=target_switch_entity,
        sensor_value=sensor_value,
        switch_state=switch_state,
        trigger_entity=trigger_entity,
        trigger_threshold=float(trigger_threshold),
        release_threshold=float(release_threshold),
        sampling_window_s=int(sampling_window_s),
        disable_window_s=int(disable_window_s),
        auto_off_min=int(auto_off_min),
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

    # Save state + debug attributes to entity
    info = _stsc_debug_dict(result, now, sensor_value)
    state.set(key, "ok")  # noqa: F821
    state.setattr(  # noqa: F821
        key + ".data",
        json.dumps(result.state_dict),
    )
    for attr_name, attr_val in info.items():
        state.setattr(  # noqa: F821
            key + "." + attr_name,
            attr_val,
        )

    # Debug logging (opt-in via blueprint)
    #    debug may arrive as bool or string depending on
    #    how HA resolves the blueprint !input tag.
    if str(debug).lower() == "true":
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
    monitored_integrations: str,
    device_exclude_regex: str,
    entity_exclude_regex: str,
    monitored_entity_domains: object,
    check_interval_minutes: str,
    dead_device_threshold_minutes: str,
    debug_output: str,
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
        evaluate_devices,
        should_run,
    )

    now = datetime.now(tz=UTC)
    auto_name = _automation_name(instance_id)
    tag = "[DW: " + auto_name + "]"

    # Verify hass is available
    try:
        hass  # noqa: F821, B018
    except NameError:
        persistent_notification.create(  # noqa: F821
            title="Device Watchdog: Configuration Error",
            message=(
                "pyscript must have hass_is_global"
                " enabled. Add to configuration.yaml:\n"
                "pyscript:\n"
                "  hass_is_global: true\n"
                "  allow_all_imports: true"
            ),
            notification_id="device_watchdog_config_error",
        )
        return

    # Interval gating
    interval = int(check_interval_minutes)
    assert interval >= 1, f"check_interval_minutes must be >= 1, got {interval}"
    if not should_run(interval, now):
        return

    # Parse config
    integrations = _normalize_list(
        monitored_integrations,
    )
    domains = _normalize_list(
        monitored_entity_domains,
    )
    threshold_m = int(dead_device_threshold_minutes)
    assert threshold_m >= 1, (
        f"dead_device_threshold_minutes must be >= 1, got {threshold_m}"
    )
    threshold_s = threshold_m * 60
    debug_logging = str(debug_output).lower() == "true"

    dev_regex = str(device_exclude_regex or "")
    ent_regex = str(entity_exclude_regex or "")

    # Validate regex patterns
    errors = []
    err = _validate_regex(dev_regex)
    if err:
        errors.append(
            'device_exclude_regex: "' + dev_regex + '": ' + err,
        )
    err = _validate_regex(ent_regex)
    if err:
        errors.append(
            'entity_exclude_regex: "' + ent_regex + '": ' + err,
        )
    if errors:
        persistent_notification.create(  # noqa: F821
            title="Device Watchdog: Invalid Regex",
            message="\n".join(
                ["Invalid regular expression for " + e for e in errors]
            ),
            notification_id=("device_watchdog_config_error"),
        )
        return

    config = Config(
        device_exclude_regex=dev_regex,
        entity_exclude_regex=ent_regex,
        monitored_entity_domains=domains,
        dead_threshold_seconds=threshold_s,
    )

    # Discover devices and their entities from
    #    monitored integrations. Only entities belonging
    #    to configured integrations are checked — we do
    #    not re-query the device registry for all entities.
    device_map: dict[str, dict[str, Any]] = {}
    for integration_id in integrations:
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
                }
            device_map[dev_id]["entity_ids"].append(
                entity_id,
            )

    # Read entity state and build DeviceInfo list
    devices = []
    for dev_id, dev_info in device_map.items():
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
                entities=entity_infos,
            ),
        )

    # Evaluate (pure logic)
    results = evaluate_devices(config, devices, now)

    # Create/dismiss notifications
    for result in results:
        if result.has_issue:
            persistent_notification.create(  # noqa: F821
                title=result.notification_title,
                message=result.notification_message,
                notification_id=result.notification_id,
            )
        else:
            persistent_notification.dismiss(  # noqa: F821
                notification_id=result.notification_id,
            )

    # Write debug attributes
    key = _state_key(instance_id)
    issues = [r for r in results if r.has_issue]
    state.set(key, "ok")  # noqa: F821
    state.setattr(  # noqa: F821
        key + ".last_run",
        now.isoformat(),
    )
    state.setattr(  # noqa: F821
        key + ".devices_checked",
        len(results),
    )
    state.setattr(  # noqa: F821
        key + ".devices_with_issues",
        len(issues),
    )
    state.setattr(  # noqa: F821
        key + ".integrations",
        json.dumps(integrations),
    )

    # Debug logging
    if debug_logging:
        issue_names = [r.device_name for r in issues]
        log.warning(  # noqa: F821
            "%s checked=%d issues=%d integrations=%s devices_with_issues=%s",
            tag,
            len(results),
            len(issues),
            integrations,
            issue_names,
        )
