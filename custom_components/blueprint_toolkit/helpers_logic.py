# This is AI generated code
"""Pure helpers (no HA imports anywhere).

The "pure" group of the three-flavour split documented in
``helpers.py``'s shim docstring. Safe to import from a
non-HA test environment; ``test_helpers_logic_has_no_ha_imports``
enforces this via AST walk.

Module-scope rule: NO ``homeassistant.*`` imports of any
kind, including ``if TYPE_CHECKING:`` (a pure helper that
needs an HA type for documentation should use
``from __future__ import annotations`` + a string-form
annotation).

Cross-flavour rule: this file imports from neither
``helpers_runtime`` nor ``helpers_lifecycle``. The single
intentional carve-out is ``make_emit_config_error``'s
returned closure, which lazily imports
``emit_config_error`` from ``helpers_runtime`` inside its
body -- the per-test allow-list in
``test_helpers_partial_order_layering`` notes this
explicitly.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from .const import DOMAIN

# Domains that respond to ``homeassistant.turn_on`` /
# ``turn_off``. Used by every on/off-driving controller's
# argparse to reject selector-bypassing YAML edits before
# the service layer dispatches a silent no-op against an
# unsupported entity. Per-blueprint selector ``domain:``
# lists are UI hints only; this set is the authoritative
# argparse-time guard.
CONTROLLABLE_DOMAINS: frozenset[str] = frozenset(
    {
        "automation",
        "climate",
        "cover",
        "fan",
        "humidifier",
        "input_boolean",
        "light",
        "lock",
        "media_player",
        "switch",
        "vacuum",
        "water_heater",
    },
)


def validate_controlled_entity_domains(
    entity_ids: list[str],
    field_name: str,
) -> list[str]:
    """Return one config-error bullet per uncontrollable entity.

    Each bullet matches the canonical
    ``"<field_name>: '<eid>' does not support on/off (pick
    an entity in one of: <sorted-domains>)"`` shape so
    every on/off-driving handler surfaces the same wording.
    Empty list when every entity is in
    ``CONTROLLABLE_DOMAINS``.

    Domain extraction is the everything-before-the-first-
    dot of each entity_id; entity_ids without a dot are
    treated as zero-length-domain (which is not in the set
    and so flags). Caller is expected to have already run
    state-existence checks if it cares about typos vs
    domain mismatches.
    """
    bullets: list[str] = []
    valid = ", ".join(sorted(CONTROLLABLE_DOMAINS))
    for eid in entity_ids:
        domain = eid.split(".", 1)[0] if "." in eid else ""
        if domain not in CONTROLLABLE_DOMAINS:
            bullets.append(
                f"{field_name}: {eid!r}"
                f" does not support on/off (pick an entity in one of:"
                f" {valid})",
            )
    return bullets


def notification_prefix(service: str, instance_id: str) -> str:
    """Common prefix for a handler's notification family.

    Format: ``blueprint_toolkit_{service}__{instance_id}__``.
    Per-category suffix is appended at each call site;
    the trailing ``__`` keeps the field separator parseable
    (HA entity IDs never contain ``__``).
    """
    return f"blueprint_toolkit_{service}__{instance_id}__"


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
) -> Callable[[Any, str, list[str]], Awaitable[None]]:
    """Return an ``emit_config_error`` closure bound to a port's identifiers.

    Saves repeating ``service=_SERVICE,
    service_tag=_SERVICE_TAG`` at every call site in a
    handler. Equivalent to a `functools.partial`, but
    typed-for-handler-callers (positional ``hass``,
    ``instance_id``, ``errors``).

    Lazily imports ``emit_config_error`` from
    ``helpers_runtime`` inside the closure body to keep
    the partial-order layering rule intact (pure cannot
    import runtime at module scope). The lazy reference
    is the single intentional cross-flavour asymmetry --
    ``test_helpers_partial_order_layering``'s allow-list
    pins this site explicitly.
    """

    async def emit(
        hass: Any,
        instance_id: str,
        errors: list[str],
    ) -> None:
        from .helpers_runtime import emit_config_error  # noqa: PLC0415

        await emit_config_error(
            hass,
            service=service,
            service_tag=service_tag,
            instance_id=instance_id,
            errors=errors,
        )

    return emit


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

    # Hass typed as ``Any`` here -- the pure flavour bans
    # HA imports even under ``TYPE_CHECKING``. Callers
    # supply the real ``HomeAssistant`` and runtime / lifecycle
    # consumers narrow the type at the receiving end.
    on_reload: Callable[[Any], None]
    on_entity_remove: Callable[[Any, str], None]
    on_entity_rename: Callable[[Any, str, str], None]
    on_teardown: Callable[[Any], None]


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
    # Hass / ServiceCall typed as ``Any`` here -- the pure
    # flavour bans HA imports even under ``TYPE_CHECKING``.
    # Callers supply real HA objects; runtime / lifecycle
    # consumers narrow the types at the receiving end.
    service_handler: Callable[[Any, Any], Awaitable[None]]
    kick_variables: dict[str, Any] | None = None
    on_reload: Callable[[Any], None] | None = None
    on_entity_remove: Callable[[Any, str], None] | None = None
    on_entity_rename: Callable[[Any, str, str], None] | None = None
    on_teardown: Callable[[Any], None] | None = None


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


__all__ = [
    "BlueprintHandlerSpec",
    "CONTROLLABLE_DOMAINS",
    "CappableResult",
    "IssueNotification",
    "LifecycleMutators",
    "PersistentNotification",
    "device_header_line",
    "format_notification",
    "format_timestamp",
    "instance_id_for_config_error",
    "instance_state_entity_id",
    "make_config_error_notification",
    "make_emit_config_error",
    "matches_pattern",
    "md_escape",
    "notification_prefix",
    "parse_entity_registry_update",
    "parse_notification_service",
    "resolve_target_integrations",
    "slugify",
    "spec_bucket",
    "validate_and_join_regex_patterns",
    "validate_controlled_entity_domains",
]
