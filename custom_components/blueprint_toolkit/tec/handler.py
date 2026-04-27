# This is AI generated code
"""HA wiring for trigger_entity_controller.

Three-layer dispatch:

1. **Entrypoint** -- the function ``hass.services.async_register``
   wires up. Receives the raw ``ServiceCall``; sole
   responsibility is to hand off to argparse. Owns
   ``blueprint_mismatch`` notifications (currently a
   no-op since vol.Schema covers that surface --
   see comment in ``_async_argparse``).

2. **Argparse** -- runs the vol.Schema, then the
   cross-field + HA-state checks (entity existence,
   notify-service existence, no overlapping sets).
   Accumulates errors and emits a single
   ``persistent_notification`` config_error per
   automation instance (matching the pyscript model:
   one notification per instance, dismissed on
   subsequent successful argparse). On success builds
   a ``logic.Config`` and hands off to the service
   layer.

3. **Service layer** -- reads HA state to populate
   ``logic.Inputs`` (current state of trigger /
   controlled / disabling entities, sun-based
   day/night gate, friendly names, persisted
   ``auto_off_at``), calls ``logic.evaluate``,
   applies the result (turn_on/turn_off propagating
   ``call.context``, schedule/cancel ``async_call_later``
   for auto-off, send notification through
   user-configured notify service).

Discovery + restart recovery: the integration walks
``hass.data[DATA_COMPONENT].entities`` (filtered by
``referenced_blueprint``) at HA-started time and kicks
each automation with a synthetic TIMER variables
payload via ``automation.trigger`` -- the catch-up
branch in ``logic._handle_timer`` then arms the timer
based on current observable state. Live updates
combine ``EVENT_AUTOMATION_RELOADED`` (rescan signal,
no payload -- see HA core's ``automation`` integration)
and ``EVENT_ENTITY_REGISTRY_UPDATED`` (delete /
rename signal).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import (
    Context,
    HomeAssistant,
    ServiceCall,
    callback,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from ..const import DOMAIN
from ..helpers import (
    BlueprintHandlerSpec,
    emit_config_error,
    format_notification,
    register_blueprint_handler,
    unregister_blueprint_handler,
    update_instance_state,
)
from . import logic

_LOGGER = logging.getLogger(__name__)

# Service identifiers (see ``..helpers`` for naming
# convention). ``_SERVICE`` is the slug used to register
# the HA service and as the bucket key under
# ``hass.data[DOMAIN]``; ``_SERVICE_TAG`` is the short
# tag used in notification titles + per-event log lines;
# ``_SERVICE_NAME`` is the human-readable name used in
# the one-time registration log.
_SERVICE = "trigger_entity_controller"
_SERVICE_TAG = "TEC"
_SERVICE_NAME = "Trigger Entity Controller"
BLUEPRINT_PATH = "blueprint_toolkit/trigger_entity_controller.yaml"

# The variable payload the integration synthesises when
# re-firing an automation for an auto-off wakeup. The
# blueprint's action: passes ``{{ trigger.entity_id }}``
# through; the service handler uses the ``"timer"``
# sentinel (matching the pyscript wrapper's TIMER event
# classification) to reach the catch-up / expiration
# branch in ``logic._handle_timer``.
_TIMER_TRIGGER_ENTITY_ID = "timer"


# --------------------------------------------------------
# Per-instance in-memory state
# --------------------------------------------------------


@dataclass
class TecInstanceState:
    """In-memory state for one TEC automation instance.

    No persistence -- restart recovery rebuilds via
    ``logic._handle_timer``'s catch-up branch off the
    live HA state. Diagnostic state visible to the
    user (``last_event`` / ``last_action`` / etc.) is
    surfaced via ``helpers.update_instance_state`` at
    every evaluate, not stashed here.
    """

    instance_id: str
    auto_off_at: datetime | None = None
    cancel_wakeup: Callable[[], None] | None = field(default=None, repr=False)


# --------------------------------------------------------
# Service-call schema (vol.Schema)
# --------------------------------------------------------
#
# Wire format mirrors the pyscript entrypoint's accepted
# kwargs (see ``pyscript/blueprint_toolkit.py``'s
# ``trigger_entity_controller_blueprint_argparse``). The
# schema covers field-shape validation only; cross-field
# rules (no overlapping entity sets) and HA-state
# validation (entity exists in hass.states, notification
# service is registered) live in ``_async_argparse``.

# Derived from the logic-side enums so the schema's
# accepted values can never drift from what the
# decision tree understands.
_PERIOD_VALUES = tuple(p.value for p in logic.Period)
_NOTIF_EVENT_VALUES = tuple(e.value for e in logic.NotificationEvent)

_SCHEMA = vol.Schema(
    {
        vol.Required("instance_id"): cv.entity_id,
        vol.Required("controlled_entities_raw"): vol.All(
            cv.ensure_list, [cv.entity_id]
        ),
        vol.Required("trigger_entity_id"): vol.Coerce(str),
        vol.Required("trigger_to_state"): vol.Coerce(str),
        vol.Required("auto_off_minutes_raw"): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=60)
        ),
        vol.Required("auto_off_disabling_entities_raw"): vol.All(
            cv.ensure_list, [cv.entity_id]
        ),
        vol.Required("trigger_entities_raw"): vol.All(
            cv.ensure_list, [cv.entity_id]
        ),
        vol.Required("trigger_period_raw"): vol.In(_PERIOD_VALUES),
        vol.Required("trigger_forces_on_raw"): cv.boolean,
        vol.Required("trigger_disabling_entities_raw"): vol.All(
            cv.ensure_list, [cv.entity_id]
        ),
        vol.Required("trigger_disabling_period_raw"): vol.In(_PERIOD_VALUES),
        vol.Required("notification_service"): vol.Coerce(str),
        vol.Required("notification_prefix_raw"): vol.Coerce(str),
        vol.Required("notification_suffix_raw"): vol.Coerce(str),
        vol.Required("notification_events_raw"): vol.All(
            cv.ensure_list, [vol.In(_NOTIF_EVENT_VALUES)]
        ),
        vol.Required("debug_logging_raw"): cv.boolean,
    },
    # We do NOT pass schema= to async_register, so HA
    # doesn't reject the call before our handler runs --
    # that lets us emit a persistent_notification on
    # config errors rather than just a log line, matching
    # the pyscript model's user-visible config_error UX.
    # Extra keys are tolerated for forward-compat.
    extra=vol.ALLOW_EXTRA,
)


# --------------------------------------------------------
# hass.data accessors
# --------------------------------------------------------


def _instances(hass: HomeAssistant) -> dict[str, TecInstanceState]:
    """Per-instance state map under our service's bucket.

    The shared ``register_blueprint_handler`` creates
    ``hass.data[DOMAIN][_SERVICE]`` with the unsubscribe
    keys; we lazily add our ``instances`` map under the
    same bucket so config-entry reloads don't drop
    diagnostic state.
    """
    bucket = hass.data.setdefault(DOMAIN, {}).setdefault(
        _SERVICE,
        {},
    )
    instances: dict[str, TecInstanceState] = bucket.setdefault(
        "instances",
        {},
    )
    return instances


# --------------------------------------------------------
# Layer 1: entrypoint (registered with hass.services)
# --------------------------------------------------------


async def _async_entrypoint(hass: HomeAssistant, call: ServiceCall) -> None:
    """Service handler -- thin wrapper, hands off to argparse.

    The pyscript wrapper's entrypoint has a
    blueprint-mismatch notification path; vol.Schema
    fulfils the same role here (missing/invalid keys
    surface via ``vol.Invalid`` and become a
    config_error notification through argparse).
    Unexpected keys are tolerated (see ``extra=vol.ALLOW_EXTRA``
    on ``_SCHEMA``) for forward-compat with future
    blueprint inputs.
    """
    await _async_argparse(hass, call)


# --------------------------------------------------------
# Layer 2: argparse (vol.Schema + cross-field + state)
# --------------------------------------------------------


def _parse_notification_service(service: str) -> tuple[str, str]:
    """Split a notify-service string into ``(domain, name)``.

    Accepts both ``notify.foo`` (full ``domain.service``)
    and the bare ``foo`` short form, defaulting to the
    ``notify`` domain.
    """
    if "." in service:
        domain, name = service.split(".", 1)
        return domain, name
    return "notify", service


def _instance_id_for_error(raw_data: dict[str, Any]) -> str:
    """Best-effort extraction of instance_id for a config error.

    If schema validation failed and the call genuinely
    has no instance_id, fall back to a sentinel so the
    notification ID doesn't collide with a real one.
    """
    candidate = raw_data.get("instance_id")
    if isinstance(candidate, str) and candidate:
        return candidate
    return "unknown"


async def _emit(
    hass: HomeAssistant,
    instance_id: str,
    errors: list[str],
) -> None:
    """Dispatch a TEC config-error spec via the shared helper.

    Wraps ``emit_config_error`` with our subsystem
    constants so the call sites stay short. Empty
    ``errors`` dismisses any prior config-error
    notification for this instance, so this can be
    called unconditionally on every successful argparse.
    """
    await emit_config_error(
        hass,
        service=_SERVICE,
        service_tag=_SERVICE_TAG,
        instance_id=instance_id,
        errors=errors,
    )


async def _async_argparse(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Validate the call, build a Config, dispatch to the service layer."""
    raw = dict(call.data)

    # --- Schema validation (field shape / type / range) ---
    # vol.MultipleInvalid carries all collected errors in
    # ``.errors``; ``str()`` on it returns only the first,
    # which would hide every key after the first failure.
    # Iterate so the user sees every problem at once.
    try:
        data = _SCHEMA(raw)
    except vol.MultipleInvalid as err:
        await _emit(
            hass,
            _instance_id_for_error(raw),
            [f"schema: {sub}" for sub in err.errors],
        )
        return
    except vol.Invalid as err:
        await _emit(
            hass,
            _instance_id_for_error(raw),
            [f"schema: {err}"],
        )
        return

    instance_id: str = data["instance_id"]
    errors: list[str] = []

    # --- Cross-field: no overlapping entity sets ---
    ctrl = set(data["controlled_entities_raw"])
    trig = set(data["trigger_entities_raw"])
    auto_dis = set(data["auto_off_disabling_entities_raw"])
    trig_dis = set(data["trigger_disabling_entities_raw"])
    all_dis = auto_dis | trig_dis
    for eid in sorted(ctrl & trig):
        errors.append(f"{eid} is in both controlled and trigger entities")
    for eid in sorted(ctrl & all_dis):
        errors.append(f"{eid} is in both controlled and disabling entities")
    for eid in sorted(trig & all_dis):
        errors.append(f"{eid} is in both trigger and disabling entities")

    # --- HA state: entities exist ---
    for eid in sorted(ctrl | trig | all_dis):
        if hass.states.get(eid) is None:
            errors.append(f"entity {eid} does not exist")

    # --- HA state: notification service exists ---
    notif = data["notification_service"]
    if notif:
        notif_domain, notif_name = _parse_notification_service(notif)
        if not hass.services.has_service(notif_domain, notif_name):
            errors.append(
                f"notification service {notif} is not registered",
            )

    # --- HA state: sun.sun must exist for time-of-day periods ---
    # Without it, ``_is_day_time()`` falls back to False and any
    # ``day-time`` configuration silently never fires; the
    # ``night-time`` configuration silently always fires. Surface
    # this loudly at config time instead of debugging a
    # never-firing automation.
    needs_sun = (
        data["trigger_period_raw"] != "always"
        or data["trigger_disabling_period_raw"] != "always"
    )
    if needs_sun and hass.states.get("sun.sun") is None:
        errors.append(
            "sun.sun entity is not available; required when "
            "trigger_period or trigger_disabling_period is set "
            "to a non-'always' value",
        )

    # Emit unconditionally: empty ``errors`` dismisses any
    # prior config-error notification for this instance.
    await _emit(hass, instance_id, errors)
    if errors:
        return

    # --- Build the Config dataclass logic.evaluate expects ---
    config = logic.Config(
        controlled_entities=list(data["controlled_entities_raw"]),
        auto_off_minutes=data["auto_off_minutes_raw"],
        auto_off_disabling_entities=list(
            data["auto_off_disabling_entities_raw"]
        ),
        trigger_entities=list(data["trigger_entities_raw"]),
        trigger_period=logic.parse_period(data["trigger_period_raw"]),
        trigger_forces_on=data["trigger_forces_on_raw"],
        trigger_disabling_entities=list(data["trigger_disabling_entities_raw"]),
        trigger_disabling_period=logic.parse_period(
            data["trigger_disabling_period_raw"]
        ),
        notification_prefix=data["notification_prefix_raw"],
        notification_suffix=data["notification_suffix_raw"],
        notification_events=logic.parse_notification_events(
            data["notification_events_raw"]
        ),
    )

    await _async_service_layer(
        hass,
        call.context,
        config,
        instance_id=instance_id,
        trigger_entity_id=data["trigger_entity_id"],
        trigger_to_state=data["trigger_to_state"],
        notification_service=data["notification_service"],
        debug_logging=data["debug_logging_raw"],
    )


# --------------------------------------------------------
# Layer 3: service (HA state -> Inputs, evaluate, apply)
# --------------------------------------------------------


def _any_on(hass: HomeAssistant, entities: list[str]) -> bool:
    return any(
        (s := hass.states.get(eid)) is not None and s.state == "on"
        for eid in entities
    )


def _friendly_names(hass: HomeAssistant, entities: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for eid in entities:
        state = hass.states.get(eid)
        if state is None:
            continue
        name = state.attributes.get("friendly_name") or ""
        if name:
            out[eid] = name
    return out


def _is_day_time(hass: HomeAssistant) -> bool:
    sun = hass.states.get("sun.sun")
    return sun is not None and sun.state == "above_horizon"


async def _async_service_layer(
    hass: HomeAssistant,
    context: Context,
    config: logic.Config,
    *,
    instance_id: str,
    trigger_entity_id: str,
    trigger_to_state: str,
    notification_service: str,
    debug_logging: bool,
) -> None:
    """Read HA state, build Inputs, evaluate, apply Result."""
    state = _instances(hass).setdefault(
        instance_id,
        TecInstanceState(instance_id=instance_id),
    )

    now = dt_util.now()

    all_disabling = (
        config.trigger_disabling_entities + config.auto_off_disabling_entities
    )
    event_type = logic.determine_event_type(
        trigger_entity_id,
        trigger_to_state,
        config.trigger_entities,
        config.controlled_entities,
        all_disabling,
    )
    if event_type is None:
        return

    inputs = logic.Inputs(
        current_time=now,
        event_type=event_type,
        changed_entity=trigger_entity_id,
        triggers_on=_any_on(hass, config.trigger_entities),
        controlled_on=_any_on(hass, config.controlled_entities),
        is_day_time=_is_day_time(hass),
        triggers_disabled=_any_on(hass, config.trigger_disabling_entities),
        auto_off_disabled=_any_on(hass, config.auto_off_disabling_entities),
        auto_off_at=state.auto_off_at,
        friendly_names=_friendly_names(hass, config.controlled_entities),
    )

    result = logic.evaluate(config, inputs)

    # --- Surface diagnostic state for the user ---
    update_instance_state(
        hass,
        service=_SERVICE,
        instance_id=instance_id,
        last_event=event_type.name,
        last_action=result.action.name,
        last_run=now,
        last_reason=result.reason or "",
        auto_off_at=result.auto_off_at,
    )

    # --- Apply: turn_on/off (context propagated for logbook) ---
    if result.action == logic.ActionType.TURN_ON and result.target_entities:
        await hass.services.async_call(
            "homeassistant",
            "turn_on",
            {"entity_id": result.target_entities},
            context=context,
            blocking=False,
        )
    elif result.action == logic.ActionType.TURN_OFF and result.target_entities:
        await hass.services.async_call(
            "homeassistant",
            "turn_off",
            {"entity_id": result.target_entities},
            context=context,
            blocking=False,
        )

    # --- Apply: scheduling auto_off_at (cancel previous) ---
    _apply_auto_off_at(hass, state, result.auto_off_at)

    # --- Apply: notification (best-effort, never raises) ---
    if notification_service and result.notification:
        await _send_notification(
            hass,
            notification_service,
            result.notification,
            context,
        )

    if debug_logging:
        _LOGGER.warning(
            "[%s: %s] event=%s action=%s reason=%r"
            " auto_off_at=%s triggers_on=%s controlled_on=%s"
            " is_day_time=%s",
            _SERVICE_TAG,
            instance_id,
            event_type.name,
            result.action.name,
            result.reason,
            (
                result.auto_off_at.isoformat()
                if result.auto_off_at is not None
                else "none"
            ),
            inputs.triggers_on,
            inputs.controlled_on,
            inputs.is_day_time,
        )


async def _send_notification(
    hass: HomeAssistant,
    service: str,
    message: str,
    context: Context,
) -> None:
    """Dispatch a finding-style notification via the user's notify.* service.

    Argparse already validated that ``service`` is
    registered, and ``blocking=False`` returns before the
    notify handler runs, so the only thing that could
    raise here is a TOCTOU window where the service
    deregistered between argparse and dispatch. We let
    that propagate -- a loud failure in HA's logbook is
    more useful than a silent miss.
    """
    domain, name = _parse_notification_service(service)
    await hass.services.async_call(
        domain,
        name,
        {"message": message},
        context=context,
        blocking=False,
    )


# --------------------------------------------------------
# Auto-off scheduling (async_call_later + automation.trigger)
# --------------------------------------------------------


def _apply_auto_off_at(
    hass: HomeAssistant,
    state: TecInstanceState,
    auto_off_at: datetime | None,
) -> None:
    """Update state.auto_off_at and (re)schedule the wakeup.

    Always cancels any prior pending wakeup before
    arming a new one or clearing.
    """
    if state.cancel_wakeup is not None:
        state.cancel_wakeup()
        state.cancel_wakeup = None
    state.auto_off_at = auto_off_at
    if auto_off_at is None:
        return
    delay = max(0.0, (auto_off_at - dt_util.now()).total_seconds())
    state.cancel_wakeup = async_call_later(
        hass,
        delay,
        _make_wakeup(hass, state.instance_id),
    )


def _make_wakeup(
    hass: HomeAssistant,
    instance_id: str,
) -> Callable[[datetime], Awaitable[None]]:
    """Build the closure async_call_later will fire at the auto-off time."""

    async def _on_wakeup(_now: datetime) -> None:
        s = _instances(hass).get(instance_id)
        if s is None:
            return
        s.cancel_wakeup = None
        # Re-fire the automation with synthetic TIMER
        # variables. HA records a fresh per-automation
        # context for this run, so any downstream
        # turn_off propagates that context and the
        # logbook attributes the action to this specific
        # automation rather than to "blueprint_toolkit".
        await hass.services.async_call(
            "automation",
            "trigger",
            {
                "entity_id": instance_id,
                "skip_condition": True,
                "variables": {
                    "trigger": {
                        "entity_id": _TIMER_TRIGGER_ENTITY_ID,
                        "to_state": {"state": ""},
                    },
                },
            },
        )

    return _on_wakeup


# --------------------------------------------------------
# Restart-recovery kick + per-port lifecycle mutators
# --------------------------------------------------------
#
# These small ``@callback`` functions feed into
# ``_SPEC`` below; the shared
# ``register_blueprint_handler`` wires them up to
# EVENT_AUTOMATION_RELOADED, EVENT_ENTITY_REGISTRY_UPDATED,
# and the HA-started recovery scheduler.


async def _async_kick_for_recovery(
    hass: HomeAssistant,
    entity_id: str,
) -> None:
    """Fire one TIMER event so the catch-up branch arms its timer.

    The variables payload is TEC-specific: synthetic
    ``trigger.entity_id == "timer"`` reaches the
    catch-up / expiration branch in
    ``logic._handle_timer``.
    """
    await hass.services.async_call(
        "automation",
        "trigger",
        {
            "entity_id": entity_id,
            "skip_condition": True,
            "variables": {
                "trigger": {
                    "entity_id": _TIMER_TRIGGER_ENTITY_ID,
                    "to_state": {"state": ""},
                },
            },
        },
    )


@callback  # type: ignore[untyped-decorator]
def _on_reload(hass: HomeAssistant) -> None:
    """Cancel pending wakeups whose AutomationEntity was replaced.

    Called on EVENT_AUTOMATION_RELOADED. Don't drop
    instance state -- entity_ids survive reload and we
    want to preserve diagnostic last_action / last_reason
    between events. The auto_off_at field gets
    re-derived by the catch-up kick the shared listener
    runs after this returns.
    """
    for s in list(_instances(hass).values()):
        if s.cancel_wakeup is not None:
            s.cancel_wakeup()
            s.cancel_wakeup = None


@callback  # type: ignore[untyped-decorator]
def _on_entity_remove(hass: HomeAssistant, entity_id: str) -> None:
    """Drop tracked state when our automation is removed."""
    s = _instances(hass).pop(entity_id, None)
    if s is not None and s.cancel_wakeup is not None:
        s.cancel_wakeup()
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
    """Move tracked state to the new entity_id on rename."""
    s = _instances(hass).pop(old_id, None)
    if s is not None:
        s.instance_id = new_id
        _instances(hass)[new_id] = s


@callback  # type: ignore[untyped-decorator]
def _on_teardown(hass: HomeAssistant) -> None:
    """Cancel all pending wakeups and drop the instance map."""
    for s in list(_instances(hass).values()):
        if s.cancel_wakeup is not None:
            s.cancel_wakeup()
    _instances(hass).clear()


# --------------------------------------------------------
# Spec + registration / teardown
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


async def async_register(hass: HomeAssistant, _entry: ConfigEntry) -> None:
    """Register TEC's service + lifecycle via the shared helper."""
    await register_blueprint_handler(hass, _SPEC)


async def async_unregister(hass: HomeAssistant) -> None:
    """Tear down TEC's service + lifecycle via the shared helper."""
    await unregister_blueprint_handler(hass, _SPEC)


# Expose helper for test imports.
__all__ = [
    "BLUEPRINT_PATH",
    "TecInstanceState",
    "async_register",
    "async_unregister",
    "format_notification",  # re-export for callers that want it
]
