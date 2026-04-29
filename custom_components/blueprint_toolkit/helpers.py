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


def parse_notification_service(service: str) -> tuple[str, str]:
    """Split a notify-service string into ``(domain, name)``.

    Accepts both ``notify.foo`` (full ``domain.service``)
    and the bare ``foo`` short form, defaulting to the
    ``notify`` domain. Used by per-port handlers in two
    spots: argparse-time validation that the service is
    registered, and the actual dispatch when a finding-
    style notification needs to be sent.
    """
    if "." in service:
        domain, name = service.split(".", 1)
        return domain, name
    return "notify", service


# --------------------------------------------------------
# CommonMark escape for ``persistent_notification`` bodies
# --------------------------------------------------------


def md_escape(s: str) -> str:
    r"""Escape CommonMark ``\``, ``[``, ``]`` for safe interpolation.

    Apply to any HA-controlled string interpolated into a
    ``persistent_notification`` ``message`` body -- both
    inside ``[text](url)`` link text *and* in plain-text
    portions, since an unescaped ``[`` in plain text can
    still pair with a later ``](`` to form a bogus link.

    Done as a single ``str.translate`` pass so the
    backslashes inserted for ``[``/``]`` are not themselves
    re-escaped by the ``\`` mapping.

    Escaping is NOT needed for:

    - Notification ``title`` strings -- HA renders titles
      as plain text (frontend ``persistent-notification-item``
      uses a Lit ``<span>`` with auto-escaping, only
      ``message`` goes through ``<ha-markdown>``).
    - Integration domains and entity_ids -- constrained
      to ``[a-z0-9_]+``, no markdown specials possible.
    - URLs -- the ``(...)`` target portion of a markdown
      link is not displayed, only the ``[...]`` text
      portion is.
    - Numeric IDs (node ids, device counts, byte sizes).
    - Values rendered inside a backtick code span
      (`` `value` ``) -- code spans suppress markdown
      interpretation, so ``[``/``]`` inside backticks
      render literally.

    Escaping IS needed for human-typed strings such as
    automation friendly names, vol.Invalid messages
    (which can include the offending input value),
    error messages from external APIs, etc.
    """
    return s.translate(
        {
            ord("\\"): "\\\\",
            ord("["): "\\[",
            ord("]"): "\\]",
        },
    )


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

    ``instance_id`` is the automation entity_id this
    notification belongs to. When set, the dispatcher
    looks the automation up in ``hass.states`` and
    prepends an ``Automation: [{name}](edit-link)\\n``
    line to the message body so users can click straight
    through to the broken / problematic automation. All
    notification builders that originate from a per-
    instance service call should set this; ad-hoc one-off
    notifications can leave it empty.
    """

    active: bool
    notification_id: str
    title: str
    message: str
    instance_id: str | None = None


def _instance_link_inputs(
    hass: HomeAssistant,
    instance_id: str,
) -> tuple[str | None, str | None]:
    """Look up the friendly name + YAML id for an automation entity.

    Used by ``process_persistent_notifications`` to build
    the ``Automation: [name](edit-link)`` prefix. Returns
    ``(None, None)`` when the automation entity isn't
    registered (e.g. the call came from Developer Tools
    rather than a real automation).
    """
    state = hass.states.get(instance_id)
    if state is None:
        return None, None
    name = state.attributes.get("friendly_name") or instance_id
    yaml_id = state.attributes.get("id")
    if not isinstance(yaml_id, str) or not yaml_id:
        return name, None
    return name, yaml_id


def _automation_link_prefix(
    hass: HomeAssistant,
    instance_id: str | None,
) -> str:
    """Render the ``Automation: [name](edit-link)\\n`` prefix.

    Returns ``""`` when ``instance_id`` is ``None`` or
    the automation entity isn't registered or hasn't been
    given a YAML ``id:``. The friendly name is
    ``md_escape``-d so user-typed ``[`` / ``]`` in the
    name don't corrupt the rendered link.
    """
    if not instance_id:
        return ""
    name, yaml_id = _instance_link_inputs(hass, instance_id)
    if name is None or yaml_id is None:
        return ""
    return (
        f"Automation: [{md_escape(name)}](/config/automation/edit/{yaml_id})\n"
    )


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

    For ``active`` specs whose ``instance_id`` is set,
    the dispatcher prepends an
    ``Automation: [name](edit-link)\\n`` header to the
    message body so users can click through to the
    associated automation. Inactive (dismiss) specs are
    not prefixed -- nothing's being shown.
    """
    for n in notifications:
        if n.active:
            link_prefix = _automation_link_prefix(hass, n.instance_id)
            await hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "notification_id": n.notification_id,
                    "title": n.title,
                    "message": f"{link_prefix}{n.message}",
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

    The body is a markdown bulleted list of the errors;
    ``process_persistent_notifications`` prepends an
    ``Automation: [name](edit-link)\\n`` header when it
    dispatches (driven by the ``instance_id`` field on
    the spec).

    Every interpolated user-controlled string -- each
    entry of ``errors`` -- is ``md_escape``-d here.
    ``vol.Invalid`` messages can include the offending
    input value, which could otherwise smuggle stray
    ``[`` / ``]`` / ``\\`` into the rendered markdown.
    """
    notif_id = _config_error_notification_id(service, instance_id)
    if not errors:
        return PersistentNotification(
            active=False,
            notification_id=notif_id,
            title="",
            message="",
            instance_id=instance_id,
        )
    title = f"Blueprint Toolkit -- {service_tag} config error: {instance_id}"
    message = "\n".join(f"- {md_escape(e)}" for e in errors)
    return PersistentNotification(
        active=True,
        notification_id=notif_id,
        title=title,
        message=message,
        instance_id=instance_id,
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
# Per-instance diagnostic state
# --------------------------------------------------------


def instance_state_entity_id(service: str, instance_id: str) -> str:
    """Build the ``blueprint_toolkit.<service>_<slug>_state`` entity_id.

    ``instance_id`` is the automation entity_id (e.g.
    ``automation.foo_bar``); we strip the
    ``automation.`` prefix so the resulting diagnostic
    entity_id reads cleanly in Developer Tools /
    templates / dashboards.
    """
    slug = instance_id.removeprefix("automation.")
    return f"{DOMAIN}.{service}_{slug}_state"


def update_instance_state(
    hass: HomeAssistant,
    *,
    service: str,
    instance_id: str,
    last_run: datetime,
    runtime: float,
    state: str = "ok",
    extra_attributes: dict[str, Any] | None = None,
) -> None:
    """Surface per-instance runtime state for debugging.

    Sets a state entry at
    ``blueprint_toolkit.<service>_<slug>_state`` with
    ``state`` as the state value (defaults to ``"ok"``;
    handlers that have a more meaningful value, e.g. TEC
    using its ``result.action.name``, override). Common
    diagnostic attributes (``instance_id``, ``last_run``,
    ``runtime``) are always written; handlers pass their
    own through ``extra_attributes``.

    The state entity is visible from
    Developer Tools > States, queryable from templates,
    and consumable by dashboards. See each port's
    handler module for the per-port attribute list.
    """
    attributes: dict[str, Any] = {
        "instance_id": instance_id,
        "last_run": last_run.isoformat(),
        "runtime": round(runtime, 2),
    }
    if extra_attributes:
        attributes.update(extra_attributes)
    hass.states.async_set(
        instance_state_entity_id(service, instance_id),
        state,
        attributes,
    )


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


def spec_bucket(entry: Any, service: str) -> dict[str, Any]:
    """Per-service slot under ``entry.runtime_data.handlers[service]``.

    Created lazily; idempotent so reloads don't lose
    pending unsubscribe handles or per-port state. Each
    port is free to stash additional keys here (e.g.
    TEC keeps its ``instances`` map under the same
    bucket).

    Public (no leading underscore) so per-port handlers
    -- e.g. ``tec/handler.py``'s ``_instances(...)``
    helper -- can fetch their own bucket without
    duplicating the entry-runtime-data wiring.
    """
    handlers: dict[str, dict[str, Any]] = entry.runtime_data.handlers
    bucket = handlers.setdefault(service, {_UNSUBS_KEY: []})
    bucket.setdefault(_UNSUBS_KEY, [])
    return bucket


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


async def unregister_blueprint_handler(
    hass: HomeAssistant,
    entry: Any,
    spec: BlueprintHandlerSpec,
) -> None:
    """Tear down the service + bus subscriptions + per-port state."""
    bucket = spec_bucket(entry, spec.service)
    if hass.services.has_service(DOMAIN, spec.service):
        hass.services.async_remove(DOMAIN, spec.service)
    for unsub in bucket[_UNSUBS_KEY]:
        unsub()
    bucket[_UNSUBS_KEY] = []
    if spec.on_teardown is not None:
        spec.on_teardown(hass)
