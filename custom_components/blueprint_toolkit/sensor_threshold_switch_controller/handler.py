# This is AI generated code
"""HA wiring for sensor_threshold_switch_controller.

STSC-specific shape on top of the standard three-layer
dispatch (see ``DEVELOPMENT.md`` for the universal
pattern):

- Three input event types: SENSOR (sensor entity state
  change), SWITCH (target switch state change), TIMER
  (periodic minute tick). Reactive triggers stay in
  the blueprint (a state-change trigger per watched
  entity); the periodic tick is integration-owned via
  ``helpers.schedule_periodic_with_jitter``.
- Per-instance state (sample window, baseline,
  override list, auto_off_started_at) lives in the
  diagnostic state entity's ``data`` attribute as a
  JSON blob. Volatile across HA restarts; the periodic
  + reactive triggers re-bootstrap state on the next
  invocation, and ``handle_service_call`` arms auto-off
  at bootstrap if the switch is currently on.
- Action: ``homeassistant.turn_on`` /
  ``homeassistant.turn_off`` against the target
  switch entity, with the caller's ``context``
  propagated so logbook attribution is correct.
- Notification dispatch: best-effort call to the
  user-configured ``notify.<service>`` (real push
  notification, not a persistent-notification
  entry). Failures don't abort -- state save lands
  before the dispatch so a notify failure can't lose
  state.
- Single notification slot for argparse / config
  errors via the shared
  ``helpers.make_config_error_notification`` /
  ``emit_config_error`` path. STSC has no per-event
  persistent-notification stream of its own; the
  per-instance sweep dismisses stale config-error
  entries on every successful run.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from ..const import DOMAIN
from ..helpers import (
    BlueprintHandlerSpec,
    automation_friendly_name,
    entry_for_domain,
    instance_state_entity_id,
    make_emit_config_error,
    parse_notification_service,
    process_persistent_notifications_with_sweep,
    register_blueprint_handler,
    schedule_periodic_with_jitter,
    spec_bucket,
    unregister_blueprint_handler,
    update_instance_state,
    validate_payload_or_emit_config_error,
)

# STSC takes a user-supplied ``notification_prefix`` string
# (the per-instance body prefix, e.g. ``"STSC: "``) as a
# blueprint input. Alias the helper that builds the per-
# instance notification-ID prefix so the two don't collide
# inside the service layer.
from ..helpers import notification_prefix as _notification_id_prefix
from . import logic

_LOGGER = logging.getLogger(__name__)

_SERVICE = "sensor_threshold_switch_controller"
_SERVICE_TAG = "STSC"
_SERVICE_NAME = "Sensor Threshold Switch Controller"
BLUEPRINT_PATH = "blueprint_toolkit/sensor_threshold_switch_controller.yaml"

# The integration-owned periodic tick fires every minute.
# Hardcoded rather than a blueprint input -- the cadence
# is load-bearing for the spike-detection sample window
# and isn't user-tunable today.
_PERIODIC_INTERVAL = timedelta(minutes=1)

# ``trigger_entity`` value the integration-owned periodic
# callback passes to mark a tick as "timer" (the third
# event type alongside SENSOR + SWITCH). The logic
# module's ``determine_event_type`` recognises
# ``"timer"`` as the canonical sentinel.
_TIMER_TRIGGER_ENTITY = "timer"

# Domains that respond to ``homeassistant.turn_on`` /
# ``turn_off``. The blueprint's selector restricts
# ``target_switch_entity`` to a subset (switch / fan /
# light / input_boolean), but a hand-edited automation
# YAML can bypass the selector. Argparse rejects out-of-
# set domains so the user gets an explanatory
# notification instead of a silent no-op when the service
# layer dispatches against an unsupported entity.
_CONTROLLABLE_DOMAINS = frozenset(
    {
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
    },
)


# --------------------------------------------------------
# Per-instance in-memory state
# --------------------------------------------------------


@dataclass
class StscInstanceState:
    """In-memory state for one STSC automation instance.

    Lost on HA restart; the periodic timer + the
    blueprint's reactive triggers re-bootstrap the
    persistent state from the diagnostic entity's
    ``data`` attribute on the next invocation.
    """

    instance_id: str
    cancel_timer: Callable[[], None] | None = field(default=None, repr=False)


# --------------------------------------------------------
# Service-call schema
# --------------------------------------------------------

_SCHEMA = vol.Schema(
    {
        vol.Required("instance_id"): cv.entity_id,
        vol.Required("trigger_id"): vol.Coerce(str),
        vol.Required("target_switch_entity"): cv.entity_id,
        vol.Required("sensor_value"): vol.Coerce(str),
        vol.Required("switch_state"): vol.Coerce(str),
        vol.Required("trigger_entity"): vol.Coerce(str),
        vol.Required("trigger_threshold_raw"): vol.Coerce(float),
        vol.Required("release_threshold_raw"): vol.Coerce(float),
        vol.Required("sampling_window_seconds_raw"): vol.All(
            vol.Coerce(int), vol.Range(min=10, max=3600)
        ),
        vol.Required("disable_window_seconds_raw"): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=60)
        ),
        vol.Required("auto_off_minutes_raw"): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=1440)
        ),
        vol.Required("notification_service"): vol.Coerce(str),
        vol.Required("notification_prefix"): vol.Coerce(str),
        vol.Required("notification_suffix"): vol.Coerce(str),
        vol.Required("debug_logging_raw"): cv.boolean,
    },
    extra=vol.ALLOW_EXTRA,
)


# --------------------------------------------------------
# Per-instance state accessor
# --------------------------------------------------------


def _instances(hass: HomeAssistant) -> dict[str, StscInstanceState]:
    """Per-instance state map under our service's bucket."""
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        return {}
    bucket = spec_bucket(entries[0], _SERVICE)
    instances: dict[str, StscInstanceState] = bucket.setdefault("instances", {})
    return instances


# --------------------------------------------------------
# Layer 1: entrypoint
# --------------------------------------------------------


async def _async_entrypoint(hass: HomeAssistant, call: ServiceCall) -> None:
    """Service handler -- thin wrapper, hands off to argparse."""
    await _async_argparse(hass, call, now=dt_util.now())


# --------------------------------------------------------
# Layer 2: argparse
# --------------------------------------------------------


_emit_config_error = make_emit_config_error(
    service=_SERVICE,
    service_tag=_SERVICE_TAG,
)


async def _async_argparse(
    hass: HomeAssistant,
    call: ServiceCall,
    *,
    now: datetime,
) -> None:
    """Validate, build context, dispatch to the service layer."""
    raw = dict(call.data)

    data = await validate_payload_or_emit_config_error(
        hass,
        raw,
        _SCHEMA,
        _emit_config_error,
    )
    if data is None:
        return

    instance_id: str = data["instance_id"]
    errors: list[str] = []

    # Cross-field: target_switch_entity must exist as a
    # state in HA today and must live in a domain that
    # responds to ``homeassistant.turn_on`` /
    # ``turn_off``. Catches typos AND selector-bypassing
    # YAML edits before the service layer dispatches a
    # silent no-op against an unsupported entity.
    target_switch_entity: str = data["target_switch_entity"]
    if hass.states.get(target_switch_entity) is None:
        errors.append(
            f"target_switch_entity: {target_switch_entity!r}"
            " is not a known entity",
        )
    else:
        domain = target_switch_entity.split(".", 1)[0]
        if domain not in _CONTROLLABLE_DOMAINS:
            errors.append(
                f"target_switch_entity: {target_switch_entity!r}"
                " does not support on/off (pick an entity in one of:"
                f" {', '.join(sorted(_CONTROLLABLE_DOMAINS))})",
            )

    # Cross-field: notification_service must be registered.
    # Empty string is a valid "no notifications" sentinel.
    notification_service: str = data["notification_service"]
    if notification_service:
        try:
            notif_domain, notif_name = parse_notification_service(
                notification_service,
            )
        except ValueError as err:
            errors.append(
                f"notification_service: {err}",
            )
            notif_domain, notif_name = "", ""
        if notif_domain and notif_name:
            if not hass.services.has_service(notif_domain, notif_name):
                errors.append(
                    f"notification_service: {notification_service!r}"
                    " is not a registered service",
                )

    # Argparse complete; emit accumulated errors (or
    # dismiss any prior config_error notification).
    await _emit_config_error(hass, instance_id, errors)
    if errors:
        return

    await _async_service_layer(
        hass,
        call,
        now=now,
        instance_id=instance_id,
        trigger_id=data["trigger_id"],
        target_switch_entity=target_switch_entity,
        sensor_value=data["sensor_value"],
        switch_state=data["switch_state"],
        trigger_entity=data["trigger_entity"],
        trigger_threshold=data["trigger_threshold_raw"],
        release_threshold=data["release_threshold_raw"],
        sampling_window_seconds=data["sampling_window_seconds_raw"],
        disable_window_seconds=data["disable_window_seconds_raw"],
        auto_off_minutes=data["auto_off_minutes_raw"],
        notification_service=notification_service,
        notification_prefix=data["notification_prefix"],
        notification_suffix=data["notification_suffix"],
        debug_logging=data["debug_logging_raw"],
    )


# --------------------------------------------------------
# Layer 3: service layer
# --------------------------------------------------------


async def _async_service_layer(
    hass: HomeAssistant,
    call: ServiceCall,
    *,
    now: datetime,
    instance_id: str,
    trigger_id: str,
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
    """Run the controller + dispatch action / notification."""
    state = _instances(hass).setdefault(
        instance_id,
        StscInstanceState(instance_id=instance_id),
    )

    # Make sure the periodic timer is armed (idempotent;
    # arms once per instance and stays until teardown).
    entry = entry_for_domain(hass)
    if entry is not None:
        _ensure_timer(hass, entry, state)

    notif_prefix = _notification_id_prefix(_SERVICE, instance_id)
    tag = f"[{_SERVICE_TAG}: {automation_friendly_name(hass, instance_id)}]"

    # Load the persistent state blob from the diagnostic
    # state entity's ``data`` attribute. Empty / missing
    # is fine -- the logic module bootstraps fresh state.
    state_data = _load_state_blob(hass, instance_id)

    # Resolve the switch's friendly name for the
    # notification body.
    switch_st = hass.states.get(target_switch_entity)
    switch_name = (
        switch_st.attributes.get("friendly_name", target_switch_entity)
        if switch_st is not None
        else target_switch_entity
    )

    # Pure-function controller call -- no HA dependencies.
    result = logic.handle_service_call(
        state_data=state_data,
        switch_name=str(switch_name),
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

    # STSC has no persistent-finding stream of its own; the
    # sweep just cleans up stale config-error notifications
    # left over from a prior bad config.
    await process_persistent_notifications_with_sweep(
        hass,
        [],
        sweep_prefix=notif_prefix,
    )

    update_instance_state(
        hass,
        service_tag=_SERVICE_TAG,
        instance_id=instance_id,
        last_run=now,
        runtime=(dt_util.now() - now).total_seconds(),
        state=result.action.name,
        extra_attributes={
            "last_trigger": trigger_id or "",
            "last_event": result.event_type,
            "last_action": result.action.name,
            "last_reason": result.reason or "n/a",
            "last_sensor": (
                str(result.sensor_value)
                if result.sensor_value is not None
                else "n/a"
            ),
            # JSON-encoded controller state for the next
            # tick's load. Volatile across HA restarts
            # (state machine is cleared); the next periodic
            # tick re-bootstraps from empty.
            "data": json.dumps(result.state_dict),
        },
    )

    # ``call.context`` propagates so the logbook attributes
    # the turn_on/off to the user who triggered the
    # automation, not to the integration.
    if result.action == logic.Action.TURN_ON:
        await hass.services.async_call(
            "homeassistant",
            "turn_on",
            {"entity_id": target_switch_entity},
            context=call.context,
            blocking=False,
        )
    elif result.action == logic.Action.TURN_OFF:
        await hass.services.async_call(
            "homeassistant",
            "turn_off",
            {"entity_id": target_switch_entity},
            context=call.context,
            blocking=False,
        )

    if notification_service and result.notification:
        await _async_send_notification(
            hass,
            notification_service,
            result.notification,
            call.context,
            tag,
        )

    if debug_logging:
        _LOGGER.warning(
            "%s event=%s sw=%s baseline=%s auto_off=%s samples=%s -> %s %r",
            tag,
            result.event_type,
            switch_state,
            result.state_dict.get("baseline"),
            result.state_dict.get("auto_off_started_at"),
            len(result.state_dict.get("samples", [])),
            result.action.name,
            result.reason,
        )


# --------------------------------------------------------
# State-blob load / send-notification helpers
# --------------------------------------------------------


def _load_state_blob(
    hass: HomeAssistant,
    instance_id: str,
) -> dict[str, Any] | None:
    """Read the JSON state blob from the diagnostic entity.

    Returns the parsed dict or ``None`` if the entity
    doesn't exist, has no ``data`` attribute, or the
    JSON is malformed. Any of those conditions means
    "no prior state" -- the logic module bootstraps
    fresh.
    """
    state_eid = instance_state_entity_id(_SERVICE_TAG, instance_id)
    st = hass.states.get(state_eid)
    if st is None:
        return None
    raw = st.attributes.get("data", "")
    if not raw:
        return None
    if not isinstance(raw, str):
        # Contract: ``data`` is the JSON blob the prior run
        # wrote via ``_save_state``, which stores a string.
        # If something else (an int, dict, etc.) is sitting
        # there, treat the slot as missing and let the
        # bootstrap path rebuild fresh state.
        return None
    try:
        loaded: dict[str, Any] = json.loads(raw)
    except ValueError:
        # Malformed blob -- treat as missing. Next save
        # will rewrite it cleanly.
        return None
    return loaded


async def _async_send_notification(
    hass: HomeAssistant,
    service: str,
    message: str,
    context: Any,
    tag: str,
) -> None:
    """Dispatch the notify.* call. Failures log + swallow.

    Best-effort: a notify-service failure must not abort
    the STSC reconcile (state has already been saved).
    Log the error so the user can diagnose; let the next
    tick try again.
    """
    try:
        domain, name = parse_notification_service(service)
    except ValueError as err:
        _LOGGER.warning("%s notify-service parse failed: %s", tag, err)
        return
    try:
        await hass.services.async_call(
            domain,
            name,
            {"message": message},
            context=context,
            blocking=False,
        )
    except Exception as err:  # noqa: BLE001
        # Notification dispatch is best-effort; log loud,
        # don't propagate.
        _LOGGER.warning(
            "%s notification dispatch via %s failed: %s",
            tag,
            service,
            err,
        )


# --------------------------------------------------------
# Periodic timer + recovery kick
# --------------------------------------------------------


def _ensure_timer(
    hass: HomeAssistant,
    entry: ConfigEntry,
    state: StscInstanceState,
) -> None:
    """Arm the periodic minute-tick timer if not yet armed.

    The interval is fixed (``_PERIODIC_INTERVAL`` = 1
    minute); no blueprint input controls it, so re-arming
    on interval change is moot. First call arms; subsequent
    calls within the same instance lifetime are no-ops.
    """
    if state.cancel_timer is not None:
        return
    state.cancel_timer = schedule_periodic_with_jitter(
        hass,
        entry,
        interval=_PERIODIC_INTERVAL,
        instance_id=state.instance_id,
        action=_make_periodic_callback(hass, state.instance_id),
    )


def _make_periodic_callback(
    hass: HomeAssistant,
    instance_id: str,
) -> Callable[[datetime], Any]:
    async def _on_tick(_now: datetime) -> None:
        # Drop the tick silently if the instance has been
        # removed between scheduling and firing.
        if instance_id not in _instances(hass):
            return
        # Override variables are flat (NOT under ``trigger.*``)
        # because HA's automation.trigger service
        # unconditionally clobbers the ``trigger`` key with
        # ``{"platform": None}``. The blueprint reads
        # ``trigger_id`` + ``trigger_entity`` directly.
        await hass.services.async_call(
            "automation",
            "trigger",
            {
                "entity_id": instance_id,
                "skip_condition": True,
                "variables": {
                    "trigger_id": "periodic",
                    "trigger_entity": _TIMER_TRIGGER_ENTITY,
                },
            },
        )

    return _on_tick


async def _async_kick_for_recovery(
    hass: HomeAssistant,
    entity_id: str,
) -> None:
    """Fire a manual scan so the instance bootstraps its timer.

    Override variables are flat (NOT under ``trigger.*``);
    see ``_make_periodic_callback`` for the full reasoning.
    The blueprint's reactive triggers don't carry
    ``trigger_id`` / ``trigger_entity`` defaults from the
    integration; the kick supplies sensible fallbacks so
    the controller's event-type determination has the
    "timer" sentinel.
    """
    await hass.services.async_call(
        "automation",
        "trigger",
        {
            "entity_id": entity_id,
            "skip_condition": True,
            "variables": {
                "trigger_id": "manual",
                "trigger_entity": _TIMER_TRIGGER_ENTITY,
            },
        },
    )


# --------------------------------------------------------
# Lifecycle mutators
# --------------------------------------------------------


@callback  # type: ignore[untyped-decorator]
def _on_reload(hass: HomeAssistant) -> None:
    """Cancel timers; per-instance state survives reload."""
    for s in list(_instances(hass).values()):
        if s.cancel_timer is not None:
            s.cancel_timer()
            s.cancel_timer = None


@callback  # type: ignore[untyped-decorator]
def _on_entity_remove(hass: HomeAssistant, entity_id: str) -> None:
    s = _instances(hass).pop(entity_id, None)
    if s is not None and s.cancel_timer is not None:
        s.cancel_timer()
        _LOGGER.info(
            "[%s] dropped %s (automation removed)",
            _SERVICE_TAG,
            entity_id,
        )


@callback  # type: ignore[untyped-decorator]
def _on_entity_rename(
    hass: HomeAssistant,
    old_id: str,
    new_id: str,
) -> None:
    s = _instances(hass).pop(old_id, None)
    if s is not None:
        s.instance_id = new_id
        _instances(hass)[new_id] = s


@callback  # type: ignore[untyped-decorator]
def _on_teardown(hass: HomeAssistant) -> None:
    for s in list(_instances(hass).values()):
        if s.cancel_timer is not None:
            s.cancel_timer()
    _instances(hass).clear()


# --------------------------------------------------------
# Spec + register / unregister
# --------------------------------------------------------


_SPEC = BlueprintHandlerSpec(
    service=_SERVICE,
    service_tag=_SERVICE_TAG,
    service_name=_SERVICE_NAME,
    blueprint_path=BLUEPRINT_PATH,
    service_handler=_async_entrypoint,
    kick=_async_kick_for_recovery,
    on_reload=_on_reload,
    on_entity_remove=_on_entity_remove,
    on_entity_rename=_on_entity_rename,
    on_teardown=_on_teardown,
)


async def async_register(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Register STSC's service + lifecycle via the shared helper."""
    await register_blueprint_handler(hass, entry, _SPEC)


async def async_unregister(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Tear down STSC's service + lifecycle via the shared helper."""
    await unregister_blueprint_handler(hass, entry, _SPEC)


__all__ = [
    "BLUEPRINT_PATH",
    "StscInstanceState",
    "async_register",
    "async_unregister",
]
