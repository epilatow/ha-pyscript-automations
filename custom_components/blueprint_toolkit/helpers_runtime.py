# This is AI generated code
"""Runtime-HA helpers (TYPE_CHECKING-only HA imports).

The "runtime-HA" group of the three-flavour split
documented in ``helpers.py``'s shim docstring. Module
import succeeds outside HA; calling the functions needs
the real HA object the signature names.

Module-scope rule: ``homeassistant.*`` imports are
permitted ONLY under ``if TYPE_CHECKING:``. No function-
body or unconditional module-scope HA import.
``test_helpers_runtime_has_no_runtime_ha_imports``
enforces this via AST walk.

Cross-flavour rule: this file imports from
``helpers_logic`` only.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .const import DOMAIN
from .helpers_logic import (
    _UNSUBS_KEY,
    BlueprintHandlerSpec,
    CappableResult,
    PersistentNotification,
    instance_id_for_config_error,
    instance_state_entity_id,
    make_config_error_notification,
    md_escape,
    spec_bucket,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


def entry_for_domain(hass: HomeAssistant) -> ConfigEntry | None:
    """Return the integration's lone config entry, if loaded.

    Single-entry integration: every native handler grabs
    the same entry to scope task lifecycle. Returns
    ``None`` when the integration is not loaded.
    """
    entries = hass.config_entries.async_entries(DOMAIN)
    return entries[0] if entries else None


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


def _automation_title_prefix(
    hass: HomeAssistant,
    instance_id: str | None,
) -> str:
    """Render the ``<friendly_name>: `` title prefix.

    Returns ``""`` when ``instance_id`` is ``None`` or the
    automation entity isn't in the state machine (the test
    harnesses + Developer-Tools service-call paths land
    here). Notifications titles use this to lead with the
    user-facing automation name so the panel shows
    ``<automation>: <category>`` consistently across every
    handler -- the dispatcher prepends so per-handler
    builders only carry the category descriptor.
    """
    if not instance_id:
        return ""
    if hass.states.get(instance_id) is None:
        return ""
    name = automation_friendly_name(hass, instance_id)
    if not name:
        return ""
    return f"{name}: "


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
    the dispatcher prepends two things:

    1. ``<friendly_name>: `` to the spec's ``title``.
       Per-handler notification builders supply just the
       category descriptor (e.g. ``"Config Error"``,
       ``"API unavailable"``) and the dispatcher leads
       every active notification with the user-facing
       automation name uniformly.
    2. ``Automation: [name](edit-link)\\n`` to the
       message body so users can click through to the
       associated automation.

    Inactive (dismiss) specs aren't prefixed -- nothing's
    being shown.

    Use this bare form when the caller is touching a
    single known notification ID (``emit_config_error``
    is the canonical case). If the caller knows it owns
    the *entire* per-instance notification space for this
    run -- e.g. a reconcile pass that emits every
    notification it expects to be visible -- use
    ``process_persistent_notifications_with_sweep`` so
    orphans from prior runs get dismissed.
    """
    for n in notifications:
        if n.active:
            title_prefix = _automation_title_prefix(hass, n.instance_id)
            link_prefix = _automation_link_prefix(hass, n.instance_id)
            await hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "notification_id": n.notification_id,
                    "title": f"{title_prefix}{n.title}",
                    "message": f"{link_prefix}{n.message}",
                },
            )
        else:
            await hass.services.async_call(
                "persistent_notification",
                "dismiss",
                {"notification_id": n.notification_id},
            )


def _active_notification_ids(hass: HomeAssistant) -> set[str] | None:
    """Return all active persistent_notification IDs, or None.

    HA stores live notifications at
    ``hass.data["persistent_notification"]`` keyed by ID.
    Returns ``None`` when that data is unavailable (test
    contexts that stub out ``hass.data``); callers treat
    ``None`` as "skip the orphan sweep" -- nothing to
    sweep against.
    """
    try:
        data = hass.data.get("persistent_notification", {})
        return set(data.keys())
    except (AttributeError, TypeError):
        return None


def _orphan_dismissals(
    hass: HomeAssistant,
    notifications: list[PersistentNotification],
    *,
    sweep_prefix: str,
    keep_pattern: str | None = None,
) -> list[PersistentNotification]:
    """Return inactive specs for prefix-matching IDs not in ``notifications``.

    Builds the dismissal list the sweep variant prepends
    to its outgoing batch. Orphans = active IDs starting
    with ``sweep_prefix`` that this run isn't re-emitting,
    minus anything matching ``keep_pattern``.

    ``keep_pattern`` is a substring opt-out for
    event-stream notifications that should persist until
    the user dismisses them. ZRM's per-attempt timeout
    notifications (IDs containing ``__timeout_<ts>``) are
    the canonical case: each retry is a distinct event,
    the user wants the history.
    """
    active_ids = _active_notification_ids(hass)
    if active_ids is None:
        return []
    current_ids = {n.notification_id for n in notifications}
    orphans: list[PersistentNotification] = []
    for nid in active_ids:
        if not nid.startswith(sweep_prefix) or nid in current_ids:
            continue
        if keep_pattern is not None and keep_pattern in nid:
            continue
        orphans.append(
            PersistentNotification(
                active=False,
                notification_id=nid,
                title="",
                message="",
            ),
        )
    return orphans


async def process_persistent_notifications_with_sweep(
    hass: HomeAssistant,
    notifications: list[PersistentNotification],
    *,
    sweep_prefix: str,
    keep_pattern: str | None = None,
) -> None:
    """Dispatch a batch + sweep prefix-matching orphans.

    Calling contract: the caller is asserting that
    ``notifications`` is the *complete* set of notification
    IDs starting with ``sweep_prefix`` it expects to be
    visible after this call. Anything currently active
    that matches the prefix and isn't in ``notifications``
    is dismissed (modulo ``keep_pattern`` opt-outs).

    Internally builds the orphan-dismissal list and hands
    everything off to ``process_persistent_notifications``,
    so create/dismiss semantics + automation-link prefix
    behavior are identical to the bare dispatcher.
    """
    orphans = _orphan_dismissals(
        hass,
        notifications,
        sweep_prefix=sweep_prefix,
        keep_pattern=keep_pattern,
    )
    await process_persistent_notifications(hass, notifications + orphans)


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
    notification for the same instance). ``service_tag``
    is used in the warning log line; the user-visible
    title is built by the dispatcher.
    """
    spec = make_config_error_notification(
        service=service,
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


async def validate_payload_or_emit_config_error(
    hass: HomeAssistant,
    raw: dict[str, Any],
    schema: Callable[[dict[str, Any]], dict[str, Any]],
    emit_config_error: Callable[
        [HomeAssistant, str, list[str]],
        Awaitable[None],
    ],
) -> dict[str, Any] | None:
    """Run ``schema`` over ``raw`` or emit a config-error notification.

    Every handler's ``_async_argparse`` opens with
    the same try / except / ``_emit_config_error`` block;
    factor it here so the failure shape (``schema:`` prefix,
    fallback ``instance_id_for_config_error`` lookup, single-
    error vs ``MultipleInvalid`` collected list) stays
    consistent across handlers as schemas evolve.

    Returns the validated payload on success, or ``None``
    when a notification was emitted -- callers short-circuit
    dispatch on ``None``. ``voluptuous`` is third-party (not
    a ``homeassistant.*`` module), so the function-body import
    here doesn't violate the runtime flavour's
    ``test_helpers_runtime_has_no_runtime_ha_imports`` rule.
    Late-importing keeps this module loadable in a no-vol
    environment, mirroring the lazy-import shape used in the
    lifecycle file's HA-side late imports.
    """
    import voluptuous as vol  # noqa: PLC0415

    try:
        return schema(raw)
    except vol.MultipleInvalid as err:
        await emit_config_error(
            hass,
            instance_id_for_config_error(raw),
            [f"schema: {sub}" for sub in err.errors],
        )
        return None
    except vol.Invalid as err:
        await emit_config_error(
            hass,
            instance_id_for_config_error(raw),
            [f"schema: {err}"],
        )
        return None


def prepare_notifications(
    results: Sequence[CappableResult],
    *,
    max_notifications: int,
    cap_notification_id: str,
    cap_title: str,
    cap_item_label: str,
    instance_id: str | None = None,
) -> list[PersistentNotification]:
    """Sort, build, and cap per-result notifications.

    The "glue" between an automation's per-result
    evaluation and the notification dispatcher.
    Responsibilities:

    1. **Sort** ``results`` by
       ``(notification_title, notification_id)``. Each
       caller used to sort itself; the helper does it
       once. Deterministic ordering means the
       shown / suppressed split below is reproducible
       across runs.
    2. **Cap** the issue subset. When the number of
       has-issue results exceeds ``max_notifications``
       (and the cap is non-zero), the first
       ``max_notifications`` are shown in full, the rest
       are passed through ``to_notification(suppress=True)``
       which dismisses their stored ID, and a
       cap-reached summary notification is appended.
    3. **Emit clean-result notifications anyway** when
       the cap is exceeded, so any lingering
       notifications from a prior run where the same
       result was in the issue partition get dismissed.
    4. **Always emit the cap-summary slot** -- active
       when the cap is reached, inactive otherwise --
       so a previously-active summary gets dismissed
       when the cap no longer applies.

    Pair with ``process_persistent_notifications_with_sweep``
    so any per-instance notifications a prior run emitted
    that this run isn't re-emitting (e.g. result whose
    ``has_issue`` flipped between runs and that wasn't in
    ``results`` at all this time) get dismissed too.

    Args:
        results: Per-result objects implementing
            ``CappableResult``. Wrap pre-built
            notifications via ``IssueNotification`` if
            you don't have a result dataclass.
        max_notifications: Per-run cap; ``0`` = unlimited.
        cap_notification_id: Persistent notification ID
            used for the cap-summary notification. Must
            be unique per automation instance.
        cap_title: Title used when the cap is reached.
        cap_item_label: Human-readable label describing
            what's being counted, e.g.
            ``"devices with issues"`` or
            ``"route issues"``. Inserted into the cap
            summary message.
        instance_id: When set, stamped on the cap-summary
            notification so the dispatcher's
            ``Automation: ...`` link prefix lands on it.
    """
    sorted_results = [
        r
        for _, _, r in sorted(
            ((r.notification_title, r.notification_id, r) for r in results),
            key=lambda triple: (triple[0], triple[1]),
        )
    ]

    notifications: list[PersistentNotification] = []
    issues = [r for r in sorted_results if r.has_issue]

    if max_notifications > 0 and len(issues) > max_notifications:
        shown = issues[:max_notifications]
        suppressed = issues[max_notifications:]
        for r in shown:
            notifications.append(r.to_notification())
        for r in suppressed:
            notifications.append(r.to_notification(suppress=True))
        # Emit notifications for clean results so any
        # lingering notifications from a prior run get
        # dismissed.
        for r in sorted_results:
            if not r.has_issue:
                notifications.append(r.to_notification())
        notifications.append(
            PersistentNotification(
                active=True,
                notification_id=cap_notification_id,
                title=cap_title,
                message=(
                    f"Showing {max_notifications} of"
                    f" {len(issues)} {cap_item_label}."
                    f" {len(suppressed)} additional notifications"
                    " were suppressed. Increase the"
                    " notification cap or fix existing"
                    " issues to see more."
                ),
                instance_id=instance_id,
            ),
        )
    else:
        for r in sorted_results:
            notifications.append(r.to_notification())
        # Cap slot always emitted so a previously-active
        # summary gets dismissed when the cap no longer
        # applies.
        notifications.append(
            PersistentNotification(
                active=False,
                notification_id=cap_notification_id,
                title="",
                message="",
            ),
        )
    return notifications


def automation_friendly_name(
    hass: HomeAssistant,
    instance_id: str,
) -> str:
    """Return the automation's user-set friendly name.

    Used to build log tags like
    ``[ZRM: Z-Wave Route Manager]`` from the canonical
    ``automation.z_wave_route_manager`` entity_id. Falls
    back to ``instance_id`` if the friendly_name attribute
    isn't available (entity not yet in the state machine,
    or test contexts without one).
    """
    state = hass.states.get(instance_id)
    if state is None:
        return instance_id
    name = state.attributes.get("friendly_name")
    if isinstance(name, str) and name:
        return name
    return instance_id


def update_instance_state(
    hass: HomeAssistant,
    *,
    service_tag: str,
    instance_id: str,
    last_run: datetime,
    runtime: float,
    state: str = "ok",
    extra_attributes: dict[str, Any] | None = None,
) -> None:
    """Surface per-instance runtime state for debugging.

    Sets a state entry at
    ``blueprint_toolkit.<service_tag>_<slug>_state`` with
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
        instance_state_entity_id(service_tag, instance_id),
        state,
        attributes,
    )


def make_periodic_trigger_callback(
    hass: HomeAssistant,
    instance_id: str,
    *,
    instances_getter: Callable[[HomeAssistant], dict[str, Any]],
    service_tag: str,
    logger: logging.Logger,
    extra_variables: dict[str, Any] | None = None,
) -> Callable[[datetime], Awaitable[Any]]:
    """Build the canonical periodic-tick callback for blueprint handlers.

    Returns an async callable suitable for
    ``schedule_periodic_with_jitter`` /
    ``async_track_time_interval`` which fires
    ``automation.trigger`` against ``instance_id`` with
    ``trigger_id="periodic"`` plus any caller-supplied
    ``extra_variables`` merged in flat (NOT under
    ``trigger.*`` -- HA's ``automation.trigger`` service
    unconditionally clobbers the ``trigger`` key with
    ``{"platform": None}``).

    Drops silently if the instance has been removed between
    scheduling and firing -- callers pass an
    ``instances_getter`` that returns the per-handler
    instance map keyed by automation entity_id.

    Swallows + WARN-logs ``automation.trigger`` failures.
    A single failed tick is a self-healing transient (the
    next tick fires anyway), and surfacing the exception
    would knock the timer task down. ``service_tag`` and
    ``logger`` parameterize the log line so each handler's
    operator sees ``[STSC] periodic automation.trigger
    failed for <entity>`` (or its ``[DW]`` / ``[EDW]`` /
    etc. equivalent).
    """
    base_vars: dict[str, Any] = {"trigger_id": "periodic"}
    if extra_variables:
        base_vars.update(extra_variables)

    async def _on_tick(_now: datetime) -> None:
        if instance_id not in instances_getter(hass):
            return
        try:
            await hass.services.async_call(
                "automation",
                "trigger",
                {
                    "entity_id": instance_id,
                    "skip_condition": True,
                    "variables": dict(base_vars),
                },
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "[%s] periodic automation.trigger failed for %s;"
                " next tick will retry",
                service_tag,
                instance_id,
                exc_info=True,
            )

    return _on_tick


async def kick_via_automation_trigger(
    hass: HomeAssistant,
    entity_id: str,
    variables: dict[str, Any],
) -> None:
    """Fire ``automation.trigger`` against ``entity_id``.

    Used by every handler's ``kick`` lifecycle hook to
    bootstrap restart-recovery, and by any other synthetic
    invocation path (manual scans, etc.). ``variables``
    keys must be flat (NOT under ``trigger.*``) -- HA's
    ``automation.trigger`` service unconditionally clobbers
    the ``trigger`` key with ``{"platform": None}``, so any
    nested ``trigger:`` overrides get silently dropped.
    """
    await hass.services.async_call(
        "automation",
        "trigger",
        {
            "entity_id": entity_id,
            "skip_condition": True,
            "variables": dict(variables),
        },
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


__all__ = [
    "automation_friendly_name",
    "emit_config_error",
    "entry_for_domain",
    "kick_via_automation_trigger",
    "make_periodic_trigger_callback",
    "prepare_notifications",
    "process_persistent_notifications",
    "process_persistent_notifications_with_sweep",
    "unregister_blueprint_handler",
    "update_instance_state",
    "validate_payload_or_emit_config_error",
]
