# This is AI generated code
"""Shared helpers for blueprint_toolkit subpackages.

Utility surface that subpackage logic + handler modules
share across all six native ports (DW, EDW, RW, STSC,
TEC, ZRM).

Three flavours of symbol live here:

- **Pure** (no HA imports anywhere): ``notification_prefix``,
  ``resolve_target_integrations``, ``format_timestamp``,
  ``format_notification``, ``parse_notification_service``,
  ``md_escape``, ``slugify``, ``matches_pattern``,
  ``validate_and_join_regex_patterns``,
  ``PersistentNotification``,
  ``make_config_error_notification``,
  ``instance_id_for_config_error``,
  ``parse_entity_registry_update``,
  ``instance_state_entity_id``, ``CappableResult``,
  ``IssueNotification``, ``BlueprintHandlerSpec``,
  ``LifecycleMutators``. Safe to import from non-HA test
  environments.
- **Runtime-HA** (takes a runtime ``hass`` / ``entry`` /
  ``ConfigEntry`` arg but doesn't import HA inside the
  function): ``entry_for_domain``,
  ``all_integration_ids``,
  ``process_persistent_notifications``,
  ``process_persistent_notifications_with_sweep``,
  ``emit_config_error``, ``make_emit_config_error``,
  ``validate_payload_or_emit_config_error``,
  ``prepare_notifications``, ``automation_friendly_name``,
  ``update_instance_state``,
  ``make_periodic_trigger_callback``,
  ``kick_via_automation_trigger``, ``spec_bucket``. Module
  import succeeds outside HA; calling the function needs
  the real HA object the signature names.
- **Lifecycle** (late-imports HA inside the function so
  module import stays cheap): ``cv_ha_domain_list``,
  ``discover_automations_using_blueprint``,
  ``recover_at_startup``,
  ``schedule_periodic_with_jitter``,
  ``make_lifecycle_mutators``,
  ``register_blueprint_handler``,
  ``unregister_blueprint_handler``. Module import still
  succeeds outside HA; calling these forces the late
  import.

When you add a new public symbol to this file, classify
it into the right group above. The classification is the
contract that lets test-only code import what it needs
without dragging in HA at module-scope; leaving a new
helper unclassified silently breaks that contract.

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

import hashlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import Event, HomeAssistant, ServiceCall

_LOGGER = logging.getLogger(__name__)


# --------------------------------------------------------
# Schema validators
# --------------------------------------------------------


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


# --------------------------------------------------------
# Cross-handler accessors
# --------------------------------------------------------


def entry_for_domain(hass: HomeAssistant) -> ConfigEntry | None:
    """Return the integration's lone config entry, if loaded.

    Single-entry integration: every native handler grabs
    the same entry to scope task lifecycle. Returns
    ``None`` when the integration is not loaded.
    """
    entries = hass.config_entries.async_entries(DOMAIN)
    return entries[0] if entries else None


def notification_prefix(service: str, instance_id: str) -> str:
    """Common prefix for a handler's notification family.

    Format: ``blueprint_toolkit_{service}__{instance_id}__``.
    Per-category suffix is appended at each call site;
    the trailing ``__`` keeps the field separator parseable
    (HA entity IDs never contain ``__``).
    """
    return f"blueprint_toolkit_{service}__{instance_id}__"


def all_integration_ids(hass: HomeAssistant) -> list[str]:
    """All distinct integration IDs across the entity registry.

    Used by the watchdog handlers to populate the truth set
    that include / exclude filters then narrow.
    """
    from homeassistant.helpers import entity_registry as er

    ent_reg = er.async_get(hass)
    integrations: set[str] = set()
    for entry in ent_reg.entities.values():
        if entry.platform:
            integrations.add(entry.platform)
    return sorted(integrations)


def resolve_target_integrations(
    all_integrations: list[str],
    include: list[str],
    exclude: list[str],
) -> set[str]:
    """Apply include / exclude filters to a list of integrations.

    Empty ``include`` means "all integrations" (matches every
    watchdog blueprint's documented behaviour). ``exclude`` is
    then subtracted from the resulting set.
    """
    if include:
        target = set(include)
    else:
        target = set(all_integrations)
    for ex in exclude:
        target.discard(ex)
    return target


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


def device_header_line(name: str, url: str) -> str:
    """Render the canonical ``Device: [<name>](<url>)`` header line.

    Used as the first body line in every per-device watchdog
    notification (DW unavailable / stale, DW disabled-
    diagnostics, EDW per-device drift). Centralised so the
    line shape stays consistent across handlers; tests pin
    the format.
    """
    return f"Device: [{md_escape(name)}]({url})"


# --------------------------------------------------------
# Slugify + regex helpers
# --------------------------------------------------------


def slugify(text: str) -> str:
    """Return a Home Assistant-compatible slug from ``text``.

    Mirrors ``homeassistant.util.slugify(text, separator="_")``
    for the ASCII-only common case: NFKD decomposition,
    drop non-ASCII characters, lowercase, collapse runs of
    non-alphanumeric characters into a single underscore,
    and strip leading and trailing underscores. Empty input
    returns ``""``; non-empty input that collapses to an
    empty slug (e.g. emoji-only, punctuation-only) returns
    ``"unknown"``, matching HA's fallback.
    """
    import re  # noqa: PLC0415
    import unicodedata  # noqa: PLC0415

    if not text:
        return ""
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_text).strip("_")
    return slug or "unknown"


def matches_pattern(text: str, pattern: str) -> bool:
    """Return True if ``text`` matches the case-insensitive regex ``pattern``.

    Empty pattern returns False (no match -- callers can
    short-circuit at the call site if they want
    "no pattern means match-all"). Invalid pattern returns
    False rather than raising; callers that need to
    surface invalid regex errors should validate the
    pattern explicitly at config-parse time via
    ``re.compile``.
    """
    import re  # noqa: PLC0415

    if not pattern:
        return False
    try:
        return bool(re.search(pattern, text, re.IGNORECASE))
    except re.error:
        return False


def validate_and_join_regex_patterns(
    raw: str,
    field_name: str,
) -> tuple[str, list[str]]:
    """Split a multi-line regex-list input, validate, and join with ``|``.

    Blueprint inputs that accept "one regex per line"
    surface as a single multi-line string at the schema
    boundary. Callers want a single combined regex they
    can hand to ``re.search`` (or to ``matches_pattern``).
    Joining naively with ``|`` would silently accept
    invalid lines and fail at runtime; we want loud
    config-time errors so the user knows which line was
    bad.

    Per-line validation:

    - Empty / whitespace-only lines are skipped silently.
    - Patterns that fail ``re.compile`` produce an error
      bullet identifying the offending line.
    - Patterns that match the empty string (``.*`` /
      ``|||||`` / ``a?`` / etc.) are rejected with an
      "matches empty string" error -- they would silently
      exclude every entity / device / id, defeating the
      purpose of the exclusion list.

    Returns ``(joined_pattern, errors)``. ``joined_pattern``
    is the pipe-joined alternation of every valid line
    (empty string when no valid lines remain). ``errors``
    is a list of ``"<field_name>: \"<line>\": <reason>"``
    strings the caller can append to its argparse errors
    list.
    """
    import re  # noqa: PLC0415

    lines = [line.strip() for line in (raw or "").splitlines()]
    valid: list[str] = []
    errors: list[str] = []
    for line in lines:
        if not line:
            continue
        try:
            compiled = re.compile(line)
        except re.error as exc:
            errors.append(f'{field_name}: "{line}": {exc}')
            continue
        if compiled.match(""):
            errors.append(
                f'{field_name}: "{line}": pattern matches empty string '
                "(would exclude everything; tighten the pattern -- e.g. "
                "anchor with ``^...$`` or drop the ``.*`` / ``?`` / "
                "trailing alternation that lets it match empty)",
            )
            continue
        valid.append(line)
    return "|".join(valid), errors


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
    the spec). The same dispatcher prepends
    ``<friendly_name>: `` to the title, so this builder
    only sets the bare ``"Config Error"`` category.

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
    message = "\n".join(f"- {md_escape(e)}" for e in errors)
    return PersistentNotification(
        active=True,
        notification_id=notif_id,
        title="Config Error",
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


def instance_id_for_config_error(raw_data: dict[str, Any]) -> str:
    """Best-effort instance_id extraction for a config-error path.

    Handlers fall back to this when schema validation
    fails before the ``instance_id`` field could be
    parsed; the sentinel keeps the resulting
    notification ID from colliding with a real instance.
    """
    candidate = raw_data.get("instance_id")
    if isinstance(candidate, str) and candidate:
        return candidate
    return "unknown"


def make_emit_config_error(
    *,
    service: str,
    service_tag: str,
) -> Callable[[HomeAssistant, str, list[str]], Awaitable[None]]:
    """Return an ``emit_config_error`` closure bound to a port's identifiers.

    Saves repeating ``service=_SERVICE,
    service_tag=_SERVICE_TAG`` at every call site in a
    handler. Equivalent to a `functools.partial`, but
    typed-for-handler-callers (positional ``hass``,
    ``instance_id``, ``errors``).
    """

    async def emit(
        hass: HomeAssistant,
        instance_id: str,
        errors: list[str],
    ) -> None:
        await emit_config_error(
            hass,
            service=service,
            service_tag=service_tag,
            instance_id=instance_id,
            errors=errors,
        )

    return emit


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
    dispatch on ``None``. ``vol`` is late-imported so the
    helpers module still imports outside HA (mirrors the
    other lifecycle helpers).
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


# --------------------------------------------------------
# Notification capping + sorting (prepare_notifications)
# --------------------------------------------------------


@runtime_checkable
class CappableResult(Protocol):
    """Structural type expected by ``prepare_notifications``.

    Watchdog result dataclasses naturally fit this shape:
    they expose

    - ``has_issue: bool``
    - ``notification_id: str``
    - ``notification_title: str``
    - ``to_notification(suppress: bool = False) -> PersistentNotification``

    Sorting uses ``(notification_title, notification_id)``
    so the shown / suppressed split is reproducible across
    runs. ``to_notification(suppress=True)`` MUST return an
    inactive notification keyed to the same ID, so the
    cap helper can dismiss prior-run notifications that
    no longer fit under the cap.

    Members are declared as ``@property`` so both
    plain-dataclass-attribute implementations
    (watchdogs) and property-backed wrappers
    (``IssueNotification``) satisfy the Protocol.
    """

    @property
    def has_issue(self) -> bool: ...

    @property
    def notification_id(self) -> str: ...

    @property
    def notification_title(self) -> str: ...

    def to_notification(
        self,
        suppress: bool = False,
    ) -> PersistentNotification:
        """Return a PersistentNotification for this result."""
        ...


@dataclass
class IssueNotification:
    """Adapter: pre-built ``PersistentNotification`` -> ``CappableResult``.

    For automations like ZRM that build issue
    notifications ad hoc rather than via a watchdog-style
    result dataclass. Always reports ``has_issue=True``;
    on ``suppress=True`` returns an inactive notification
    keyed to the same ID + ``instance_id``.
    """

    notification: PersistentNotification

    @property
    def has_issue(self) -> bool:
        return True

    @property
    def notification_id(self) -> str:
        return self.notification.notification_id

    @property
    def notification_title(self) -> str:
        return self.notification.title

    def to_notification(
        self,
        suppress: bool = False,
    ) -> PersistentNotification:
        if suppress:
            return PersistentNotification(
                active=False,
                notification_id=self.notification.notification_id,
                title="",
                message="",
                instance_id=self.notification.instance_id,
            )
        return self.notification


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


# --------------------------------------------------------
# Automation friendly-name resolution (for log tags)
# --------------------------------------------------------


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


# --------------------------------------------------------
# Per-instance diagnostic state
# --------------------------------------------------------


def instance_state_entity_id(service_tag: str, instance_id: str) -> str:
    """Build the ``blueprint_toolkit.<service_tag>_<slug>_state`` entity_id.

    ``service_tag`` is the per-handler short tag (``STSC`` /
    ``TEC`` / ``EDW`` / ``DW`` / ``RW`` / ``ZRM``); HA entity
    IDs require lowercase, so the helper lowercases it
    internally -- callers can pass the uppercase
    ``_SERVICE_TAG`` constant directly. ``instance_id`` is
    the automation entity_id (e.g. ``automation.foo_bar``);
    we strip the ``automation.`` prefix so the resulting
    diagnostic entity_id reads cleanly in Developer Tools /
    templates / dashboards.

    HA's `VALID_ENTITY_ID` regex rejects double-underscores
    anywhere in the entity_id, so a `__` visual separator
    between tag and slug isn't usable -- single `_`
    everywhere.
    """
    slug = instance_id.removeprefix("automation.")
    return f"{DOMAIN}.{service_tag.lower()}_{slug}_state"


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
# Periodic scheduling with per-instance jitter
# --------------------------------------------------------


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


# --------------------------------------------------------
# Periodic-tick + automation.trigger helpers
# --------------------------------------------------------


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


# --------------------------------------------------------
# Lifecycle mutator factory
# --------------------------------------------------------


@dataclass(frozen=True)
class LifecycleMutators:
    """The four standard per-instance mutator callbacks.

    Returned by ``make_lifecycle_mutators``; each field
    matches the corresponding hook on
    ``BlueprintHandlerSpec`` so callers can wire them
    directly:

    .. code-block:: python

        _MUTATORS = make_lifecycle_mutators(...)
        _SPEC = BlueprintHandlerSpec(
            ...,
            on_reload=_MUTATORS.on_reload,
            on_entity_remove=_MUTATORS.on_entity_remove,
            on_entity_rename=_MUTATORS.on_entity_rename,
            on_teardown=_MUTATORS.on_teardown,
        )
    """

    on_reload: Callable[[HomeAssistant], None]
    on_entity_remove: Callable[[HomeAssistant, str], None]
    on_entity_rename: Callable[[HomeAssistant, str, str], None]
    on_teardown: Callable[[HomeAssistant], None]


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
        kick_variables: When set, restart-recovery is
            enabled. At HA-started time + after every
            ``EVENT_AUTOMATION_RELOADED``, the dispatcher
            walks every automation using ``blueprint_path``
            and fires ``automation.trigger`` against each
            with this flat top-level ``variables`` payload.
            Per-handler ``_async_kick_for_recovery``
            wrappers used to live in each port; the spec
            now carries just the payload and the
            ``register_blueprint_handler`` dispatcher
            builds the call via
            ``kick_via_automation_trigger``.
        on_reload: When set, ``EVENT_AUTOMATION_RELOADED``
            invokes this synchronously (typical use:
            cancel pending per-instance work whose
            AutomationEntity objects have been
            replaced). Recovery still runs afterwards
            if ``kick_variables`` is also set.
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
    kick_variables: dict[str, Any] | None = None
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
