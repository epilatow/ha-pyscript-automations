# This is AI generated code
"""PyScript service wrappers.

Thin layer bridging Home Assistant and logic modules.
All business logic lives in modules/ and is tested
separately.

IMPORTANT: No sleeping, no waiting. Services are purely
reactive: trigger -> evaluate -> act -> exit.

Home Assistant packages (homeassistant.*) must be
imported inside function bodies, not at module level.
Tests exec() this file into a mock namespace that does
not have homeassistant installed; a top-level import
would fail during test collection.
"""

import json
import os
import time
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

from helpers import (  # noqa: F821
    DeviceEntry,
    EntityRegistryInfo,
    PersistentNotification,
    on_interval,
)

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

    # Type-check-only imports for logic-module types
    # referenced by wrapper helper signatures. Keep mypy
    # strict happy without importing at runtime (the
    # modules run under PyScript's AST evaluator, not
    # standard Python imports).
    from entity_defaults_watchdog import DevicelessEntityInfo
    from reference_watchdog import TruthSet

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


def _get_active_notification_ids(
    hass_obj: object,
) -> set[str] | None:
    """Return IDs of all active persistent notifications.

    Reads the live notification dict from hass.data.
    Returns None if unavailable (e.g., in tests), in
    which case callers should dismiss unconditionally.
    """
    try:
        data = hass_obj.data.get(  # type: ignore[attr-defined]
            "persistent_notification",
            {},
        )
        return set(data.keys())
    except (AttributeError, TypeError):
        return None


def _md_escape(s: str) -> str:
    """Escape CommonMark link-text special chars.

    Applied to the display portion of ``[text](url)`` so
    brackets or backslashes in user-supplied names don't
    break the link. Done as a single ``str.translate`` pass
    so the backslashes we insert for ``[``/``]`` are not
    themselves re-escaped by the ``\\`` mapping.
    """
    return s.translate(
        {
            ord("\\"): "\\\\",
            ord("["): "\\[",
            ord("]"): "\\]",
        },
    )


def _process_persistent_notifications(
    notifications: "list[PersistentNotification]",
    instance_id: str,
    active_ids: set[str] | None = None,
) -> None:
    """Create or dismiss persistent notifications.

    Active notifications get an ``Automation: [<name>](<url>)``
    line prepended to their message so the user can jump
    straight to the generating automation's edit page.  If
    the automation entity has no ``id`` attribute the link
    is omitted and the message is dispatched unchanged.

    When active_ids is provided, dismissals are skipped
    for notification IDs not in the set (they were never
    created, so dismissing them is a no-op waste of an
    HA service call).
    """
    try:
        attrs = state.getattr(instance_id)  # noqa: F821
    except Exception:
        attrs = {}
    auto_id = attrs.get("id", "")
    auto_name = attrs.get("friendly_name", "") or instance_id
    link_prefix = ""
    if auto_id:
        link_prefix = (
            "Automation: ["
            + _md_escape(auto_name)
            + "](/config/automation/edit/"
            + str(auto_id)
            + ")\n"
        )

    for n in notifications:
        if n.active:
            message = n.message
            if link_prefix:
                message = link_prefix + message
            persistent_notification.create(  # noqa: F821
                title=n.title,
                message=message,
                notification_id=n.notification_id,
            )
        elif active_ids is None or n.notification_id in active_ids:
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


def _send_notification(
    notification_service: str,
    message: str,
    tag: str,
) -> None:
    """Best-effort notification dispatch.

    notification_service may be a bare ``"notify"`` or
    a fully-qualified ``"notify.<service>"`` name; the
    ``"notify."`` prefix is added when missing. Empty
    or falsy values are a no-op.

    The service.call wrapper does not document the set
    of exceptions it can raise: a misconfigured target,
    a renamed integration, a transient HA error all
    surface as different exception types. Catch broadly
    so callers can treat notification dispatch as
    fire-and-forget and continue executing regardless
    of outcome. Failures are logged at warning level so
    the user has a breadcrumb when notifications go
    silent.
    """
    if not notification_service or not message:
        return
    svc = str(notification_service)
    if not svc.startswith("notify."):
        svc = "notify." + svc
    parts = svc.split(".", 1)
    try:
        service.call(  # noqa: F821
            parts[0],
            parts[1],
            message=message,
        )
    except Exception as e:  # noqa: BLE001
        log.warning(  # noqa: F821
            "%s notification dispatch via %s failed: %s",
            tag,
            svc,
            e,
        )


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


def _normalize_frozenset(value: object) -> frozenset[str]:
    """Ensure value is a frozenset of strings.

    Same defensive parsing as ``_normalize_list`` but
    returns a frozenset, suitable for ``in``-tests and
    comparison against an all-checks constant.
    """
    return frozenset(_normalize_list(value))


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


def _check_hass_available() -> str | None:
    """Return error if hass global is unavailable."""
    try:
        hass  # noqa: F821, B018
    except NameError:
        return "pyscript hass_is_global not enabled (see docs)"
    return None


def _validate_and_join_patterns(
    raw: str,
    field_name: str,
    errors: list[str],
) -> str:
    """Validate multiline regex patterns and join.

    Splits the raw string on newlines, validates each
    non-empty line individually, appends errors, and
    returns the combined regex joined with |.
    """
    lines = [line.strip() for line in str(raw or "").splitlines()]
    valid: list[str] = []
    for line in lines:
        if not line:
            continue
        err = _validate_regex(line)
        if err:
            errors.append(
                field_name + ': "' + line + '": ' + err,
            )
        else:
            valid.append(line)
    return "|".join(valid)


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
    return {
        "id": entry.device_id,
        "name": name,
        "default_name": device.name or "",
    }


def _get_all_integration_ids(
    hass_obj: object,
) -> list[str]:
    """All distinct integration IDs from entity registry."""
    import homeassistant.helpers.entity_registry as er  # noqa: F821

    ent_reg = er.async_get(hass_obj)
    integrations: set[str] = set()
    for entry in ent_reg.entities.values():
        if entry.platform:
            integrations.add(entry.platform)
    return sorted(integrations)


def _discover_devices(
    hass_obj: object,
    integrations: list[str] | None = None,
) -> "dict[str, DeviceEntry]":
    """Discover devices across all integrations.

    Always scans every integration for accurate
    multi-integration device detection. The optional
    integrations parameter filters which integrations
    populate entity IDs. Omit or pass None for all
    integrations (no filtering).
    """
    all_ids = _get_all_integration_ids(hass_obj)
    populate = set(integrations) if integrations is not None else None
    device_map: dict[str, DeviceEntry] = {}
    for integration_id in all_ids:
        try:
            entities = _get_integration_entities(
                hass_obj,
                integration_id,
            )
        except (KeyError, ValueError):
            # Integration not found or invalid
            continue
        for entity_id in entities:
            try:
                info = _get_device_for_entity(
                    hass_obj,
                    entity_id,
                )
            except (KeyError, ValueError):
                # Entity or device not in registry
                continue
            if not info:
                continue
            dev_id = info["id"]
            if dev_id not in device_map:
                url = "/config/devices/device/" + dev_id
                device_map[dev_id] = DeviceEntry(
                    id=dev_id,
                    url=url,
                    name=info["name"],
                    default_name=info["default_name"],
                )
            ie = device_map[dev_id].integration_entities
            if integration_id not in ie:
                ie[integration_id] = set()
            if populate is None or integration_id in populate:
                ie[integration_id].add(entity_id)
    return device_map


def _read_entity_state(
    entity_id: str,
) -> tuple[Any, Any]:
    """Read entity state + last_reported."""
    entity_state = state.get(entity_id)  # noqa: F821
    last_reported = state.get(  # noqa: F821
        entity_id + ".last_reported",
    )
    return entity_state, last_reported


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


def _validate_notification_service(
    notification_service: str,
) -> list[str]:
    """Validate that a notify service is registered.

    Returns a single-element error list if the input is
    non-empty and HA does not have a matching service in
    the ``notify`` domain. An empty/blank input means
    notifications are disabled and is always valid.

    NameError/AttributeError/TypeError are caught
    defensively so test environments without a real
    ``hass`` object skip the check rather than crash,
    matching the convention in ``_validate_entities``
    (NameError on missing ``state``) and
    ``_get_active_notification_ids``.
    """
    if not notification_service:
        return []
    svc = str(notification_service)
    if not svc.startswith("notify."):
        svc = "notify." + svc
    parts = svc.split(".", 1)
    try:
        if hass.services.has_service(  # noqa: F821
            parts[0],
            parts[1],
        ):
            return []
    except (NameError, AttributeError, TypeError):
        return []
    return [svc + " notify service does not exist"]


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
        instance_id,
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

    # Validate entities and notification service
    errors = _validate_entities(
        [target_switch_entity],
        EntityType.CONTROLLABLE,
    )
    errors += _validate_notification_service(
        notification_service,
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

    # Save state before dispatching notifications.
    # State is load-bearing; notifications are best-
    # effort. A notify failure must never lose state.
    info = _stsc_debug_dict(result, now, sensor_value)
    info["data"] = json.dumps(result.state_dict)
    _save_state(key, now, info)

    # Best-effort notification dispatch.
    _send_notification(
        notification_service,
        result.notification,
        tag,
    )

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


# ── Worker thread executor ──────────────────────────


@pyscript_executor  # type: ignore[name-defined,untyped-decorator]  # noqa: F821
def _run_in_executor(
    modules_dir: str,
    func_name: str,
    *args: object,
) -> object:
    """Import and call a logic module function in a worker thread.

    Compiled to native Python by ``@pyscript_executor``.
    PyScript's modules aren't in ``sys.modules``, so we
    add the modules directory to ``sys.path`` and import
    via standard Python's ``importlib``.

    ``func_name`` is ``"module_name.function_name"``.

    A pyscript reload refreshes the AST-evaluated code
    but leaves the executor thread's ``sys.modules``
    cache alone, so subsequent calls would otherwise
    keep running the pre-reload module object. Force a
    reload when the module is already cached so edits
    on disk take effect on the next tick.  We reload the
    shared ``helpers`` module first (every logic module
    imports from it), then the target logic module,
    because ``importlib.reload`` re-executes the target
    body and its ``from helpers import ...`` statements
    would otherwise pick up stale names from a cached
    pre-deploy ``helpers``.
    """
    import importlib
    import sys

    if "." not in func_name:
        raise ValueError(
            f"func_name must be 'module.function', got {func_name!r}"
        )
    if modules_dir not in sys.path:
        sys.path.insert(0, modules_dir)

    helpers_mod = sys.modules.get("helpers")
    if helpers_mod is not None:
        helpers_origin = getattr(helpers_mod, "__file__", "") or ""
        if helpers_origin.startswith(modules_dir):
            importlib.reload(helpers_mod)

    mod_name, attr_name = func_name.rsplit(".", 1)
    if mod_name in sys.modules:
        mod = importlib.reload(sys.modules[mod_name])
    else:
        mod = importlib.import_module(mod_name)
    func = getattr(mod, attr_name)
    return func(*args)


# ── Device Watchdog ─────────────────────────────────


# Parameter defaults are defined in the blueprint YAML,
# so don't duplicate them here.
@service  # noqa: F821
def device_watchdog(
    instance_id: str,
    include_integrations_raw: object,
    exclude_integrations_raw: object,
    device_exclude_regex_raw: str,
    entity_id_exclude_regex_raw: str,
    monitored_entity_domains_raw: object,
    check_interval_minutes_raw: str,
    dead_device_threshold_minutes_raw: str,
    enabled_checks_raw: object,
    max_device_notifications_raw: str,
    debug_logging_raw: str,
    trigger_platform_raw: str,
) -> None:
    """Evaluate device health across integrations.

    Called by blueprint-generated automation.
    Purely reactive: evaluate -> act -> exit.
    No sleeping, no waiting.
    """
    from device_watchdog import (  # noqa: F821
        CHECK_ALL,
        CHECK_DISABLED_DIAGNOSTICS,
        Config,
        DeviceInfo,
        EntityInfo,
        RegistryEntry,
    )

    start_time = time.monotonic()
    now = datetime.now(tz=UTC)
    auto_name = _automation_name(instance_id)
    tag = "[DW: " + auto_name + "]"

    # Parse all config inputs
    include_integrations = _normalize_list(
        include_integrations_raw,
    )
    exclude_integrations = _normalize_list(
        exclude_integrations_raw,
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
    enabled_checks = _normalize_frozenset(enabled_checks_raw)
    debug_logging = _parse_bool(debug_logging_raw)
    check_interval_minutes = int(
        check_interval_minutes_raw,
    )
    max_notifications = int(
        max_device_notifications_raw,
    )

    # Validate config (accumulate all errors)
    errors: list[str] = []
    if hass_err := _check_hass_available():
        errors.append(hass_err)
    device_exclude_regex = _validate_and_join_patterns(
        device_exclude_regex_raw,
        "device_exclude_regex",
        errors,
    )
    entity_id_exclude_regex = _validate_and_join_patterns(
        entity_id_exclude_regex_raw,
        "entity_id_exclude_regex",
        errors,
    )
    unknown_checks = [c for c in enabled_checks if c not in CHECK_ALL]
    if unknown_checks:
        bad = ", ".join(sorted(unknown_checks))
        valid = ", ".join(sorted(CHECK_ALL))
        errors.append(
            f"enabled_checks: unknown value(s) {bad}. Valid values: {valid}.",
        )
    # Empty selection means "all checks" (blueprint default
    # is also all three; this just mirrors the
    # include_integrations convention of empty == all).
    if not enabled_checks:
        enabled_checks = CHECK_ALL
    _manage_config_error_persistent_notification(
        errors,
        instance_id,
        "Device Watchdog",
    )
    if errors:
        return

    # Interval gating (skip for timed triggers only;
    # manual UI runs always execute)
    assert check_interval_minutes >= 1, (
        f"check_interval_minutes must be >= 1, got {check_interval_minutes}"
    )
    if str(trigger_platform_raw) == "time_pattern":
        if not on_interval(check_interval_minutes, now, instance_id):
            return

    config = Config(
        device_exclude_regex=device_exclude_regex,
        entity_id_exclude_regex=entity_id_exclude_regex,
        monitored_entity_domains=monitored_entity_domains,
        dead_threshold_seconds=dead_threshold_seconds,
        enabled_checks=enabled_checks,
    )

    # Determine target integrations.
    all_integrations = set(
        _get_all_integration_ids(hass),  # noqa: F821
    )
    if include_integrations:
        target_integrations = set(include_integrations)
    else:
        target_integrations = set(all_integrations)
    for ex in exclude_integrations:
        target_integrations.discard(ex)

    # Discover devices (scans all integrations for
    # accurate multi-integration detection; only
    # populates entity IDs for target integrations).
    if CHECK_DISABLED_DIAGNOSTICS in enabled_checks:
        import homeassistant.helpers.entity_registry as er  # noqa: F821

        ent_reg = er.async_get(hass)  # noqa: F821

    device_map = _discover_devices(
        hass,  # noqa: F821
        list(target_integrations),
    )

    # Build DeviceInfo with state + registry data.
    devices = []
    for dev_entry in device_map.values():
        # Build registry entries if diagnostic check
        # is enabled (requires entity registry access)
        registry_entries: list[RegistryEntry] = []
        if CHECK_DISABLED_DIAGNOSTICS in enabled_checks:
            all_reg_entries = er.async_entries_for_device(
                ent_reg,
                dev_entry.id,
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
                if e.platform in target_integrations
            ]

        entity_infos = []
        for eids in dev_entry.integration_entities.values():
            for eid in eids:
                try:
                    ent_state, last_reported = _read_entity_state(eid)
                    if ent_state is not None:
                        entity_infos.append(
                            EntityInfo(
                                entity_id=eid,
                                state=str(ent_state),
                                last_reported=last_reported,
                            ),
                        )
                except (NameError, AttributeError):
                    # Entity state unavailable
                    continue

        devices.append(
            DeviceInfo(
                de=dev_entry,
                entities=entity_infos,
                registry_entries=registry_entries,
            ),
        )

    # Run evaluation in a worker thread.
    modules_dir = os.path.join(
        hass.config.config_dir,  # noqa: F821
        "pyscript",
        "modules",
    )
    ev = _run_in_executor(
        modules_dir,
        "device_watchdog.run_evaluation",
        config,
        devices,
        now,
        len(all_integrations),
        max_notifications,
    )

    active_ids = _get_active_notification_ids(
        hass,  # noqa: F821
    )
    _process_persistent_notifications(
        ev.notifications,
        instance_id,
        active_ids,
    )

    elapsed = time.monotonic() - start_time
    key = _state_key(instance_id)

    _save_state(
        key,
        now,
        {
            "runtime": str(round(elapsed, 2)),
            "integrations": ev.all_integrations_count,
            "devices": len(ev.results),
            "entities": ev.stat_entities,
            "integrations_excluded": (
                ev.all_integrations_count - len(target_integrations)
            ),
            "devices_excluded": ev.stat_devices_excluded,
            "entities_excluded": ev.stat_entities_excluded,
            "device_issues": ev.issues_count,
            "entity_issues": ev.stat_entity_issues,
            "device_stale_issues": ev.stat_stale,
        },
    )

    # Debug logging
    if debug_logging:
        log.warning(  # noqa: F821
            "%s integrations=%d devices=%d"
            " entities=%d device_issues=%d"
            " entity_issues=%d",
            tag,
            ev.all_integrations_count,
            len(ev.results),
            ev.stat_entities,
            ev.issues_count,
            ev.stat_entity_issues,
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
    errors += _validate_notification_service(
        notification_service,
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

    # Save state before dispatching notifications.
    # State is load-bearing; notifications are best-
    # effort. A notify failure must never lose state.
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

    # Best-effort notification dispatch.
    _send_notification(
        notification_service,
        result.notification,
        tag,
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


# ── Entity Defaults Watchdog ──────────────────────


def _get_entity_info(
    hass_obj: object,
    entity_id: str,
) -> "EntityRegistryInfo | None":
    """Return registry fields for an entity.

    Returns None if the entity is not in the registry.
    """
    import homeassistant.helpers.entity_registry as er  # noqa: F821

    ent_reg = er.async_get(hass_obj)
    entry = ent_reg.async_get(entity_id)
    if not entry:
        return None
    return EntityRegistryInfo(
        entity_id=entry.entity_id,
        name=entry.name,
        original_name=entry.original_name,
        has_entity_name=entry.has_entity_name,
        device_id=entry.device_id,
    )


def _compute_expected_entity_id(
    hass_obj: object,
    entity_id: str,
) -> str | None:
    """Compute the expected entity ID from current state.

    Uses HA's async_regenerate_entity_id which accounts
    for current device name and any name override.
    Returns None if the entry is not found or the method
    is unavailable.
    """
    import homeassistant.helpers.entity_registry as er  # noqa: F821

    ent_reg = er.async_get(hass_obj)
    entry = ent_reg.async_get(entity_id)
    if not entry:
        return None
    try:
        return str(
            ent_reg.async_regenerate_entity_id(entry),
        )
    except (AttributeError, TypeError):
        # AttributeError: method missing in older HA
        # TypeError: unexpected argument types
        return None


def _default_friendly_name(obj_id: str) -> str:
    """HA-style default friendly name for an ``obj_id``.

    Mirrors what HA shows for an entity lacking a
    ``friendly_name`` attribute: underscores become spaces
    and the result is title-cased. ``slugify`` round-trips
    this back to ``obj_id`` so a deviceless entity with no
    explicit name is classified as non-drifting by default.
    """
    return obj_id.replace("_", " ").title()


def _discover_deviceless_entities(
    hass_obj: object,
    domains: "frozenset[str]",
    target_integrations: "set[str] | None" = None,
) -> tuple[
    "list[DevicelessEntityInfo]",
    "dict[str, set[str]]",
]:
    """Walk registry and state list for deviceless entities.

    Primary source: entity registry entries where
    ``device_id is None`` and domain is in ``domains``.
    Supplementary source: state-list entities in the same
    domains not present in the registry at all (YAML-
    defined entities without ``unique_id:``) — caught via
    their state's ``friendly_name`` attribute.

    ``target_integrations`` optionally restricts the
    registry-backed slice to entries whose ``platform`` is
    in the set.  State-only entries have no platform and
    are unaffected by this filter.  Pass ``None`` for no
    filtering.

    Returns ``(entities, peers_by_domain)``.  ``peers``
    is the union of registry and state-only object_ids
    per domain and is NOT integration-filtered, so the
    logic module's collision-suffix classifier still sees
    every peer that could justify a ``_N`` suffix.
    """
    import homeassistant.helpers.entity_registry as er  # noqa: F821
    from entity_defaults_watchdog import (  # noqa: F821
        DevicelessEntityInfo,
    )

    entities: list[DevicelessEntityInfo] = []
    peers: dict[str, set[str]] = {}
    # Track every registry entity_id (including device-
    # attached and disabled entries) so the state-list
    # safety net below only picks up entities that truly
    # have no registry entry. Without this, a device-
    # attached registry entry whose device wasn't
    # enumerated (or a disabled one) would get
    # misclassified as ``from_registry=False`` and surface
    # in the deviceless bucket with no integration info.
    seen_eids: set[str] = set()

    ent_reg = er.async_get(hass_obj)
    for entry in ent_reg.entities.values():
        seen_eids.add(entry.entity_id)
        if entry.device_id is not None:
            continue
        if entry.disabled_by is not None:
            continue
        dom, obj = entry.entity_id.split(".", 1)
        if dom not in domains:
            continue
        # Add to peers before the integration filter so
        # collision-suffix detection still sees filtered
        # peers (otherwise a ``foo_2`` whose ``foo`` peer
        # was filtered out would be falsely flagged as
        # stale).
        peers.setdefault(dom, set()).add(obj)
        if (
            target_integrations is not None
            and entry.platform
            and entry.platform not in target_integrations
        ):
            continue
        effective = str(
            entry.name or entry.original_name or _default_friendly_name(obj),
        )
        entities.append(
            DevicelessEntityInfo(
                entity_id=entry.entity_id,
                effective_name=effective,
                platform=entry.platform,
                unique_id=entry.unique_id,
                from_registry=True,
                config_entry_id=entry.config_entry_id,
            ),
        )

    # State-only safety net — YAML entities without
    # unique_id don't appear in the registry but do have
    # state. We compare attributes.friendly_name; when it
    # equals HA's default (title-cased obj_id) slugify
    # will match obj_id exactly and the logic module
    # won't flag it.
    try:
        states = hass_obj.states.async_all()  # type: ignore[attr-defined]
    except AttributeError:
        states = []
    for st in states:
        eid = st.entity_id
        if eid in seen_eids:
            continue
        dom, obj = eid.split(".", 1)
        if dom not in domains:
            continue
        try:
            fn = str(st.attributes.get("friendly_name", "") or "")
        except (AttributeError, TypeError):
            continue
        if not fn:
            fn = _default_friendly_name(obj)
        entities.append(
            DevicelessEntityInfo(
                entity_id=eid,
                effective_name=fn,
                platform=None,
                unique_id=None,
                from_registry=False,
            ),
        )
        peers.setdefault(dom, set()).add(obj)

    return (entities, peers)


# Parameter defaults are defined in the blueprint YAML,
# so don't duplicate them here.
@service  # noqa: F821
def entity_defaults_watchdog(
    instance_id: str,
    trigger_platform_raw: str,
    drift_checks_raw: object,
    include_integrations_raw: object,
    exclude_integrations_raw: object,
    device_exclude_regex_raw: str,
    exclude_entities_raw: object,
    entity_id_exclude_regex_raw: str,
    entity_name_exclude_regex_raw: str,
    check_interval_minutes_raw: str,
    max_device_notifications_raw: str,
    debug_logging_raw: str,
) -> None:
    """Detect entity ID and name drift.

    Called by blueprint-generated automation.
    Purely reactive: evaluate -> act -> exit.
    No sleeping, no waiting.
    """
    from entity_defaults_watchdog import (  # noqa: F821
        CHECK_ALL,
        DEVICELESS_DOMAINS,
        Config,
        DeviceInfo,
        EntityDriftInfo,
    )

    start_time = time.monotonic()
    now = datetime.now(tz=UTC)
    auto_name = _automation_name(instance_id)
    tag = "[EDW: " + auto_name + "]"

    # Parse all config inputs
    drift_checks = _normalize_frozenset(drift_checks_raw)
    include_integrations = _normalize_list(
        include_integrations_raw,
    )
    exclude_integrations = _normalize_list(
        exclude_integrations_raw,
    )
    exclude_entities = _normalize_list(
        exclude_entities_raw,
    )
    debug_logging = _parse_bool(debug_logging_raw)
    check_interval_minutes = int(
        check_interval_minutes_raw,
    )
    max_notifications = int(
        max_device_notifications_raw,
    )

    # Validate config (accumulate all errors)
    errors: list[str] = []
    if hass_err := _check_hass_available():
        errors.append(hass_err)
    unknown_checks = [c for c in drift_checks if c not in CHECK_ALL]
    if unknown_checks:
        bad = ", ".join(sorted(unknown_checks))
        valid = ", ".join(sorted(CHECK_ALL))
        errors.append(
            f"drift_checks: unknown value(s) {bad}. Valid values: {valid}.",
        )
    # Empty selection means "all checks" (mirrors
    # device_watchdog and the include_integrations
    # convention of empty == all).
    if not drift_checks:
        drift_checks = CHECK_ALL
    device_exclude_regex = _validate_and_join_patterns(
        device_exclude_regex_raw,
        "device_exclude_regex",
        errors,
    )
    entity_id_exclude_regex = _validate_and_join_patterns(
        entity_id_exclude_regex_raw,
        "entity_id_exclude_regex",
        errors,
    )
    entity_name_exclude_regex = _validate_and_join_patterns(
        entity_name_exclude_regex_raw,
        "entity_name_exclude_regex",
        errors,
    )
    _manage_config_error_persistent_notification(
        errors,
        instance_id,
        "Entity Defaults Watchdog",
    )
    if errors:
        return

    # Interval gating (skip for timed triggers only;
    # manual UI runs always execute)
    assert check_interval_minutes >= 1, (
        f"check_interval_minutes must be >= 1, got {check_interval_minutes}"
    )
    if str(trigger_platform_raw) == "time_pattern":
        if not on_interval(check_interval_minutes, now, instance_id):
            return

    config = Config(
        drift_checks=drift_checks,
        device_exclude_regex=device_exclude_regex,
        exclude_entity_ids=exclude_entities,
        entity_id_exclude_regex=entity_id_exclude_regex,
        entity_name_exclude_regex=(entity_name_exclude_regex),
    )

    # Determine target integrations.
    all_integrations = set(
        _get_all_integration_ids(hass),  # noqa: F821
    )
    if include_integrations:
        target_integrations = set(include_integrations)
    else:
        target_integrations = set(all_integrations)
    for ex in exclude_integrations:
        target_integrations.discard(ex)

    # Discover devices (scans all integrations for
    # accurate multi-integration detection; only
    # populates entity IDs for target integrations).
    device_map = _discover_devices(
        hass,  # noqa: F821
        list(target_integrations),
    )

    # Compute drift data.
    devices = []
    for dev_entry in device_map.values():
        entity_infos: list[EntityDriftInfo] = []
        for eids in dev_entry.integration_entities.values():
            for eid in eids:
                reg_info = _get_entity_info(
                    hass,  # noqa: F821
                    eid,
                )
                if not reg_info:
                    continue

                has_name_override = reg_info.name is not None

                # Expected entity ID via HA's regeneration
                expected_id = _compute_expected_entity_id(
                    hass,  # noqa: F821
                    eid,
                )

                # Current and expected name
                current_name = str(
                    reg_info.name or reg_info.original_name or "",
                )
                expected_name = None
                if has_name_override:
                    expected_name = str(
                        reg_info.original_name or "",
                    )

                entity_infos.append(
                    EntityDriftInfo(
                        entity_id=eid,
                        has_entity_name=(reg_info.has_entity_name),
                        has_name_override=has_name_override,
                        expected_entity_id=expected_id,
                        current_name=current_name,
                        expected_name=expected_name,
                    ),
                )

        devices.append(
            DeviceInfo(
                de=dev_entry,
                entities=entity_infos,
            ),
        )

    # Discover deviceless entities (automations, helpers,
    # template sensors, etc.) for the new entity-id check.
    # Pass target_integrations so include/exclude filters
    # apply to registry-backed deviceless entries too
    # (state-only entries have no platform and can't be
    # integration-filtered).
    deviceless_entities, peers_by_domain = _discover_deviceless_entities(
        hass,  # noqa: F821
        DEVICELESS_DOMAINS,
        target_integrations,
    )

    # Run evaluation in a worker thread.
    modules_dir = os.path.join(
        hass.config.config_dir,  # noqa: F821
        "pyscript",
        "modules",
    )
    ev = _run_in_executor(
        modules_dir,
        "entity_defaults_watchdog.run_evaluation",
        config,
        devices,
        deviceless_entities,
        peers_by_domain,
        len(all_integrations),
        max_notifications,
    )

    active_ids = _get_active_notification_ids(
        hass,  # noqa: F821
    )
    _process_persistent_notifications(
        ev.notifications,
        instance_id,
        active_ids,
    )

    elapsed = time.monotonic() - start_time
    key = _state_key(instance_id)

    _save_state(
        key,
        now,
        {
            "runtime": str(round(elapsed, 2)),
            "integrations": ev.all_integrations_count,
            "devices": len(ev.results),
            "entities": ev.stat_entities,
            "integrations_excluded": (
                ev.all_integrations_count - len(target_integrations)
            ),
            "devices_excluded": ev.stat_devices_excluded,
            "entities_excluded": ev.stat_entities_excluded,
            "device_issues": ev.issues_count,
            "entity_issues": ev.stat_entity_issues,
            "entity_name_issues": ev.stat_name_issues,
            "entity_id_issues": ev.stat_id_issues,
            "deviceless_entities": ev.stat_deviceless_entities,
            "deviceless_excluded": ev.stat_deviceless_excluded,
            "deviceless_drift": ev.stat_deviceless_drift,
            "deviceless_stale": ev.stat_deviceless_stale,
        },
    )

    # Debug logging
    if debug_logging:
        log.warning(  # noqa: F821
            "%s integrations=%d devices=%d"
            " entities=%d device_issues=%d"
            " entity_issues=%d",
            tag,
            ev.all_integrations_count,
            len(ev.results),
            ev.stat_entities,
            ev.issues_count,
            ev.stat_entity_issues,
        )


# ── Reference Watchdog ────────────────────────────


def _rw_build_truth_set(hass_obj: Any) -> "TruthSet":
    """Assemble a TruthSet from live HA runtime state.

    Pulls entity registry, device registry, hass.states,
    hass.services (for the negative truth set), and
    label registry into a single TruthSet dataclass
    instance that the logic module uses for validation
    and owner lookup. TruthSet is frozen, so accumulate
    into mutable staging collections and construct it
    once at the end.
    """
    import homeassistant.helpers.device_registry as dr  # noqa: F821
    import homeassistant.helpers.entity_registry as er  # noqa: F821
    from reference_watchdog import (  # noqa: F821
        SEED_DOMAINS,
        RegistryEntry,
        TruthSet,
    )

    entity_ids: set[str] = set()
    disabled_entity_ids: set[str] = set()
    device_ids: set[str] = set()
    service_names: set[str] = set()
    label_ids: set[str] = set()
    domains: set[str] = set(SEED_DOMAINS)
    registry: dict[str, RegistryEntry] = {}
    entity_by_unique_id: dict[tuple[str, str], str] = {}
    config_entries_with_entities: set[str] = set()

    ent_reg = er.async_get(hass_obj)
    for entry in ent_reg.entities.values():
        eid = entry.entity_id
        entity_ids.add(eid)
        domains.add(eid.split(".", 1)[0])
        is_disabled = entry.disabled_by is not None
        if is_disabled:
            disabled_entity_ids.add(eid)
        platform = entry.platform or ""
        unique_id = str(entry.unique_id or "")
        reg_entry = RegistryEntry(
            entity_id=eid,
            platform=platform,
            unique_id=unique_id,
            config_entry_id=entry.config_entry_id,
            disabled=is_disabled,
            name=entry.name,
            original_name=entry.original_name,
        )
        registry[eid] = reg_entry
        if platform and unique_id:
            entity_by_unique_id[(platform, unique_id)] = eid
        if entry.config_entry_id:
            config_entries_with_entities.add(entry.config_entry_id)

    dev_reg = dr.async_get(hass_obj)
    for device in dev_reg.devices.values():
        device_ids.add(device.id)

    # Live states augment entity_ids (catches built-ins
    # like sun.sun / weather.home that aren't in the
    # registry) and domains.
    try:
        states = hass_obj.states.async_all()
    except (AttributeError, TypeError):
        states = []
    for s in states:
        eid = s.entity_id
        entity_ids.add(eid)
        domains.add(eid.split(".", 1)[0])

    # Service registry — negative truth set that filters
    # sniff matches that look like entity IDs but are
    # actually registered services.
    try:
        services = hass_obj.services.async_services()
    except (AttributeError, TypeError):
        services = {}
    for dom in services:
        svcs = services[dom]
        for svc_name in svcs:
            service_names.add(str(dom) + "." + str(svc_name))

    # Label registry (v1: stored but not yet validated
    # by any adapter; see docs/reference_watchdog.md
    # follow-ups).
    try:
        import homeassistant.helpers.label_registry as lr  # noqa: F821

        lab_reg = lr.async_get(hass_obj)
        for label in lab_reg.labels.values():
            label_ids.add(label.label_id)
    except (ImportError, AttributeError):
        pass

    return TruthSet(
        entity_ids=frozenset(entity_ids),
        disabled_entity_ids=frozenset(disabled_entity_ids),
        device_ids=frozenset(device_ids),
        service_names=frozenset(service_names),
        label_ids=frozenset(label_ids),
        domains=frozenset(domains),
        registry=registry,
        entity_by_unique_id=entity_by_unique_id,
        config_entries_with_entities=frozenset(config_entries_with_entities),
    )


# Parameter defaults are defined in the blueprint YAML,
# so don't duplicate them here.
@service  # noqa: F821
def reference_watchdog(
    instance_id: str,
    trigger_platform_raw: str,
    scan_sources_raw: object,
    exclude_paths_raw: str,
    exclude_integrations_raw: object,
    exclude_entities_raw: object,
    exclude_entity_regex_raw: str,
    check_disabled_entities_raw: str,
    check_interval_minutes_raw: str,
    max_source_notifications_raw: str,
    debug_logging_raw: str,
) -> None:
    """Scan HA config for broken entity and device references.

    Called by blueprint-generated automation.
    Purely reactive: evaluate -> act -> exit.
    No sleeping, no waiting.
    """
    from reference_watchdog import Config  # noqa: F821

    start_time = time.monotonic()
    now = datetime.now(tz=UTC)
    auto_name = _automation_name(instance_id)
    tag = "[RW: " + auto_name + "]"

    # Parse inputs
    scan_sources = _normalize_list(scan_sources_raw)
    exclude_integrations = _normalize_list(exclude_integrations_raw)
    exclude_entities = _normalize_list(exclude_entities_raw)
    debug_logging = _parse_bool(debug_logging_raw)
    check_disabled_entities = _parse_bool(check_disabled_entities_raw)
    check_interval_minutes = int(check_interval_minutes_raw)
    max_notifications = int(max_source_notifications_raw)

    # Validate config (accumulate all errors)
    errors: list[str] = []
    if hass_err := _check_hass_available():
        errors.append(hass_err)
    exclude_entity_regex = _validate_and_join_patterns(
        exclude_entity_regex_raw,
        "exclude_entity_regex",
        errors,
    )
    exclude_paths_list = [
        p.strip()
        for p in str(exclude_paths_raw or "").splitlines()
        if p.strip()
    ]
    _manage_config_error_persistent_notification(
        errors,
        instance_id,
        "Reference Watchdog",
    )
    if errors:
        return

    # Interval gating (skip for timed triggers only;
    # manual UI runs always execute)
    assert check_interval_minutes >= 1, (
        f"check_interval_minutes must be >= 1, got {check_interval_minutes}"
    )
    if str(trigger_platform_raw) == "time_pattern":
        if not on_interval(check_interval_minutes, now, instance_id):
            return

    config = Config(
        scan_sources=scan_sources,
        exclude_paths=exclude_paths_list,
        exclude_integrations=exclude_integrations,
        exclude_entities=exclude_entities,
        exclude_entity_regex=exclude_entity_regex,
        check_disabled_entities=check_disabled_entities,
    )

    # Build truth set on the main thread (requires HA
    # registries which are only accessible from the
    # event loop).
    truth_set = _rw_build_truth_set(hass)  # noqa: F821

    try:
        config_dir = hass.config.config_dir  # noqa: F821
    except AttributeError:
        return

    # Run the heavy work in a worker thread so the event
    # loop stays responsive. _run_in_executor is compiled
    # to native Python via @pyscript_executor; it imports
    # the logic module via standard Python importlib and
    # calls run_evaluation in a thread pool.
    modules_dir = os.path.join(config_dir, "pyscript", "modules")
    ev = _run_in_executor(
        modules_dir,
        "reference_watchdog.run_evaluation",
        config_dir,
        config,
        scan_sources,
        truth_set,
        exclude_paths_list,
        max_notifications,
    )

    active_ids = _get_active_notification_ids(
        hass,  # noqa: F821
    )
    _process_persistent_notifications(
        ev.notifications,
        instance_id,
        active_ids,
    )

    # ── Stats ──────────────────────────────────────

    elapsed = time.monotonic() - start_time
    key = _state_key(instance_id)

    _save_state(
        key,
        now,
        {
            "runtime": str(round(elapsed, 2)),
            "paths_included": ev.paths_included,
            "paths_excluded": ev.paths_excluded,
            "owners_total": ev.owners_total,
            "owners_with_refs": ev.owners_with_refs,
            "owners_without_refs": ev.owners_without_refs,
            "owners_with_issues": ev.owners_with_issues,
            "total_findings": ev.total_findings,
            "broken_entity_count": ev.broken_entity_count,
            "broken_device_count": ev.broken_device_count,
            "disabled_entity_count": ev.disabled_entity_count,
            "refs_total": ev.refs_total,
            "refs_structural": ev.refs_structural,
            "refs_jinja": ev.refs_jinja,
            "refs_sniff": ev.refs_sniff,
            "refs_service_skipped": ev.refs_service_skipped,
        },
    )

    # Debug logging
    if debug_logging:
        log.warning(  # noqa: F821
            "%s owners=%d with_issues=%d findings=%d refs=%d"
            " (struct=%d jinja=%d sniff=%d svc_skipped=%d)",
            tag,
            ev.owners_total,
            ev.owners_with_issues,
            ev.total_findings,
            ev.refs_total,
            ev.refs_structural,
            ev.refs_jinja,
            ev.refs_sniff,
            ev.refs_service_skipped,
        )
