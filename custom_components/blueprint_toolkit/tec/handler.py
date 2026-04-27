# This is AI generated code
# mypy: ignore-errors
# (Prototype port. Strict mypy on hass.data dynamic
# dicts and HA's @callback decorator stays out until
# the prototype graduates.)
"""Native HA wiring for trigger_entity_controller.

Three-layer dispatch matching the existing pyscript
convention:

1. **Entrypoint** -- the function ``hass.services.async_register``
   wires up. Receives the raw ``ServiceCall``; sole
   responsibility is to hand off to argparse. Owns
   ``blueprint_mismatch`` notifications (currently a
   no-op since vol.Schema covers that surface for
   native -- see comment in ``_async_argparse``).

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
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import voluptuous as vol
from homeassistant.components.automation import (
    DATA_COMPONENT,
    EVENT_AUTOMATION_RELOADED,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import (
    Context,
    Event,
    HomeAssistant,
    ServiceCall,
    callback,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from ..const import DOMAIN
from . import logic
from .helpers import format_notification

_LOGGER = logging.getLogger(__name__)

SERVICE_NAME = "trigger_entity_controller"
NATIVE_BLUEPRINT_PATH = (
    "blueprint_toolkit/trigger_entity_controller_native.yaml"
)

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
    live HA state. The fields after ``auto_off_at`` are
    diagnostic-only (mirror the attributes the pyscript
    wrapper writes to ``pyscript.automation_<slug>_state``).
    """

    instance_id: str
    auto_off_at: datetime | None = None
    cancel_wakeup: Callable[[], None] | None = field(default=None, repr=False)
    last_event: str = "NONE"
    last_action: str = "NONE"
    last_reason: str = ""
    last_run: datetime | None = None


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

_PERIOD_VALUES = ("always", "night-time", "day-time")
_NOTIF_EVENT_VALUES = ("triggered-on", "forced-on", "auto-off")

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


def _bucket(hass: HomeAssistant) -> dict[str, Any]:
    """Return our slot under ``hass.data[DOMAIN]['tec_native']``.

    Created lazily; idempotent so config-entry reloads
    don't lose pending wakeup handles or instance state.
    """
    return hass.data.setdefault(DOMAIN, {}).setdefault(
        "tec_native",
        {
            "instances": {},
            "unsub_reload": None,
            "unsub_er": None,
        },
    )


def _instances(hass: HomeAssistant) -> dict[str, TecInstanceState]:
    return _bucket(hass)["instances"]


# --------------------------------------------------------
# Layer 1: entrypoint (registered with hass.services)
# --------------------------------------------------------


async def _async_entrypoint(hass: HomeAssistant, call: ServiceCall) -> None:
    """Service handler -- thin wrapper, hands off to argparse.

    The pyscript wrapper's entrypoint has a
    blueprint-mismatch notification path; vol.Schema
    fulfils the same role here (extra/missing keys
    surface via ``vol.Invalid`` and become a
    config_error notification through argparse).
    """
    await _async_argparse(hass, call)


# --------------------------------------------------------
# Layer 2: argparse (vol.Schema + cross-field + state)
# --------------------------------------------------------


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


def _config_error_notification_id(instance_id: str) -> str:
    return f"blueprint_toolkit_tec__{instance_id}__config_error"


async def _emit_config_error(
    hass: HomeAssistant,
    instance_id: str,
    errors: list[str],
) -> None:
    """Surface argparse failures as a persistent notification.

    Matches the pyscript model: one notification per
    instance (deterministic notification_id), so a fresh
    failure overwrites prior content and a successful
    invocation can dismiss it.
    """
    notif_id = _config_error_notification_id(instance_id)
    title = f"Blueprint Toolkit -- TEC config error: {instance_id}"
    body_lines = [f"- {e}" for e in errors] or ["(no details)"]
    message = "\n".join(body_lines)
    await hass.services.async_call(
        "persistent_notification",
        "create",
        {
            "notification_id": notif_id,
            "title": title,
            "message": message,
        },
    )
    _LOGGER.warning(
        "TEC native: config error for %s: %s",
        instance_id,
        "; ".join(errors),
    )


async def _dismiss_config_error(
    hass: HomeAssistant,
    instance_id: str,
) -> None:
    """Dismiss a previously-emitted config_error notification.

    The persistent_notification.dismiss service is a
    no-op when the notification doesn't exist, so we can
    fire it unconditionally on every successful argparse
    without checking first.
    """
    notif_id = _config_error_notification_id(instance_id)
    await hass.services.async_call(
        "persistent_notification",
        "dismiss",
        {"notification_id": notif_id},
    )


async def _async_argparse(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Validate the call, build a Config, dispatch to the service layer."""
    raw = dict(call.data)

    # --- Schema validation (field shape / type / range) ---
    try:
        data = _SCHEMA(raw)
    except vol.Invalid as err:
        await _emit_config_error(
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
        if "." in notif:
            notif_domain, notif_name = notif.split(".", 1)
        else:
            notif_domain, notif_name = "notify", notif
        if not hass.services.has_service(notif_domain, notif_name):
            errors.append(
                f"notification service {notif} is not registered",
            )

    if errors:
        await _emit_config_error(hass, instance_id, errors)
        return

    await _dismiss_config_error(hass, instance_id)

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
    state.last_run = now

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
    state.last_event = event_type.name

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
    state.last_action = result.action.name
    state.last_reason = result.reason or ""

    # --- Apply: turn_on/off (context propagated for logbook) ---
    if result.action == logic.ActionType.TURN_ON:
        await _do_call(
            hass,
            "homeassistant",
            "turn_on",
            result.target_entities,
            context,
        )
    elif result.action == logic.ActionType.TURN_OFF:
        await _do_call(
            hass,
            "homeassistant",
            "turn_off",
            result.target_entities,
            context,
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
            "[TEC native: %s] event=%s action=%s reason=%r"
            " auto_off_at=%s triggers_on=%s controlled_on=%s"
            " is_day_time=%s",
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


async def _do_call(
    hass: HomeAssistant,
    domain: str,
    service: str,
    entities: list[str],
    context: Context,
) -> None:
    if not entities:
        return
    await hass.services.async_call(
        domain,
        service,
        {"entity_id": entities},
        context=context,
        blocking=False,
    )


async def _send_notification(
    hass: HomeAssistant,
    service: str,
    message: str,
    context: Context,
) -> None:
    """Dispatch a finding-style notification via the user's notify.* service.

    Failures (e.g., the notify integration is briefly
    down) are swallowed -- the side effect on the
    controlled entity has already happened, dropping
    state for a missed user-facing message would be
    worse than a silent miss.
    """
    if "." in service:
        domain, name = service.split(".", 1)
    else:
        domain, name = "notify", service
    try:
        await hass.services.async_call(
            domain,
            name,
            {"message": message},
            context=context,
            blocking=False,
        )
    except Exception as e:  # noqa: BLE001
        _LOGGER.warning(
            "TEC native: notification via %s failed: %s",
            service,
            e,
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
) -> Callable[[datetime], Any]:
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
# Discovery + restart recovery
# --------------------------------------------------------


def _discover_automations(hass: HomeAssistant) -> list[str]:
    """Return entity_ids of automations using our native blueprint.

    Uses ``hass.data[DATA_COMPONENT].entities`` and the
    public ``BaseAutomationEntity.referenced_blueprint``
    property (HA core's
    ``homeassistant/components/automation/__init__.py``).
    """
    component = hass.data.get(DATA_COMPONENT)
    if component is None:
        return []
    out: list[str] = []
    for ent in component.entities:
        ref = getattr(ent, "referenced_blueprint", None)
        if ref == NATIVE_BLUEPRINT_PATH:
            out.append(ent.entity_id)
    return out


async def _async_kick_for_recovery(
    hass: HomeAssistant,
    entity_id: str,
) -> None:
    """Fire one TIMER event so the catch-up branch arms its timer."""
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


async def _async_recover_at_startup(hass: HomeAssistant) -> None:
    discovered = _discover_automations(hass)
    if not discovered:
        _LOGGER.info(
            "TEC native: no automations using %s discovered at startup",
            NATIVE_BLUEPRINT_PATH,
        )
        return
    _LOGGER.info(
        "TEC native: kicking %d discovered automations for catch-up",
        len(discovered),
    )
    for entity_id in discovered:
        await _async_kick_for_recovery(hass, entity_id)


# --------------------------------------------------------
# Live add/remove subscriptions
# --------------------------------------------------------


@callback
def _on_automation_reloaded(hass: HomeAssistant, _event: Event) -> None:
    """Rescan + reconcile after any automation config change.

    ``EVENT_AUTOMATION_RELOADED`` carries no payload by
    design -- we can't tell which automation changed.
    Cancel pending wakeups for instances we knew about
    (the old AutomationEntity objects have been replaced)
    and let the catch-up kick re-arm what's still needed.
    """
    instances = _instances(hass)
    for s in list(instances.values()):
        if s.cancel_wakeup is not None:
            s.cancel_wakeup()
            s.cancel_wakeup = None
    # Don't drop instance state -- entity_ids survive
    # reload, and we want to preserve diagnostic
    # last_action / last_reason between events. The
    # auto_off_at field gets re-derived by the catch-up
    # kick.
    hass.async_create_task(_async_recover_at_startup(hass))


@callback
def _on_entity_registry_updated(hass: HomeAssistant, event: Event) -> None:
    """Drop tracked state when our automation is removed or renamed."""
    data = event.data
    action = data.get("action")
    new_id = data.get("entity_id") or ""
    old_id = data.get("old_entity_id") or new_id
    if not (
        new_id.startswith("automation.") or old_id.startswith("automation.")
    ):
        return
    instances = _instances(hass)
    if action == "remove":
        s = instances.pop(old_id, None)
        if s is not None and s.cancel_wakeup is not None:
            s.cancel_wakeup()
            _LOGGER.info(
                "TEC native: dropped %s (automation removed)",
                old_id,
            )
    elif action == "update" and old_id != new_id:
        s = instances.pop(old_id, None)
        if s is not None:
            s.instance_id = new_id
            instances[new_id] = s


# --------------------------------------------------------
# Registration / teardown
# --------------------------------------------------------


async def async_register(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Wire up the native TEC service + lifecycle hooks.

    Idempotent under config-entry reload: subsequent
    calls just re-register the service handler and
    refresh the bus subscriptions, leaving in-flight
    wakeups intact.
    """
    bucket = _bucket(hass)

    if hass.services.has_service(DOMAIN, SERVICE_NAME):
        hass.services.async_remove(DOMAIN, SERVICE_NAME)

    async def _service_handler(call: ServiceCall) -> None:
        await _async_entrypoint(hass, call)

    hass.services.async_register(
        DOMAIN,
        SERVICE_NAME,
        _service_handler,
    )

    # Bus subscriptions -- swap any prior handles before
    # re-subscribing so reloads don't accumulate
    # listeners.
    prior = bucket.get("unsub_reload")
    if callable(prior):
        prior()
    bucket["unsub_reload"] = hass.bus.async_listen(
        EVENT_AUTOMATION_RELOADED,
        lambda e: _on_automation_reloaded(hass, e),
    )
    prior = bucket.get("unsub_er")
    if callable(prior):
        prior()
    bucket["unsub_er"] = hass.bus.async_listen(
        er.EVENT_ENTITY_REGISTRY_UPDATED,
        lambda e: _on_entity_registry_updated(hass, e),
    )

    # Restart-recovery: defer the discovery + catch-up
    # kick until HA finishes starting (DATA_COMPONENT
    # may not be populated until then; automations
    # mid-load may not yet be triggerable).
    if hass.is_running:
        hass.async_create_task(_async_recover_at_startup(hass))
    else:

        async def _recover_when_ready(_event: Event) -> None:
            await _async_recover_at_startup(hass)

        hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STARTED,
            _recover_when_ready,
        )

    _LOGGER.info(
        "TEC native: service %s.%s registered (blueprint=%s)",
        DOMAIN,
        SERVICE_NAME,
        NATIVE_BLUEPRINT_PATH,
    )


async def async_unregister(hass: HomeAssistant) -> None:
    """Tear down service + cancel pending wakeups + drop state."""
    bucket = _bucket(hass)
    if hass.services.has_service(DOMAIN, SERVICE_NAME):
        hass.services.async_remove(DOMAIN, SERVICE_NAME)
    for key in ("unsub_reload", "unsub_er"):
        unsub = bucket.get(key)
        if callable(unsub):
            unsub()
            bucket[key] = None
    for s in list(_instances(hass).values()):
        if s.cancel_wakeup is not None:
            s.cancel_wakeup()
    _instances(hass).clear()


# Expose helper for test imports.
__all__ = [
    "NATIVE_BLUEPRINT_PATH",
    "SERVICE_NAME",
    "TecInstanceState",
    "async_register",
    "async_unregister",
    "format_notification",  # re-export for callers that want it
]
