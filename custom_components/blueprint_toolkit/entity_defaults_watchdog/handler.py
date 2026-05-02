# This is AI generated code
"""HA wiring for entity_defaults_watchdog.

EDW-specific shape on top of the standard three-layer
dispatch (see ``DEVELOPMENT.md`` for the universal
pattern):

- Periodic scan via integration-owned scheduling. The
  blueprint's ``time_pattern`` minute trigger is gone;
  ``helpers.schedule_periodic_with_jitter`` arms a per-
  instance offset so multiple instances of this blueprint
  don't hammer the registries simultaneously on shared
  intervals.
- Truth set (entity registry, device registry, target-
  integration filter, deviceless peers) is built on the
  event loop because HA registries are loop-only. Heavy
  work (per-device drift classification, deviceless
  collision-suffix scan, notification body assembly)
  runs in the executor via
  ``hass.async_add_executor_job(logic.run_evaluation, ...)``.
- Three notification slots: per-device drift findings
  (capped by ``max_device_notifications`` via
  ``helpers.prepare_notifications``), the cap-summary slot
  the helper always emits, and a single deviceless
  aggregate slot. The complete per-instance notification
  set is sweep-dispatched via
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

_SERVICE = "entity_defaults_watchdog"
_SERVICE_TAG = "EDW"
_SERVICE_NAME = "Entity Defaults Watchdog"
BLUEPRINT_PATH = "blueprint_toolkit/entity_defaults_watchdog.yaml"


# --------------------------------------------------------
# Per-instance in-memory state
# --------------------------------------------------------


@dataclass
class EdwInstanceState:
    """In-memory state for one EDW automation instance.

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
        vol.Required("drift_checks_raw"): vol.All(
            cv.ensure_list, [vol.Coerce(str)]
        ),
        vol.Required("include_integrations_raw"): cv_ha_domain_list,
        vol.Required("exclude_integrations_raw"): cv_ha_domain_list,
        vol.Required("exclude_device_name_regex_raw"): vol.Coerce(str),
        vol.Required("exclude_entities_raw"): cv.entity_ids,
        vol.Required("exclude_entity_id_regex_raw"): vol.Coerce(str),
        vol.Required("exclude_entity_name_regex_raw"): vol.Coerce(str),
        vol.Required("check_interval_minutes_raw"): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=10080)
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


def _instances(hass: HomeAssistant) -> dict[str, EdwInstanceState]:
    """Per-instance state map under our service's bucket."""
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        return {}
    bucket = spec_bucket(entries[0], _SERVICE)
    instances: dict[str, EdwInstanceState] = bucket.setdefault("instances", {})
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

    # Drift-check cross-validation: each requested check
    # must be in CHECK_ALL. Empty list means "all checks"
    # (mirrors the include_integrations empty-means-all
    # pattern in this same handler).
    drift_checks_raw: list[str] = list(data["drift_checks_raw"])
    unknown_checks = [c for c in drift_checks_raw if c not in logic.CHECK_ALL]
    if unknown_checks:
        bad = ", ".join(sorted(unknown_checks))
        valid = ", ".join(sorted(logic.CHECK_ALL))
        errors.append(
            f"drift_checks: unknown value(s) {bad}. Valid values: {valid}."
        )
    drift_checks: frozenset[str] = (
        logic.CHECK_ALL if not drift_checks_raw else frozenset(drift_checks_raw)
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
    exclude_entity_name_regex, en_errors = validate_and_join_regex_patterns(
        data["exclude_entity_name_regex_raw"],
        "exclude_entity_name_regex",
    )
    errors.extend(en_errors)

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
        drift_checks=drift_checks,
        include_integrations=list(data["include_integrations_raw"]),
        exclude_integrations=list(data["exclude_integrations_raw"]),
        exclude_device_name_regex=exclude_device_name_regex,
        exclude_entities=list(data["exclude_entities_raw"]),
        exclude_entity_id_regex=exclude_entity_id_regex,
        exclude_entity_name_regex=exclude_entity_name_regex,
        check_interval_minutes=data["check_interval_minutes_raw"],
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
    drift_checks: frozenset[str],
    include_integrations: list[str],
    exclude_integrations: list[str],
    exclude_device_name_regex: str,
    exclude_entities: list[str],
    exclude_entity_id_regex: str,
    exclude_entity_name_regex: str,
    check_interval_minutes: int,
    max_notifications: int,
    debug_logging: bool,
) -> None:
    """Run a scan + dispatch notifications + persist diagnostics."""
    state = _instances(hass).setdefault(
        instance_id,
        EdwInstanceState(instance_id=instance_id),
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
        drift_checks=drift_checks,
        exclude_device_name_regex=exclude_device_name_regex,
        exclude_entity_ids=exclude_entities,
        exclude_entity_id_regex=exclude_entity_id_regex,
        exclude_entity_name_regex=exclude_entity_name_regex,
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
    )
    deviceless_entities, peers_by_domain = _build_deviceless_inputs(
        hass,
        logic.DEVICELESS_DOMAINS,
        target_integrations,
    )

    # Heavy work (per-device drift classification,
    # deviceless collision-suffix scan, notification
    # body assembly) runs in HA's executor pool so the
    # event loop stays responsive.
    ev = await hass.async_add_executor_job(
        logic.run_evaluation,
        config,
        devices,
        deviceless_entities,
        peers_by_domain,
        len(all_integrations),
        max_notifications,
    )

    # Sweep so prior-run notifications no longer present
    # this run (e.g. a device whose drift cleared between
    # runs) get dismissed automatically.
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
            "entity_name_issues": ev.stat_name_issues,
            "entity_id_issues": ev.stat_id_issues,
            "deviceless_entities": ev.stat_deviceless_entities,
            "deviceless_excluded": ev.stat_deviceless_excluded,
            "deviceless_drift": ev.stat_deviceless_drift,
            "deviceless_stale": ev.stat_deviceless_stale,
        },
    )

    if debug_logging:
        _LOGGER.warning(
            "%s integrations=%d devices=%d entities=%d"
            " device_issues=%d entity_issues=%d"
            " deviceless_drift=%d deviceless_stale=%d",
            tag,
            ev.all_integrations_count,
            len(ev.results),
            ev.stat_entities,
            ev.issues_count,
            ev.stat_entity_issues,
            ev.stat_deviceless_drift,
            ev.stat_deviceless_stale,
        )


# --------------------------------------------------------
# Truth-set assembly (event-loop only)
# --------------------------------------------------------


def _build_device_inputs(
    hass: HomeAssistant,
    all_integration_ids: list[str],
    target_integrations: set[str],
) -> list[logic.DeviceInfo]:
    """Walk registries to build ``DeviceInfo`` per device.

    Always scans every integration so multi-integration
    detection (which gates the recommended-override path)
    stays accurate. ``target_integrations`` filters which
    integrations populate entity drift snapshots; the
    device's ``integration_entities`` map carries every
    integration the device touches but only the targeted
    ones contribute ``EntityDriftInfo`` rows.

    ``all_integration_ids`` is threaded in by the caller
    (already computed for filter resolution) so we don't
    re-walk the entity registry here just to enumerate
    integrations.
    """
    ent_reg = er.async_get(hass)
    dev_reg = dr.async_get(hass)

    device_map: dict[str, logic.DeviceEntry] = {}
    # Map device_id -> {integration_id -> [entity_id]} for
    # the entities we want full drift snapshots on.
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
            if entry.disabled_by is not None:
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
        entity_infos: list[logic.EntityDriftInfo] = []
        for eids in populate_eids.get(dev_id, {}).values():
            for eid in eids:
                entry = ent_reg.async_get(eid)
                if entry is None:
                    continue
                expected_id = str(ent_reg.async_regenerate_entity_id(entry))
                has_name_override = entry.name is not None
                current_name = str(
                    entry.name or entry.original_name or "",
                )
                expected_name: str | None = None
                if has_name_override:
                    expected_name = str(entry.original_name or "")
                entity_infos.append(
                    logic.EntityDriftInfo(
                        entity_id=eid,
                        has_entity_name=entry.has_entity_name,
                        has_name_override=has_name_override,
                        expected_entity_id=expected_id,
                        current_name=current_name,
                        expected_name=expected_name,
                    ),
                )
        devices.append(
            logic.DeviceInfo(de=dev_entry, entities=entity_infos),
        )
    return devices


def _default_friendly_name(obj_id: str) -> str:
    """HA-style default friendly name for an ``obj_id``.

    Mirrors what HA shows for an entity lacking a
    ``friendly_name`` attribute: underscores become spaces
    and the result is title-cased. ``slugify`` round-trips
    this back to ``obj_id`` so a deviceless entity with no
    explicit name is classified as non-drifting by default.
    """
    return obj_id.replace("_", " ").title()


def _build_deviceless_inputs(
    hass: HomeAssistant,
    domains: frozenset[str],
    target_integrations: set[str] | None,
) -> tuple[list[logic.DevicelessEntityInfo], dict[str, set[str]]]:
    """Walk registry + state list for deviceless entities.

    Primary source: entity registry entries where
    ``device_id is None`` and domain is in ``domains``.
    Supplementary source: state-list entities in the same
    domains not present in the registry at all (YAML-
    defined entities without ``unique_id:``) -- caught via
    their state's ``friendly_name`` attribute.

    ``target_integrations`` optionally restricts the
    registry-backed slice to entries whose ``platform`` is
    in the set. State-only entries have no platform and
    are unaffected by this filter; ``None`` means no
    filtering.

    Returns ``(entities, peers_by_domain)``. ``peers`` is
    the union of registry and state-only object_ids per
    domain and is NOT integration-filtered, so the logic
    module's collision-suffix classifier still sees every
    peer that could justify a ``_N`` suffix.
    """
    entities: list[logic.DevicelessEntityInfo] = []
    peers: dict[str, set[str]] = {}
    # Track every registry entity_id (including device-
    # attached and disabled entries) so the state-list
    # safety net only picks up entities that truly have
    # no registry entry.
    seen_eids: set[str] = set()

    ent_reg = er.async_get(hass)
    for entry in ent_reg.entities.values():
        seen_eids.add(entry.entity_id)
        if entry.device_id is not None:
            continue
        if entry.disabled_by is not None:
            continue
        dom, obj = entry.entity_id.split(".", 1)
        if dom not in domains:
            continue
        # Add to peers BEFORE the integration filter so
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
            logic.DevicelessEntityInfo(
                entity_id=entry.entity_id,
                effective_name=effective,
                platform=entry.platform,
                unique_id=entry.unique_id,
                from_registry=True,
                config_entry_id=entry.config_entry_id,
            ),
        )

    # State-only safety net -- YAML entities without a
    # unique_id don't appear in the registry but do have
    # state. friendly_name comparison: when it equals HA's
    # default (title-cased obj_id) slugify will match
    # obj_id exactly and the logic module won't flag it.
    for st in hass.states.async_all():
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
            logic.DevicelessEntityInfo(
                entity_id=eid,
                effective_name=fn,
                platform=None,
                unique_id=None,
                from_registry=False,
            ),
        )
        peers.setdefault(dom, set()).add(obj)

    return (entities, peers)


# --------------------------------------------------------
# Periodic timer + recovery kick
# --------------------------------------------------------


def _ensure_timer(
    hass: HomeAssistant,
    entry: ConfigEntry,
    state: EdwInstanceState,
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
    """Register EDW's service + lifecycle via the shared helper."""
    await register_blueprint_handler(hass, entry, _SPEC)


async def async_unregister(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Tear down EDW's service + lifecycle via the shared helper."""
    await unregister_blueprint_handler(hass, entry, _SPEC)


__all__ = [
    "BLUEPRINT_PATH",
    "EdwInstanceState",
    "async_register",
    "async_unregister",
]
