# This is AI generated code
"""HA wiring for reference_watchdog.

Three-layer dispatch (entrypoint / argparse / service)
mirroring the trigger_entity_controller and
zwave_route_manager ports. RW-specific shape:

- Periodic scan via integration-owned scheduling. The
  blueprint's ``time_pattern`` minute trigger is gone;
  ``helpers.schedule_periodic_with_jitter`` arms a per-
  instance offset so multiple watchdog instances don't
  hammer the registries simultaneously on shared
  intervals.
- Truth set (entity / device / service / label registries
  + live states) is built on the event loop because HA
  registries are loop-only. Heavy work (filesystem walk,
  YAML parsing, jinja AST extraction, notification
  building) runs in HA's executor via
  ``hass.async_add_executor_job(logic.run_evaluation, ...)``.
- Two notification categories: per-owner findings (capped
  by ``max_source_notifications`` via
  ``helpers.prepare_notifications``) plus a single
  source-orphans summary slot. The complete per-instance
  notification set is sweep-dispatched via
  ``process_persistent_notifications_with_sweep`` so
  prior-run notifications no longer present this run get
  cleaned up.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from ..const import DOMAIN
from ..helpers import (
    BlueprintHandlerSpec,
    automation_friendly_name,
    instance_id_for_config_error,
    make_emit_config_error,
    process_persistent_notifications_with_sweep,
    register_blueprint_handler,
    schedule_periodic_with_jitter,
    spec_bucket,
    unregister_blueprint_handler,
    update_instance_state,
    validate_and_join_regex_patterns,
)
from . import logic

_LOGGER = logging.getLogger(__name__)

_SERVICE = "reference_watchdog"
_SERVICE_TAG = "RW"
_SERVICE_NAME = "Reference Watchdog"
BLUEPRINT_PATH = "blueprint_toolkit/reference_watchdog.yaml"


# --------------------------------------------------------
# Per-instance in-memory state
# --------------------------------------------------------


@dataclass
class RwInstanceState:
    """In-memory state for one RW automation instance.

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
        vol.Required("exclude_paths_raw"): vol.Coerce(str),
        vol.Required("exclude_integrations_raw"): vol.All(
            cv.ensure_list, [vol.Coerce(str)]
        ),
        vol.Required("exclude_entities_raw"): vol.All(
            cv.ensure_list, [vol.Coerce(str)]
        ),
        vol.Required("exclude_entity_regex_raw"): vol.Coerce(str),
        vol.Required("check_disabled_entities_raw"): cv.boolean,
        vol.Required("check_interval_minutes_raw"): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=10080)
        ),
        vol.Required("max_source_notifications_raw"): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=1000)
        ),
        vol.Required("debug_logging_raw"): cv.boolean,
    },
    extra=vol.ALLOW_EXTRA,
)


# --------------------------------------------------------
# Per-instance state accessor
# --------------------------------------------------------


def _instances(hass: HomeAssistant) -> dict[str, RwInstanceState]:
    """Per-instance state map under our service's bucket."""
    entries = hass.config_entries.async_entries(DOMAIN)
    if not entries:
        return {}
    bucket = spec_bucket(entries[0], _SERVICE)
    instances: dict[str, RwInstanceState] = bucket.setdefault("instances", {})
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


_emit_config_error = make_emit_config_error(
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
        await _emit_config_error(
            hass,
            instance_id_for_config_error(raw),
            [f"schema: {sub}" for sub in err.errors],
        )
        return
    except vol.Invalid as err:
        await _emit_config_error(
            hass,
            instance_id_for_config_error(raw),
            [f"schema: {err}"],
        )
        return

    instance_id: str = data["instance_id"]
    errors: list[str] = []

    # Multi-line input: split, validate per-line, reject
    # empty-matching patterns (``.*`` / ``|||||`` / ``a?``
    # would silently exclude everything), and join the
    # surviving lines with ``|`` so the logic module gets
    # a single alternation regex it can hand to
    # ``re.search``.
    exclude_entity_regex, regex_errors = validate_and_join_regex_patterns(
        data["exclude_entity_regex_raw"],
        "exclude_entity_regex",
    )
    errors.extend(regex_errors)

    # Argparse complete; emit accumulated errors (or
    # dismiss any prior config_error notification).
    await _emit_config_error(hass, instance_id, errors)
    if errors:
        return

    # Parse the multi-line exclude_paths input the way
    # pyscript did: one path per line, stripped, empty
    # lines dropped.
    exclude_paths: list[str] = [
        p.strip() for p in data["exclude_paths_raw"].splitlines() if p.strip()
    ]

    await _async_service_layer(
        hass,
        call,
        instance_id=instance_id,
        trigger_id=data["trigger_id"],
        exclude_paths=exclude_paths,
        exclude_integrations=list(data["exclude_integrations_raw"]),
        exclude_entities=list(data["exclude_entities_raw"]),
        exclude_entity_regex=exclude_entity_regex,
        check_disabled_entities=data["check_disabled_entities_raw"],
        check_interval_minutes=data["check_interval_minutes_raw"],
        max_notifications=data["max_source_notifications_raw"],
        debug_logging=data["debug_logging_raw"],
    )


# --------------------------------------------------------
# Layer 3: service layer
# --------------------------------------------------------


async def _async_service_layer(
    hass: HomeAssistant,
    call: ServiceCall,
    *,
    instance_id: str,
    trigger_id: str,
    exclude_paths: list[str],
    exclude_integrations: list[str],
    exclude_entities: list[str],
    exclude_entity_regex: str,
    check_disabled_entities: bool,
    check_interval_minutes: int,
    max_notifications: int,
    debug_logging: bool,
) -> None:
    """Run a scan + dispatch notifications + persist diagnostics."""
    started = time.monotonic()
    state = _instances(hass).setdefault(
        instance_id,
        RwInstanceState(instance_id=instance_id),
    )

    # Make sure the periodic timer is armed with the
    # current interval (handles first-run + interval
    # changes mid-flight).
    _ensure_timer(hass, state, check_interval_minutes)

    now = dt_util.now()
    notif_prefix = _notification_prefix(instance_id)
    tag = f"[{_SERVICE_TAG}: {automation_friendly_name(hass, instance_id)}]"

    config = logic.Config(
        exclude_paths=exclude_paths,
        exclude_integrations=exclude_integrations,
        exclude_entities=exclude_entities,
        exclude_entity_regex=exclude_entity_regex,
        check_disabled_entities=check_disabled_entities,
        notification_prefix=notif_prefix,
    )

    # Build the truth set on the event loop -- the
    # registries it queries are loop-only.
    truth_set = _build_truth_set(hass)

    config_dir = hass.config.config_dir

    # Heavy work (filesystem walk, YAML/jinja parsing,
    # notification building) runs in HA's executor pool
    # so the event loop stays responsive.
    ev = await hass.async_add_executor_job(
        logic.run_evaluation,
        config_dir,
        config,
        truth_set,
        exclude_paths,
        max_notifications,
    )

    # Sweep so prior-run notifications no longer present
    # this run (e.g. an owner whose findings cleared
    # between runs) get dismissed automatically.
    await process_persistent_notifications_with_sweep(
        hass,
        ev.notifications,
        sweep_prefix=notif_prefix,
    )

    # Persist diagnostic state.
    update_instance_state(
        hass,
        service=_SERVICE,
        instance_id=instance_id,
        last_run=now,
        runtime=time.monotonic() - started,
        extra_attributes={
            "last_trigger": trigger_id or "",
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

    if debug_logging:
        _LOGGER.warning(
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


def _notification_prefix(instance_id: str) -> str:
    """Common prefix for the RW notification family."""
    return f"blueprint_toolkit_{_SERVICE}__{instance_id}__"


# --------------------------------------------------------
# Truth-set assembly (event-loop only)
# --------------------------------------------------------


def _build_truth_set(hass: HomeAssistant) -> logic.TruthSet:
    """Assemble a TruthSet from live HA runtime state.

    Pulls entity registry, device registry, hass.states,
    hass.services (for the negative truth set), and
    label registry into a single TruthSet dataclass
    instance that the logic module uses for validation
    and owner lookup. TruthSet is frozen, so accumulate
    into mutable staging collections and construct it
    once at the end.
    """
    entity_ids: set[str] = set()
    disabled_entity_ids: set[str] = set()
    device_ids: set[str] = set()
    service_names: set[str] = set()
    label_ids: set[str] = set()
    domains: set[str] = set(logic.SEED_DOMAINS)
    registry: dict[str, logic.RegistryEntry] = {}
    entity_by_unique_id: dict[tuple[str, str], str] = {}
    config_entries_with_entities: set[str] = set()

    ent_reg = er.async_get(hass)
    for entry in ent_reg.entities.values():
        eid = entry.entity_id
        entity_ids.add(eid)
        domains.add(eid.split(".", 1)[0])
        is_disabled = entry.disabled_by is not None
        if is_disabled:
            disabled_entity_ids.add(eid)
        platform = entry.platform or ""
        unique_id = str(entry.unique_id or "")
        registry[eid] = logic.RegistryEntry(
            entity_id=eid,
            platform=platform,
            unique_id=unique_id,
            config_entry_id=entry.config_entry_id,
            disabled=is_disabled,
            name=entry.name,
            original_name=entry.original_name,
        )
        if platform and unique_id:
            entity_by_unique_id[(platform, unique_id)] = eid
        if entry.config_entry_id:
            config_entries_with_entities.add(entry.config_entry_id)

    dev_reg = dr.async_get(hass)
    for device in dev_reg.devices.values():
        device_ids.add(device.id)

    # Live states catch built-ins (sun.sun, weather.home)
    # not in the entity registry.
    for s in hass.states.async_all():
        eid = s.entity_id
        entity_ids.add(eid)
        domains.add(eid.split(".", 1)[0])

    # Service registry -- negative truth set that filters
    # sniff matches that look like entity IDs but are
    # actually registered services.
    services = hass.services.async_services()
    for dom, svcs in services.items():
        for svc_name in svcs:
            service_names.add(f"{dom}.{svc_name}")

    # Label registry (v1: stored but not validated by any
    # adapter yet; see docs follow-ups).
    try:
        from homeassistant.helpers import (  # noqa: PLC0415
            label_registry as lr,
        )

        lab_reg = lr.async_get(hass)
        for label in lab_reg.labels.values():
            label_ids.add(label.label_id)
    except (ImportError, AttributeError):
        pass

    return logic.TruthSet(
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


# --------------------------------------------------------
# Periodic timer + recovery kick
# --------------------------------------------------------


def _ensure_timer(
    hass: HomeAssistant,
    state: RwInstanceState,
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
        interval=timedelta(minutes=interval_minutes),
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
        # Override variable is flat (NOT under ``trigger.*``)
        # because HA's automation.trigger service
        # unconditionally clobbers the ``trigger`` key with
        # ``{"platform": None}``. The blueprint action reads
        # ``trigger_id`` directly.
        await hass.services.async_call(
            "automation",
            "trigger",
            {
                "entity_id": instance_id,
                "skip_condition": True,
                "variables": {"trigger_id": "periodic"},
            },
        )

    return _on_tick


async def _async_kick_for_recovery(
    hass: HomeAssistant,
    entity_id: str,
) -> None:
    """Fire a manual scan so the instance bootstraps its timer.

    Override variable is flat (NOT under ``trigger.*``);
    see ``_make_periodic_callback`` for the full reasoning.
    """
    await hass.services.async_call(
        "automation",
        "trigger",
        {
            "entity_id": entity_id,
            "skip_condition": True,
            "variables": {"trigger_id": "manual"},
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
    """Register RW's service + lifecycle via the shared helper."""
    await register_blueprint_handler(hass, entry, _SPEC)


async def async_unregister(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Tear down RW's service + lifecycle via the shared helper."""
    await unregister_blueprint_handler(hass, entry, _SPEC)


__all__ = [
    "BLUEPRINT_PATH",
    "RwInstanceState",
    "async_register",
    "async_unregister",
]
