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

import helpers  # noqa: F821

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
    notifications: "list[helpers.PersistentNotification]",
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
            helpers.PersistentNotification(  # noqa: F821
                active=False,
                notification_id=nid,
                title="",
                message="",
            ),
        )


def _sweep_and_process_notifications(
    hass_obj: Any,
    notifications: "list[helpers.PersistentNotification]",
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
    notifications: "list[helpers.PersistentNotification]",
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
            f"Automation: [{helpers.md_escape(auto_name)}]"
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
) -> "dict[str, helpers.DeviceEntry]":
    """Discover devices across all integrations.

    Always scans every integration for accurate
    multi-integration device detection. The optional
    integrations parameter filters which integrations
    populate entity IDs. Omit or pass None for all
    integrations (no filtering).
    """
    all_ids = _get_all_integration_ids(hass_obj)
    populate = set(integrations) if integrations is not None else None
    device_map: dict[str, helpers.DeviceEntry] = {}
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
                device_map[dev_id] = helpers.DeviceEntry(
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
) -> helpers.PersistentNotification:
    """Build a config-error persistent notification.

    Returns a helpers.PersistentNotification the caller dispatches.
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

    return helpers.PersistentNotification(
        active=bool(errors),
        notification_id=notif_id,
        title=f"{name}: Invalid Configuration",
        message=message,
    )


# -- Three-layer blueprint dispatch --------------------

# Hardcoded because ``__file__`` is NameError under
# pyscript's AST evaluator.
_WRAPPER_BASENAME = "blueprint_toolkit.py"


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
    "sensor_threshold_switch_controller",
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
    imports a logic module (``device_watchdog``,
    ``sensor_threshold_switch_controller``) fails with
    ``ModuleNotFoundError``.

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
) -> helpers.PersistentNotification:
    """Build the blueprint-vs-pyscript mismatch notification."""
    notif_id = f"{notif_prefix}blueprint_mismatch"
    if not missing and not extras:
        # Empty input returns an inactive notification
        # so any stale one gets dismissed by the sweep.
        return helpers.PersistentNotification(
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
        " Blueprint Toolkit integration is installed"
        " correctly and restart Home Assistant.",
    )
    return helpers.PersistentNotification(
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
    import sensor_threshold_switch_controller as stsc  # noqa: F821

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
    result = stsc.handle_service_call(
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
    if result.action == stsc.Action.TURN_ON:
        homeassistant.turn_on(  # noqa: F821
            entity_id=target_switch_entity,
        )
    elif result.action == stsc.Action.TURN_OFF:
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

    Compiled to native Python by ``@pyscript_executor`` and
    dispatched into a thread pool. ``func_name`` is
    ``"module_name.function_name"``.
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
    import device_watchdog as dw  # noqa: F821

    start_time = time.monotonic()
    now = datetime.now(tz=UTC)
    auto_name = _automation_name(instance_id)
    tag = f"[DW: {auto_name}]"

    # Interval gating (skip for timed triggers only;
    # manual UI runs always execute)
    if trigger_platform == "time_pattern":
        if not helpers.on_interval(check_interval_minutes, now, instance_id):
            return

    config = dw.Config(
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
    if dw.CHECK_DISABLED_DIAGNOSTICS in enabled_checks:
        import homeassistant.helpers.entity_registry as er  # noqa: F821

        ent_reg = er.async_get(hass)  # noqa: F821

    device_map = _discover_devices(
        hass,  # noqa: F821
        list(target_integrations),
    )

    # Build dw.DeviceInfo with state + registry data.
    devices = []
    for dev_entry in device_map.values():
        # Build registry entries if diagnostic check
        # is enabled (requires entity registry access)
        registry_entries: list[dw.RegistryEntry] = []
        if dw.CHECK_DISABLED_DIAGNOSTICS in enabled_checks:
            all_reg_entries = er.async_entries_for_device(
                ent_reg,
                dev_entry.id,
                include_disabled_entities=True,
            )
            registry_entries = [
                dw.RegistryEntry(
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
                            dw.EntityInfo(
                                entity_id=eid,
                                state=str(ent_state),
                                last_reported=last_reported,
                            ),
                        )
                except (NameError, AttributeError):
                    # Entity state unavailable
                    continue

        devices.append(
            dw.DeviceInfo(
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
    import device_watchdog as dw  # noqa: F821

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
    unknown_checks = [c for c in enabled_checks if c not in dw.CHECK_ALL]
    if unknown_checks:
        bad = ", ".join(sorted(unknown_checks))
        valid = ", ".join(sorted(dw.CHECK_ALL))
        errors.append(
            f"enabled_checks: unknown value(s) {bad}. Valid values: {valid}.",
        )
    # Empty selection means "all checks" (blueprint default
    # is also all three; this just mirrors the
    # include_integrations convention of empty == all).
    if not enabled_checks:
        enabled_checks = dw.CHECK_ALL

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
