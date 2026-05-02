# This is AI generated code
"""HA wiring for device_watchdog.

DW-specific shape on top of the standard three-layer
dispatch (see ``DEVELOPMENT.md`` for the universal
pattern):

- Periodic scan via integration-owned scheduling. The
  blueprint's ``time_pattern`` minute trigger is gone;
  ``helpers.schedule_periodic_with_jitter`` arms a
  per-instance offset so multiple instances of this
  blueprint don't hammer the registries simultaneously
  on shared intervals.
- Truth set (entity registry, device registry, target-
  integration filter) is built on the event loop because
  HA registries are loop-only. Heavy work (per-device
  unavailable / staleness classification, disabled-
  diagnostic scan, notification body assembly) runs in
  the executor via
  ``hass.async_add_executor_job(logic.run_evaluation, ...)``.
- Three notification slots: per-device health findings
  (capped by ``max_device_notifications`` via
  ``helpers.prepare_notifications``), the cap-summary slot
  the helper always emits, and per-device disabled-
  diagnostic notifications (separate stream, separate
  notification IDs). The complete per-instance
  notification set is sweep-dispatched via
  ``process_persistent_notifications_with_sweep`` so
  prior-run notifications no longer present this run get
  cleaned up.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import template as ha_tmpl
from homeassistant.util import dt as dt_util

from ..const import DOMAIN
from ..helpers import (
    BlueprintHandlerSpec,
    all_integration_ids,
    automation_friendly_name,
    cv_ha_domain_list,
    entry_for_domain,
    make_emit_config_error,
    make_lifecycle_mutators,
    make_periodic_trigger_callback,
    notification_prefix,
    process_persistent_notifications_with_sweep,
    register_blueprint_handler,
    resolve_target_integrations,
    schedule_periodic_with_jitter,
    spec_bucket,
    unregister_blueprint_handler,
    update_instance_state,
    validate_and_join_regex_patterns,
    validate_payload_or_emit_config_error,
)
from . import logic

_LOGGER = logging.getLogger(__name__)

_SERVICE = "device_watchdog"
_SERVICE_TAG = "DW"
_SERVICE_NAME = "Device Watchdog"
BLUEPRINT_PATH = "blueprint_toolkit/device_watchdog.yaml"


# --------------------------------------------------------
# Per-instance in-memory state
# --------------------------------------------------------


@dataclass
class DwInstanceState:
    """In-memory state for one DW automation instance.

    Lost on HA restart; the periodic timer + restart-
    recovery kick re-arm everything from scratch on the
    next tick.
    """

    instance_id: str
    # Tracks the interval the timer was last armed with so
    # we can detect blueprint-input changes and re-arm.
    armed_interval_minutes: int = 0
    cancel_timer: Callable[[], None] | None = field(default=None, repr=False)


# --------------------------------------------------------
# Service-call schema
# --------------------------------------------------------

_SCHEMA = vol.Schema(
    {
        vol.Required("instance_id"): cv.entity_id,
        vol.Required("trigger_id"): vol.Coerce(str),
        vol.Required("include_integrations_raw"): cv_ha_domain_list,
        vol.Required("exclude_integrations_raw"): cv_ha_domain_list,
        vol.Required("exclude_device_name_regex_raw"): vol.Coerce(str),
        vol.Required("exclude_entity_id_regex_raw"): vol.Coerce(str),
        vol.Required("monitored_entity_domains_raw"): cv_ha_domain_list,
        vol.Required("check_interval_minutes_raw"): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=10080)
        ),
        vol.Required("dead_device_threshold_minutes_raw"): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=10080)
        ),
        vol.Required("enabled_checks_raw"): vol.All(
            cv.ensure_list, [vol.Coerce(str)]
        ),
        vol.Required("max_device_notifications_raw"): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=1000)
        ),
        vol.Required("debug_logging_raw"): cv.boolean,
    },
    extra=vol.ALLOW_EXTRA,
)


# --------------------------------------------------------
# Per-instance state accessor
# --------------------------------------------------------


def _instances(hass: HomeAssistant) -> dict[str, DwInstanceState]:
    """Per-instance state map under our service's bucket."""
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        return {}
    bucket = spec_bucket(entries[0], _SERVICE)
    instances: dict[str, DwInstanceState] = bucket.setdefault("instances", {})
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

    # Enabled-checks cross-validation: each requested
    # check must be in CHECK_ALL. Empty list means "all
    # checks" (mirrors the include_integrations
    # empty-means-all pattern in this same handler).
    enabled_checks_raw: list[str] = list(data["enabled_checks_raw"])
    unknown_checks = [c for c in enabled_checks_raw if c not in logic.CHECK_ALL]
    if unknown_checks:
        bad = ", ".join(sorted(unknown_checks))
        valid = ", ".join(sorted(logic.CHECK_ALL))
        errors.append(
            f"enabled_checks: unknown value(s) {bad}. Valid values: {valid}."
        )
    enabled_checks: frozenset[str] = (
        logic.CHECK_ALL
        if not enabled_checks_raw
        else frozenset(enabled_checks_raw)
    )

    # Multi-line regex inputs go through the shared helper
    # so per-line ``re.compile`` validation, empty-match
    # rejection, and alternation join behave identically.
    # See ``test_helpers_lifecycle.TestValidateAndJoinRegexPatterns``
    # for the parser contract.
    exclude_device_name_regex, dev_errors = validate_and_join_regex_patterns(
        data["exclude_device_name_regex_raw"],
        "exclude_device_name_regex",
    )
    errors.extend(dev_errors)
    exclude_entity_id_regex, eid_errors = validate_and_join_regex_patterns(
        data["exclude_entity_id_regex_raw"],
        "exclude_entity_id_regex",
    )
    errors.extend(eid_errors)

    # Argparse complete; emit accumulated errors (or
    # dismiss any prior config_error notification).
    await _emit_config_error(hass, instance_id, errors)
    if errors:
        return

    # Blueprint passes the threshold in minutes; the logic
    # module's Config carries seconds.
    dead_threshold_seconds = int(data["dead_device_threshold_minutes_raw"]) * 60

    await _async_service_layer(
        hass,
        call,
        now=now,
        instance_id=instance_id,
        trigger_id=data["trigger_id"],
        include_integrations=list(data["include_integrations_raw"]),
        exclude_integrations=list(data["exclude_integrations_raw"]),
        exclude_device_name_regex=exclude_device_name_regex,
        exclude_entity_id_regex=exclude_entity_id_regex,
        monitored_entity_domains=list(data["monitored_entity_domains_raw"]),
        check_interval_minutes=data["check_interval_minutes_raw"],
        dead_threshold_seconds=dead_threshold_seconds,
        enabled_checks=enabled_checks,
        max_notifications=data["max_device_notifications_raw"],
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
    include_integrations: list[str],
    exclude_integrations: list[str],
    exclude_device_name_regex: str,
    exclude_entity_id_regex: str,
    monitored_entity_domains: list[str],
    check_interval_minutes: int,
    dead_threshold_seconds: int,
    enabled_checks: frozenset[str],
    max_notifications: int,
    debug_logging: bool,
) -> None:
    """Run a scan + dispatch notifications + persist diagnostics."""
    state = _instances(hass).setdefault(
        instance_id,
        DwInstanceState(instance_id=instance_id),
    )

    # Make sure the periodic timer is armed with the
    # current interval (handles first-run + interval
    # changes mid-flight).
    entry = entry_for_domain(hass)
    if entry is not None:
        _ensure_timer(hass, entry, state, check_interval_minutes)

    notif_prefix = notification_prefix(_SERVICE, instance_id)
    tag = f"[{_SERVICE_TAG}: {automation_friendly_name(hass, instance_id)}]"

    config = logic.Config(
        exclude_device_name_regex=exclude_device_name_regex,
        exclude_entity_id_regex=exclude_entity_id_regex,
        monitored_entity_domains=monitored_entity_domains,
        dead_threshold_seconds=dead_threshold_seconds,
        enabled_checks=enabled_checks,
        notification_prefix=notif_prefix,
        instance_id=instance_id,
    )

    # Resolve target integrations + assemble inputs on the
    # event loop -- the registries we walk are loop-only.
    all_integrations = all_integration_ids(hass)
    target_integrations = resolve_target_integrations(
        all_integrations,
        include_integrations,
        exclude_integrations,
    )
    devices = _build_device_inputs(
        hass,
        all_integrations,
        target_integrations,
        diag_check_enabled=(logic.CHECK_DISABLED_DIAGNOSTICS in enabled_checks),
    )

    # Heavy work (per-device classification, disabled-
    # diag scan, notification body assembly) runs in HA's
    # executor pool so the event loop stays responsive.
    ev = await hass.async_add_executor_job(
        logic.run_evaluation,
        config,
        devices,
        now,
        len(all_integrations),
        max_notifications,
    )

    # Sweep so prior-run notifications no longer present
    # this run (e.g. a device whose health cleared
    # between runs) get dismissed automatically.
    await process_persistent_notifications_with_sweep(
        hass,
        ev.notifications,
        sweep_prefix=notif_prefix,
    )

    # Persist diagnostic state.
    update_instance_state(
        hass,
        service_tag=_SERVICE_TAG,
        instance_id=instance_id,
        last_run=now,
        runtime=(dt_util.now() - now).total_seconds(),
        extra_attributes={
            "last_trigger": trigger_id or "",
            "integrations": ev.all_integrations_count,
            "integrations_excluded": (
                ev.all_integrations_count - len(target_integrations)
            ),
            "devices": len(ev.results),
            "devices_excluded": ev.stat_devices_excluded,
            "entities": ev.stat_entities,
            "entities_excluded": ev.stat_entities_excluded,
            "device_issues": ev.issues_count,
            "entity_issues": ev.stat_entity_issues,
            # Attribute name preserved verbatim so existing
            # operator diagnostic-state queries don't silently
            # break. The doc at
            # ``bundled/docs/device_watchdog.md`` describes
            # this name.
            "device_stale_issues": ev.stat_stale,
        },
    )

    if debug_logging:
        _LOGGER.warning(
            "%s integrations=%d devices=%d entities=%d"
            " device_issues=%d entity_issues=%d stale=%d",
            tag,
            ev.all_integrations_count,
            len(ev.results),
            ev.stat_entities,
            ev.issues_count,
            ev.stat_entity_issues,
            ev.stat_stale,
        )


def _entity_state_snapshot(
    hass: HomeAssistant,
    entity_id: str,
) -> tuple[str, datetime | None] | None:
    """Read entity state + last_reported timestamp.

    Returns ``(state_value, last_reported)`` or ``None``
    if the entity has no current state.
    """
    st = hass.states.get(entity_id)
    if st is None:
        return None
    return (str(st.state), st.last_reported)


def _build_device_inputs(
    hass: HomeAssistant,
    all_integration_ids: list[str],
    target_integrations: set[str],
    *,
    diag_check_enabled: bool,
) -> list[logic.DeviceInfo]:
    """Walk registries to build ``DeviceInfo`` per device.

    Always scans every integration so multi-integration
    detection stays accurate. ``target_integrations``
    filters which integrations populate per-device entity
    state snapshots; the device's ``integration_entities``
    map carries every integration the device touches but
    only the targeted ones contribute ``EntityInfo`` rows.

    ``all_integration_ids`` is threaded in by the caller
    (already computed for filter resolution) so we don't
    re-walk the entity registry here just to enumerate
    integrations. ``diag_check_enabled`` gates the
    disabled-diagnostic registry-entry collection -- skip
    the per-device registry walk entirely when the check
    isn't requested.
    """
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    device_map: dict[str, logic.DeviceEntry] = {}
    # Map device_id -> {integration_id -> [entity_id]} for
    # the entities we want full state snapshots on.
    populate_eids: dict[str, dict[str, list[str]]] = {}

    for integration_id in all_integration_ids:
        try:
            entity_ids = list(
                ha_tmpl.integration_entities(hass, integration_id),
            )
        except (KeyError, ValueError):
            # Integration not found or invalid -- skip.
            continue
        for entity_id in entity_ids:
            entry = ent_reg.async_get(entity_id)
            if entry is None or entry.device_id is None:
                continue
            device = dev_reg.async_get(entry.device_id)
            if device is None:
                continue
            dev_id = entry.device_id
            if dev_id not in device_map:
                device_map[dev_id] = logic.DeviceEntry(
                    id=dev_id,
                    url=f"/config/devices/device/{dev_id}",
                    name=device.name_by_user or device.name or "",
                    default_name=device.name or "",
                )
                populate_eids[dev_id] = {}
            ie = device_map[dev_id].integration_entities
            if integration_id not in ie:
                ie[integration_id] = set()
            if integration_id in target_integrations:
                ie[integration_id].add(entity_id)
                populate_eids[dev_id].setdefault(integration_id, []).append(
                    entity_id
                )

    devices: list[logic.DeviceInfo] = []
    for dev_id, dev_entry in device_map.items():
        # Collect entity state snapshots for the targeted
        # integrations.
        entity_infos: list[logic.EntityInfo] = []
        for eids in populate_eids.get(dev_id, {}).values():
            for eid in eids:
                snap = _entity_state_snapshot(hass, eid)
                if snap is None:
                    continue
                state_value, last_reported = snap
                entity_infos.append(
                    logic.EntityInfo(
                        entity_id=eid,
                        state=state_value,
                        last_reported=last_reported,
                    ),
                )

        # Disabled-diagnostic registry walk: only when the
        # check is enabled, only for integrations in the
        # target set. Includes disabled entries because the
        # whole point is to flag them.
        registry_entries: list[logic.RegistryEntry] = []
        if diag_check_enabled:
            for reg in er.async_entries_for_device(
                ent_reg,
                dev_id,
                include_disabled_entities=True,
            ):
                if reg.platform not in target_integrations:
                    continue
                registry_entries.append(
                    logic.RegistryEntry(
                        entity_id=reg.entity_id,
                        original_name=reg.original_name or "",
                        platform=reg.platform or "",
                        entity_category=(
                            str(reg.entity_category.value)
                            if reg.entity_category
                            else None
                        ),
                        disabled=(reg.disabled_by is not None),
                    ),
                )

        devices.append(
            logic.DeviceInfo(
                de=dev_entry,
                entities=entity_infos,
                registry_entries=registry_entries,
            ),
        )
    return devices


# --------------------------------------------------------
# Periodic timer + recovery kick
# --------------------------------------------------------


def _ensure_timer(
    hass: HomeAssistant,
    entry: ConfigEntry,
    state: DwInstanceState,
    interval_minutes: int,
) -> None:
    """(Re)arm the periodic timer if the interval changed."""
    if state.armed_interval_minutes == interval_minutes:
        return
    if state.cancel_timer is not None:
        state.cancel_timer()
        state.cancel_timer = None
    state.armed_interval_minutes = interval_minutes
    state.cancel_timer = schedule_periodic_with_jitter(
        hass,
        entry,
        interval=timedelta(minutes=interval_minutes),
        instance_id=state.instance_id,
        action=make_periodic_trigger_callback(
            hass,
            state.instance_id,
            instances_getter=_instances,
            service_tag=_SERVICE_TAG,
            logger=_LOGGER,
        ),
    )


# --------------------------------------------------------
# Lifecycle mutators
# --------------------------------------------------------


_MUTATORS = make_lifecycle_mutators(
    instances_getter=_instances,
    cancel_field="cancel_timer",
    service_tag=_SERVICE_TAG,
    logger=_LOGGER,
    reset_armed_interval_on_reload=True,
)
_on_reload = _MUTATORS.on_reload
_on_entity_remove = _MUTATORS.on_entity_remove
_on_entity_rename = _MUTATORS.on_entity_rename
_on_teardown = _MUTATORS.on_teardown


# --------------------------------------------------------
# Spec + register / unregister
# --------------------------------------------------------


_SPEC = BlueprintHandlerSpec(
    service=_SERVICE,
    service_tag=_SERVICE_TAG,
    service_name=_SERVICE_NAME,
    blueprint_path=BLUEPRINT_PATH,
    service_handler=_async_entrypoint,
    kick_variables={"trigger_id": "manual"},
    on_reload=_on_reload,
    on_entity_remove=_on_entity_remove,
    on_entity_rename=_on_entity_rename,
    on_teardown=_on_teardown,
)


async def async_register(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Register DW's service + lifecycle via the shared helper."""
    await register_blueprint_handler(hass, entry, _SPEC)


async def async_unregister(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Tear down DW's service + lifecycle via the shared helper."""
    await unregister_blueprint_handler(hass, entry, _SPEC)


__all__ = [
    "BLUEPRINT_PATH",
    "DwInstanceState",
    "async_register",
    "async_unregister",
]
