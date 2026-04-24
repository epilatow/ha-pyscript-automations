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
    md_escape,
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
        # PyScript's @service decorator accepts both sync
        # ``def`` and ``async def`` entrypoints. The stub
        # uses ``Callable[..., Any]`` so mypy accepts either.
        def __call__(
            self,
            fn: Callable[..., Any],
        ) -> Callable[..., Any]: ...
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
    from trigger_entity_controller import (
        NotificationEvent,
        Period,
    )

# -- Shared helpers ----------------------------------


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
        f"{key}.last_run",
        now.isoformat(),
    )
    for name, value in attrs.items():
        state.setattr(  # noqa: F821
            f"{key}.{name}",
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


def _notification_prefix(
    service_label: str,
    instance_id: str,
) -> str:
    """Build the canonical per-instance notification prefix.

    Every persistent notification emitted by this codebase
    must use the form ``<service_slug>_<safe_id>__<context>``.
    This helper returns the prefix including the trailing
    ``__`` separator so callers just append a context string
    (e.g. ``"cap"``, ``"device_<id>"``). Using a single builder
    guarantees the orphan sweep can reliably scope dismissals
    to one instance.
    """
    service_slug = service_label.lower().replace(" ", "_")
    safe_id = instance_id.replace(".", "_")
    return f"{service_slug}_{safe_id}__"


def _sweep_orphan_notifications(
    prefix: str,
    active_ids: "set[str] | None",
    notifications: "list[PersistentNotification]",
    keep_pattern: str | None = None,
) -> None:
    """Append dismissals for orphaned per-instance notifications.

    Finds every existing persistent notification whose ID
    starts with ``prefix`` but isn't in ``notifications``,
    and appends an ``active=False`` entry so
    ``_process_persistent_notifications`` dismisses it.

    Orphans arise when the thing a notification tracks
    (device, node, reference owner) is deleted between
    runs -- the current run no longer emits an ID for it,
    so without this sweep the stale notification would
    linger forever.

    The prefix encodes both the service label and the
    instance ID, so the sweep never touches notifications
    owned by a different service or a different instance
    of the same service.

    ``keep_pattern`` is an opt-out for event-stream
    notifications that should persist until the user
    dismisses them. Any active ID containing the substring
    is left alone. Used for the route-manager's per-attempt
    timeout notifications: each fires once per timeout event
    and must not be auto-cleared by the next tick.

    ``active_ids`` is ``None`` in tests (no live hass
    data); the sweep is a no-op in that case, which is
    fine because there's nothing to orphan.
    """
    if active_ids is None:
        return
    current_ids = {n.notification_id for n in notifications}
    for nid in active_ids:
        if not nid.startswith(prefix) or nid in current_ids:
            continue
        if keep_pattern is not None and keep_pattern in nid:
            continue
        notifications.append(
            PersistentNotification(  # noqa: F821
                active=False,
                notification_id=nid,
                title="",
                message="",
            ),
        )


def _sweep_and_process_notifications(
    hass_obj: Any,
    notifications: "list[PersistentNotification]",
    instance_id: str,
    notif_prefix: str,
    keep_pattern: str | None = None,
) -> None:
    """Publish a batch of notifications with orphan cleanup.

    Wraps the standard three-step pattern every automation
    uses to emit notifications: read the live active-ID set,
    dismiss any prefix-matching notifications this run didn't
    re-emit (sweep), then create / dismiss the current batch.

    ``keep_pattern`` is forwarded to
    ``_sweep_orphan_notifications`` so event-stream IDs
    (e.g. ZRM's per-attempt timeout notifications) can opt
    out of the auto-clear.
    """
    active_ids = _get_active_notification_ids(hass_obj)
    _sweep_orphan_notifications(
        notif_prefix,
        active_ids,
        notifications,
        keep_pattern=keep_pattern,
    )
    _process_persistent_notifications(
        notifications,
        instance_id,
        active_ids,
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
            f"Automation: [{md_escape(auto_name)}]"
            f"(/config/automation/edit/{auto_id})\n"
        )

    for n in notifications:
        if n.active:
            message = n.message
            if link_prefix:
                message = f"{link_prefix}{message}"
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


def _parse_int_input(
    raw: object,
    min_val: int,
    max_val: int,
) -> tuple[int, str | None]:
    """Parse and range-check a blueprint integer input.

    Returns ``(value, error_or_None)``. ``value`` is the
    parsed int on success, or ``min_val`` as a safe
    placeholder on failure so callers can collect multiple
    errors in one pass without special-casing each parse.
    The error string names the parse or range violation;
    callers wrap it with whatever location/field prefix
    their notification path expects.
    """
    try:
        value = int(raw)  # type: ignore[call-overload]
    except (ValueError, TypeError):
        return min_val, f"must be an integer; got {raw!r}"
    if value < min_val or value > max_val:
        return min_val, (
            f"must be between {min_val} and {max_val}; got {value}"
        )
    return value, None


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
        svc = f"notify.{svc}"
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


def _hass_or_none() -> Any:
    """Return the pyscript ``hass`` global or ``None``."""
    try:
        return hass  # noqa: F821
    except NameError:
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
                url = f"/config/devices/device/{dev_id}"
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
        f"{entity_id}.last_reported",
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
                f"{eid} does not exist",
            )
        elif entity_type in _DOMAIN_MAP:
            allowed, msg = _DOMAIN_MAP[entity_type]
            domain = _entity_domain(eid)
            if domain not in allowed:
                errors.append(
                    f"{eid} (domain: {domain}) {msg}",
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
        svc = f"notify.{svc}"
    parts = svc.split(".", 1)
    try:
        if hass.services.has_service(  # noqa: F821
            parts[0],
            parts[1],
        ):
            return []
    except (NameError, AttributeError, TypeError):
        return []
    return [f"{svc} notify service does not exist"]


def _build_config_error_notification(
    errors: list[str],
    instance_id: str,
    service_label: str,
    debug_logging: bool,
    tag: str,
) -> PersistentNotification:
    """Build a config-error persistent notification.

    Returns a PersistentNotification the caller dispatches.
    ``active=True`` when errors is non-empty; otherwise
    ``active=False`` so any prior config_error notif gets
    dismissed. ``errors`` entries become bullet points
    verbatim -- callers pre-format per-domain bullet text
    (e.g. ZRM prefixes with a location/entity ref).

    When ``debug_logging`` is True and ``errors`` is non-
    empty, a warning is also written to the pyscript log
    using ``tag`` as the line prefix.
    """
    prefix = _notification_prefix(service_label, instance_id)
    notif_id = f"{prefix}config_error"
    name = _automation_name(instance_id)

    message = ""
    if errors:
        error_list = "\n- ".join(errors)
        message = (
            f"Configuration errors:\n\n- {error_list}"
            "\n\nPlease fix the automation configuration."
        )
        if debug_logging:
            log.warning(  # noqa: F821
                "%s invalid config: %s",
                tag,
                errors,
            )

    return PersistentNotification(
        active=bool(errors),
        notification_id=notif_id,
        title=f"{name}: Invalid Configuration",
        message=message,
    )


# -- Three-layer blueprint dispatch --------------------

# Hardcoded because ``__file__`` is NameError under
# pyscript's AST evaluator.
_WRAPPER_BASENAME = "ha_pyscript_automations.py"


_BLUEPRINT_SERVICES: dict[
    str,
    "tuple[str, frozenset[str], Callable[..., None]]",
] = {}


# Kwargs pyscript's @service decorator auto-injects into
# any service function whose signature accepts them,
# including ``**kwargs``. Mirrors
# ``TRIGGER_KWARGS`` in pyscript's ``eval.py``. The
# blueprint's ``data:`` block never sets these, so the
# dispatcher pops them from ``kwargs`` before the shape
# check -- otherwise every automation-triggered call
# would surface them as "unexpected parameters".
_PYSCRIPT_TRIGGER_KWARGS: frozenset[str] = frozenset(
    [
        "context",
        "event_type",
        "old_value",
        "payload",
        "payload_obj",
        "qos",
        "retain",
        "topic",
        "trigger_type",
        "trigger_time",
        "var_name",
        "value",
        "webhook_id",
    ],
)


# -- Module reload coordination ----------------------
#
# pyscript.reload re-parses AST-evaluated files in
# pyscript/ but does not refresh entries in sys.modules
# loaded via importlib.import_module (i.e. every file in
# pyscript/modules/). Without help, logic modules and
# helpers.py would keep running their pre-edit bytecode
# forever.
#
# The dispatcher owns reload: _maybe_reload_changed_modules
# is called at the top of every blueprint dispatch and
# reloads any tracked module whose file mtime advanced since
# the last check. A reader-writer lock keeps in-flight
# service calls (readers) from racing reloads (writers) --
# the writer drains existing readers, excludes other
# writers, and holds off new readers while sys.modules is
# being mutated.
#
# The lock is built from asyncio primitives (not threading)
# because HA's event loop is single-threaded and every
# @service entrypoint runs on it as a task. argparse and
# service layers await ``@pyscript_executor`` calls, so a
# read lock can be held across awaits; a threading-based
# ``wait()`` would stall the whole event loop, preventing
# the task that owns the read lock from ever running to
# completion -- a self-deadlock on the first reload that
# catches two concurrent dispatches. asyncio's ``wait()``
# yields control cooperatively so the holder of the read
# lock can still make progress.
#
# Mtimes are pre-seeded to 0.0 at wrapper-load time so the
# first dispatch after every pyscript.reload force-reloads
# every tracked module (sys.modules state may be stale
# relative to edits the user made before triggering the
# reload).

_RELOAD_MODULES: tuple[str, ...] = (
    "helpers",
    "device_watchdog",
    "entity_defaults_watchdog",
    "reference_watchdog",
    "sensor_threshold_switch_controller",
    "trigger_entity_controller",
    "zwave_route_manager",
    "zwave_js_ui_bridge",
)


_MODULE_MTIMES: dict[str, float] = {name: 0.0 for name in _RELOAD_MODULES}


# Single-element lists so mutation is visible across nested
# helper functions without ``global`` declarations (which
# pyscript's AST evaluator handles awkwardly). Protected by
# the condition returned by ``_get_lock_cond()``.
_MODULE_READER_COUNT: list[int] = [0]
_MODULE_WRITERS_WAITING: list[int] = [0]
_MODULE_WRITER_ACTIVE: list[bool] = [False]
_MODULE_LOCK_COND_REF: list[Any] = [None]


def _get_lock_cond() -> Any:
    """Return the shared ``asyncio.Condition``, creating on first call.

    Lazy construction avoids module-load-time loop binding
    under pyscript's AST evaluator. The first dispatch fires
    inside the HA event loop, which is the loop the
    condition then binds to for the life of this wrapper
    module (pyscript.reload reinstantiates the module, which
    resets this ref to None and rebuilds the condition).
    """
    if _MODULE_LOCK_COND_REF[0] is None:
        import asyncio  # noqa: PLC0415 - keep async imports local

        _MODULE_LOCK_COND_REF[0] = asyncio.Condition()
    return _MODULE_LOCK_COND_REF[0]


async def _acquire_read_lock() -> None:
    """Register as reader; wait out any pending or active writer."""
    cond = _get_lock_cond()
    async with cond:
        while _MODULE_WRITERS_WAITING[0] > 0 or _MODULE_WRITER_ACTIVE[0]:
            await cond.wait()
        _MODULE_READER_COUNT[0] += 1


async def _release_read_lock() -> None:
    """Deregister reader; wake waiters if we were last out."""
    cond = _get_lock_cond()
    async with cond:
        _MODULE_READER_COUNT[0] -= 1
        if _MODULE_READER_COUNT[0] == 0:
            cond.notify_all()


async def _acquire_write_lock() -> None:
    """Drain readers and any active writer; hold off new readers.

    Increments ``_MODULE_WRITERS_WAITING`` before blocking so
    new readers see a pending writer and wait in turn --
    prevents a continuous trickle of reader dispatches from
    starving the reload. Only flips ``_MODULE_WRITER_ACTIVE``
    true once the wait condition clears, giving proper
    writer-vs-writer exclusion.
    """
    cond = _get_lock_cond()
    async with cond:
        _MODULE_WRITERS_WAITING[0] += 1
        try:
            while _MODULE_READER_COUNT[0] > 0 or _MODULE_WRITER_ACTIVE[0]:
                await cond.wait()
            _MODULE_WRITER_ACTIVE[0] = True
        finally:
            _MODULE_WRITERS_WAITING[0] -= 1


async def _release_write_lock() -> None:
    """Clear writer-active flag and wake everyone blocked on it."""
    cond = _get_lock_cond()
    async with cond:
        _MODULE_WRITER_ACTIVE[0] = False
        cond.notify_all()


def _module_file_mtime(name: str) -> float | None:
    """Current file mtime of a loaded module, None if unavailable."""
    import sys

    mod = sys.modules.get(name)
    if mod is None:
        return None
    path = getattr(mod, "__file__", "") or ""
    if not path:
        return None
    try:
        return os.stat(path).st_mtime
    except OSError:
        return None


def _ensure_modules_on_sys_path() -> None:
    """Ensure ``/config/pyscript/modules/`` is on sys.path.

    ``@pyscript_executor`` functions are compiled to native
    Python by pyscript and resolve their own ``import``
    statements via standard ``importlib``, which doesn't
    know about pyscript's modules directory unless it's on
    sys.path. Without this, any executor function that
    imports a logic module (``zwave_route_manager``,
    ``device_watchdog``, ``zwave_js_ui_bridge``, etc.)
    fails with ``ModuleNotFoundError``.

    Called from ``_dispatch_blueprint_service`` before any
    executor-path import in a dispatch. Idempotent.
    Silently no-ops if ``hass`` isn't available (running
    under test) -- real dispatch paths hit
    ``_check_hass_available`` further in and error out
    with a clear notification.
    """
    import sys

    hass_obj = _hass_or_none()
    if hass_obj is None:
        return
    modules_dir = os.path.join(
        hass_obj.config.config_dir,
        "pyscript",
        "modules",
    )
    if modules_dir not in sys.path:
        sys.path.insert(0, modules_dir)


async def _maybe_reload_changed_modules() -> None:
    """Reload any tracked module whose file mtime has advanced.

    Walks ``_RELOAD_MODULES`` in order (helpers first so
    dependencies see fresh symbols when their own bodies
    re-execute). The mtime check is outside the write lock;
    if any module looks stale we take the lock and re-check
    inside to avoid redundant reloads when two dispatches
    race here.
    """
    import importlib

    pending: list[str] = []
    for name in _RELOAD_MODULES:
        mtime = _module_file_mtime(name)
        if mtime is None:
            continue
        if mtime > _MODULE_MTIMES.get(name, 0.0):
            pending.append(name)
    if not pending:
        return

    await _acquire_write_lock()
    try:
        import sys

        for name in _RELOAD_MODULES:
            if name not in pending:
                continue
            mtime = _module_file_mtime(name)
            if mtime is None:
                continue
            if mtime <= _MODULE_MTIMES.get(name, 0.0):
                continue
            mod = sys.modules.get(name)
            if mod is None:
                continue
            importlib.reload(mod)
            # Re-read mtime post-reload -- the reload itself
            # does not bump the file's mtime, so this is
            # normally a no-op, but a concurrent edit mid-
            # reload would be captured here.
            _MODULE_MTIMES[name] = _module_file_mtime(name) or mtime
    finally:
        await _release_write_lock()


def _build_blueprint_mismatch_notification(
    service_label: str,
    instance_id: str,
    notif_prefix: str,
    blueprint_basename: str,
    missing: list[str],
    extras: list[str],
) -> PersistentNotification:
    """Build the blueprint-vs-pyscript mismatch notification."""
    notif_id = f"{notif_prefix}blueprint_mismatch"
    if not missing and not extras:
        # Empty input returns an inactive notification
        # so any stale one gets dismissed by the sweep.
        return PersistentNotification(
            active=False,
            notification_id=notif_id,
            title="",
            message="",
        )
    lines: list[str] = [
        (
            f"The pyscript service wrapper for {service_label}"
            " has received invalid parameters. This indicates"
            " that the blueprint source"
            f" ({blueprint_basename}) and the pyscript service"
            f" wrapper ({_WRAPPER_BASENAME}) are out of sync."
        ),
    ]
    if missing:
        lines.append("")
        lines.append(
            "The following required parameters were missing:",
        )
        for name in missing:
            lines.append(f"  - {name}")
    if extras:
        lines.append("")
        lines.append(
            "The following invalid parameters were received:",
        )
        for name in extras:
            lines.append(f"  - {name}")
    lines.append("")
    lines.append(
        "To fix this issue, please ensure the"
        " ha-pyscript-automations repository is installed"
        " correctly and restart Home Assistant.",
    )
    return PersistentNotification(
        active=True,
        notification_id=notif_id,
        title=f"{service_label}: blueprint vs pyscript mismatch",
        message="\n".join(lines),
    )


async def _dispatch_blueprint_service(
    service_label: str,
    kwargs: dict[str, object],
) -> None:
    """Shape-check kwargs, then forward to the argparse layer."""
    blueprint_basename, expected_keys, argparse_fn = _BLUEPRINT_SERVICES[
        service_label
    ]
    # Drop pyscript-injected trigger kwargs; they are
    # never in the blueprint's data block.
    blueprint_kwargs = {
        k: v for k, v in kwargs.items() if k not in _PYSCRIPT_TRIGGER_KWARGS
    }
    instance_id = str(blueprint_kwargs.get("instance_id", "unknown"))
    notif_prefix = _notification_prefix(service_label, instance_id)
    given = frozenset(blueprint_kwargs.keys())
    missing = sorted(expected_keys - given)
    extras = sorted(given - expected_keys)
    notification = _build_blueprint_mismatch_notification(
        service_label=service_label,
        instance_id=instance_id,
        notif_prefix=notif_prefix,
        blueprint_basename=blueprint_basename,
        missing=missing,
        extras=extras,
    )
    _process_persistent_notifications(
        [notification],
        instance_id,
    )
    if missing or extras:
        return
    _ensure_modules_on_sys_path()
    await _maybe_reload_changed_modules()
    await _acquire_read_lock()
    try:
        argparse_fn(**blueprint_kwargs)
    finally:
        await _release_read_lock()


# -- Sensor Threshold Switch Controller --------------


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
_STSC_SERVICE_LABEL = "Sensor Threshold Switch Controller"


def sensor_threshold_switch_controller(
    instance_id: str,
    target_switch_entity: str,
    sensor_value: str,
    switch_state: str,
    trigger_entity: str,
    trigger_threshold: float,
    release_threshold: float,
    sampling_window_seconds: int,
    disable_window_seconds: int,
    auto_off_minutes: int,
    notification_service: str,
    notification_prefix: str,
    notification_suffix: str,
    debug_logging: bool,
) -> None:
    """Evaluate sensor threshold switch controller."""
    from sensor_threshold_switch_controller import (  # noqa: F821
        Action,
        handle_service_call,
    )

    now = datetime.now()
    auto_name = _automation_name(instance_id)
    tag = f"[STSC: {auto_name}]"

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
        trigger_threshold=trigger_threshold,
        release_threshold=release_threshold,
        sampling_window_seconds=sampling_window_seconds,
        disable_window_seconds=disable_window_seconds,
        auto_off_minutes=auto_off_minutes,
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

    # STSC has no persistent findings; a no-op sweep
    # with an empty batch lets the instance-prefix
    # orphan sweep pick up stale entrypoint / argparse
    # notifications still lingering from prior runs.
    _sweep_and_process_notifications(
        hass,  # noqa: F821
        [],
        instance_id,
        _notification_prefix(_STSC_SERVICE_LABEL, instance_id),
    )


def sensor_threshold_switch_controller_blueprint_argparse(
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
    """Parse and validate STSC blueprint inputs."""
    debug_logging = _parse_bool(debug_logging_raw)
    auto_name = _automation_name(instance_id)
    tag = f"[STSC: {auto_name}]"

    errors: list[str] = []
    try:
        trigger_threshold = float(trigger_threshold_raw)
    except (TypeError, ValueError):
        trigger_threshold = 0.0
        errors.append(
            "blueprint input: trigger_threshold: must be a number;"
            f" got {trigger_threshold_raw!r}",
        )
    try:
        release_threshold = float(release_threshold_raw)
    except (TypeError, ValueError):
        release_threshold = 0.0
        errors.append(
            "blueprint input: release_threshold: must be a number;"
            f" got {release_threshold_raw!r}",
        )
    sampling_window_seconds, err = _parse_int_input(
        sampling_window_seconds_raw,
        10,
        3600,
    )
    if err is not None:
        errors.append(f"blueprint input: sampling_window_seconds: {err}")
    disable_window_seconds, err = _parse_int_input(
        disable_window_seconds_raw,
        0,
        60,
    )
    if err is not None:
        errors.append(f"blueprint input: disable_window_seconds: {err}")
    auto_off_minutes, err = _parse_int_input(
        auto_off_minutes_raw,
        0,
        1440,
    )
    if err is not None:
        errors.append(f"blueprint input: auto_off_minutes: {err}")

    errors += _validate_entities(
        [target_switch_entity],
        EntityType.CONTROLLABLE,
    )
    errors += _validate_notification_service(notification_service)

    config_error = _build_config_error_notification(
        errors,
        instance_id,
        _STSC_SERVICE_LABEL,
        debug_logging,
        tag,
    )
    _process_persistent_notifications(
        [config_error],
        instance_id,
    )
    if errors:
        return

    sensor_threshold_switch_controller(
        instance_id=instance_id,
        target_switch_entity=target_switch_entity,
        sensor_value=sensor_value,
        switch_state=switch_state,
        trigger_entity=trigger_entity,
        trigger_threshold=trigger_threshold,
        release_threshold=release_threshold,
        sampling_window_seconds=sampling_window_seconds,
        disable_window_seconds=disable_window_seconds,
        auto_off_minutes=auto_off_minutes,
        notification_service=notification_service,
        notification_prefix=notification_prefix,
        notification_suffix=notification_suffix,
        debug_logging=debug_logging,
    )


_BLUEPRINT_SERVICES[_STSC_SERVICE_LABEL] = (
    "sensor_threshold_switch_controller.yaml",
    frozenset(
        [
            "instance_id",
            "target_switch_entity",
            "sensor_value",
            "switch_state",
            "trigger_entity",
            "trigger_threshold_raw",
            "release_threshold_raw",
            "sampling_window_seconds_raw",
            "disable_window_seconds_raw",
            "auto_off_minutes_raw",
            "notification_service",
            "notification_prefix",
            "notification_suffix",
            "debug_logging_raw",
        ],
    ),
    sensor_threshold_switch_controller_blueprint_argparse,
)


@service  # noqa: F821
async def sensor_threshold_switch_controller_blueprint_entrypoint(
    **kwargs: object,
) -> None:
    """Blueprint-facing entrypoint for STSC."""
    await _dispatch_blueprint_service(_STSC_SERVICE_LABEL, kwargs)


# -- Worker thread executor --------------------------


@pyscript_executor  # type: ignore[name-defined,untyped-decorator]  # noqa: F821
def _run_in_executor(
    func_name: str,
    *args: object,
) -> object:
    """Import and call a logic module function in a worker thread.

    Compiled to native Python by ``@pyscript_executor``.
    ``func_name`` is ``"module_name.function_name"``.
    """
    import importlib

    if "." not in func_name:
        raise ValueError(
            f"func_name must be 'module.function', got {func_name!r}"
        )

    mod_name, attr_name = func_name.rsplit(".", 1)
    mod = importlib.import_module(mod_name)
    func = getattr(mod, attr_name)
    return func(*args)


# -- Device Watchdog ---------------------------------


# Parameter defaults are defined in the blueprint YAML,
# so don't duplicate them here.
_DW_SERVICE_LABEL = "Device Watchdog"


def device_watchdog(
    instance_id: str,
    trigger_platform: str,
    include_integrations: list[str],
    exclude_integrations: list[str],
    device_exclude_regex: str,
    entity_id_exclude_regex: str,
    monitored_entity_domains: list[str],
    check_interval_minutes: int,
    dead_threshold_seconds: int,
    enabled_checks: frozenset[str],
    max_notifications: int,
    debug_logging: bool,
) -> None:
    """Evaluate device health across integrations."""
    from device_watchdog import (  # noqa: F821
        CHECK_DISABLED_DIAGNOSTICS,
        Config,
        DeviceInfo,
        EntityInfo,
        RegistryEntry,
    )

    start_time = time.monotonic()
    now = datetime.now(tz=UTC)
    auto_name = _automation_name(instance_id)
    tag = f"[DW: {auto_name}]"

    # Interval gating (skip for timed triggers only;
    # manual UI runs always execute)
    if trigger_platform == "time_pattern":
        if not on_interval(check_interval_minutes, now, instance_id):
            return

    config = Config(
        device_exclude_regex=device_exclude_regex,
        entity_id_exclude_regex=entity_id_exclude_regex,
        monitored_entity_domains=monitored_entity_domains,
        dead_threshold_seconds=dead_threshold_seconds,
        enabled_checks=enabled_checks,
        notification_prefix=_notification_prefix(
            _DW_SERVICE_LABEL,
            instance_id,
        ),
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
    ev = _run_in_executor(
        "device_watchdog.run_evaluation",
        config,
        devices,
        now,
        len(all_integrations),
        max_notifications,
    )

    _sweep_and_process_notifications(
        hass,  # noqa: F821
        ev.notifications,
        instance_id,
        config.notification_prefix,
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


def device_watchdog_blueprint_argparse(
    instance_id: str,
    trigger_platform_raw: str,
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
) -> None:
    """Parse and validate DW blueprint inputs."""
    from device_watchdog import CHECK_ALL  # noqa: F821

    include_integrations = _normalize_list(include_integrations_raw)
    exclude_integrations = _normalize_list(exclude_integrations_raw)
    monitored_entity_domains = _normalize_list(
        monitored_entity_domains_raw,
    )
    enabled_checks = _normalize_frozenset(enabled_checks_raw)
    debug_logging = _parse_bool(debug_logging_raw)
    trigger_platform = str(trigger_platform_raw)
    tag = f"[DW: {_automation_name(instance_id)}]"

    errors: list[str] = []
    check_interval_minutes, err = _parse_int_input(
        check_interval_minutes_raw,
        1,
        10080,
    )
    if err is not None:
        errors.append(f"blueprint input: check_interval_minutes: {err}")
    dead_device_threshold_minutes, err = _parse_int_input(
        dead_device_threshold_minutes_raw,
        1,
        10080,
    )
    if err is not None:
        errors.append(
            f"blueprint input: dead_device_threshold_minutes: {err}",
        )
    dead_threshold_seconds = dead_device_threshold_minutes * 60
    max_notifications, err = _parse_int_input(
        max_device_notifications_raw,
        0,
        1000,
    )
    if err is not None:
        errors.append(f"blueprint input: max_device_notifications: {err}")
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

    config_error = _build_config_error_notification(
        errors,
        instance_id,
        _DW_SERVICE_LABEL,
        debug_logging,
        tag,
    )
    _process_persistent_notifications(
        [config_error],
        instance_id,
    )
    if errors:
        return

    device_watchdog(
        instance_id=instance_id,
        trigger_platform=trigger_platform,
        include_integrations=include_integrations,
        exclude_integrations=exclude_integrations,
        device_exclude_regex=device_exclude_regex,
        entity_id_exclude_regex=entity_id_exclude_regex,
        monitored_entity_domains=monitored_entity_domains,
        check_interval_minutes=check_interval_minutes,
        dead_threshold_seconds=dead_threshold_seconds,
        enabled_checks=enabled_checks,
        max_notifications=max_notifications,
        debug_logging=debug_logging,
    )


_BLUEPRINT_SERVICES[_DW_SERVICE_LABEL] = (
    "device_watchdog.yaml",
    frozenset(
        [
            "instance_id",
            "trigger_platform_raw",
            "include_integrations_raw",
            "exclude_integrations_raw",
            "device_exclude_regex_raw",
            "entity_id_exclude_regex_raw",
            "monitored_entity_domains_raw",
            "check_interval_minutes_raw",
            "dead_device_threshold_minutes_raw",
            "enabled_checks_raw",
            "max_device_notifications_raw",
            "debug_logging_raw",
        ],
    ),
    device_watchdog_blueprint_argparse,
)


@service  # noqa: F821
async def device_watchdog_blueprint_entrypoint(**kwargs: object) -> None:
    """Blueprint-facing entrypoint for Device Watchdog."""
    await _dispatch_blueprint_service(_DW_SERVICE_LABEL, kwargs)


# -- Trigger Entity Controller --------------------


# Parameter defaults are defined in the blueprint YAML,
# so don't duplicate them here.
_TEC_SERVICE_LABEL = "Trigger Entity Controller"


def trigger_entity_controller(
    instance_id: str,
    controlled_entities: list[str],
    trigger_entity_id: str,
    trigger_to_state: str,
    auto_off_minutes: int,
    auto_off_disabling_entities: list[str],
    trigger_entities: list[str],
    trigger_period: "Period",
    trigger_forces_on: bool,
    trigger_disabling_entities: list[str],
    trigger_disabling_period: "Period",
    notification_service: str,
    notification_prefix: str,
    notification_suffix: str,
    notification_events: "list[NotificationEvent]",
    debug_logging: bool,
) -> None:
    """Control entities with trigger-based activation."""
    from trigger_entity_controller import (  # noqa: F821
        ActionType,
        Config,
        Inputs,
        determine_event_type,
        evaluate,
    )

    now = datetime.now()
    auto_name = _automation_name(instance_id)
    tag = f"[TEC: {auto_name}]"

    # Determine event type
    all_disabling = trigger_disabling_entities + auto_off_disabling_entities
    event_type = determine_event_type(
        trigger_entity_id,
        trigger_to_state,
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
    config = Config(
        controlled_entities=controlled_entities,
        auto_off_minutes=auto_off_minutes,
        auto_off_disabling_entities=auto_off_disabling_entities,
        trigger_entities=trigger_entities,
        trigger_period=trigger_period,
        trigger_forces_on=trigger_forces_on,
        trigger_disabling_entities=trigger_disabling_entities,
        trigger_disabling_period=trigger_disabling_period,
        notification_prefix=notification_prefix,
        notification_suffix=notification_suffix,
        notification_events=notification_events,
    )
    inputs = Inputs(
        current_time=now,
        event_type=event_type,
        changed_entity=trigger_entity_id,
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

    # TEC has no persistent findings; a no-op sweep
    # with an empty batch lets the instance-prefix
    # orphan sweep pick up stale entrypoint / argparse
    # notifications still lingering from prior runs.
    _sweep_and_process_notifications(
        hass,  # noqa: F821
        [],
        instance_id,
        _notification_prefix(_TEC_SERVICE_LABEL, instance_id),
    )


def trigger_entity_controller_blueprint_argparse(
    instance_id: str,
    controlled_entities_raw: object,
    trigger_entity_id: str,
    trigger_to_state: str,
    auto_off_minutes_raw: str,
    auto_off_disabling_entities_raw: object,
    trigger_entities_raw: object,
    trigger_period_raw: str,
    trigger_forces_on_raw: str,
    trigger_disabling_entities_raw: object,
    trigger_disabling_period_raw: str,
    notification_service: str,
    notification_prefix_raw: str,
    notification_suffix_raw: str,
    notification_events_raw: object,
    debug_logging_raw: str,
) -> None:
    """Parse and validate TEC blueprint inputs."""
    from trigger_entity_controller import (  # noqa: F821
        parse_notification_events,
        parse_period,
    )

    debug_logging = _parse_bool(debug_logging_raw)
    auto_name = _automation_name(instance_id)
    tag = f"[TEC: {auto_name}]"

    # Parse inputs to native types
    controlled_entities = _normalize_list(
        controlled_entities_raw,
    )
    trigger_entities = _normalize_list(trigger_entities_raw)
    trigger_disabling_entities = _normalize_list(
        trigger_disabling_entities_raw,
    )
    auto_off_disabling_entities = _normalize_list(
        auto_off_disabling_entities_raw,
    )
    notification_events = parse_notification_events(
        _normalize_list(notification_events_raw),
    )
    trigger_period = parse_period(str(trigger_period_raw))
    trigger_forces_on = _parse_bool(trigger_forces_on_raw)
    trigger_disabling_period = parse_period(
        str(trigger_disabling_period_raw),
    )
    notification_prefix = str(notification_prefix_raw or "")
    notification_suffix = str(notification_suffix_raw or "")

    # Parse + range-check int inputs. Blueprint selectors
    # enforce these in the UI but direct service calls
    # can still pass garbage; parse errors surface
    # through the standard config-error notification.
    errors: list[str] = []
    auto_off_minutes, err = _parse_int_input(
        auto_off_minutes_raw,
        0,
        60,
    )
    if err is not None:
        errors.append(f"blueprint input: auto_off_minutes: {err}")

    # Validate entities exist and have expected domains
    errors += _validate_entities(
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
            f"{eid} is in both controlled and trigger entities",
        )
    for eid in ctrl_set & dis_set:
        errors.append(
            f"{eid} is in both controlled and disabling entities",
        )
    for eid in trig_set & dis_set:
        errors.append(
            f"{eid} is in both trigger and disabling entities",
        )
    errors += _validate_notification_service(notification_service)

    config_error = _build_config_error_notification(
        errors,
        instance_id,
        _TEC_SERVICE_LABEL,
        debug_logging,
        tag,
    )
    _process_persistent_notifications(
        [config_error],
        instance_id,
    )
    if errors:
        return

    # Dispatch to the service layer with native types.
    trigger_entity_controller(
        instance_id=instance_id,
        controlled_entities=controlled_entities,
        trigger_entity_id=str(trigger_entity_id or ""),
        trigger_to_state=str(trigger_to_state or ""),
        auto_off_minutes=auto_off_minutes,
        auto_off_disabling_entities=auto_off_disabling_entities,
        trigger_entities=trigger_entities,
        trigger_period=trigger_period,
        trigger_forces_on=trigger_forces_on,
        trigger_disabling_entities=trigger_disabling_entities,
        trigger_disabling_period=trigger_disabling_period,
        notification_service=notification_service,
        notification_prefix=notification_prefix,
        notification_suffix=notification_suffix,
        notification_events=notification_events,
        debug_logging=debug_logging,
    )


_BLUEPRINT_SERVICES[_TEC_SERVICE_LABEL] = (
    "trigger_entity_controller.yaml",
    frozenset(
        [
            "instance_id",
            "controlled_entities_raw",
            "trigger_entity_id",
            "trigger_to_state",
            "auto_off_minutes_raw",
            "auto_off_disabling_entities_raw",
            "trigger_entities_raw",
            "trigger_period_raw",
            "trigger_forces_on_raw",
            "trigger_disabling_entities_raw",
            "trigger_disabling_period_raw",
            "notification_service",
            "notification_prefix_raw",
            "notification_suffix_raw",
            "notification_events_raw",
            "debug_logging_raw",
        ],
    ),
    trigger_entity_controller_blueprint_argparse,
)


@service  # noqa: F821
async def trigger_entity_controller_blueprint_entrypoint(
    **kwargs: object,
) -> None:
    """Blueprint-facing entrypoint for TEC."""
    await _dispatch_blueprint_service(_TEC_SERVICE_LABEL, kwargs)


# -- Entity Defaults Watchdog ----------------------


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
    defined entities without ``unique_id:``) -- caught via
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

    # State-only safety net -- YAML entities without
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
_EDW_SERVICE_LABEL = "Entity Defaults Watchdog"


def entity_defaults_watchdog(
    instance_id: str,
    trigger_platform: str,
    drift_checks: frozenset[str],
    include_integrations: list[str],
    exclude_integrations: list[str],
    device_exclude_regex: str,
    exclude_entities: list[str],
    entity_id_exclude_regex: str,
    entity_name_exclude_regex: str,
    check_interval_minutes: int,
    max_notifications: int,
    debug_logging: bool,
) -> None:
    """Detect entity ID and name drift."""
    from entity_defaults_watchdog import (  # noqa: F821
        DEVICELESS_DOMAINS,
        Config,
        DeviceInfo,
        EntityDriftInfo,
    )

    start_time = time.monotonic()
    now = datetime.now(tz=UTC)
    auto_name = _automation_name(instance_id)
    tag = f"[EDW: {auto_name}]"

    # Interval gating (skip for timed triggers only;
    # manual UI runs always execute)
    if trigger_platform == "time_pattern":
        if not on_interval(check_interval_minutes, now, instance_id):
            return

    config = Config(
        drift_checks=drift_checks,
        device_exclude_regex=device_exclude_regex,
        exclude_entity_ids=exclude_entities,
        entity_id_exclude_regex=entity_id_exclude_regex,
        entity_name_exclude_regex=entity_name_exclude_regex,
        notification_prefix=_notification_prefix(
            _EDW_SERVICE_LABEL,
            instance_id,
        ),
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
    ev = _run_in_executor(
        "entity_defaults_watchdog.run_evaluation",
        config,
        devices,
        deviceless_entities,
        peers_by_domain,
        len(all_integrations),
        max_notifications,
    )

    _sweep_and_process_notifications(
        hass,  # noqa: F821
        ev.notifications,
        instance_id,
        config.notification_prefix,
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


def entity_defaults_watchdog_blueprint_argparse(
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
    """Parse and validate EDW blueprint inputs."""
    from entity_defaults_watchdog import CHECK_ALL  # noqa: F821

    drift_checks = _normalize_frozenset(drift_checks_raw)
    include_integrations = _normalize_list(include_integrations_raw)
    exclude_integrations = _normalize_list(exclude_integrations_raw)
    exclude_entities = _normalize_list(exclude_entities_raw)
    debug_logging = _parse_bool(debug_logging_raw)
    trigger_platform = str(trigger_platform_raw)
    tag = f"[EDW: {_automation_name(instance_id)}]"

    errors: list[str] = []
    check_interval_minutes, err = _parse_int_input(
        check_interval_minutes_raw,
        1,
        10080,
    )
    if err is not None:
        errors.append(f"blueprint input: check_interval_minutes: {err}")
    max_notifications, err = _parse_int_input(
        max_device_notifications_raw,
        0,
        1000,
    )
    if err is not None:
        errors.append(f"blueprint input: max_device_notifications: {err}")
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

    config_error = _build_config_error_notification(
        errors,
        instance_id,
        _EDW_SERVICE_LABEL,
        debug_logging,
        tag,
    )
    _process_persistent_notifications(
        [config_error],
        instance_id,
    )
    if errors:
        return

    entity_defaults_watchdog(
        instance_id=instance_id,
        trigger_platform=trigger_platform,
        drift_checks=drift_checks,
        include_integrations=include_integrations,
        exclude_integrations=exclude_integrations,
        device_exclude_regex=device_exclude_regex,
        exclude_entities=exclude_entities,
        entity_id_exclude_regex=entity_id_exclude_regex,
        entity_name_exclude_regex=entity_name_exclude_regex,
        check_interval_minutes=check_interval_minutes,
        max_notifications=max_notifications,
        debug_logging=debug_logging,
    )


_BLUEPRINT_SERVICES[_EDW_SERVICE_LABEL] = (
    "entity_defaults_watchdog.yaml",
    frozenset(
        [
            "instance_id",
            "trigger_platform_raw",
            "drift_checks_raw",
            "include_integrations_raw",
            "exclude_integrations_raw",
            "device_exclude_regex_raw",
            "exclude_entities_raw",
            "entity_id_exclude_regex_raw",
            "entity_name_exclude_regex_raw",
            "check_interval_minutes_raw",
            "max_device_notifications_raw",
            "debug_logging_raw",
        ],
    ),
    entity_defaults_watchdog_blueprint_argparse,
)


@service  # noqa: F821
async def entity_defaults_watchdog_blueprint_entrypoint(
    **kwargs: object,
) -> None:
    """Blueprint-facing entrypoint for Entity Defaults Watchdog."""
    await _dispatch_blueprint_service(_EDW_SERVICE_LABEL, kwargs)


# -- Reference Watchdog ----------------------------


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

    # Service registry -- negative truth set that filters
    # sniff matches that look like entity IDs but are
    # actually registered services.
    try:
        services = hass_obj.services.async_services()
    except (AttributeError, TypeError):
        services = {}
    for dom in services:
        svcs = services[dom]
        for svc_name in svcs:
            service_names.add(f"{dom}.{svc_name}")

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
_RW_SERVICE_LABEL = "Reference Watchdog"


def reference_watchdog(
    instance_id: str,
    trigger_platform: str,
    exclude_paths: list[str],
    exclude_integrations: list[str],
    exclude_entities: list[str],
    exclude_entity_regex: str,
    check_disabled_entities: bool,
    check_interval_minutes: int,
    max_notifications: int,
    debug_logging: bool,
) -> None:
    """Scan HA config for broken entity and device references."""
    from reference_watchdog import Config  # noqa: F821

    start_time = time.monotonic()
    now = datetime.now(tz=UTC)
    auto_name = _automation_name(instance_id)
    tag = f"[RW: {auto_name}]"

    # Interval gating (skip for timed triggers only;
    # manual UI runs always execute)
    if trigger_platform == "time_pattern":
        if not on_interval(check_interval_minutes, now, instance_id):
            return

    config = Config(
        exclude_paths=exclude_paths,
        exclude_integrations=exclude_integrations,
        exclude_entities=exclude_entities,
        exclude_entity_regex=exclude_entity_regex,
        check_disabled_entities=check_disabled_entities,
        notification_prefix=_notification_prefix(
            _RW_SERVICE_LABEL,
            instance_id,
        ),
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
    # to native Python via @pyscript_executor and dispatches
    # the call into a thread pool.
    ev = _run_in_executor(
        "reference_watchdog.run_evaluation",
        config_dir,
        config,
        truth_set,
        exclude_paths,
        max_notifications,
    )

    _sweep_and_process_notifications(
        hass,  # noqa: F821
        ev.notifications,
        instance_id,
        config.notification_prefix,
    )

    # -- Stats --------------------------------------

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
            "source_orphan_count": ev.source_orphan_count,
            "source_orphan_candidates": ev.source_orphan_candidates,
        },
    )

    # Debug logging
    if debug_logging:
        log.warning(  # noqa: F821
            "%s owners=%d with_issues=%d findings=%d refs=%d"
            " (struct=%d jinja=%d sniff=%d svc_skipped=%d)"
            " orphans=%d/%d",
            tag,
            ev.owners_total,
            ev.owners_with_issues,
            ev.total_findings,
            ev.refs_total,
            ev.refs_structural,
            ev.refs_jinja,
            ev.refs_sniff,
            ev.refs_service_skipped,
            ev.source_orphan_count,
            ev.source_orphan_candidates,
        )


def reference_watchdog_blueprint_argparse(
    instance_id: str,
    trigger_platform_raw: str,
    exclude_paths_raw: str,
    exclude_integrations_raw: object,
    exclude_entities_raw: object,
    exclude_entity_regex_raw: str,
    check_disabled_entities_raw: str,
    check_interval_minutes_raw: str,
    max_source_notifications_raw: str,
    debug_logging_raw: str,
) -> None:
    """Parse and validate RW blueprint inputs."""
    exclude_integrations = _normalize_list(exclude_integrations_raw)
    exclude_entities = _normalize_list(exclude_entities_raw)
    debug_logging = _parse_bool(debug_logging_raw)
    check_disabled_entities = _parse_bool(check_disabled_entities_raw)
    trigger_platform = str(trigger_platform_raw)
    tag = f"[RW: {_automation_name(instance_id)}]"
    exclude_paths = [
        p.strip()
        for p in str(exclude_paths_raw or "").splitlines()
        if p.strip()
    ]

    errors: list[str] = []
    check_interval_minutes, err = _parse_int_input(
        check_interval_minutes_raw,
        1,
        10080,
    )
    if err is not None:
        errors.append(f"blueprint input: check_interval_minutes: {err}")
    max_notifications, err = _parse_int_input(
        max_source_notifications_raw,
        0,
        1000,
    )
    if err is not None:
        errors.append(f"blueprint input: max_source_notifications: {err}")
    if hass_err := _check_hass_available():
        errors.append(hass_err)
    exclude_entity_regex = _validate_and_join_patterns(
        exclude_entity_regex_raw,
        "exclude_entity_regex",
        errors,
    )

    config_error = _build_config_error_notification(
        errors,
        instance_id,
        _RW_SERVICE_LABEL,
        debug_logging,
        tag,
    )
    _process_persistent_notifications(
        [config_error],
        instance_id,
    )
    if errors:
        return

    reference_watchdog(
        instance_id=instance_id,
        trigger_platform=trigger_platform,
        exclude_paths=exclude_paths,
        exclude_integrations=exclude_integrations,
        exclude_entities=exclude_entities,
        exclude_entity_regex=exclude_entity_regex,
        check_disabled_entities=check_disabled_entities,
        check_interval_minutes=check_interval_minutes,
        max_notifications=max_notifications,
        debug_logging=debug_logging,
    )


_BLUEPRINT_SERVICES[_RW_SERVICE_LABEL] = (
    "reference_watchdog.yaml",
    frozenset(
        [
            "instance_id",
            "trigger_platform_raw",
            "exclude_paths_raw",
            "exclude_integrations_raw",
            "exclude_entities_raw",
            "exclude_entity_regex_raw",
            "check_disabled_entities_raw",
            "check_interval_minutes_raw",
            "max_source_notifications_raw",
            "debug_logging_raw",
        ],
    ),
    reference_watchdog_blueprint_argparse,
)


@service  # noqa: F821
async def reference_watchdog_blueprint_entrypoint(**kwargs: object) -> None:
    """Blueprint-facing entrypoint for Reference Watchdog."""
    await _dispatch_blueprint_service(_RW_SERVICE_LABEL, kwargs)


# -- Z-Wave Route Manager --------------------------


@pyscript_executor  # type: ignore[name-defined,untyped-decorator]  # noqa: F821
def _zrm_bridge_get_nodes(
    host: str,
    port: int,
    token: str,
) -> dict[str, Any]:
    """Connect to zwave-js-ui and fetch nodes with fresh routes.

    Returns a dict with keys:

    - ``"ok"``: bool -- overall success
    - ``"api_result"``: ApiResult from the ``getNodes`` call
      (``None`` if we never got that far)
    - ``"nodes"``: list[NodeInfo] (empty if the call failed)
    - ``"error"``: str -- connect/transient-exception message or ""

    The api_echo + success fields on ``api_result`` are checked
    by the caller, same as for write APIs -- we don't run a
    separate probe call (which would be a redundant getNodes
    round-trip).

    Runs in a worker thread via @pyscript_executor so the
    asyncio socket.io client can run natively without
    interfering with HA's event loop.
    """
    import asyncio

    import zwave_js_ui_bridge as bridge

    result: dict[str, Any] = {
        "ok": False,
        "api_result": None,
        "nodes": [],
        "error": "",
    }

    async def _run() -> None:
        client = bridge.ZwaveJsUiClient(
            host=host,
            port=port,
            token=token or None,
        )
        try:
            try:
                await client.connect()
            except (ConnectionError, TimeoutError, OSError) as e:
                # Addon likely booting or misconfigured. Let
                # the caller carry reconcile_pending forward.
                result["error"] = str(e) or type(e).__name__
                return
            # Use the fresh-routes variant so route-state diffs
            # ride on per-node ``getPriorityRoute`` /
            # ``getPrioritySUCReturnRoute`` rather than the bulk
            # snapshot's cached fields, which can flap to None
            # while the controller still holds the route.
            bulk_r, nodes = await client.get_nodes_with_fresh_routes()
            result["api_result"] = bulk_r
            result["nodes"] = nodes
            # "ok" means the call landed; the caller still
            # inspects api_result for api_echo / success to
            # detect an unavailable API.
            result["ok"] = True
        finally:
            await client.disconnect()

    asyncio.run(_run())
    return result


# Per-action timeouts (seconds). Sleepy battery nodes never
# ACK route commands until they wake, so awaiting a response
# would either block the reconcile or time out; use a short
# fire-and-forget window and let the next reconcile confirm.
# Line-powered and FLiRS nodes respond quickly when healthy;
# a timeout there indicates a real apply failure worth
# surfacing immediately.
_ZRM_SLEEPY_APPLY_TIMEOUT = 1.0
_ZRM_AWAKE_APPLY_TIMEOUT = 15.0


@pyscript_executor  # type: ignore[name-defined,untyped-decorator]  # noqa: F821
def _zrm_bridge_apply_actions(
    host: str,
    port: int,
    token: str,
    actions: list[Any],
    sleepy_node_ids: frozenset[int],
) -> list[tuple[Any, Any]]:
    """Apply RouteActions via the bridge.

    Returns a list of (action, ApiResult) tuples in the
    same order as ``actions``. Each tuple carries the
    original RouteAction plus the bridge's response so the
    main thread can distinguish success / api-unavailable /
    apply-failure / fire-and-forget-queued.

    Timeouts per action depend on whether the target is a
    sleepy battery node. Sleepy -> 1s, awake -> 15s. On timeout
    for a sleepy node we return success with a "queued"
    message; on timeout for an awake node we return failure
    (the user sees an apply-error notification for that node).
    """
    import asyncio

    import zwave_js_ui_bridge as bridge
    import zwave_route_manager as zrm

    results: list[tuple[Any, Any]] = []

    async def _dispatch(client: Any, action: Any) -> Any:
        kind = action.kind
        if kind == zrm.RouteActionKind.SET_APPLICATION_ROUTE:
            return await client.set_application_route(
                action.node_id,
                action.repeaters,
                action.route_speed,
            )
        if kind == zrm.RouteActionKind.CLEAR_APPLICATION_ROUTE:
            return await client.remove_application_route(action.node_id)
        if kind == zrm.RouteActionKind.SET_PRIORITY_SUC_RETURN_ROUTE:
            return await client.assign_priority_suc_return_route(
                action.node_id,
                action.repeaters,
                action.route_speed,
            )
        if kind == zrm.RouteActionKind.CLEAR_PRIORITY_SUC_RETURN_ROUTES:
            return await client.delete_suc_return_routes(action.node_id)
        return bridge.ApiResult(
            success=False,
            message=f"unknown RouteActionKind: {kind}",
            api_echo=None,
            result=None,
        )

    async def _one(client: Any, action: Any) -> Any:
        is_sleepy = action.node_id in sleepy_node_ids
        timeout = (
            _ZRM_SLEEPY_APPLY_TIMEOUT if is_sleepy else _ZRM_AWAKE_APPLY_TIMEOUT
        )
        try:
            return await asyncio.wait_for(
                _dispatch(client, action),
                timeout=timeout,
            )
        except TimeoutError:
            if is_sleepy:
                # Expected: command was queued in zwave-js's
                # transmission queue and will deliver on the
                # node's next wake. Next reconcile confirms via
                # the cached route state.
                return bridge.ApiResult(
                    success=True,
                    message="queued (sleepy node; will apply on wake)",
                    api_echo=None,
                    result=None,
                )
            # Awake node that didn't respond in 15s -- real
            # apply failure. Surface as notification so the
            # user investigates now rather than waiting for the
            # 24h pending-timeout.
            return bridge.ApiResult(
                success=False,
                message=(
                    f"timeout awaiting ACK after "
                    f"{_ZRM_AWAKE_APPLY_TIMEOUT}s on non-sleepy node"
                ),
                api_echo=None,
                result=None,
            )

    async def _run() -> None:
        # Per-call socket.io timeout acts as a safety net in
        # case the asyncio.wait_for cancellation doesn't
        # unblock promptly. Pick a value comfortably above the
        # awake-node timeout.
        client = bridge.ZwaveJsUiClient(
            host=host,
            port=port,
            token=token or None,
            timeout_seconds=30.0,
        )
        await client.connect()
        try:
            # Fire all actions concurrently. Total elapsed time
            # is bounded by the slowest awake action (~15s) or
            # the sleepy-timeout (~1s), whichever applies.
            coros = [_one(client, a) for a in actions]
            responses = await asyncio.gather(*coros)
            for action, r in zip(actions, responses, strict=True):
                results.append((action, r))
        finally:
            await client.disconnect()

    asyncio.run(_run())
    return results


def _zrm_resolve_path(config_file_path: str) -> str:
    """Resolve a user-supplied path against /config if relative."""
    if os.path.isabs(config_file_path):
        return config_file_path
    config_dir = hass.config.config_dir  # noqa: F821
    return os.path.join(config_dir, config_file_path)


def _zrm_read_config_mtime(path: str) -> float:
    """Return mtime or 0.0 if file missing."""
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _zrm_read_config_text(path: str) -> tuple[str, str | None]:
    """Read the YAML config file. Returns (text, error_or_None)."""
    import io  # noqa: PLC0415 - pyscript bans bare open()

    try:
        with io.open(path, encoding="utf-8") as f:  # noqa: UP020
            return f.read(), None
    except FileNotFoundError:
        return "", f"config file not found: {path}"
    except OSError as e:
        return "", f"could not read config file: {e}"


def _zrm_build_entity_to_resolution(
    hass_obj: Any,
    nodes: list[Any],
) -> tuple[dict[str, Any], Any]:
    """Build entity_id -> DeviceResolution from HA registries + nodes.

    Returns (map, controller_resolution_or_None).
    controller_resolution is None if we couldn't find node_id=1
    in ``nodes``, which should never happen on a healthy setup.
    """
    import homeassistant.helpers.device_registry as dr  # noqa: F821
    import homeassistant.helpers.entity_registry as er  # noqa: F821
    import zwave_route_manager as zrm

    dev_reg = dr.async_get(hass_obj)
    ent_reg = er.async_get(hass_obj)

    nodes_by_id: dict[int, Any] = {}
    for n in nodes:
        nodes_by_id[n.node_id] = n

    # Map HA device_id -> zwave node_id via device identifiers.
    # zwave_js stores (domain="zwave_js", id="<homeId>-<nodeId>-...")
    # in device.identifiers.
    device_to_node: dict[str, int] = {}
    for dev in dev_reg.devices.values():
        for ident in dev.identifiers:
            if len(ident) < 2:
                continue
            domain = ident[0]
            if domain != "zwave_js":
                continue
            raw = str(ident[1])
            # Shape: "<homeId>-<nodeId>" or
            # "<homeId>-<nodeId>-<endpoint>-<...>"
            parts = raw.split("-")
            if len(parts) < 2:
                continue
            try:
                node_id = int(parts[1])
            except ValueError:
                continue
            device_to_node[dev.id] = node_id
            break

    entity_to_resolution: dict[str, Any] = {}
    for entry in ent_reg.entities.values():
        if entry.platform != "zwave_js":
            continue
        if entry.disabled_by is not None:
            continue
        maybe_node_id = device_to_node.get(entry.device_id or "")
        if maybe_node_id is None:
            continue
        node_id = maybe_node_id
        ni = nodes_by_id.get(node_id)
        if ni is None:
            continue
        entity_to_resolution[entry.entity_id] = zrm.DeviceResolution(
            entity_id=entry.entity_id,
            device_id=entry.device_id or "",
            node_id=node_id,
            is_routing=ni.is_routing,
            is_listening=ni.is_listening,
            is_frequent_listening=ni.is_frequent_listening,
            failed=ni.failed,
            is_long_range=ni.is_long_range,
            max_data_rate_bps=ni.max_data_rate_bps,
        )

    controller_node = nodes_by_id.get(1)
    controller_resolution = None
    if controller_node is not None:
        controller_resolution = zrm.DeviceResolution(
            entity_id="",
            device_id="",
            node_id=1,
            is_routing=controller_node.is_routing,
            is_listening=controller_node.is_listening,
            is_frequent_listening=controller_node.is_frequent_listening,
            failed=controller_node.failed,
            is_long_range=controller_node.is_long_range,
            max_data_rate_bps=controller_node.max_data_rate_bps,
        )

    return entity_to_resolution, controller_resolution


def _zrm_entity_bullet_ref(
    entity_id: str | None,
    device_id: str | None,
) -> str:
    """Render an entity reference for a notification bullet.

    When ``device_id`` is present, the entity id is wrapped in
    a markdown link pointing at the HA device page. Otherwise
    just the backticked entity id is returned. Empty string
    when the error doesn't reference an entity at all.
    """
    if not entity_id:
        return ""
    escaped = md_escape(entity_id)
    if device_id:
        return f"[`{escaped}`](/config/devices/device/{device_id})"
    return f"`{escaped}`"


def _zrm_error_bullets(errors: list[Any]) -> list[str]:
    """Render ZRM ConfigErrors as notification bullet strings.

    Each bullet names the YAML location (and an entity/device
    ref when present) before the error reason, so users can
    tell which config entry produced the problem. Feeds into
    ``_build_config_error_notification``.
    """
    bullets: list[str] = []
    for e in errors:
        ref = _zrm_entity_bullet_ref(e.entity_id, e.device_id)
        if ref:
            bullets.append(f"{ref} (`{e.location}`): {e.reason}")
        else:
            bullets.append(f"`{e.location}`: {e.reason}")
    return bullets


def _zrm_api_notification(
    notif_prefix: str,
    error: str,
) -> PersistentNotification:
    """Single API-availability notification."""
    title = "Z-Wave Route Manager: API unavailable"
    body = (
        f"Could not reach or use the Z-Wave JS UI API: {md_escape(error)}"
        "\n\nCheck that the Z-Wave JS addon is running and "
        "that the blueprint's host/port inputs match the addon."
    )
    return PersistentNotification(
        active=bool(error),
        notification_id=f"{notif_prefix}api",
        title=title,
        message=body,
    )


def _zrm_apply_notification(
    notif_prefix: str,
    action: Any,
    api_result: Any,
) -> PersistentNotification:
    """Per-node apply-failure notification."""
    title = f"Z-Wave Route Manager: apply failed for node {action.node_id}"
    message_lines = [
        f"Action: {action.kind.value}",
        f"Node: {action.node_id}",
    ]
    if action.client_entity_id:
        message_lines.append(f"Entity: `{action.client_entity_id}`")
    if action.repeaters:
        reps = ", ".join([str(r) for r in action.repeaters])
        message_lines.append(f"Repeaters: {reps}")
    message_lines.append(
        f"Server response: {md_escape(api_result.message or '(empty)')}",
    )
    return PersistentNotification(
        active=True,
        notification_id=f"{notif_prefix}apply_{action.node_id}",
        title=title,
        message="\n".join(message_lines),
    )


def _zrm_timeout_notification(
    notif_prefix: str,
    node_id: int,
    route_type: Any,
    old_requested_at: datetime,
    timeout_count: int,
    pending_timeout_hours: int,
) -> PersistentNotification:
    """One-shot notification for a timed-out route attempt.

    The notification ID is keyed to the attempt that just
    timed out (``old_requested_at``), so each retry generates
    a unique notification that the user must dismiss manually.
    The orphan sweep is configured to leave ``__timeout_*``
    IDs alone so they don't auto-clear on the next tick.
    """
    title = (
        f"Z-Wave Route Manager: route pending > "
        f"{pending_timeout_hours}h for node {node_id}"
    )
    body = (
        f"A {route_type.value} route command sent to node"
        f" {node_id} did not land within {pending_timeout_hours}"
        " hours. The route has been re-issued automatically."
        f" This is timeout #{timeout_count} for the request."
        " The node may be unreachable, asleep longer than"
        " expected, or unable to accept this route. Remove the"
        " device from the YAML config to stop further retries."
    )
    safe_ts = old_requested_at.isoformat().replace(":", "_").replace(".", "_")
    return PersistentNotification(
        active=True,
        notification_id=(
            f"{notif_prefix}timeout_{node_id}_{route_type.value}_{safe_ts}"
        ),
        title=title,
        message=body,
    )


def _zrm_expected_api_for_kind(kind: Any) -> str:
    """Return the wire-level API name we expect echoed back.

    Used by the api_echo mismatch check to verify zwave-js-ui
    allow-lists the API we just called. Both set-app and
    clear-app actions dispatch ``setPriorityRoute`` under the
    hood; clearing is just a ``setPriorityRoute`` with empty
    repeaters.
    """
    import zwave_js_ui_bridge as bridge
    import zwave_route_manager as zrm

    # Module attributes are typed ``Any`` -- cast to str so
    # this function's declared return type is honoured.
    if kind == zrm.RouteActionKind.SET_APPLICATION_ROUTE:
        return str(bridge.API_SET_APPLICATION_ROUTE)
    if kind == zrm.RouteActionKind.CLEAR_APPLICATION_ROUTE:
        return str(bridge.API_SET_APPLICATION_ROUTE)
    if kind == zrm.RouteActionKind.SET_PRIORITY_SUC_RETURN_ROUTE:
        return str(bridge.API_ASSIGN_PRIORITY_SUC_RETURN_ROUTE)
    return str(bridge.API_DELETE_SUC_RETURN_ROUTES)


def _zrm_api_unavailable_message(
    api_result: Any,
    expected_api: str,
) -> str | None:
    """Return a user-facing message if the call says the API is
    unavailable, else ``None``.

    The zwave-js-ui server replies to every ZWAVE_API call with
    an envelope that includes ``api`` (an echo of the api name
    it processed), ``success``, and ``message``. If the
    requested api is missing from the server's allow-list the
    echo comes back different from what we sent; if the driver
    rejects the call the envelope's ``success`` is False. Both
    symptoms mean "this API call isn't going to work," and the
    caller should bail with an API-unavailable notification
    rather than proceeding.

    ``api_echo=None`` is treated as benign (timeout /
    malformed response / fire-and-forget queued); those have
    their own handling paths elsewhere.
    """
    echo = api_result.api_echo
    if echo is not None and echo != expected_api:
        return (
            f"zwave-js-ui rejected API {expected_api!r} "
            f"(echoed {echo!r}). Check that your zwave-js-ui "
            "version allow-lists this api."
        )
    if not api_result.success and echo == expected_api:
        # Server processed our call but the driver said no;
        # surface whatever error message it sent.
        msg = str(api_result.message or "").strip()
        return (
            f"zwave-js-ui reported {expected_api!r} failed: "
            f"{msg or 'no message'}"
        )
    return None


def _zrm_api_echo_mismatch(
    apply_results: list[tuple[Any, Any]],
) -> tuple[Any, str] | None:
    """Scan apply results for an api_echo vs expected mismatch.

    Returns ``(action, message)`` for the first mismatch, or
    ``None`` if every write result echoed its expected api (or
    had ``api_echo=None``, which is handled elsewhere).

    A single mismatch means zwave-js-ui isn't allow-listing one
    of the write APIs; the whole reconcile should bail because
    subsequent actions will keep failing the same way.
    """
    for action, api_result in apply_results:
        expected = _zrm_expected_api_for_kind(action.kind)
        msg = _zrm_api_unavailable_message(api_result, expected)
        if msg is not None:
            return (action, msg)
    return None


def _zrm_node_to_entity(resolved: list[Any]) -> dict[int, str]:
    """Build a node_id -> entity_id lookup for storage display.

    Each ``ResolvedRoute`` carries the YAML-referenced entity
    id for its client and (now) repeater(s); flattening them
    here gives us the lookup the storage helpers need to
    annotate node ids with their human-readable entity ids.
    """
    out: dict[int, str] = {}
    for r in resolved:
        out[r.client_node_id] = r.client_entity_id
        repeaters = getattr(r, "repeater_node_ids", []) or []
        rep_entities = getattr(r, "repeater_entity_ids", []) or []
        for nid, eid in zip(repeaters, rep_entities, strict=False):
            out.setdefault(nid, eid)
    return out


def _zrm_repeaters_to_storage(
    reps: list[int],
    node_to_entity: dict[int, str],
) -> list[dict[str, Any]]:
    """Serialize a repeater list as ``[{"id": N, "entity_id": str}]``."""
    return [
        {"id": rid, "entity_id": node_to_entity.get(rid, "")} for rid in reps
    ]


def _zrm_repeaters_from_storage(raw: Any) -> list[int]:
    """Parse the repeater list from either shape the tool has written:

    * ``[{"id": N, ...}]`` -- current.
    * ``[N]`` -- historical (before the entity-id annotation).
    """
    if not isinstance(raw, list):
        return []
    out: list[int] = []
    for item in raw:
        if isinstance(item, int):
            out.append(item)
        elif isinstance(item, dict) and isinstance(item.get("id"), int):
            out.append(item["id"])
    return out


def _zrm_speed_from_storage(raw: Any) -> Any:
    """Look up the ``RouteSpeed`` matching a stored speed string."""
    import zwave_js_ui_bridge as bridge

    if not isinstance(raw, str):
        return None
    for rs in bridge.RouteSpeed:
        if rs.value == raw:
            return rs
    return None


def _zrm_ts_to_storage(ts: Any) -> str:
    """ISO-format a datetime, or empty string when None."""
    return ts.isoformat() if ts is not None else ""


def _zrm_ts_from_storage(raw: Any) -> Any:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _zrm_path_to_storage(
    path: Any,
    node_to_entity: dict[int, str],
) -> dict[str, Any]:
    """Serialize one ``RouteRequest``.

    The two timestamps are both emitted unconditionally (as
    empty strings when None) so consumers can distinguish
    "route went through pending->applied" (both present) from
    "observed already applied" (only confirmed_at present).
    """
    # ``speed`` is ``None`` for clear entries (empty
    # repeaters); render as "-" so the state entity stays
    # scannable in dev tools.
    speed_display = path.speed.value if path.speed is not None else "-"
    return {
        "type": path.type.value,
        "repeaters": _zrm_repeaters_to_storage(
            list(path.repeater_node_ids),
            node_to_entity,
        ),
        "speed": speed_display,
        "requested_at": _zrm_ts_to_storage(path.requested_at),
        "confirmed_at": _zrm_ts_to_storage(path.confirmed_at),
        "timeout_count": path.timeout_count,
    }


def _zrm_path_from_storage(raw: Any) -> Any:
    """Inverse of _zrm_path_to_storage. Returns ``None`` on junk."""
    import zwave_route_manager as zrm

    if not isinstance(raw, dict):
        return None
    type_str = raw.get("type")
    if not isinstance(type_str, str):
        return None
    route_type = None
    for rt in zrm.RouteType:
        if rt.value == type_str:
            route_type = rt
            break
    if route_type is None:
        return None
    reps = _zrm_repeaters_from_storage(raw.get("repeaters"))
    if not reps:
        # Pending clear. Speed is irrelevant for clears and
        # stored as "-"; leave it ``None`` rather than
        # inventing a placeholder enum value. Legacy stored
        # state that kept a real speed here is simply dropped.
        speed = None
    else:
        speed = _zrm_speed_from_storage(raw.get("speed"))
        if speed is None:
            return None
    raw_count = raw.get("timeout_count")
    timeout_count = raw_count if isinstance(raw_count, int) else 0
    return zrm.RouteRequest(
        type=route_type,
        repeater_node_ids=reps,
        speed=speed,
        requested_at=_zrm_ts_from_storage(raw.get("requested_at")),
        confirmed_at=_zrm_ts_from_storage(raw.get("confirmed_at")),
        timeout_count=timeout_count,
    )


def _zrm_paths_to_storage(
    paths: dict[int, list[Any]],
    node_to_entity: dict[int, str],
) -> dict[str, Any]:
    """Serialize ``dict[NodeID, list[RouteRequest]]`` with one entry per node.

    Each node maps to a dict of ``entity_id`` + ``paths`` (the
    list of RouteRequest dicts). Keeping the entity-id at the node
    level avoids repeating it for every path and mirrors how
    the tool renders the state entity in dev tools.
    """
    out: dict[str, Any] = {}
    for node_id, path_list in paths.items():
        if not path_list:
            continue
        out[str(node_id)] = {
            "entity_id": node_to_entity.get(node_id, ""),
            "paths": [
                _zrm_path_to_storage(p, node_to_entity) for p in path_list
            ],
        }
    return out


def _zrm_paths_from_storage(stored: Any) -> dict[int, list[Any]]:
    """Inverse of _zrm_paths_to_storage. Tolerant of junk data.

    Unknown or malformed entries are silently dropped; the
    route manager's next reconcile will re-derive everything
    from the current zwave-js-ui state.
    """
    out: dict[int, list[Any]] = {}
    if not isinstance(stored, dict):
        return out
    for key, val in stored.items():
        try:
            node_id = int(key)
        except (ValueError, TypeError):
            continue
        if not isinstance(val, dict):
            continue
        raw_paths = val.get("paths")
        if not isinstance(raw_paths, list):
            continue
        paths = []
        for raw in raw_paths:
            path = _zrm_path_from_storage(raw)
            if path is not None:
                paths.append(path)
        if paths:
            out[node_id] = paths
    return out


def _zrm_save_failure_state(
    key: str,
    now: datetime,
    start_time: float,
    current_mtime: float,
    last_reconcile_iso: str,
    trigger_id: str,
    extra: dict[str, Any],
) -> None:
    """Persist state for a bailing-out reconcile.

    Every error path writes the same common attributes so
    operators can see why a reconcile stopped. Centralised
    so the paths stay consistent (one used to be missing a
    save entirely).
    """
    attrs: dict[str, Any] = {
        "runtime": str(round(time.monotonic() - start_time, 2)),
        "reconcile_pending": True,
        "last_reconcile": last_reconcile_iso,
        "last_config_mtime": current_mtime,
        "last_trigger": str(trigger_id or ""),
    }
    for name, value in extra.items():
        attrs[name] = value
    _save_state(key, now, attrs)


_ZRM_SERVICE_LABEL = "Z-Wave Route Manager"


def zwave_route_manager(
    instance_id: str,
    trigger_id: str,
    config_file_path: str,
    host: str,
    port: int,
    token: str,
    clear_unmanaged_routes: bool,
    reconcile_interval_minutes: int,
    pending_timeout_hours: int,
    default_route_speed: Any,
    max_notifications: int,
    debug_logging: bool,
) -> None:
    """Reconcile Z-Wave priority routes against a YAML config."""
    from datetime import timedelta

    import zwave_js_ui_bridge as bridge
    import zwave_route_manager as zrm

    start_time = time.monotonic()
    now = datetime.now(tz=UTC)
    auto_name = _automation_name(instance_id)
    tag = f"[ZRM: {auto_name}]"
    notif_prefix = _notification_prefix(
        _ZRM_SERVICE_LABEL,
        instance_id,
    )

    # Load state: reconcile_pending, last_reconcile,
    # last_config_mtime, pending, applied.
    key = _state_key(instance_id)
    try:
        stored_attrs = state.getattr(key)  # noqa: F821
    except NameError:
        stored_attrs = None
    # getattr returns None when the entity doesn't exist yet
    # (first run after install). Normalize to an empty dict.
    if not isinstance(stored_attrs, dict):
        stored_attrs = {}
    reconcile_pending = bool(
        stored_attrs.get("reconcile_pending", False),
    )
    last_config_mtime = float(
        stored_attrs.get("last_config_mtime", 0.0) or 0.0,
    )
    last_reconcile_iso = str(
        stored_attrs.get("last_reconcile", "") or "",
    )
    try:
        last_reconcile_dt = (
            datetime.fromisoformat(last_reconcile_iso)
            if last_reconcile_iso
            else None
        )
    except ValueError:
        last_reconcile_dt = None

    pending = _zrm_paths_from_storage(stored_attrs.get("pending"))
    applied_state = _zrm_paths_from_storage(stored_attrs.get("applied"))

    # Resolve config path + check mtime for change detection
    abs_config_path = _zrm_resolve_path(config_file_path)
    current_mtime = _zrm_read_config_mtime(abs_config_path)

    # Gate decision. Reconcile when any of:
    # - this is an HA-start trigger
    # - the config file's mtime changed since last run
    # - the reconcile interval has elapsed since last reconcile
    # - we had a prior deferred reconcile pending
    # - a manual service call (service tool, dev tools)
    triggered_by_ha_start = str(trigger_id or "") == "ha_start"
    mtime_changed = current_mtime != last_config_mtime
    interval_elapsed = True
    if last_reconcile_dt is not None:
        interval_elapsed = (now - last_reconcile_dt) > timedelta(
            minutes=reconcile_interval_minutes
        )
    manual_trigger = str(trigger_id or "") == "manual"

    should_reconcile = (
        triggered_by_ha_start
        or mtime_changed
        or interval_elapsed
        or reconcile_pending
        or manual_trigger
    )

    if not should_reconcile:
        # Periodic tick with nothing to do. Update last_run
        # and exit.
        _save_state(
            key,
            now,
            {
                "runtime": str(
                    round(time.monotonic() - start_time, 2),
                ),
                "reconcile_pending": False,
                "last_reconcile": last_reconcile_iso,
                "last_config_mtime": last_config_mtime,
                "last_trigger": str(trigger_id or ""),
            },
        )
        return

    # Read + parse config. Missing or empty files are
    # treated as empty configs -- combined with
    # clear_unmanaged this means "clear all routes" which
    # may or may not be what the user wants. The blueprint
    # notes this in its description.
    text, read_err = _zrm_read_config_text(abs_config_path)
    config_errors: list[Any] = []
    if read_err is not None:
        config_errors.append(
            zrm.ConfigError(
                location="(file)",
                entity_id=None,
                reason=read_err,
            ),
        )
        config = zrm.Config()
    else:
        config, config_errors = zrm.parse_config(text)

    # If parse errors, halt -- don't proceed to resolve/diff.
    if config_errors:
        notif = _build_config_error_notification(
            _zrm_error_bullets(config_errors),
            instance_id,
            _ZRM_SERVICE_LABEL,
            debug_logging,
            tag,
        )
        _sweep_and_process_notifications(
            hass,  # noqa: F821
            [notif],
            instance_id,
            notif_prefix,
            keep_pattern="__timeout_",
        )
        _zrm_save_failure_state(
            key,
            now,
            start_time,
            current_mtime,
            last_reconcile_iso,
            trigger_id,
            {"config_errors": len(config_errors)},
        )
        return

    # Clear any stale config-error notification.
    inactive_config_notif = PersistentNotification(
        active=False,
        notification_id=f"{notif_prefix}config",
        title="",
        message="",
    )

    # Fetch nodes via bridge. Unlike a separate probe() call,
    # the getNodes ApiResult from this fetch is reused below
    # for the api_echo/success check -- the same lazy
    # allow-list detection we apply to the write APIs.
    bridge_result = _zrm_bridge_get_nodes(
        host,
        port,
        token,
    )
    if bridge_result.get("error"):
        # Likely a transient connection error. Carry reconcile
        # forward, no notification (noise on every HA restart).
        _zrm_save_failure_state(
            key,
            now,
            start_time,
            current_mtime,
            last_reconcile_iso,
            trigger_id,
            {"bridge_error": str(bridge_result.get("error"))},
        )
        if debug_logging:
            log.warning(  # noqa: F821
                "%s bridge not ready: %s",
                tag,
                bridge_result.get("error"),
            )
        return

    getnodes_result = bridge_result.get("api_result")
    err_msg = None
    if getnodes_result is None:
        err_msg = "zwave-js-ui did not respond to getNodes"
    else:
        err_msg = _zrm_api_unavailable_message(
            getnodes_result,
            str(bridge.API_GET_NODES),
        )
    if err_msg is not None:
        notif = _zrm_api_notification(notif_prefix, err_msg)
        _sweep_and_process_notifications(
            hass,  # noqa: F821
            [inactive_config_notif, notif],
            instance_id,
            notif_prefix,
            keep_pattern="__timeout_",
        )
        _zrm_save_failure_state(
            key,
            now,
            start_time,
            current_mtime,
            last_reconcile_iso,
            trigger_id,
            {"api_error": err_msg},
        )
        return

    nodes = bridge_result.get("nodes", [])
    nodes_by_id: dict[int, Any] = {}
    sleepy_list: list[int] = []
    for n in nodes:
        nodes_by_id[n.node_id] = n
        # Sleepy = battery-powered non-FLiRS. These nodes queue
        # route commands at the controller until their next
        # wake and never ACK synchronously. isFrequentListening
        # is a string like "1000ms" for FLiRS; ``bool(...)``
        # correctly treats the empty string / False as "not
        # FLiRS" and any non-empty string as "is FLiRS".
        if not n.is_listening and not bool(n.is_frequent_listening):
            sleepy_list.append(n.node_id)
    sleepy_node_ids: frozenset[int] = frozenset(sleepy_list)

    # Build entity->DeviceResolution map (main thread, uses HA registries).
    entity_map, controller = _zrm_build_entity_to_resolution(
        hass,  # noqa: F821
        nodes,
    )

    if controller is None:
        err_msg = "controller (node 1) not found in getNodes() response"
        notif = _zrm_api_notification(notif_prefix, err_msg)
        _sweep_and_process_notifications(
            hass,  # noqa: F821
            [inactive_config_notif, notif],
            instance_id,
            notif_prefix,
            keep_pattern="__timeout_",
        )
        _zrm_save_failure_state(
            key,
            now,
            start_time,
            current_mtime,
            last_reconcile_iso,
            trigger_id,
            {"api_error": err_msg},
        )
        return

    # Resolve entities -> concrete ResolvedRoutes.
    resolved, resolve_errors = zrm.resolve_entities(
        config,
        default_route_speed,
        entity_map,
        controller,
    )

    if resolve_errors:
        notif = _build_config_error_notification(
            _zrm_error_bullets(resolve_errors),
            instance_id,
            _ZRM_SERVICE_LABEL,
            debug_logging,
            tag,
        )
        _sweep_and_process_notifications(
            hass,  # noqa: F821
            [notif],
            instance_id,
            notif_prefix,
            keep_pattern="__timeout_",
        )
        _zrm_save_failure_state(
            key,
            now,
            start_time,
            current_mtime,
            last_reconcile_iso,
            trigger_id,
            {"resolve_errors": len(resolve_errors)},
        )
        return

    # Diff + plan (pure, on main thread).
    reconcile = zrm.diff_and_plan(
        resolved,
        nodes_by_id,
        pending,
        applied_state,
        now,
        timedelta(hours=pending_timeout_hours),
        clear_unmanaged_routes,
    )

    # Apply actions via bridge (worker thread).
    apply_notifications: list[PersistentNotification] = []
    applied_actions_by_node: dict[int, list[Any]] = {}
    failed_actions_by_node: dict[int, list[Any]] = {}

    if reconcile.actions:
        apply_results = _zrm_bridge_apply_actions(
            host,
            port,
            token,
            reconcile.actions,
            sleepy_node_ids,
        )
        # Per-action api_echo / success check. A mismatch or
        # non-success with matching echo means zwave-js-ui
        # can't run the write api we tried to use; every later
        # action will fail the same way, so surface as
        # API-unavailable and bail.
        mismatch = _zrm_api_echo_mismatch(apply_results)
        if mismatch is not None:
            _mismatch_action, err_msg = mismatch
            notif = _zrm_api_notification(notif_prefix, err_msg)
            _sweep_and_process_notifications(
                hass,  # noqa: F821
                [inactive_config_notif, notif],
                instance_id,
                notif_prefix,
                keep_pattern="__timeout_",
            )
            _zrm_save_failure_state(
                key,
                now,
                start_time,
                current_mtime,
                last_reconcile_iso,
                trigger_id,
                {"api_error": err_msg},
            )
            return
        for action, api_result in apply_results:
            node_id = action.node_id
            if api_result.success:
                applied_actions_by_node.setdefault(
                    node_id,
                    [],
                ).append(action)
            else:
                failed_actions_by_node.setdefault(
                    node_id,
                    [],
                ).append((action, api_result))
                apply_notifications.append(
                    _zrm_apply_notification(
                        notif_prefix,
                        action,
                        api_result,
                    ),
                )

    # Build the final pending + applied dicts.
    #
    # diff_and_plan tells us the *intended* per-route state
    # assuming every action succeeds. We then adjust per node:
    #
    # - If any action for the node failed: drop the failed
    #   route(s) from pending (the apply notification already
    #   fired). Other routes for that node (different type or
    #   no action this run) carry through as-is.
    # - For an awake node where every just-emitted action ACKed
    #   synchronously: those routes have effectively landed, so
    #   move them from pending -> applied this reconcile rather
    #   than waiting for the next pass to confirm them via
    #   cached state.
    # - For sleepy nodes, fire-and-forget commands sit in
    #   pending until the node wakes and the route shows up in
    #   the cached state on a later reconcile.
    failed_route_types_by_node: dict[int, set[Any]] = {}
    for node_id, failures in failed_actions_by_node.items():
        for action, _api_result in failures:
            route_type = zrm.type_for_action_kind(action.kind)
            failed_route_types_by_node.setdefault(node_id, set()).add(
                route_type,
            )

    final_pending: dict[int, list[Any]] = {}
    final_applied: dict[int, list[Any]] = {
        nid: list(paths) for nid, paths in reconcile.new_applied.items()
    }
    for node_id, paths in reconcile.new_pending.items():
        failed_types = failed_route_types_by_node.get(node_id, set())
        is_awake = node_id not in sleepy_node_ids
        keep_pending: list[Any] = []
        promote_to_applied: list[Any] = []
        for path in paths:
            if path.type in failed_types:
                # Apply attempt failed -- drop. The apply
                # notification already covered this.
                continue
            is_clear = not path.repeater_node_ids
            if is_clear:
                # Clears stay in pending until the next
                # reconcile observes ``current is None`` and
                # drops them. They never enter ``applied``:
                # ``applied`` only tracks routes currently at
                # a specific non-default value.
                keep_pending.append(path)
            elif is_awake:
                # Set ACKed synchronously: promote to applied
                # with confirmed_at = now (requested_at
                # carries through from the just-issued
                # command).
                promote_to_applied.append(
                    zrm.RouteRequest(
                        type=path.type,
                        repeater_node_ids=list(path.repeater_node_ids),
                        speed=path.speed,
                        requested_at=path.requested_at,
                        confirmed_at=now,
                    ),
                )
            else:
                keep_pending.append(path)
        if keep_pending:
            final_pending[node_id] = keep_pending
        if promote_to_applied:
            final_applied.setdefault(node_id, []).extend(
                promote_to_applied,
            )

    # Per-attempt timeout notifications. One notification per
    # (node, route_type, old_requested_at) tuple so each
    # retry gets its own persistent notification -- see the
    # ``keep_pattern`` arg passed to the orphan sweep below.
    timeout_notifications: list[PersistentNotification] = []
    for node_id, route_type, old_requested_at, count in reconcile.new_timeouts:
        timeout_notifications.append(
            _zrm_timeout_notification(
                notif_prefix,
                node_id,
                route_type,
                old_requested_at,
                count,
                pending_timeout_hours,
            ),
        )

    # Cap notifications if configured.
    issue_notifications = apply_notifications + timeout_notifications
    if max_notifications > 0 and len(issue_notifications) > max_notifications:
        # Keep the first N (deterministic) + a cap summary.
        kept = issue_notifications[:max_notifications]
        cap_summary = PersistentNotification(
            active=True,
            notification_id=f"{notif_prefix}cap",
            title="Z-Wave Route Manager: notification cap reached",
            message=(
                f"Showing {max_notifications} of"
                f" {len(issue_notifications)} route issues."
                " Increase the blueprint's"
                " max_notifications input to see all."
            ),
        )
        issue_notifications = kept + [cap_summary]
    else:
        # Clear any stale cap notification.
        issue_notifications.append(
            PersistentNotification(
                active=False,
                notification_id=f"{notif_prefix}cap",
                title="",
                message="",
            ),
        )

    # Emit / dismiss. Always include the inactive config &
    # api notifications so any leftovers get cleared. The
    # orphan sweep then dismisses any prefix-matching
    # notifications from a prior run that this run didn't
    # re-emit -- covers nodes whose apply or pending issues
    # have cleared, and nodes that have been removed from
    # the Z-Wave network entirely.
    inactive_api_notif = PersistentNotification(
        active=False,
        notification_id=f"{notif_prefix}api",
        title="",
        message="",
    )
    final_notifications = [
        inactive_config_notif,
        inactive_api_notif,
    ] + issue_notifications
    _sweep_and_process_notifications(
        hass,  # noqa: F821
        final_notifications,
        instance_id,
        notif_prefix,
        keep_pattern="__timeout_",
    )

    # Counts are per route direction: each configured device
    # contributes one entry per managed route type. A node
    # with one type applied and one type pending shows up in
    # both ``routes_applied`` and ``routes_pending``.
    node_to_entity = _zrm_node_to_entity(resolved)
    routes_applied = sum([len(p) for p in final_applied.values()])
    routes_pending = sum([len(p) for p in final_pending.values()])
    routes_errored = sum([len(f) for f in failed_actions_by_node.values()])

    # Persist state.
    reconcile_complete = len(failed_actions_by_node) == 0
    _save_state(
        key,
        now,
        {
            "runtime": str(
                round(time.monotonic() - start_time, 2),
            ),
            "reconcile_pending": not reconcile_complete,
            "last_reconcile": now.isoformat(),
            "last_config_mtime": current_mtime,
            "last_trigger": str(trigger_id or ""),
            "routes_in_config": len(resolved) * len(zrm.MANAGED_ROUTE_TYPES),
            "routes_applied": routes_applied,
            "routes_pending": routes_pending,
            "routes_errored": routes_errored,
            # Zero out error counters on success so stale values
            # from prior failed runs don't persist.
            "config_errors": 0,
            "resolve_errors": 0,
            "api_error": "",
            "bridge_error": "",
            "pending": _zrm_paths_to_storage(final_pending, node_to_entity),
            "applied": _zrm_paths_to_storage(final_applied, node_to_entity),
        },
    )

    if debug_logging:
        log.warning(  # noqa: F821
            "%s configured=%d applied=%d pending=%d errored=%d"
            " new_timeouts=%d actions_executed=%d",
            tag,
            len(resolved) * len(zrm.MANAGED_ROUTE_TYPES),
            routes_applied,
            routes_pending,
            routes_errored,
            len(reconcile.new_timeouts),
            len(reconcile.actions),
        )


def zwave_route_manager_blueprint_argparse(
    instance_id: str,
    trigger_id: str,
    config_file_path: str,
    zwave_js_ui_host_raw: str,
    zwave_js_ui_port_raw: str,
    zwave_js_ui_token_raw: str,
    clear_unmanaged_routes_raw: object,
    reconcile_interval_minutes_raw: str,
    pending_timeout_hours_raw: str,
    default_route_speed_raw: str,
    max_notifications_raw: str,
    debug_logging_raw: object,
) -> None:
    """Parse and validate ZRM blueprint inputs."""
    import zwave_route_manager as zrm

    host = str(zwave_js_ui_host_raw).strip() or "core-zwave-js"
    token = str(zwave_js_ui_token_raw or "").strip()
    clear_unmanaged_routes = _parse_bool(clear_unmanaged_routes_raw)
    debug_logging = _parse_bool(debug_logging_raw)
    tag = f"[ZRM: {_automation_name(instance_id)}]"

    # No hass means the service function below will crash
    # on its first state read; bail with a plain-string
    # config_error. A later tick with hass back up dismisses
    # it via the empty-errors path (same notification_id).
    hass_err = _check_hass_available()
    if hass_err is not None:
        config_error = _build_config_error_notification(
            [hass_err],
            instance_id,
            _ZRM_SERVICE_LABEL,
            debug_logging,
            tag,
        )
        _process_persistent_notifications(
            [config_error],
            instance_id,
        )
        return

    # Blueprint selectors enforce types in the UI but direct
    # service calls can still hand us garbage. Collect errors
    # as zrm.ConfigError so they flow through the same bullet
    # formatter as YAML config errors.
    typed_errors: list[Any] = []
    port, _err = _parse_int_input(zwave_js_ui_port_raw, 1, 65535)
    if _err is not None:
        typed_errors.append(
            zrm.ConfigError(
                location="blueprint input: zwave_js_ui_port",
                entity_id=None,
                reason=_err,
            ),
        )
    reconcile_interval_minutes, _err = _parse_int_input(
        reconcile_interval_minutes_raw,
        1,
        10080,
    )
    if _err is not None:
        typed_errors.append(
            zrm.ConfigError(
                location="blueprint input: reconcile_interval_minutes",
                entity_id=None,
                reason=_err,
            ),
        )
    pending_timeout_hours, _err = _parse_int_input(
        pending_timeout_hours_raw,
        1,
        168,
    )
    if _err is not None:
        typed_errors.append(
            zrm.ConfigError(
                location="blueprint input: pending_timeout_hours",
                entity_id=None,
                reason=_err,
            ),
        )
    max_notifications, _err = _parse_int_input(
        max_notifications_raw,
        0,
        1000,
    )
    if _err is not None:
        typed_errors.append(
            zrm.ConfigError(
                location="blueprint input: max_notifications",
                entity_id=None,
                reason=_err,
            ),
        )

    # Default speed: "auto" -> None. Any other value goes
    # through the logic module's parser; a bad value joins
    # typed_errors.
    default_speed_str = str(default_route_speed_raw or "auto").strip()
    default_route_speed = None
    if default_speed_str != "auto":
        resolved, speed_err = zrm.parse_route_speed_value(
            default_speed_str,
            "blueprint input: default_route_speed",
        )
        default_route_speed = resolved
        if speed_err is not None:
            typed_errors.append(speed_err)

    # Always emit config_error: active with bullets when there
    # are errors, inactive (dismissal) when clean. Matches DW/
    # EDW/RW/STSC/TEC convention -- argparse owns only its own
    # config_error notification; sweep is the service
    # function's territory.
    config_error = _build_config_error_notification(
        _zrm_error_bullets(typed_errors),
        instance_id,
        _ZRM_SERVICE_LABEL,
        debug_logging,
        tag,
    )
    _process_persistent_notifications(
        [config_error],
        instance_id,
    )
    if typed_errors:
        return

    zwave_route_manager(
        instance_id=instance_id,
        trigger_id=trigger_id,
        config_file_path=config_file_path,
        host=host,
        port=port,
        token=token,
        clear_unmanaged_routes=clear_unmanaged_routes,
        reconcile_interval_minutes=reconcile_interval_minutes,
        pending_timeout_hours=pending_timeout_hours,
        default_route_speed=default_route_speed,
        max_notifications=max_notifications,
        debug_logging=debug_logging,
    )


_BLUEPRINT_SERVICES[_ZRM_SERVICE_LABEL] = (
    "zwave_route_manager.yaml",
    frozenset(
        [
            "instance_id",
            "trigger_id",
            "config_file_path",
            "zwave_js_ui_host_raw",
            "zwave_js_ui_port_raw",
            "zwave_js_ui_token_raw",
            "clear_unmanaged_routes_raw",
            "reconcile_interval_minutes_raw",
            "pending_timeout_hours_raw",
            "default_route_speed_raw",
            "max_notifications_raw",
            "debug_logging_raw",
        ],
    ),
    zwave_route_manager_blueprint_argparse,
)


@service  # noqa: F821
async def zwave_route_manager_blueprint_entrypoint(**kwargs: object) -> None:
    """Blueprint-facing entrypoint for Z-Wave Route Manager."""
    await _dispatch_blueprint_service(_ZRM_SERVICE_LABEL, kwargs)
