# This is AI generated code
"""Shared helpers for blueprint_toolkit subpackages.

Utility surface that subpackage logic + handler modules
share. Lifted incrementally as ports land (today: TEC;
future: DW, EDW, RW, STSC, ZWRM).

Three flavours of symbol live here:

- **Pure** (no HA imports): ``format_timestamp``,
  ``format_notification``, ``PersistentNotification``,
  ``make_config_error_notification``,
  ``parse_entity_registry_update``. Safe to import from
  non-HA test environments.
- **Runtime-HA** (uses the runtime ``hass`` argument
  but doesn't import HA at module scope):
  ``process_persistent_notifications``,
  ``emit_config_error``, ``recover_at_startup``. Module
  import succeeds outside HA; calling the function
  needs a real ``HomeAssistant`` instance.
- **Lifecycle** (late-imports HA inside the function):
  ``discover_automations_using_blueprint``,
  ``register_blueprint_handler``,
  ``unregister_blueprint_handler``. Module import still
  succeeds outside HA; calling these forces the late
  import.

Subsystem identifier convention:

- ``service`` -- slug used for the HA service name
  (``blueprint_toolkit.<service>``) and as the bucket
  key under ``hass.data[DOMAIN]``. Same string in both
  places by design. Example: ``trigger_entity_controller``.
- ``service_tag`` -- short tag for notification titles
  and per-event log lines. Example: ``TEC``.
- ``service_name`` -- human-readable name for the
  one-time registration log and any other verbose
  context. Example: ``Trigger Entity Controller``.

Notification IDs follow the convention
``blueprint_toolkit_{service}__{instance_id}__{kind}``
so each subpackage's notifications stay disambiguated
in the HA persistent-notification namespace.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import Event, HomeAssistant, ServiceCall

_LOGGER = logging.getLogger(__name__)


# --------------------------------------------------------
# Timestamp + notification text formatting
# --------------------------------------------------------


def format_timestamp(template: str, dt: datetime) -> str:
    """Format timestamp tokens in a template string.

    Supported tokens: YYYY, YY, MM, DD, HH, mm, ss.
    """
    if not template:
        return ""
    # Replace longest tokens first so YYYY is consumed
    # before YY can match.
    result = template
    result = result.replace("YYYY", f"{dt.year:04d}")
    result = result.replace("YY", f"{dt.year % 100:02d}")
    result = result.replace("MM", f"{dt.month:02d}")
    result = result.replace("DD", f"{dt.day:02d}")
    result = result.replace("HH", f"{dt.hour:02d}")
    result = result.replace("mm", f"{dt.minute:02d}")
    result = result.replace("ss", f"{dt.second:02d}")
    return result


def format_notification(
    text: str,
    prefix: str,
    suffix: str,
    current_time: datetime,
) -> str:
    """Format notification with prefix/suffix and timestamp tokens."""
    formatted_prefix = format_timestamp(prefix, current_time)
    formatted_suffix = format_timestamp(suffix, current_time)
    return f"{formatted_prefix}{text}{formatted_suffix}"


# --------------------------------------------------------
# Persistent notification spec + dispatcher
# --------------------------------------------------------


@dataclass
class PersistentNotification:
    """A persistent notification to create or dismiss.

    ``active=True`` means create (or refresh in place);
    ``active=False`` means dismiss. Pure data so logic
    layers can return these without taking an HA
    dependency, and ``process_persistent_notifications``
    can apply them in one batch.
    """

    active: bool
    notification_id: str
    title: str
    message: str


async def process_persistent_notifications(
    hass: HomeAssistant,
    notifications: list[PersistentNotification],
) -> None:
    """Apply a batch of notification specs against HA.

    Each ``active`` entry becomes a
    ``persistent_notification.create`` call; each
    inactive entry becomes a
    ``persistent_notification.dismiss`` call (which is
    a no-op if the notification doesn't exist, so it's
    always safe to fire).
    """
    for n in notifications:
        if n.active:
            await hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "notification_id": n.notification_id,
                    "title": n.title,
                    "message": n.message,
                },
            )
        else:
            await hass.services.async_call(
                "persistent_notification",
                "dismiss",
                {"notification_id": n.notification_id},
            )


# --------------------------------------------------------
# Config-error notification convention
# --------------------------------------------------------


def _config_error_notification_id(service: str, instance_id: str) -> str:
    # ``__`` is reserved as the field separator. HA entity_ids
    # (which is what ``instance_id`` always is) cannot contain
    # ``__`` -- ``slugify`` collapses repeated underscores --
    # so the resulting ID stays unambiguously parseable
    # ``blueprint_toolkit_{service}__{instance_id}__{kind}``.
    return f"blueprint_toolkit_{service}__{instance_id}__config_error"


def make_config_error_notification(
    *,
    service: str,
    service_tag: str,
    instance_id: str,
    errors: list[str],
) -> PersistentNotification:
    """Build a config-error spec with the standard wire format.

    When ``errors`` is empty, the returned spec has
    ``active=False`` -- pass it straight through to the
    dispatcher and any prior config-error notification
    for this instance is dismissed. This lets handlers
    call ``emit_config_error`` unconditionally on every
    successful argparse without branching.
    """
    notif_id = _config_error_notification_id(service, instance_id)
    if not errors:
        return PersistentNotification(
            active=False,
            notification_id=notif_id,
            title="",
            message="",
        )
    title = f"Blueprint Toolkit -- {service_tag} config error: {instance_id}"
    message = "\n".join(f"- {e}" for e in errors)
    return PersistentNotification(
        active=True,
        notification_id=notif_id,
        title=title,
        message=message,
    )


async def emit_config_error(
    hass: HomeAssistant,
    *,
    service: str,
    service_tag: str,
    instance_id: str,
    errors: list[str],
) -> None:
    """Build a config-error spec and dispatch it.

    Convenience wrapper -- handlers typically call this
    once per argparse with whatever ``errors`` they
    accumulated (empty list dismisses any prior
    notification for the same instance).
    """
    spec = make_config_error_notification(
        service=service,
        service_tag=service_tag,
        instance_id=instance_id,
        errors=errors,
    )
    if errors:
        _LOGGER.warning(
            "[%s] config error for %s: %s",
            service_tag,
            instance_id,
            "; ".join(errors),
        )
    await process_persistent_notifications(hass, [spec])


# --------------------------------------------------------
# Blueprint discovery + restart-recovery
# --------------------------------------------------------


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


# --------------------------------------------------------
# Entity-registry event parsing
# --------------------------------------------------------


def parse_entity_registry_update(
    event_data: dict[str, Any],
) -> tuple[str, str, str] | None:
    """Extract ``(action, old_id, new_id)`` for an automation entity event.

    Returns ``None`` when the event is for a non-automation
    entity (the listener fires for every registry change),
    so callers can early-return cleanly. ``action`` is one
    of HA's registry actions: ``create`` / ``update`` /
    ``remove``. The dispatcher in
    ``register_blueprint_handler`` only acts on ``remove``
    and ``update`` (renames); ``create`` events are
    intentionally ignored because new automations come in
    through the blueprint reload path, which the
    automation_reload listener covers.
    """
    action = event_data.get("action")
    new_id = event_data.get("entity_id") or ""
    old_id = event_data.get("old_entity_id") or new_id
    if not (
        new_id.startswith("automation.") or old_id.startswith("automation.")
    ):
        return None
    if not isinstance(action, str):
        return None
    return action, old_id, new_id


# --------------------------------------------------------
# Blueprint handler lifecycle
# --------------------------------------------------------


@dataclass
class BlueprintHandlerSpec:
    """Per-port configuration for a blueprint handler.

    Bundles the identifiers, service callback, and
    optional lifecycle hooks the shared register /
    unregister helpers need to wire up the standard
    plumbing (idempotent service registration, bus
    subscriptions, restart-recovery scheduling, log
    messages).

    Required:
        service: Slug for the HA service registered as
            ``blueprint_toolkit.<service>`` and as the
            bucket key under ``hass.data[DOMAIN]``.
        service_tag: Short tag for notification titles
            and per-event log messages (e.g. ``TEC``).
        service_name: Human-readable name for the
            one-time registration log (e.g.
            ``Trigger Entity Controller``).
        blueprint_path: HA-relative path to the
            blueprint that uses this handler. Used for
            restart-recovery discovery.
        service_handler: Async service callback;
            receives ``(hass, ServiceCall)``.

    All lifecycle hooks default to ``None``. Each
    one a port supplies enables one piece of plumbing;
    a port that needs none of them (e.g. a periodic
    watchdog) gets just the service registration.

    Lifecycle hooks:
        kick: When set, restart-recovery is enabled --
            at HA-started time, every automation using
            ``blueprint_path`` is discovered and ``kick``
            is invoked with its entity_id. The
            automation_reload listener also re-runs
            recovery. Most handlers want this.
        on_reload: When set, ``EVENT_AUTOMATION_RELOADED``
            invokes this synchronously (typical use:
            cancel pending per-instance work whose
            AutomationEntity objects have been
            replaced). Recovery still runs afterwards
            if ``kick`` is also set.
        on_entity_remove: When set, an automation's
            entity-registry remove event invokes this
            with its entity_id (typical use: drop
            tracked state, cancel pending timers).
        on_entity_rename: When set, an automation's
            entity-registry rename event invokes this
            with ``(old_id, new_id)`` (typical use:
            move the per-instance state map entry).
        on_teardown: Invoked from
            ``unregister_blueprint_handler`` (typical
            use: cancel all pending work and clear
            tracked state).
    """

    service: str
    service_tag: str
    service_name: str
    blueprint_path: str
    service_handler: Callable[[HomeAssistant, ServiceCall], Awaitable[None]]
    kick: Callable[[HomeAssistant, str], Awaitable[None]] | None = None
    on_reload: Callable[[HomeAssistant], None] | None = None
    on_entity_remove: Callable[[HomeAssistant, str], None] | None = None
    on_entity_rename: Callable[[HomeAssistant, str, str], None] | None = None
    on_teardown: Callable[[HomeAssistant], None] | None = None


# Bucket key under which ``register_blueprint_handler``
# stashes the unsubscribe callables for every bus
# listener it registered. ``unregister_blueprint_handler``
# iterates and calls each. Generic list (no per-listener
# slot names) so future ports can add new listener types
# without changing the bookkeeping shape.
_UNSUBS_KEY = "unsubs"


def _spec_bucket(hass: HomeAssistant, service: str) -> dict[str, Any]:
    """Per-service slot under ``hass.data[DOMAIN][service]``.

    Created lazily; idempotent so reloads don't lose
    pending unsubscribe handles or per-port state. Each
    port is free to stash additional keys here (e.g.
    TEC keeps its ``instances`` map under the same
    bucket).
    """
    bucket: dict[str, Any] = hass.data.setdefault(DOMAIN, {}).setdefault(
        service,
        {_UNSUBS_KEY: []},
    )
    bucket.setdefault(_UNSUBS_KEY, [])
    return bucket


async def register_blueprint_handler(
    hass: HomeAssistant,
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

    bucket = _spec_bucket(hass, spec.service)

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
    kick = spec.kick

    # --- Reload listener (if any per-reload behaviour
    # is configured) ---
    if on_reload is not None or kick is not None:

        @callback  # type: ignore[untyped-decorator]
        def _reload_listener(_event: Event) -> None:
            if on_reload is not None:
                on_reload(hass)
            if kick is not None:
                hass.async_create_task(
                    recover_at_startup(
                        hass,
                        service_tag=spec.service_tag,
                        blueprint_path=spec.blueprint_path,
                        kick=kick,
                    ),
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
        if hass.is_running:
            hass.async_create_task(
                recover_at_startup(
                    hass,
                    service_tag=spec.service_tag,
                    blueprint_path=spec.blueprint_path,
                    kick=kick,
                ),
            )
        else:

            async def _recover_when_ready(_event: Event) -> None:
                await recover_at_startup(
                    hass,
                    service_tag=spec.service_tag,
                    blueprint_path=spec.blueprint_path,
                    kick=kick,
                )

            # Capture the once-listener unsub: if the
            # config entry unloads before HA finishes
            # starting, unregister tears this listener
            # down so a deferred kick doesn't fire into
            # stale state.
            unsubs.append(
                hass.bus.async_listen_once(
                    EVENT_HOMEASSISTANT_STARTED,
                    _recover_when_ready,
                ),
            )

    _LOGGER.info(
        "%s [%s]: service %s.%s registered (blueprint=%s)",
        spec.service_name,
        spec.service_tag,
        DOMAIN,
        spec.service,
        spec.blueprint_path,
    )


async def unregister_blueprint_handler(
    hass: HomeAssistant,
    spec: BlueprintHandlerSpec,
) -> None:
    """Tear down the service + bus subscriptions + per-port state."""
    bucket = _spec_bucket(hass, spec.service)
    if hass.services.has_service(DOMAIN, spec.service):
        hass.services.async_remove(DOMAIN, spec.service)
    for unsub in bucket[_UNSUBS_KEY]:
        unsub()
    bucket[_UNSUBS_KEY] = []
    if spec.on_teardown is not None:
        spec.on_teardown(hass)
