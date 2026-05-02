# This is AI generated code
"""Lifecycle / setup-time helpers (function-body HA imports OK).

The "lifecycle" group of the three-flavour split
documented in ``helpers.py``'s shim docstring. These
helpers wire setup-time / registration-time HA machinery
and late-import HA modules inside their function bodies
to keep module import cheap.

Module-scope rule: module-scope ``homeassistant.*``
imports must be under ``if TYPE_CHECKING:``. Function-
body imports are unrestricted (the whole point of the
lifecycle group). The structural test
``test_helpers_lifecycle_module_scope_ha_imports_are_type_checking_only``
enforces this via AST walk.

Cross-flavour rule: this file may import from
``helpers_logic`` and ``helpers_runtime``.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from .const import DOMAIN
from .helpers_logic import (
    _UNSUBS_KEY,
    BlueprintHandlerSpec,
    LifecycleMutators,
    parse_entity_registry_update,
    spec_bucket,
)
from .helpers_runtime import (
    kick_via_automation_trigger,
)

if TYPE_CHECKING:
    from homeassistant.core import Event, HomeAssistant, ServiceCall

_LOGGER = logging.getLogger(__name__)


def all_integration_ids(hass: HomeAssistant) -> list[str]:
    """All distinct integration IDs across the entity registry.

    Used by the watchdog handlers to populate the truth set
    that include / exclude filters then narrow. Lives in the
    lifecycle flavour because it needs a function-body
    ``from homeassistant.helpers import entity_registry``
    -- HA removed the ``hass.helpers.*`` accessor surface,
    so module-imports are the only path to the registry.
    """
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    ent_reg = er.async_get(hass)
    integrations: set[str] = set()
    for entry in ent_reg.entities.values():
        if entry.platform:
            integrations.add(entry.platform)
    return sorted(integrations)


def cv_ha_domain_list(value: object) -> list[str]:
    """Validate a list of HA integration / domain slugs.

    Coerces the input to a list (per ``cv.ensure_list``),
    then rejects any item that doesn't match HA's actual
    domain charset (``homeassistant.core.valid_domain``):
    lowercase letters / digits / underscores, no leading
    or trailing underscore, no double-underscores. Leading
    digits are allowed (real HA core integrations like
    ``3_day_blinds`` rely on this).

    Designed for use as a ``vol.Schema`` value.
    """
    import voluptuous as vol
    from homeassistant.core import valid_domain
    from homeassistant.helpers import config_validation as cv

    items = [str(i) for i in cv.ensure_list(value)]
    invalid = [i for i in items if not valid_domain(i)]
    if invalid:
        msg = (
            f"Invalid HA integration / domain id(s): "
            f"{', '.join(repr(i) for i in invalid)}. "
            "Each value must be lowercase letters, digits, "
            "and underscores, with no leading or trailing "
            "underscore and no double-underscore."
        )
        raise vol.Invalid(msg)
    return items


def discover_automations_using_blueprint(
    hass: HomeAssistant,
    blueprint_path: str,
) -> list[str]:
    """Return entity_ids of automations using ``blueprint_path``.

    Walks ``hass.data[DATA_COMPONENT].entities`` and
    matches ``BaseAutomationEntity.referenced_blueprint``
    (HA core's ``homeassistant/components/automation/__init__.py``).
    Returns an empty list when the automation component
    isn't loaded yet (early in HA startup).
    """
    from homeassistant.components.automation import (  # noqa: PLC0415
        DATA_COMPONENT,
    )

    component = hass.data.get(DATA_COMPONENT)
    if component is None:
        return []
    return [
        ent.entity_id
        for ent in component.entities
        if getattr(ent, "referenced_blueprint", None) == blueprint_path
    ]


async def recover_at_startup(
    hass: HomeAssistant,
    *,
    service_tag: str,
    blueprint_path: str,
    kick: Callable[[HomeAssistant, str], Awaitable[None]],
) -> None:
    """Discover, log, and kick every automation using ``blueprint_path``.

    Fires the per-port ``kick`` callable once per
    discovered automation entity_id. Standardises the
    "no automations discovered" / "kicking N for catch-up"
    INFO log lines so all subpackages surface the same
    diagnostic shape.
    """
    discovered = discover_automations_using_blueprint(hass, blueprint_path)
    if not discovered:
        _LOGGER.info(
            "[%s] no automations using %s discovered at startup",
            service_tag,
            blueprint_path,
        )
        return
    _LOGGER.info(
        "[%s] kicking %d discovered automations for catch-up",
        service_tag,
        len(discovered),
    )
    # Best-effort: a single bad automation entity must
    # not stop recovery for the rest of the discovered
    # set. Catch + log, then continue.
    for entity_id in discovered:
        try:
            await kick(hass, entity_id)
        except Exception as e:  # noqa: BLE001
            _LOGGER.warning(
                "[%s] catch-up kick for %s failed: %s",
                service_tag,
                entity_id,
                e,
            )


def schedule_periodic_with_jitter(
    hass: HomeAssistant,
    entry: Any,
    *,
    interval: timedelta,
    instance_id: str,
    action: Callable[[datetime], Awaitable[Any]],
) -> Callable[[], None]:
    """Schedule ``action`` every ``interval`` with a deterministic
    per-instance offset.

    Multiple instances sharing the same interval would
    otherwise all fire on the exact same wall-clock tick
    (HA boot, integration reload arms every per-instance
    timer at the same instant). The jitter spreads them
    across the interval window to avoid a thundering-herd
    on shared registries / file systems / external APIs.

    The offset is derived from a stable hash of
    ``instance_id`` (first 4 bytes of SHA-1, big-endian,
    mod the interval in seconds), so a given automation
    always lands on the same per-interval slot across
    restarts -- handy for log readers correlating across
    days. Mechanically:

    1. Schedule the first call via ``async_call_later``
       at ``now + jitter_seconds``.
    2. When that one-shot fires, arm
       ``async_track_time_interval`` for steady-state
       and run ``action`` once now.

    Returns a single unsubscribe callable that cancels
    whichever timer is currently active. Imported lazily
    to keep module import safe in non-HA test
    environments.

    ``action`` must be a coroutine function; it's invoked
    via ``entry.async_create_background_task`` so an entry
    unload mid-tick cancels the in-flight action rather than
    leaving it running detached against a torn-down service
    registration.
    """
    from homeassistant.core import callback  # noqa: PLC0415
    from homeassistant.helpers.event import (  # noqa: PLC0415
        async_call_later,
        async_track_time_interval,
    )

    interval_seconds = max(1, int(interval.total_seconds()))
    digest = hashlib.sha1(instance_id.encode("utf-8")).digest()
    jitter_seconds = int.from_bytes(digest[:4], "big") % interval_seconds

    # Single-slot mutable holder so the unsub closure can
    # see whichever timer is currently armed (initial
    # one-shot or steady-state interval).
    cancel_holder: dict[str, Callable[[], None] | None] = {"current": None}

    task_name = f"{DOMAIN}_periodic_tick_{instance_id}"

    @callback  # type: ignore[untyped-decorator]
    def _fire_action(now: datetime) -> None:
        # Wrap so every tick (jittered first fire AND each
        # steady-state tick) goes through
        # ``entry.async_create_background_task``. Passing
        # ``action`` directly to ``async_track_time_interval``
        # would route subsequent ticks through HA's internal
        # ``hass.async_create_task``, leaving them detached
        # from entry unload.
        entry.async_create_background_task(hass, action(now), task_name)

    @callback  # type: ignore[untyped-decorator]
    def _on_first_fire(now: datetime) -> None:
        # The one-shot fired and HA already removed it.
        # Arm the steady-state tracker before kicking off
        # the action so an early teardown still cancels
        # subsequent ticks.
        cancel_holder["current"] = async_track_time_interval(
            hass,
            _fire_action,
            interval,
        )
        _fire_action(now)

    cancel_holder["current"] = async_call_later(
        hass,
        jitter_seconds,
        _on_first_fire,
    )

    def _unsub() -> None:
        cur = cancel_holder["current"]
        if cur is not None:
            cur()
            cancel_holder["current"] = None

    return _unsub


def make_lifecycle_mutators(
    *,
    instances_getter: Callable[[HomeAssistant], dict[str, Any]],
    cancel_field: str,
    service_tag: str,
    logger: logging.Logger,
    reset_armed_interval_on_reload: bool = False,
) -> LifecycleMutators:
    """Build the four standard lifecycle mutator callbacks.

    Every blueprint handler keeps a per-instance state map
    keyed by automation entity_id and shares an
    almost-identical shape for the four mutator callbacks
    plumbed through ``BlueprintHandlerSpec``: cancel pending
    timers / wakeups on reload, drop tracked state on
    removal, move tracked state on rename, clear everything
    on teardown.

    ``cancel_field`` is the attribute name of the cancel-
    callable on each instance-state object (typically
    ``cancel_timer`` for periodic handlers,
    ``cancel_wakeup`` for one-shot handlers like TEC).
    Reading via ``getattr`` keeps this generic across the
    field-name variants without forcing a shared dataclass
    base.

    ``reset_armed_interval_on_reload`` clears
    ``armed_interval_minutes`` to 0 on reload; set ``True``
    for handlers whose ``_ensure_timer`` re-arm decision
    compares against this field (DW / EDW / RW / ZRM) and
    leave ``False`` for handlers with no such field
    (STSC / TEC).
    """
    from homeassistant.core import callback  # noqa: PLC0415

    @callback  # type: ignore[untyped-decorator]
    def _on_reload(hass: HomeAssistant) -> None:
        for s in list(instances_getter(hass).values()):
            cancel = getattr(s, cancel_field, None)
            if cancel is not None:
                cancel()
                setattr(s, cancel_field, None)
                if reset_armed_interval_on_reload:
                    s.armed_interval_minutes = 0

    @callback  # type: ignore[untyped-decorator]
    def _on_entity_remove(hass: HomeAssistant, entity_id: str) -> None:
        s = instances_getter(hass).pop(entity_id, None)
        if s is None:
            return
        cancel = getattr(s, cancel_field, None)
        if cancel is not None:
            cancel()
            logger.info(
                "[%s] dropped %s (automation removed)",
                service_tag,
                entity_id,
            )

    @callback  # type: ignore[untyped-decorator]
    def _on_entity_rename(
        hass: HomeAssistant,
        old_id: str,
        new_id: str,
    ) -> None:
        s = instances_getter(hass).pop(old_id, None)
        if s is not None:
            s.instance_id = new_id
            instances_getter(hass)[new_id] = s

    @callback  # type: ignore[untyped-decorator]
    def _on_teardown(hass: HomeAssistant) -> None:
        for s in list(instances_getter(hass).values()):
            cancel = getattr(s, cancel_field, None)
            if cancel is not None:
                cancel()
        instances_getter(hass).clear()

    return LifecycleMutators(
        on_reload=_on_reload,
        on_entity_remove=_on_entity_remove,
        on_entity_rename=_on_entity_rename,
        on_teardown=_on_teardown,
    )


async def register_blueprint_handler(
    hass: HomeAssistant,
    entry: Any,
    spec: BlueprintHandlerSpec,
) -> None:
    """Register the service + every lifecycle hook the spec opted into.

    Idempotent under config-entry reload -- existing
    service registration is removed first; existing
    bus subscriptions are unsubscribed before
    re-subscribing.
    """
    from homeassistant.components.automation import (  # noqa: PLC0415
        EVENT_AUTOMATION_RELOADED,
    )
    from homeassistant.const import EVENT_HOMEASSISTANT_STARTED  # noqa: PLC0415
    from homeassistant.core import callback  # noqa: PLC0415
    from homeassistant.helpers import (  # noqa: PLC0415
        entity_registry as er,
    )

    bucket = spec_bucket(entry, spec.service)

    # --- Service registration (always) ---
    if hass.services.has_service(DOMAIN, spec.service):
        hass.services.async_remove(DOMAIN, spec.service)

    async def _service_wrapper(call: ServiceCall) -> None:
        await spec.service_handler(hass, call)

    hass.services.async_register(DOMAIN, spec.service, _service_wrapper)

    # Idempotent re-register: tear down every prior unsub
    # before re-subscribing so listener counts stay 1.
    unsubs: list[Callable[[], None]] = bucket[_UNSUBS_KEY]
    for prior in unsubs:
        prior()
    unsubs.clear()

    # Local-capture the optional hooks so closures see
    # the narrowed (non-None) type and so mypy doesn't
    # have to track narrowing through closure boundaries.
    on_reload = spec.on_reload
    on_entity_remove = spec.on_entity_remove
    on_entity_rename = spec.on_entity_rename
    # The ``kick`` action is derived from ``spec.kick_variables``
    # if set: every per-port kick is just an
    # ``automation.trigger`` with a flat-variables payload, so
    # the spec carries the payload and the dispatcher builds
    # the action. Per-handler ``_async_kick_for_recovery``
    # wrappers have all been deleted.
    kick: Callable[[HomeAssistant, str], Awaitable[None]] | None
    if spec.kick_variables is not None:
        kick_variables = spec.kick_variables

        async def _kick(hass: HomeAssistant, entity_id: str) -> None:
            await kick_via_automation_trigger(hass, entity_id, kick_variables)

        kick = _kick
    else:
        kick = None

    # --- Reload listener (if any per-reload behaviour
    # is configured) ---
    if on_reload is not None or kick is not None:
        reload_recover_task_name = f"{DOMAIN}_{spec.service}_reload_recover"

        @callback  # type: ignore[untyped-decorator]
        def _reload_listener(_event: Event) -> None:
            if on_reload is not None:
                on_reload(hass)
            if kick is not None:
                # Entry-scoped: matches the startup-recovery
                # path below. Without this, an entry unload
                # racing the reload would leave the recover
                # task running detached against a torn-down
                # service registration.
                entry.async_create_background_task(
                    hass,
                    recover_at_startup(
                        hass,
                        service_tag=spec.service_tag,
                        blueprint_path=spec.blueprint_path,
                        kick=kick,
                    ),
                    reload_recover_task_name,
                )

        unsubs.append(
            hass.bus.async_listen(
                EVENT_AUTOMATION_RELOADED,
                _reload_listener,
            ),
        )

    # --- Entity-registry listener (if either remove or
    # rename hook is set) ---
    if on_entity_remove is not None or on_entity_rename is not None:

        @callback  # type: ignore[untyped-decorator]
        def _er_listener(event: Event) -> None:
            parsed = parse_entity_registry_update(event.data)
            if parsed is None:
                return
            action, old_id, new_id = parsed
            if action == "remove" and on_entity_remove is not None:
                on_entity_remove(hass, old_id)
            elif (
                action == "update"
                and old_id != new_id
                and on_entity_rename is not None
            ):
                on_entity_rename(hass, old_id, new_id)

        unsubs.append(
            hass.bus.async_listen(
                er.EVENT_ENTITY_REGISTRY_UPDATED,
                _er_listener,
            ),
        )

    # --- Restart recovery (if kick is configured) ---
    if kick is not None:
        # Both branches schedule via
        # ``entry.async_create_background_task`` rather than
        # ``hass.async_create_task`` so the recovery work is
        # entry-scoped: if the config entry unloads (e.g.
        # the user disables the integration) while the task
        # is still queued or mid-flight, HA cancels it
        # automatically. Without this, an unload that races
        # the recover task would leave kicks firing into a
        # detached service registration.
        recover_task_name = f"{DOMAIN}_{spec.service}_recover_at_startup"
        if hass.is_running:
            entry.async_create_background_task(
                hass,
                recover_at_startup(
                    hass,
                    service_tag=spec.service_tag,
                    blueprint_path=spec.blueprint_path,
                    kick=kick,
                ),
                recover_task_name,
            )
        else:
            # ``async_listen_once`` returns an unsubscribe
            # callable AND auto-detaches the listener when
            # the event fires. If the listener fires and we
            # later call the stored unsub (e.g. on
            # integration unload), HA logs ``Unable to
            # remove unknown job listener`` at ERROR level.
            # Drop our bookkeeping handle synchronously
            # inside the dispatch so any concurrent
            # ``unregister_blueprint_handler`` won't see it.
            #
            # The wrapper is ``@callback`` (sync) so the
            # ``unsubs.remove`` runs in the same synchronous
            # block as HA's listener detach inside
            # ``Bus.async_fire``; the background-task
            # creation then schedules the actual recovery
            # work. If the wrapper were ``async def``
            # instead, the recovery would be scheduled as a
            # separate task and there'd be a (tiny but real)
            # race window where unregister could fire and
            # call the stale unsub before our async body
            # removed it.
            once_unsub: Callable[[], None] | None = None

            @callback  # type: ignore[untyped-decorator]
            def _on_started_sync(_event: Event) -> None:
                if once_unsub is not None and once_unsub in unsubs:
                    unsubs.remove(once_unsub)
                entry.async_create_background_task(
                    hass,
                    recover_at_startup(
                        hass,
                        service_tag=spec.service_tag,
                        blueprint_path=spec.blueprint_path,
                        kick=kick,
                    ),
                    recover_task_name,
                )

            once_unsub = hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED,
                _on_started_sync,
            )
            # Stored so unregister can detach the listener
            # if the entry unloads before HA finishes
            # starting (i.e. before the once-listener fires
            # and removes itself).
            unsubs.append(once_unsub)

    _LOGGER.info(
        "%s [%s]: service %s.%s registered (blueprint=%s)",
        spec.service_name,
        spec.service_tag,
        DOMAIN,
        spec.service,
        spec.blueprint_path,
    )


__all__ = [
    "all_integration_ids",
    "cv_ha_domain_list",
    "discover_automations_using_blueprint",
    "make_lifecycle_mutators",
    "recover_at_startup",
    "register_blueprint_handler",
    "schedule_periodic_with_jitter",
]
