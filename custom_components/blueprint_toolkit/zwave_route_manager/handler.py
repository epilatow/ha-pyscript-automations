# This is AI generated code
"""HA wiring for zwave_route_manager.

Three-layer dispatch (entrypoint / argparse / service)
mirroring the trigger_entity_controller port. ZRM-specific
additions:

- Periodic reconcile timer per instance via
  ``async_track_time_interval`` (replaces the
  ``time_pattern`` blueprint trigger).
- Async bridge calls (no thread offloading; ``socketio`` is
  already async).
- File-mtime change detection for the YAML config so the
  user can edit the config and have the next tick pick it
  up without a restart.
- Bridge-timeout circuit breaker that pauses periodic
  reconciles when the controller is unresponsive; manual
  triggers bypass the breaker so the user can force a
  retry.
- Four persistent-notification categories:
  ``config_error`` (argparse / YAML / resolve errors),
  ``api_unavailable`` (zwave-js-ui not reachable or
  rejecting our APIs), ``apply_*`` (per-node apply
  failures), ``timeout_*`` (per-route pending timeouts).

State persistence: in-memory only. On HA restart, pending
and applied dicts re-derive from the controller's actual
route state on the first reconcile (driven by the
ha_start trigger from the blueprint plus
``recover_at_startup``).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    callback,
)
from homeassistant.helpers import (
    config_validation as cv,
)
from homeassistant.helpers import (
    device_registry as dr,
)
from homeassistant.helpers import (
    entity_registry as er,
)
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from ..const import DOMAIN
from ..helpers import (
    BlueprintHandlerSpec,
    PersistentNotification,
    instance_id_for_config_error,
    make_emit_config_error,
    md_escape,
    process_persistent_notifications,
    register_blueprint_handler,
    spec_bucket,
    unregister_blueprint_handler,
    update_instance_state,
)
from . import bridge, logic

_LOGGER = logging.getLogger(__name__)

_SERVICE = "zwave_route_manager"
_SERVICE_TAG = "ZRM"
_SERVICE_NAME = "Z-Wave Route Manager"
BLUEPRINT_PATH = "blueprint_toolkit/zwave_route_manager.yaml"

# Per-action apply timeouts. Sleepy battery nodes never ACK
# route commands until they wake; use a short
# fire-and-forget window and let the next reconcile
# confirm. Line-powered + FLiRS nodes respond quickly when
# healthy; a timeout there is a real apply failure.
_SLEEPY_APPLY_TIMEOUT = 1.0
_AWAKE_APPLY_TIMEOUT = 15.0

# Per-call socket.io timeout, comfortably above the slowest
# awake-action timeout so the lower-level cancel never beats
# our asyncio.wait_for cancellation.
_BRIDGE_TIMEOUT = 30.0

_DEFAULT_SPEED_VALUES = ("auto", "100k", "40k", "9600")
# trigger_id values supplied by the blueprint:
# - "ha_start" from the homeassistant trigger
# - "periodic" from the integration-owned timer (we set
#   variables={trigger:{id:"periodic"}} when firing
#   automation.trigger from the timer)
# - "manual" default when no trigger is set (dev tools call,
#   restart-recovery kick)
_TRIGGER_VALUES = ("ha_start", "periodic", "manual")


# --------------------------------------------------------
# Per-instance in-memory state
# --------------------------------------------------------


@dataclass
class ZrmInstanceState:
    """In-memory state for one ZRM automation instance.

    Lost on HA restart; re-derived from the controller's
    actual route state on the first reconcile (driven by
    the ha_start blueprint trigger + the integration's
    ``recover_at_startup`` kick).
    """

    instance_id: str
    pending: dict[int, list[logic.RouteRequest]] = field(default_factory=dict)
    applied: dict[int, list[logic.RouteRequest]] = field(default_factory=dict)
    # Cached node_id -> entity_id from the last successful
    # resolve. Used to render the pending/applied dicts in
    # the diagnostic state entity even on bail-out paths
    # (config error, bridge error) where we don't have a
    # fresh ResolvedRoute list.
    node_to_entity: dict[int, str] = field(default_factory=dict)
    # Diagnostic counters carried on the dataclass so the
    # state entity can show them on bail-out paths --
    # ``hass.states.async_set`` replaces all attrs per call,
    # so anything not explicitly written each time would
    # otherwise vanish on a bridge-error or config-error
    # reconcile. Each reconcile path resets only the keys
    # it has fresh information for and leaves the rest
    # alone.
    last_routes_in_config: int = 0
    last_routes_applied: int = 0
    last_routes_pending: int = 0
    last_routes_errored: int = 0
    last_config_errors: int = 0
    last_resolve_errors: int = 0
    last_api_error: str = ""
    last_bridge_error: str = ""
    circuit: logic.CircuitBreakerState = field(
        default_factory=lambda: logic.CircuitBreakerState(
            streak=0, open_until=None
        )
    )
    last_config_mtime: float = 0.0
    last_reconcile_dt: datetime | None = None
    reconcile_pending: bool = False
    # Tracks the interval the timer was last armed with so
    # we can detect blueprint-input changes and re-arm.
    armed_interval_minutes: int = 0
    cancel_timer: Callable[[], None] | None = field(default=None, repr=False)
    # Lock so the integration timer's automation.trigger
    # can't interleave with a manual call to the same
    # instance mid-flight.
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


# --------------------------------------------------------
# Service-call schema (vol.Schema)
# --------------------------------------------------------

_SCHEMA = vol.Schema(
    {
        vol.Required("instance_id"): cv.entity_id,
        vol.Required("trigger_id"): vol.In(_TRIGGER_VALUES),
        vol.Required("config_file_path"): vol.Coerce(str),
        vol.Required("zwave_js_ui_host_raw"): vol.Coerce(str),
        vol.Required("zwave_js_ui_port_raw"): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=65535)
        ),
        vol.Required("zwave_js_ui_token_raw"): vol.Coerce(str),
        vol.Required("clear_unmanaged_routes_raw"): cv.boolean,
        vol.Required("reconcile_interval_minutes_raw"): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=10080)
        ),
        vol.Required("pending_timeout_hours_raw"): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=168)
        ),
        vol.Required("default_route_speed_raw"): vol.In(_DEFAULT_SPEED_VALUES),
        vol.Required("max_notifications_raw"): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=1000)
        ),
        vol.Required("debug_logging_raw"): cv.boolean,
    },
    extra=vol.ALLOW_EXTRA,
)


# --------------------------------------------------------
# Per-instance state accessor
# --------------------------------------------------------


def _instances(hass: HomeAssistant) -> dict[str, ZrmInstanceState]:
    """Per-instance state map under our service's bucket."""
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        return {}
    bucket = spec_bucket(entries[0], _SERVICE)
    instances: dict[str, ZrmInstanceState] = bucket.setdefault(
        "instances",
        {},
    )
    return instances


# --------------------------------------------------------
# Layer 1: entrypoint
# --------------------------------------------------------


async def _async_entrypoint(hass: HomeAssistant, call: ServiceCall) -> None:
    """Service handler -- thin wrapper, hands off to argparse."""
    await _async_argparse(hass, call)


# --------------------------------------------------------
# Layer 2: argparse
# --------------------------------------------------------


# Per-port closure over the shared ``emit_config_error``
# helper. Saves repeating ``service=_SERVICE,
# service_tag=_SERVICE_TAG`` at every call site.
_emit = make_emit_config_error(
    service=_SERVICE,
    service_tag=_SERVICE_TAG,
)


async def _async_argparse(
    hass: HomeAssistant,
    call: ServiceCall,
) -> None:
    """Validate, build context, dispatch to the service layer."""
    raw = dict(call.data)

    try:
        data = _SCHEMA(raw)
    except vol.MultipleInvalid as err:
        await _emit(
            hass,
            instance_id_for_config_error(raw),
            [f"schema: {sub}" for sub in err.errors],
        )
        return
    except vol.Invalid as err:
        await _emit(
            hass,
            instance_id_for_config_error(raw),
            [f"schema: {err}"],
        )
        return

    instance_id: str = data["instance_id"]

    # Default speed: "auto" means None.
    default_speed_raw: str = data["default_route_speed_raw"].strip()
    default_route_speed: bridge.RouteSpeed | None
    if default_speed_raw == "auto":
        default_route_speed = None
    else:
        default_route_speed, speed_err = logic.parse_route_speed_value(
            default_speed_raw,
            "blueprint input: default_route_speed",
        )
        if speed_err is not None:
            await _emit(
                hass,
                instance_id,
                [_format_config_error(speed_err)],
            )
            return

    # Argparse passed; clear any stale config-error
    # notification before handing off to the service layer.
    # The service layer owns its own notification dispatch
    # (api_unavailable, apply_*, timeout_*) plus its own
    # config-error category for YAML / resolve errors.
    await _emit(hass, instance_id, [])

    await _async_service_layer(
        hass,
        call,
        instance_id=instance_id,
        trigger_id=data["trigger_id"],
        config_file_path=data["config_file_path"],
        host=data["zwave_js_ui_host_raw"].strip() or "core-zwave-js",
        port=data["zwave_js_ui_port_raw"],
        token=data["zwave_js_ui_token_raw"].strip(),
        clear_unmanaged_routes=data["clear_unmanaged_routes_raw"],
        reconcile_interval_minutes=data["reconcile_interval_minutes_raw"],
        pending_timeout_hours=data["pending_timeout_hours_raw"],
        default_route_speed=default_route_speed,
        max_notifications=data["max_notifications_raw"],
        debug_logging=data["debug_logging_raw"],
    )


def _format_config_error(err: logic.ConfigError) -> str:
    """Render one logic.ConfigError as a notification bullet."""
    location = md_escape(err.location)
    reason = md_escape(err.reason)
    if err.entity_id:
        ref = md_escape(err.entity_id)
        if err.device_id:
            return (
                f"[`{ref}`](/config/devices/device/{err.device_id})"
                f" (`{location}`): {reason}"
            )
        return f"`{ref}` (`{location}`): {reason}"
    return f"`{location}`: {reason}"


# --------------------------------------------------------
# Layer 3: service layer (async reconcile)
# --------------------------------------------------------


async def _async_service_layer(
    hass: HomeAssistant,
    call: ServiceCall,
    *,
    instance_id: str,
    trigger_id: str,
    config_file_path: str,
    host: str,
    port: int,
    token: str,
    clear_unmanaged_routes: bool,
    reconcile_interval_minutes: int,
    pending_timeout_hours: int,
    default_route_speed: bridge.RouteSpeed | None,
    max_notifications: int,
    debug_logging: bool,
) -> None:
    """Reconcile pass: gate, fetch nodes, plan, apply, notify."""
    started = time.monotonic()
    state = _instances(hass).setdefault(
        instance_id,
        ZrmInstanceState(instance_id=instance_id),
    )

    # (Re-)arm the periodic reconcile timer for this
    # instance. First call bootstraps it; subsequent calls
    # re-arm only when the interval changes.
    _ensure_timer(hass, state, reconcile_interval_minutes)

    # Serialise the reconcile per instance.
    async with state.lock:
        await _do_reconcile(
            hass,
            state,
            started=started,
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


async def _do_reconcile(  # noqa: PLR0912, PLR0913, PLR0915
    hass: HomeAssistant,
    state: ZrmInstanceState,
    *,
    started: float,
    trigger_id: str,
    config_file_path: str,
    host: str,
    port: int,
    token: str,
    clear_unmanaged_routes: bool,
    reconcile_interval_minutes: int,
    pending_timeout_hours: int,
    default_route_speed: bridge.RouteSpeed | None,
    max_notifications: int,
    debug_logging: bool,
) -> None:
    instance_id = state.instance_id
    notif_prefix = _notification_prefix(instance_id)
    now = dt_util.now()
    tag = f"[{_SERVICE_TAG}: {instance_id}]"

    # File-mtime change detection: edit the YAML and the next
    # tick picks it up even if the periodic interval hasn't
    # elapsed.
    abs_config_path = _resolve_config_path(hass, config_file_path)
    current_mtime = await hass.async_add_executor_job(
        _read_config_mtime,
        abs_config_path,
    )

    triggered_by_ha_start = trigger_id == "ha_start"
    mtime_changed = current_mtime != state.last_config_mtime
    interval_elapsed = True
    if state.last_reconcile_dt is not None:
        interval_elapsed = (now - state.last_reconcile_dt) > timedelta(
            minutes=reconcile_interval_minutes
        )
    manual_trigger = trigger_id == "manual"

    should_reconcile = (
        triggered_by_ha_start
        or mtime_changed
        or interval_elapsed
        or state.reconcile_pending
        or manual_trigger
    )

    if not should_reconcile:
        # Periodic tick with nothing to do. Update last_run
        # and exit. state.reconcile_pending is already False
        # by the gate logic; the diag entity reflects it.
        _persist_diagnostic(
            hass,
            state,
            now=now,
            started=started,
            current_mtime=current_mtime,
            trigger_id=trigger_id,
        )
        return

    # Circuit breaker gate: skip when open unless the user
    # forces it via a manual trigger.
    if logic.circuit_breaker_is_open(state.circuit, now) and not manual_trigger:
        state.last_bridge_error = "circuit breaker open"
        _persist_diagnostic(
            hass,
            state,
            now=now,
            started=started,
            current_mtime=current_mtime,
            trigger_id=trigger_id,
        )
        if debug_logging:
            _LOGGER.warning(
                "%s circuit breaker open until %s; skipping reconcile",
                tag,
                state.circuit.open_until.isoformat()
                if state.circuit.open_until
                else "?",
            )
        return

    # Read + parse config. Missing or empty -> empty Config.
    text, read_err = await hass.async_add_executor_job(
        _read_config_text,
        abs_config_path,
    )
    config_errors: list[logic.ConfigError] = []
    if read_err is not None:
        config_errors.append(
            logic.ConfigError(
                location="(file)",
                entity_id=None,
                reason=read_err,
            ),
        )
        config = logic.Config()
    else:
        config, config_errors = logic.parse_config(text)

    if config_errors:
        await _emit(
            hass,
            instance_id,
            [_format_config_error(e) for e in config_errors],
        )
        state.last_config_errors = len(config_errors)
        _persist_diagnostic(
            hass,
            state,
            now=now,
            started=started,
            current_mtime=current_mtime,
            trigger_id=trigger_id,
            mark_pending=True,
        )
        return

    # Fetch nodes via bridge.
    bridge_result = await _bridge_get_nodes(host, port, token)
    bridge_error = bridge_result.error
    if bridge_error:
        bridge_timed_out = logic.is_bridge_timeout_error(bridge_error)
        state.circuit, cb_transition = logic.circuit_breaker_next(
            state.circuit,
            now,
            bridge_succeeded=False,
            bridge_timed_out=bridge_timed_out,
        )
        notifs: list[PersistentNotification] = []
        if (
            cb_transition in ("open", "extended")
            and state.circuit.open_until is not None
        ):
            notifs.append(
                _circuit_breaker_notification(
                    notif_prefix,
                    instance_id,
                    now,
                    state.circuit.open_until,
                    state.circuit.streak,
                ),
            )
        # Also emit an api_unavailable notification so the
        # user sees the raw bridge error directly without
        # having to wait for the circuit breaker to open.
        notifs.append(
            _api_notification(notif_prefix, instance_id, bridge_error),
        )
        if notifs:
            await process_persistent_notifications(hass, notifs)
        state.last_bridge_error = str(bridge_error)
        _persist_diagnostic(
            hass,
            state,
            now=now,
            started=started,
            current_mtime=current_mtime,
            trigger_id=trigger_id,
            mark_pending=True,
        )
        if debug_logging:
            _LOGGER.warning(
                "%s bridge not ready: %s (streak=%d)",
                tag,
                bridge_error,
                state.circuit.streak,
            )
        return

    # Bridge call landed. Reset breaker.
    state.circuit, _ = logic.circuit_breaker_next(
        state.circuit,
        now,
        bridge_succeeded=True,
        bridge_timed_out=False,
    )

    # Verify the api_echo on getNodes itself.
    err_msg = _api_unavailable_message(
        bridge_result.api_result,
        bridge.API_GET_NODES,
    )
    if err_msg is None and bridge_result.api_result is None:
        err_msg = "zwave-js-ui did not respond to getNodes"
    if err_msg is not None:
        await process_persistent_notifications(
            hass,
            [_api_notification(notif_prefix, instance_id, err_msg)],
        )
        state.last_api_error = err_msg
        _persist_diagnostic(
            hass,
            state,
            now=now,
            started=started,
            current_mtime=current_mtime,
            trigger_id=trigger_id,
            mark_pending=True,
        )
        return

    nodes = bridge_result.nodes
    nodes_by_id: dict[int, bridge.NodeInfo] = {n.node_id: n for n in nodes}
    sleepy_node_ids = frozenset(
        n.node_id
        for n in nodes
        if not n.is_listening and not bool(n.is_frequent_listening)
    )

    entity_map, controller = _build_entity_to_resolution(hass, nodes)
    if controller is None:
        err_msg = "controller (node 1) not found in getNodes() response"
        await process_persistent_notifications(
            hass,
            [_api_notification(notif_prefix, instance_id, err_msg)],
        )
        state.last_api_error = err_msg
        _persist_diagnostic(
            hass,
            state,
            now=now,
            started=started,
            current_mtime=current_mtime,
            trigger_id=trigger_id,
            mark_pending=True,
        )
        return

    # Resolve entities -> ResolvedRoutes.
    resolved, resolve_errors = logic.resolve_entities(
        config,
        default_route_speed,
        entity_map,
        controller,
    )
    if resolve_errors:
        await _emit(
            hass,
            instance_id,
            [_format_config_error(e) for e in resolve_errors],
        )
        state.last_resolve_errors = len(resolve_errors)
        _persist_diagnostic(
            hass,
            state,
            now=now,
            started=started,
            current_mtime=current_mtime,
            trigger_id=trigger_id,
            mark_pending=True,
        )
        return

    # Diff + plan (pure).
    reconcile = logic.diff_and_plan(
        resolved,
        nodes_by_id,
        state.pending,
        state.applied,
        now,
        timedelta(hours=pending_timeout_hours),
        clear_unmanaged_routes,
    )

    apply_notifications: list[PersistentNotification] = []
    failed_actions_by_node: dict[
        int,
        list[tuple[logic.RouteAction, bridge.ApiResult]],
    ] = {}

    if reconcile.actions:
        apply_outcome = await _bridge_apply_actions(
            host,
            port,
            token,
            reconcile.actions,
            sleepy_node_ids,
        )
        # Connect failure mid-reconcile: collapse to one
        # api_unavailable notification rather than N
        # per-action failure notifications.
        if isinstance(apply_outcome, _ApplyConnectError):
            err_msg = f"connection lost during apply: {apply_outcome.message}"
            await process_persistent_notifications(
                hass,
                [_api_notification(notif_prefix, instance_id, err_msg)],
            )
            state.last_api_error = err_msg
            _persist_diagnostic(
                hass,
                state,
                now=now,
                started=started,
                current_mtime=current_mtime,
                trigger_id=trigger_id,
                mark_pending=True,
            )
            return
        apply_results = apply_outcome
        # Per-action api_echo / success check. Mismatch
        # means zwave-js-ui can't run the write api; bail.
        mismatch = _api_echo_mismatch(apply_results)
        if mismatch is not None:
            _mismatch_action, err_msg = mismatch
            await process_persistent_notifications(
                hass,
                [_api_notification(notif_prefix, instance_id, err_msg)],
            )
            state.last_api_error = err_msg
            _persist_diagnostic(
                hass,
                state,
                now=now,
                started=started,
                current_mtime=current_mtime,
                trigger_id=trigger_id,
                mark_pending=True,
            )
            return
        for action, api_result in apply_results:
            if api_result.success:
                continue
            failed_actions_by_node.setdefault(action.node_id, []).append(
                (action, api_result),
            )
            apply_notifications.append(
                _apply_notification(
                    notif_prefix,
                    instance_id,
                    action,
                    api_result,
                ),
            )

    # Build final pending + applied dicts. Drop failed
    # routes from pending, promote awake-node successes to
    # applied immediately, leave clears + sleepy successes
    # in pending until the next reconcile observes the
    # cached state.
    failed_route_types_by_node: dict[int, set[logic.RouteType]] = {}
    for node_id, failures in failed_actions_by_node.items():
        for action, _api_result in failures:
            route_type = logic.type_for_action_kind(action.kind)
            failed_route_types_by_node.setdefault(node_id, set()).add(
                route_type,
            )

    final_pending: dict[int, list[logic.RouteRequest]] = {}
    final_applied: dict[int, list[logic.RouteRequest]] = {
        nid: list(paths) for nid, paths in reconcile.new_applied.items()
    }
    for node_id, paths in reconcile.new_pending.items():
        failed_types = failed_route_types_by_node.get(node_id, set())
        is_awake = node_id not in sleepy_node_ids
        keep_pending: list[logic.RouteRequest] = []
        promote_to_applied: list[logic.RouteRequest] = []
        for path in paths:
            if path.type in failed_types:
                continue
            is_clear = not path.repeater_node_ids
            if is_clear:
                keep_pending.append(path)
            elif is_awake:
                promote_to_applied.append(
                    logic.RouteRequest(
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
            final_applied.setdefault(node_id, []).extend(promote_to_applied)

    state.pending = final_pending
    state.applied = final_applied

    # Per-attempt timeout notifications. The notification ID
    # is keyed to the attempt that just timed out; each
    # retry produces a distinct ID the user must dismiss.
    timeout_notifications: list[PersistentNotification] = []
    for (
        node_id,
        route_type,
        old_requested_at,
        count,
    ) in reconcile.new_timeouts:
        timeout_notifications.append(
            _timeout_notification(
                notif_prefix,
                instance_id,
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
        kept = issue_notifications[:max_notifications]
        cap_summary = PersistentNotification(
            active=True,
            notification_id=f"{notif_prefix}cap",
            title=f"{_SERVICE_NAME}: notification cap reached",
            message=(
                f"Showing {max_notifications} of"
                f" {len(issue_notifications)} route issues."
                " Increase the blueprint's max_notifications"
                " input to see all."
            ),
            instance_id=instance_id,
        )
        issue_notifications = kept + [cap_summary]
    else:
        issue_notifications.append(
            PersistentNotification(
                active=False,
                notification_id=f"{notif_prefix}cap",
                title="",
                message="",
            ),
        )

    # Always include inactive api notification so any
    # stale one is cleared.
    inactive_api = PersistentNotification(
        active=False,
        notification_id=f"{notif_prefix}api",
        title="",
        message="",
    )
    inactive_breaker = PersistentNotification(
        active=False,
        notification_id=f"{notif_prefix}circuit_breaker",
        title="",
        message="",
    )
    final_notifications = [
        inactive_api,
        inactive_breaker,
    ] + issue_notifications
    await process_persistent_notifications(hass, final_notifications)

    # Persist diagnostic state.
    routes_in_config = len(resolved) * len(logic.MANAGED_ROUTE_TYPES)
    routes_applied = sum(len(p) for p in final_applied.values())
    routes_pending = sum(len(p) for p in final_pending.values())
    routes_errored = sum(len(f) for f in failed_actions_by_node.values())
    reconcile_complete = not failed_actions_by_node

    state.last_config_mtime = current_mtime
    state.last_reconcile_dt = now
    state.reconcile_pending = not reconcile_complete
    # Cache node_to_entity + counters for the next reconcile's
    # bail-out paths. Error counters reset to "clean" so a
    # successful reconcile clears stale categories.
    state.node_to_entity = _node_to_entity(resolved)
    state.last_routes_in_config = routes_in_config
    state.last_routes_applied = routes_applied
    state.last_routes_pending = routes_pending
    state.last_routes_errored = routes_errored
    state.last_config_errors = 0
    state.last_resolve_errors = 0
    state.last_api_error = ""
    state.last_bridge_error = ""

    _persist_diagnostic(
        hass,
        state,
        now=now,
        started=started,
        current_mtime=current_mtime,
        trigger_id=trigger_id,
    )

    if debug_logging:
        _LOGGER.warning(
            "%s configured=%d applied=%d pending=%d errored=%d"
            " new_timeouts=%d actions_executed=%d",
            tag,
            routes_in_config,
            routes_applied,
            routes_pending,
            routes_errored,
            len(reconcile.new_timeouts),
            len(reconcile.actions),
        )


# --------------------------------------------------------
# Diagnostic state
# --------------------------------------------------------


def _persist_diagnostic(
    hass: HomeAssistant,
    state: ZrmInstanceState,
    *,
    now: datetime,
    started: float,
    current_mtime: float,
    trigger_id: str,
    mark_pending: bool = False,
) -> None:
    """Write a diagnostic snapshot to the state entity.

    Always serializes everything from the per-instance
    ``ZrmInstanceState`` -- route counters, error counters,
    pending / applied dicts, circuit breaker. The dataclass
    is the single source of truth; callers mutate the
    relevant fields before calling here. ``mark_pending``
    is a small ergonomic shortcut for the common bail-out
    pattern of "set reconcile_pending and persist".
    """
    if mark_pending:
        state.reconcile_pending = True
    attrs: dict[str, Any] = {
        "reconcile_pending": state.reconcile_pending,
        "last_reconcile": (
            state.last_reconcile_dt.isoformat()
            if state.last_reconcile_dt is not None
            else ""
        ),
        "last_config_mtime": current_mtime,
        "last_trigger": trigger_id or "",
        "routes_in_config": state.last_routes_in_config,
        "routes_applied": state.last_routes_applied,
        "routes_pending": state.last_routes_pending,
        "routes_errored": state.last_routes_errored,
        "config_errors": state.last_config_errors,
        "resolve_errors": state.last_resolve_errors,
        "api_error": state.last_api_error,
        "bridge_error": state.last_bridge_error,
        "pending": _paths_to_storage(state.pending, state.node_to_entity),
        "applied": _paths_to_storage(state.applied, state.node_to_entity),
    }
    attrs.update(logic.circuit_breaker_attrs(state.circuit))
    update_instance_state(
        hass,
        service=_SERVICE,
        instance_id=state.instance_id,
        last_run=now,
        runtime=time.monotonic() - started,
        extra_attributes=attrs,
    )


def _node_to_entity(
    resolved: list[logic.ResolvedRoute],
) -> dict[int, str]:
    """Build a node_id -> entity_id lookup from resolved routes.

    Each ``ResolvedRoute`` carries its client + repeater
    entity ids; flattening gives the lookup the storage
    serializer needs to annotate node ids with their
    user-visible entity ids in the diagnostic state.
    """
    out: dict[int, str] = {}
    for r in resolved:
        out[r.client_node_id] = r.client_entity_id
        repeaters = list(r.repeater_node_ids)
        rep_entities = list(r.repeater_entity_ids)
        for nid, eid in zip(repeaters, rep_entities, strict=False):
            out.setdefault(nid, eid)
    return out


def _path_to_storage(
    path: logic.RouteRequest,
    node_to_entity: dict[int, str],
) -> dict[str, Any]:
    """Serialise one ``RouteRequest`` to a JSON-friendly dict."""
    speed_display = path.speed.value if path.speed is not None else "-"
    return {
        "type": path.type.value,
        "repeaters": [
            {"id": r, "entity_id": node_to_entity.get(r, "")}
            for r in path.repeater_node_ids
        ],
        "speed": speed_display,
        "requested_at": (
            path.requested_at.isoformat() if path.requested_at else ""
        ),
        "confirmed_at": (
            path.confirmed_at.isoformat() if path.confirmed_at else ""
        ),
        "timeout_count": path.timeout_count,
    }


def _paths_to_storage(
    paths: dict[int, list[logic.RouteRequest]],
    node_to_entity: dict[int, str],
) -> dict[str, Any]:
    """Serialise a per-node ``RouteRequest`` map."""
    out: dict[str, Any] = {}
    for node_id, path_list in paths.items():
        if not path_list:
            continue
        out[str(node_id)] = {
            "entity_id": node_to_entity.get(node_id, ""),
            "paths": [_path_to_storage(p, node_to_entity) for p in path_list],
        }
    return out


# --------------------------------------------------------
# Notification builders
# --------------------------------------------------------


def _notification_prefix(instance_id: str) -> str:
    """Common prefix for the ZRM notification family.

    Extra category-specific suffix is appended per
    notification kind. Trailing ``__`` keeps the suffix
    parseable per the helpers convention.
    """
    return f"blueprint_toolkit_{_SERVICE}__{instance_id}__"


def _api_notification(
    notif_prefix: str,
    instance_id: str,
    error: str,
) -> PersistentNotification:
    title = f"{_SERVICE_NAME}: API unavailable"
    escaped = md_escape(error)
    body = (
        f"Could not reach or use the Z-Wave JS UI API: {escaped}"
        "\n\nCheck that the Z-Wave JS addon is running and "
        "that the blueprint's host/port inputs match the addon."
    )
    return PersistentNotification(
        active=bool(error),
        notification_id=f"{notif_prefix}api",
        title=title,
        message=body,
        instance_id=instance_id,
    )


def _circuit_breaker_notification(
    notif_prefix: str,
    instance_id: str,
    now: datetime,
    open_until: datetime,
    streak: int,
) -> PersistentNotification:
    cooldown_minutes = max(
        1,
        int(round((open_until - now).total_seconds() / 60)),
    )
    try:
        local_resume = open_until.astimezone().strftime("%H:%M")
    except (OverflowError, ValueError):
        local_resume = open_until.isoformat(timespec="minutes")
    title = f"{_SERVICE_NAME}: controller unresponsive"
    body = (
        "Z-Wave route reconcile paused -- controller is "
        "unresponsive to API queries after "
        f"{streak} consecutive attempts timed out. "
        f"Automation will retry after {cooldown_minutes} "
        f"minutes (at {local_resume})."
    )
    return PersistentNotification(
        active=True,
        notification_id=f"{notif_prefix}circuit_breaker",
        title=title,
        message=body,
        instance_id=instance_id,
    )


def _apply_notification(
    notif_prefix: str,
    instance_id: str,
    action: logic.RouteAction,
    api_result: bridge.ApiResult,
) -> PersistentNotification:
    title = f"{_SERVICE_NAME}: apply failed for node {action.node_id}"
    lines = [
        f"Action: {action.kind.value}",
        f"Node: {action.node_id}",
    ]
    if action.client_entity_id:
        lines.append(f"Entity: `{md_escape(action.client_entity_id)}`")
    if action.repeaters:
        reps = ", ".join(str(r) for r in action.repeaters)
        lines.append(f"Repeaters: {reps}")
    response = md_escape(api_result.message or "(empty)")
    lines.append(f"Server response: {response}")
    return PersistentNotification(
        active=True,
        notification_id=f"{notif_prefix}apply_{action.node_id}",
        title=title,
        message="\n".join(lines),
        instance_id=instance_id,
    )


def _timeout_notification(
    notif_prefix: str,
    instance_id: str,
    node_id: int,
    route_type: logic.RouteType,
    old_requested_at: datetime,
    timeout_count: int,
    pending_timeout_hours: int,
) -> PersistentNotification:
    title = (
        f"{_SERVICE_NAME}: route pending > "
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
        instance_id=instance_id,
    )


# --------------------------------------------------------
# Bridge async wrappers
# --------------------------------------------------------


@dataclass
class _GetNodesResult:
    """Outcome of one bridge get_nodes call."""

    api_result: bridge.ApiResult | None
    nodes: list[bridge.NodeInfo]
    error: str  # empty string on success


@dataclass
class _ApplyConnectError:
    """Sentinel: bridge connect failed during the apply phase.

    Returned by ``_bridge_apply_actions`` when
    ``client.connect()`` blows up (the connection died
    between the get_nodes call earlier this reconcile and
    the apply call). The caller surfaces a single
    ``api_unavailable`` notification rather than N
    per-action ``apply_*`` notifications, since the
    failure is a single connectivity event, not N
    independent route-write rejections.
    """

    message: str


def _format_bridge_exception(exc: BaseException) -> str:
    """Always include the exception class name.

    The downstream ``is_bridge_timeout_error`` classifier
    matches on ``"TimeoutError"`` substring; an empty-message
    timeout would otherwise bypass the breaker.
    """
    detail = str(exc)
    if detail:
        return f"{type(exc).__name__}: {detail}"
    return type(exc).__name__


def _bridge_timeout_excs() -> tuple[type[BaseException], ...]:
    """Late import: socketio types only available at runtime."""
    import socketio.exceptions as sio_exc  # noqa: PLC0415

    return (
        ConnectionError,
        TimeoutError,
        OSError,
        sio_exc.TimeoutError,
    )


async def _bridge_get_nodes(
    host: str,
    port: int,
    token: str,
) -> _GetNodesResult:
    """Connect to zwave-js-ui and fetch nodes with fresh routes."""
    timeout_excs = _bridge_timeout_excs()
    client = bridge.ZwaveJsUiClient(
        host=host,
        port=port,
        token=token or None,
    )
    try:
        try:
            await client.connect()
        except timeout_excs as e:
            return _GetNodesResult(
                api_result=None,
                nodes=[],
                error=_format_bridge_exception(e),
            )
        try:
            bulk_r, nodes = await client.get_nodes_with_fresh_routes()
        except timeout_excs as e:
            return _GetNodesResult(
                api_result=None,
                nodes=[],
                error=_format_bridge_exception(e),
            )
        return _GetNodesResult(
            api_result=bulk_r,
            nodes=nodes,
            error="",
        )
    finally:
        try:
            await client.disconnect()
        except timeout_excs:
            # Best-effort close; the next reconcile builds
            # a fresh client anyway.
            pass


async def _bridge_apply_actions(
    host: str,
    port: int,
    token: str,
    actions: list[logic.RouteAction],
    sleepy_node_ids: frozenset[int],
) -> list[tuple[logic.RouteAction, bridge.ApiResult]] | _ApplyConnectError:
    """Apply RouteActions concurrently.

    Sleepy nodes get short fire-and-forget; awake nodes
    must ACK within ``_AWAKE_APPLY_TIMEOUT``. Returns
    ``_ApplyConnectError`` if the bridge connect fails
    between phases (e.g. addon restart mid-reconcile),
    so the caller can collapse N per-action failures into
    one ``api_unavailable`` notification.
    """
    timeout_excs = _bridge_timeout_excs()

    async def _dispatch(
        client: bridge.ZwaveJsUiClient,
        action: logic.RouteAction,
    ) -> bridge.ApiResult:
        kind = action.kind
        if kind == logic.RouteActionKind.SET_APPLICATION_ROUTE:
            # logic guarantees ``route_speed`` is set for SET_* actions;
            # the type is ``RouteSpeed | None`` because CLEAR_* actions
            # share the dataclass and leave it unset.
            assert action.route_speed is not None
            return await client.set_application_route(
                action.node_id,
                list(action.repeaters),
                action.route_speed,
            )
        if kind == logic.RouteActionKind.CLEAR_APPLICATION_ROUTE:
            return await client.remove_application_route(action.node_id)
        if kind == logic.RouteActionKind.SET_PRIORITY_SUC_RETURN_ROUTE:
            assert action.route_speed is not None
            return await client.assign_priority_suc_return_route(
                action.node_id,
                list(action.repeaters),
                action.route_speed,
            )
        if kind == logic.RouteActionKind.CLEAR_PRIORITY_SUC_RETURN_ROUTES:
            return await client.delete_suc_return_routes(action.node_id)
        return bridge.ApiResult(
            success=False,
            message=f"unknown RouteActionKind: {action.kind!r}",
            api_echo=None,
            result=None,
        )

    async def _one(
        client: bridge.ZwaveJsUiClient,
        action: logic.RouteAction,
    ) -> bridge.ApiResult:
        is_sleepy = action.node_id in sleepy_node_ids
        timeout = _SLEEPY_APPLY_TIMEOUT if is_sleepy else _AWAKE_APPLY_TIMEOUT
        try:
            return await asyncio.wait_for(
                _dispatch(client, action),
                timeout=timeout,
            )
        except TimeoutError:
            if is_sleepy:
                return bridge.ApiResult(
                    success=True,
                    message="queued (sleepy node; will apply on wake)",
                    api_echo=None,
                    result=None,
                )
            return bridge.ApiResult(
                success=False,
                message=(
                    f"timeout awaiting ACK after "
                    f"{_AWAKE_APPLY_TIMEOUT}s on non-sleepy node"
                ),
                api_echo=None,
                result=None,
            )

    client = bridge.ZwaveJsUiClient(
        host=host,
        port=port,
        token=token or None,
        timeout_seconds=_BRIDGE_TIMEOUT,
    )
    try:
        await client.connect()
    except timeout_excs as e:
        # Bridge died between phases. Return a sentinel so
        # the caller can surface one api_unavailable
        # notification rather than N per-action ones.
        return _ApplyConnectError(message=_format_bridge_exception(e))
    try:
        coros = [_one(client, a) for a in actions]
        responses = await asyncio.gather(*coros)
        return list(zip(actions, responses, strict=True))
    finally:
        try:
            await client.disconnect()
        except timeout_excs:
            pass


# --------------------------------------------------------
# api_echo + api_unavailable analysis
# --------------------------------------------------------


def _expected_api_for_kind(kind: logic.RouteActionKind) -> str:
    """Return the wire-level API name we expect echoed back."""
    if kind == logic.RouteActionKind.SET_APPLICATION_ROUTE:
        return bridge.API_SET_APPLICATION_ROUTE
    if kind == logic.RouteActionKind.CLEAR_APPLICATION_ROUTE:
        return bridge.API_SET_APPLICATION_ROUTE
    if kind == logic.RouteActionKind.SET_PRIORITY_SUC_RETURN_ROUTE:
        return bridge.API_ASSIGN_PRIORITY_SUC_RETURN_ROUTE
    return bridge.API_DELETE_SUC_RETURN_ROUTES


def _api_unavailable_message(
    api_result: bridge.ApiResult | None,
    expected_api: str,
) -> str | None:
    """Return a user-facing message if the call says the API is unavailable."""
    if api_result is None:
        return None
    echo = api_result.api_echo
    if echo is not None and echo != expected_api:
        return (
            f"zwave-js-ui rejected API {expected_api!r} "
            f"(echoed {echo!r}). Check that your zwave-js-ui "
            "version allow-lists this api."
        )
    if not api_result.success and echo == expected_api:
        msg = (api_result.message or "").strip()
        return (
            f"zwave-js-ui reported {expected_api!r} failed: "
            f"{msg or 'no message'}"
        )
    return None


def _api_echo_mismatch(
    apply_results: list[tuple[logic.RouteAction, bridge.ApiResult]],
) -> tuple[logic.RouteAction, str] | None:
    """Scan apply results for an api_echo vs expected mismatch."""
    for action, api_result in apply_results:
        expected = _expected_api_for_kind(action.kind)
        msg = _api_unavailable_message(api_result, expected)
        if msg is not None:
            return (action, msg)
    return None


# --------------------------------------------------------
# Config file I/O (sync; runs in the executor)
# --------------------------------------------------------


def _resolve_config_path(hass: HomeAssistant, config_file_path: str) -> str:
    if os.path.isabs(config_file_path):
        return config_file_path
    return os.path.join(hass.config.config_dir, config_file_path)


def _read_config_mtime(path: str) -> float:
    """Return mtime, or 0.0 if file missing."""
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _read_config_text(path: str) -> tuple[str, str | None]:
    """Read the YAML config file. Returns (text, error_or_None)."""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read(), None
    except FileNotFoundError:
        return "", f"config file not found: {path}"
    except OSError as e:
        return "", f"could not read config file: {e}"


# --------------------------------------------------------
# Entity-to-node resolution (HA registries)
# --------------------------------------------------------


def _build_entity_to_resolution(
    hass: HomeAssistant,
    nodes: list[bridge.NodeInfo],
) -> tuple[dict[str, logic.DeviceResolution], logic.DeviceResolution | None]:
    """Build entity_id -> DeviceResolution from HA registries + nodes."""
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    nodes_by_id: dict[int, bridge.NodeInfo] = {n.node_id: n for n in nodes}

    device_to_node: dict[str, int] = {}
    for dev in dev_reg.devices.values():
        for ident in dev.identifiers:
            if len(ident) < 2:
                continue
            domain = ident[0]
            if domain != "zwave_js":
                continue
            raw = str(ident[1])
            parts = raw.split("-")
            if len(parts) < 2:
                continue
            try:
                node_id = int(parts[1])
            except ValueError:
                continue
            device_to_node[dev.id] = node_id
            break

    entity_to_resolution: dict[str, logic.DeviceResolution] = {}
    for entry in ent_reg.entities.values():
        if entry.platform != "zwave_js":
            continue
        if entry.disabled_by is not None:
            continue
        entry_node_id = device_to_node.get(entry.device_id or "")
        if entry_node_id is None:
            continue
        ni = nodes_by_id.get(entry_node_id)
        if ni is None:
            continue
        entity_to_resolution[entry.entity_id] = logic.DeviceResolution(
            entity_id=entry.entity_id,
            device_id=entry.device_id or "",
            node_id=entry_node_id,
            is_routing=ni.is_routing,
            is_listening=ni.is_listening,
            is_frequent_listening=ni.is_frequent_listening,
            failed=ni.failed,
            is_long_range=ni.is_long_range,
            max_data_rate_bps=ni.max_data_rate_bps,
        )

    controller_node = nodes_by_id.get(1)
    controller: logic.DeviceResolution | None = None
    if controller_node is not None:
        controller = logic.DeviceResolution(
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
    return entity_to_resolution, controller


# --------------------------------------------------------
# Periodic timer
# --------------------------------------------------------


def _ensure_timer(
    hass: HomeAssistant,
    state: ZrmInstanceState,
    interval_minutes: int,
) -> None:
    """Arm or re-arm the per-instance periodic reconcile timer."""
    if (
        state.cancel_timer is not None
        and state.armed_interval_minutes == interval_minutes
    ):
        return
    if state.cancel_timer is not None:
        state.cancel_timer()
        state.cancel_timer = None
    state.armed_interval_minutes = interval_minutes
    state.cancel_timer = async_track_time_interval(
        hass,
        _make_periodic_callback(hass, state.instance_id),
        timedelta(minutes=interval_minutes),
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
        await hass.services.async_call(
            "automation",
            "trigger",
            {
                "entity_id": instance_id,
                "skip_condition": True,
                "variables": {"trigger": {"id": "periodic"}},
            },
        )

    return _on_tick


# --------------------------------------------------------
# Restart-recovery kick + per-port lifecycle mutators
# --------------------------------------------------------


async def _async_kick_for_recovery(
    hass: HomeAssistant,
    entity_id: str,
) -> None:
    """Fire a manual reconcile so the instance bootstraps its timer."""
    await hass.services.async_call(
        "automation",
        "trigger",
        {
            "entity_id": entity_id,
            "skip_condition": True,
            "variables": {"trigger": {"id": "manual"}},
        },
    )


@callback  # type: ignore[untyped-decorator]
def _on_reload(hass: HomeAssistant) -> None:
    """Cancel timers; instance state survives the reload."""
    for s in list(_instances(hass).values()):
        if s.cancel_timer is not None:
            s.cancel_timer()
            s.cancel_timer = None
            s.armed_interval_minutes = 0


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
    """Register ZRM's service + lifecycle via the shared helper."""
    await register_blueprint_handler(hass, entry, _SPEC)


async def async_unregister(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Tear down ZRM's service + lifecycle via the shared helper."""
    await unregister_blueprint_handler(hass, entry, _SPEC)


__all__ = [
    "BLUEPRINT_PATH",
    "ZrmInstanceState",
    "async_register",
    "async_unregister",
]
