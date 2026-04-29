# This is AI generated code
"""Business logic for reference-integrity watchdog.

Does not use PyScript-injected globals.

Scans YAML and storage JSON sources for references to Home
Assistant entities and devices, validates them against live
HA state, and reports broken references grouped by the
owner (automation, script, dashboard, helper, etc.) that
holds them.

Detection strategy
------------------

Three mechanisms run in parallel on every parsed source
tree:

1. **Structural walk.** Recursive walk over dict/list
   structures. When a key matches ``_ENTITY_KEYS``
   (``entity``, ``entity_id``, ``entities``,
   ``entity_ids``, ``source``, ``target_entity``, ...)
   its value is emitted as an entity reference. When a
   key matches ``_DEVICE_KEYS`` its value is checked
   against ``_DEVICE_ID_RE`` (32-char lowercase hex) and
   emitted as a device reference. This handles the
   well-known HA config shapes.

2. **Jinja AST extraction.** Any string leaf that
   contains ``{{`` or ``{%`` is parsed with
   ``jinja2.Environment().parse()``. The AST is walked
   for two patterns:

   - ``Const`` string literals that look like entity
     IDs (e.g. ``states('sensor.foo')`` yields
     ``sensor.foo``).
   - ``Getattr`` chains rooted at ``Name('states')``
     (``states.sensor.foo`` yields ``sensor.foo``).

   Non-constant expressions (``states('sensor.' ~ n)``)
   are intentionally skipped -- we only validate refs we
   can prove statically.

3. **String sniff.** For string leaves that are neither
   under a structural REF_KEY nor inside a Jinja template,
   check if the whole stripped string matches
   ``_ENTITY_ID_RE`` with a domain in the known-domain
   set. This catches blueprint inputs where the parent
   key name is custom (``controlled_entities:``,
   ``trigger_entities:``, ``notification_service:``).

   The sniff is explicitly disabled under ``_ENTITY_KEYS``
   subtrees (the structural walk already emitted those)
   and under ``_SERVICE_KEYS`` subtrees (values at
   ``service:``/``action:`` keys are always service
   names, not entity refs).

Service-name negative truth set
-------------------------------

HA service names and entity IDs share the ``domain.name``
shape. ``light.turn_on`` is a service; ``light.kitchen``
is an entity. The string sniff cannot distinguish them by
syntax alone.

The service wrapper pulls the service registry
(``hass.services.async_services()``) and hands the full
set of ``<domain>.<service>`` names to the logic module
via ``TruthSet.service_names``. When a sniff-emitted ref
matches an entry in that set, the ref is dropped before
becoming a finding (tracked as ``refs_service_skipped``
for coverage reporting). Without this backstop, every
``notification_service: notify.mobile_app_foo`` blueprint
input would surface as a broken-entity false positive.

Owner attribution
-----------------

Findings are grouped by **owner** -- the entity or
structural unit that holds the broken reference. Owner
attribution rules per source adapter are documented in
the comment block above the ``scan_*`` functions.

In short, where possible each owner gets:

- A human-readable name
- An entity_id (looked up in the registry)
- A clickable URL into HA's config UI
- A ``yaml_only`` flag set when the registry says
  ``config_entry_id is None``, meaning the helper is
  viewable in the UI but must be edited in YAML

YAML-defined helpers without an edit URL display a
"YAML-only, edit <file>" note in their notification
body so users know where to look.

Known limitations
-----------------

See ``docs/reference_watchdog.md`` section "Known limitations"
for the full list. The headline cases:

- Runtime-computed entity IDs embedded as string
  literals inside YAML scalars (e.g. multi-line list
  strings consumed via ``in`` checks) aren't caught --
  they're neither Jinja-templated nor whole-string
  entity IDs. We intentionally do not run a regex
  fallback because it's the source of most false
  positives in tools like Watchman.
- ``label_id`` and ``area_id`` references aren't
  validated in v1. The truth set loads both but the
  adapters don't wire them through yet.
- Unregistered YAML entities (e.g. the legacy ``plant``
  integration, which doesn't register entities) can't be
  reached by ``exclude_integrations`` -- use
  ``exclude_paths`` for those cases.
"""

import re
from collections.abc import Callable, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field

import jinja2
import jinja2.nodes

from ..helpers import (
    PersistentNotification,
    matches_pattern,
    md_escape,
    prepare_notifications,
)

_JINJA_ENV = jinja2.Environment(autoescape=False)

# -- Constants -----------------------------------------------------------


# Seed set of HA entity domains, used to recognize strings that look
# like entity IDs (domain.object_id). Augmented at runtime by domains
# seen in the live entity registry + states, so refs to entities in
# custom domains still resolve.
SEED_DOMAINS: frozenset[str] = frozenset(
    [
        "air_quality",
        "alarm_control_panel",
        "assist_satellite",
        "automation",
        "binary_sensor",
        "button",
        "calendar",
        "camera",
        "climate",
        "conversation",
        "counter",
        "cover",
        "date",
        "datetime",
        "device_tracker",
        "event",
        "fan",
        "group",
        "humidifier",
        "image",
        "image_processing",
        "input_boolean",
        "input_button",
        "input_datetime",
        "input_number",
        "input_select",
        "input_text",
        "lawn_mower",
        "light",
        "lock",
        "mailbox",
        "media_player",
        "notify",
        "number",
        "person",
        "plant",
        "proximity",
        "remote",
        "scene",
        "schedule",
        "script",
        "select",
        "sensor",
        "siren",
        "stt",
        "sun",
        "switch",
        "text",
        "time",
        "timer",
        "todo",
        "tts",
        "update",
        "vacuum",
        "valve",
        "wake_word",
        "water_heater",
        "weather",
        "zone",
    ]
)


_ENTITY_ID_RE = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")

# HA device registry IDs are 32-char lowercase hex strings. Other
# strings at device_id/device/devices keys (mobile-app UDIDs, DLNA
# UPnP UUIDs, /dev/ serial paths) must not be flagged.
_DEVICE_ID_RE = re.compile(r"^[a-f0-9]{32}$")


# Dict keys whose string values (or string leaves of list values)
# hold entity references by convention in HA config. The structural
# walk emits refs from values at these keys directly, without
# inspecting the surrounding context.
_ENTITY_KEYS: frozenset[str] = frozenset(
    [
        "entity",
        "entity_id",
        "entities",
        "entity_ids",
        "source",
        "source_entity",
        "source_entity_id",
        "target_entity",
        "target_entity_id",
    ]
)

# Dict keys whose string values are HA device registry IDs. Values
# here are validated against _DEVICE_ID_RE and the device truth set.
_DEVICE_KEYS: frozenset[str] = frozenset(["device", "device_id", "devices"])

# Dict keys whose string values are always HA service/action names
# (e.g. "light.turn_on"), never entity IDs. The sniff pass is
# disabled in subtrees under these keys so service names never get
# misflagged as broken entity refs.
_SERVICE_KEYS: frozenset[str] = frozenset(["service", "action"])


# -- Dataclasses ---------------------------------------------------------


@dataclass
class Config:
    """Parsed blueprint inputs for one watchdog instance.

    Created in the service wrapper from blueprint-supplied
    raw values. Passed to ``_evaluate_sources`` as a single
    immutable argument.
    """

    exclude_paths: list[str]
    exclude_integrations: list[str]
    exclude_entities: list[str]
    exclude_entity_regex: str
    check_disabled_entities: bool
    # Per-instance notification ID prefix, ending with
    # the canonical ``__`` separator. Every notification
    # this module mints must start with this string so
    # the service wrapper's orphan sweep can safely scope
    # dismissals to one instance.
    notification_prefix: str = ""


@dataclass(frozen=True)
class RegistryEntry:
    """One row from ``core.entity_registry`` as a value type.

    Used inside ``TruthSet.registry`` so the logic module
    can reverse-lookup full registry metadata for an
    entity_id -- specifically ``config_entry_id`` (drives
    the ``yaml_only`` owner flag) and ``platform`` (used
    by ``exclude_integrations`` filtering of registered
    owners).
    """

    entity_id: str
    platform: str
    unique_id: str
    config_entry_id: str | None
    disabled: bool
    name: str | None
    original_name: str | None


@dataclass(frozen=True)
class TruthSet:
    """Live HA runtime state needed for reference validation.

    Built once per watchdog run in the service wrapper
    from ``hass.states``, entity registry, device
    registry, service registry, label registry, and
    config entries. Treated as immutable: set fields are
    frozensets and the dataclass is frozen.

    The split between set-based fields (``entity_ids``,
    ``disabled_entity_ids``, ``device_ids``,
    ``service_names``, ``label_ids``, ``domains``) and
    the ``registry`` dict is intentional: set membership
    is the fast-path for per-ref validation (called
    thousands of times per run), while registry lookup
    is the slow-path for per-owner metadata (dozens of
    times per run).

    ``config_entries_with_entities`` is a pre-computed
    index over ``registry`` so ``_scan_config_entries``
    can check URL-worthiness in O(1) instead of scanning
    the full registry per entry.
    """

    entity_ids: frozenset[str] = field(default_factory=frozenset)
    disabled_entity_ids: frozenset[str] = field(default_factory=frozenset)
    device_ids: frozenset[str] = field(default_factory=frozenset)
    service_names: frozenset[str] = field(default_factory=frozenset)
    label_ids: frozenset[str] = field(default_factory=frozenset)
    domains: frozenset[str] = field(default_factory=frozenset)
    registry: dict[str, RegistryEntry] = field(default_factory=dict)
    # Reverse-lookup: (platform, unique_id) -> entity_id. Used by
    # source adapters to resolve owner entity IDs from YAML fields
    # like automation `id` or template `unique_id` without scanning
    # every registry entry linearly.
    entity_by_unique_id: dict[tuple[str, str], str] = field(
        default_factory=dict,
    )
    # Pre-computed set of ``config_entry_id`` values that own at
    # least one entity in the registry. Drives the
    # ``config_entries/?config_entry=<id>`` URL suppression in
    # ``_scan_config_entries`` without an O(N_entries x N_registry)
    # scan per config entry.
    config_entries_with_entities: frozenset[str] = field(
        default_factory=frozenset,
    )


@dataclass
class SourceInput:
    """One parsed source file ready for walking.

    Created in the service wrapper after reading and
    parsing each source file. Contains **no HA runtime
    data** -- only the parsed content of one file, its
    logical type (dispatch key for adapters), and any
    source-specific metadata the adapter needs.
    """

    source_type: str
    path: str
    parsed: object
    extra: dict[str, str] = field(default_factory=dict)


@dataclass
class Ref:
    """One detected reference candidate.

    Yielded internally by ``_walk_tree``. ``context``
    records the structural path where the ref was found
    ("action[0].target.entity_id") or the detection
    mechanism ("jinja:...", "sniff:..."), and is used
    for deduplication inside ``_collect_findings`` as
    well as for notification body formatting.
    """

    kind: str
    value: str
    context: str


@dataclass
class Owner:
    """The entity or structural unit holding a reference.

    Each broken reference is attributed to exactly one
    owner. A notification is emitted per owner with all
    of its findings aggregated.

    Owners are built by per-source adapter functions.

    Identity and rendering
    ----------------------

    ``(source_file, block_path, friendly_name)`` is the
    stable identity tuple used for the notification ID
    hash. Callers that need a human-readable label
    should use ``_owner_display_name(owner)``.

    ``integration`` is both the ``Integration:`` value in
    the notification body *and* the matching key for the
    ``exclude_integrations`` blueprint input. Whenever
    ``integration`` is non-None, the owner is filterable
    by that value; when None, the notification omits the
    ``Integration:`` line and ``exclude_integrations``
    cannot reach the owner (users should reach for
    ``exclude_paths`` instead).

    ``block_path`` describes where the owner lives in
    hand-editable YAML files, e.g.
    ``"config-block[0].sensor[1]"``. Set to ``None`` for
    JSON-backed sources (``.storage/*``) where users
    don't hand-edit and have UI URLs instead, and for
    scalar-top-level YAML files that have no block
    structure.

    ``friendly_name`` is a human name when available
    (automation alias, entity name, customize entity_id,
    dict key, ...). May be ``None`` for purely structural
    owners like ``config-block[0].trigger[0]``.
    """

    source_file: str
    integration: str | None = None
    block_path: str | None = None
    friendly_name: str | None = None
    entity_id: str | None = None
    url_path: str | None = None
    yaml_only: bool = False


def _owner_display_name(owner: Owner) -> str:
    """Computed display label for an owner.

    Priority:
      1. ``friendly_name`` when set.
      2. ``integration + " - " + block_path`` when
         integration is known.
      3. ``source_file + " - " + block_path`` when the
         integration is unknown (generic YAML).
      4. ``source_file`` as a last resort.

    Used for the notification title and as the
    right-hand side of the ``Owner:`` header line when
    no block_path is present.
    """
    if owner.friendly_name:
        return owner.friendly_name
    if owner.integration and owner.block_path:
        return f"{owner.integration} - {owner.block_path}"
    if owner.block_path:
        return f"{owner.source_file} - {owner.block_path}"
    return owner.source_file


def _owner_header_label(owner: Owner) -> str:
    """Header label for the ``Owner:`` line of a notification.

    Same structure as ``_owner_display_name`` but keeps
    the block path and friendly name both visible when
    both are present. The title (``_owner_display_name``)
    prefers the friendly name alone for concision; the
    header trades concision for locatability -- it tells
    the user both *where* in the file and *what* it is.
    """
    if owner.block_path and owner.friendly_name:
        return f"{owner.block_path} - {owner.friendly_name}"
    return _owner_display_name(owner)


@dataclass
class Finding:
    """A validated reference plus its outcome.

    ``disabled=True`` means the target entity exists in
    the registry but is disabled -- whether this counts
    as a "finding" depends on ``Config.check_disabled_entities``.
    """

    ref: Ref
    disabled: bool = False


@dataclass
class OwnerResult:
    """Per-owner evaluation result, consumed by the wrapper.

    Returned from ``_evaluate_sources``. Implements the
    ``CappableResult`` protocol from ``helpers.py``
    (``has_issue`` + ``to_notification``) so the shared
    ``prepare_notifications`` helper can fold it in
    alongside results from the other watchdogs.
    """

    owner: Owner
    has_issue: bool
    notification_id: str
    notification_title: str
    notification_message: str
    findings: list[Finding]
    refs_total: int
    refs_structural: int
    refs_jinja: int
    refs_sniff: int
    refs_valid: int
    refs_disabled: int
    refs_broken: int
    refs_service_skipped: int

    def to_notification(
        self,
        suppress: bool = False,
    ) -> PersistentNotification:
        return PersistentNotification(
            active=self.has_issue and not suppress,
            notification_id=self.notification_id,
            title=self.notification_title,
            message=self.notification_message,
        )


# -- Detection primitives ------------------------------------------------


def _looks_like_entity_id(s: str, known_domains: AbstractSet[str]) -> bool:
    """True if ``s`` syntactically matches a known-domain entity id."""
    if not _ENTITY_ID_RE.match(s):
        return False
    return s.split(".", 1)[0] in known_domains


def _walk_jinja(
    node: jinja2.nodes.Node,
    known_domains: AbstractSet[str],
    out: list[str],
) -> None:
    """Recursive Jinja-AST walker used by ``_extract_refs_from_template``.

    Appends to ``out`` every constant entity-id string
    literal (``'sensor.foo'``) and every attribute chain
    rooted at ``Name('states')`` (``states.sensor.foo``)
    found in the AST.
    """
    if isinstance(node, jinja2.nodes.Const) and isinstance(node.value, str):
        v = node.value.strip()
        if _looks_like_entity_id(v, known_domains):
            out.append(v)

    if isinstance(node, jinja2.nodes.Getattr):
        chain: list[str] = []
        cur: jinja2.nodes.Node = node
        while isinstance(cur, jinja2.nodes.Getattr):
            chain.append(cur.attr)
            cur = cur.node
        if isinstance(cur, jinja2.nodes.Name) and cur.name == "states":
            chain.reverse()
            if len(chain) >= 2:
                eid = f"{chain[0]}.{chain[1]}"
                if _looks_like_entity_id(eid, known_domains):
                    out.append(eid)

    for child in node.iter_child_nodes():
        _walk_jinja(child, known_domains, out)


def _extract_refs_from_template(
    s: str,
    known_domains: AbstractSet[str],
) -> list[str]:
    """Return entity IDs found inside a Jinja template string.

    Only extracts refs that are fully constant at parse
    time. Dynamic expressions like
    ``states('sensor.' ~ name)`` yield nothing because
    the entity ID can't be verified without runtime data.
    """
    if "{{" not in s and "{%" not in s:
        return []
    try:
        ast = _JINJA_ENV.parse(s)
    except jinja2.TemplateSyntaxError:
        return []
    out: list[str] = []
    _walk_jinja(ast, known_domains, out)
    return out


def _fmt(path: list[str]) -> str:
    """Format a walker path list for context strings."""
    return ".".join(path) if path else "(root)"


def _emit_refs(
    value: object,
    kind: str,
    ctx: str,
    known_domains: AbstractSet[str],
) -> list[Ref]:
    """Return Refs from the value at a _ENTITY_KEYS / _DEVICE_KEYS key.

    Walks string / list values (lists may be nested).
    Dict values are handled by the caller recursing into
    the subtree via ``_walk_tree``.
    """
    out: list[Ref] = []
    if isinstance(value, str):
        v = value.strip()
        if kind == "entity":
            if _looks_like_entity_id(v, known_domains):
                out.append(Ref(kind="entity", value=v, context=ctx))
        else:
            # Only real HA device registry IDs (32-char lowercase
            # hex) count. Rejects mobile-app UDIDs, DLNA UPnP
            # UUIDs, /dev/ serial paths, and other integration-
            # internal identifiers that overload device_id keys.
            if _DEVICE_ID_RE.match(v):
                out.append(Ref(kind="device", value=v, context=ctx))
    elif isinstance(value, list):
        for i, item in enumerate(value):
            out.extend(
                _emit_refs(
                    item,
                    kind,
                    f"{ctx}[{i}]",
                    known_domains,
                ),
            )
    return out


def _walk_tree(
    node: object,
    path: list[str],
    known_domains: AbstractSet[str],
    sniff_strings: bool = True,
) -> list[Ref]:
    """Walk a parsed YAML/JSON tree returning reference candidates.

    The three detection mechanisms -- structural walk,
    Jinja AST extraction, and string sniff -- all fire
    from this function. See the module docstring for the
    strategy summary.

    The ``sniff_strings`` flag is turned off when we
    recurse into a subtree that was already emitted
    structurally (``_ENTITY_KEYS`` / ``_DEVICE_KEYS``) or
    whose contents are always service names
    (``_SERVICE_KEYS``). Without this flag we'd
    double-count entity-ID values at known ref keys and
    misflag service names as entity refs.
    """
    out: list[Ref] = []
    if isinstance(node, dict):
        for k, v in node.items():
            key = str(k)
            new_path = path + [key]
            if key in _ENTITY_KEYS:
                out.extend(
                    _emit_refs(
                        v,
                        "entity",
                        _fmt(new_path),
                        known_domains,
                    ),
                )
            if key in _DEVICE_KEYS:
                out.extend(
                    _emit_refs(
                        v,
                        "device",
                        _fmt(new_path),
                        known_domains,
                    ),
                )
            if key in _ENTITY_KEYS | _DEVICE_KEYS | _SERVICE_KEYS:
                if isinstance(v, (dict, list)):
                    out.extend(
                        _walk_tree(
                            v,
                            new_path,
                            known_domains,
                            sniff_strings=False,
                        ),
                    )
            else:
                out.extend(
                    _walk_tree(
                        v,
                        new_path,
                        known_domains,
                        sniff_strings,
                    ),
                )
    elif isinstance(node, list):
        for i, item in enumerate(node):
            out.extend(
                _walk_tree(
                    item,
                    path + [f"[{i}]"],
                    known_domains,
                    sniff_strings,
                ),
            )
    elif isinstance(node, str):
        # Jinja AST pass runs on every string leaf
        # regardless of sniff_strings -- a template inside
        # a _ENTITY_KEYS subtree is still a valid detection
        # candidate distinct from the raw value.
        for eid in _extract_refs_from_template(node, known_domains):
            out.append(
                Ref(
                    kind="entity",
                    value=eid,
                    context=f"jinja:{_fmt(path)}",
                ),
            )
        if sniff_strings:
            stripped = node.strip()
            if _looks_like_entity_id(stripped, known_domains):
                out.append(
                    Ref(
                        kind="entity",
                        value=stripped,
                        context=f"sniff:{_fmt(path)}",
                    ),
                )
    return out


# -- Exclusion helpers ---------------------------------------------------


def _is_path_excluded(path: str, patterns: list[str]) -> bool:
    """True if ``path`` matches any fnmatch-style glob in ``patterns``."""
    if not patterns:
        return False
    from fnmatch import fnmatch

    for pat in patterns:
        if not pat:
            continue
        if fnmatch(path, pat):
            return True
    return False


def _is_integration_excluded(
    integration: str | None,
    excluded: list[str],
) -> bool:
    """True if an integration handle is in the exclude list.

    Returns False for ``None`` -- owners with no
    associated integration (generic YAML files, file-
    level owners) cannot be integration-excluded.
    """
    if integration is None or not excluded:
        return False
    return integration in excluded


def _is_entity_excluded(
    value: str,
    exclude_list: list[str],
    exclude_regex: str,
) -> bool:
    """True if an entity or device value should be dropped.

    Applied symmetrically to source (owner entity_id) and
    target (ref value) sides, per the unified-exclusion
    model.
    """
    if value in exclude_list:
        return True
    if exclude_regex and matches_pattern(value, exclude_regex):
        return True
    return False


# -- Per-source adapters -------------------------------------------------

# Owner attribution rules
# -----------------------
#
# Each adapter below is responsible for turning one
# ``SourceInput`` into one or more ``Owner`` instances
# plus their raw subtrees for ref walking. The rules
# below define, per source type, how each owner's
# ``name``, ``entity_id``, ``url_path``, and
# ``yaml_only`` fields are populated.
#
# - **automations.yaml** (_scan_automations): top-level
#   is a list of automation entries. Owner per entry.
#   Name = ``alias`` (or ``(no alias)``). Entity_id =
#   ``entity_by_unique_id[("automation", entry.id)]``
#   when found. URL = ``/config/automation/edit/<id>``
#   regardless of registry presence (HA's automation
#   editor handles both YAML and UI-sourced entries).
#   Integration = ``automation``.
#
# - **scripts.yaml** (_scan_scripts): top-level is a dict
#   keyed by script slug. Owner per key. Name =
#   ``body.alias`` or the slug. Entity_id =
#   ``script.<slug>`` when present in the registry. URL
#   = ``/config/script/edit/<slug>``. Integration =
#   ``script``.
#
# - **template.yaml** (_scan_template): top-level is a
#   list of template config blocks. Owner per entity
#   (one per item in each ``sensor:``/``binary_sensor:``/
#   etc. sub-list). Entity_id looked up via
#   ``entity_by_unique_id[("template", unique_id)]``
#   when ``unique_id`` is present; otherwise owner is
#   nameless and the file becomes a fallback owner for
#   block-level ``trigger:``/``action:``/
#   ``variables:`` content. Integration = ``template``.
#
# - **customize.yaml** (_scan_customize): top-level dict
#   where each key is an entity_id being customized.
#   Owner per key. Name = the entity_id. The adapter
#   does NOT walk values for refs -- the keys themselves
#   are the refs, validated by the ``kind == "customize"``
#   branch in ``_collect_findings``. Integration =
#   ``homeassistant``.
#
# - **.storage/core.config_entries** (_scan_config_entries):
#   top-level data.entries list. Owner per entry. Name =
#   ``entry.title`` or ``entry_id``. Entity_id =
#   ``entity_by_unique_id[(domain, entry_id)]`` if found
#   (helpers usually map this way). URL =
#   ``/config/helpers/?config_entry=<entry_id>`` for
#   helper-domain entries. Integration = ``entry.domain``
#   directly, enabling per-integration exclusion.
#
# - **.storage/lovelace.<dashboard_id>** (_scan_lovelace):
#   one file per dashboard. Owner per dashboard (not per
#   view). Name / URL pulled from the
#   ``lovelace_dashboards`` index via
#   ``SourceInput.extra``. Integration = ``lovelace``.
#
# - **generic_yaml** (_scan_generic_yaml): catch-all for
#   YAML files without dedicated adapters. Uses
#   structural owner derivation: dict top-level -> owner
#   per key; list top-level -> owner per item (named
#   from ``item.name`` / ``item.alias`` / ``item.id``
#   if present, else index); scalar/other -> file-level
#   owner. Integration = None (not filterable by
#   integration -- use ``exclude_paths`` instead).
#
# Every adapter sets ``yaml_only`` by looking up the
# owner entity in ``TruthSet.registry`` and checking
# ``config_entry_id is None``. Unregistered entities
# default to ``yaml_only=True`` since they can only be
# edited in a file.


_NOTIF_ID_SANITIZE_RE = re.compile(r"[^a-z0-9_]")


def _sanitize_notification_id(s: str) -> str:
    """Sanitize a string for use in a notification ID.

    Lowercases and replaces every non ``[a-z0-9_]``
    character with ``_``. Callers needing collision
    resistance (e.g. two distinct owner identities that
    sanitize to the same string) append a hash suffix
    from the raw input themselves.
    """
    return _NOTIF_ID_SANITIZE_RE.sub("_", s.lower())


def _owner_from_registry(
    owner: Owner,
    truth_set: TruthSet,
) -> None:
    """Set ``owner.yaml_only`` from registry.config_entry_id.

    Called at the end of owner construction. If the owner
    has an ``entity_id`` that resolves to a registry
    entry with ``config_entry_id is None``, flag it as
    YAML-only. If the entity isn't in the registry at
    all, also flag as YAML-only -- it's either a legacy
    YAML integration (plants) or a built-in, both of
    which need manual file edits.
    """
    if owner.entity_id is None:
        return
    entry = truth_set.registry.get(owner.entity_id)
    if entry is None:
        # Not in registry -> assume YAML-only.
        owner.yaml_only = True
    elif entry.config_entry_id is None:
        owner.yaml_only = True


def _scan_automations(
    source: SourceInput,
    truth_set: TruthSet,
) -> list[tuple[Owner, object]]:
    """Owner per automation entry in automations.yaml."""
    owners: list[tuple[Owner, object]] = []
    parsed = source.parsed
    if not isinstance(parsed, list):
        return owners
    for i, entry in enumerate(parsed):
        if not isinstance(entry, dict):
            continue
        auto_id = str(entry.get("id") or "")
        alias_raw = entry.get("alias") or ""
        friendly = str(alias_raw) if alias_raw else None
        owner_eid = truth_set.entity_by_unique_id.get(
            ("automation", auto_id),
        )
        url = f"/config/automation/edit/{auto_id}" if auto_id else None
        owner = Owner(
            source_file=source.path,
            integration="automation",
            block_path=f"config-block[{i}]",
            friendly_name=friendly,
            entity_id=owner_eid,
            url_path=url,
        )
        _owner_from_registry(owner, truth_set)
        owners.append((owner, entry))
    return owners


def _scan_scripts(
    source: SourceInput,
    truth_set: TruthSet,
) -> list[tuple[Owner, object]]:
    """Owner per script definition in scripts.yaml."""
    owners: list[tuple[Owner, object]] = []
    parsed = source.parsed
    if not isinstance(parsed, dict):
        return owners
    for i, (script_id, body) in enumerate(parsed.items()):
        sid = str(script_id)
        owner_eid: str | None = f"script.{sid}"
        if owner_eid not in truth_set.entity_ids:
            owner_eid = None
        # Alias if set, slug otherwise -- both survive a
        # text search through scripts.yaml.
        friendly = sid
        if isinstance(body, dict):
            body_alias = body.get("alias")
            if body_alias:
                friendly = str(body_alias)
        owner = Owner(
            source_file=source.path,
            integration="script",
            block_path=f"config-block[{i}]",
            friendly_name=friendly,
            entity_id=owner_eid,
            url_path=f"/config/script/edit/{sid}",
        )
        _owner_from_registry(owner, truth_set)
        owners.append((owner, body))
    return owners


_TEMPLATE_PLATFORM_DOMAINS: tuple[str, ...] = (
    "alarm_control_panel",
    "binary_sensor",
    "button",
    "cover",
    "fan",
    "image",
    "light",
    "lock",
    "number",
    "select",
    "sensor",
    "switch",
    "vacuum",
    "weather",
)


# Non-entity sub-keys inside a template config block.
# ``trigger`` and ``action`` are lists -> owner per item.
# ``variables`` is a dict -> one owner for the whole dict
# because per-variable owners would explode for little
# gain and variables share evaluation context anyway.
_TEMPLATE_LIST_SUBKEYS: tuple[str, ...] = ("trigger", "action")
_TEMPLATE_DICT_SUBKEYS: tuple[str, ...] = ("variables",)


def _scan_template(
    source: SourceInput,
    truth_set: TruthSet,
) -> list[tuple[Owner, object]]:
    """One owner per addressable unit of template.yaml.

    Block paths land on each addressable unit:

    - ``config-block[N].<platform>[M]`` -- entity item M
      inside platform (sensor/binary_sensor/...) of block N.
    - ``config-block[N].trigger[M]`` /
      ``config-block[N].action[M]`` -- list-items of the
      trigger/action lists (if present).
    - ``config-block[N].variables`` -- a single owner for
      the per-block variables dict.

    Each entity-platform item also receives a resolved
    ``entity_id`` (when ``unique_id`` is in the registry)
    and a human ``friendly_name`` from ``item.name``.
    Block-level non-entity owners have no friendly name --
    their display label falls back to
    ``integration + " - " + block_path``.
    """
    owners: list[tuple[Owner, object]] = []
    parsed = source.parsed
    if not isinstance(parsed, list):
        return owners

    for i, block in enumerate(parsed):
        if not isinstance(block, dict):
            continue

        # Entity platforms -- owner per entity item.
        for domain in _TEMPLATE_PLATFORM_DOMAINS:
            items = block.get(domain)
            if items is None:
                continue
            # HA accepts sensor: as either a dict (single
            # entity) or a list of dicts. Coerce dict ->
            # single-element list so we iterate uniformly.
            if isinstance(items, dict):
                items = [items]
            if not isinstance(items, list):
                continue
            for m, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                unique_id = item.get("unique_id") or ""
                name_raw = item.get("name") or ""
                friendly: str | None = None
                if name_raw:
                    friendly = str(name_raw)
                elif unique_id:
                    friendly = str(unique_id)
                owner_eid = None
                if unique_id:
                    owner_eid = truth_set.entity_by_unique_id.get(
                        ("template", str(unique_id))
                    )
                owner = Owner(
                    source_file=source.path,
                    integration="template",
                    block_path=f"config-block[{i}].{domain}[{m}]",
                    friendly_name=friendly,
                    entity_id=owner_eid,
                )
                _owner_from_registry(owner, truth_set)
                owners.append((owner, item))

        # List-backed block-level keys -- owner per item.
        for subkey in _TEMPLATE_LIST_SUBKEYS:
            raw = block.get(subkey)
            if raw is None:
                continue
            if isinstance(raw, dict):
                raw = [raw]
            if not isinstance(raw, list):
                continue
            for m, item in enumerate(raw):
                sub_owner = Owner(
                    source_file=source.path,
                    integration="template",
                    block_path=f"config-block[{i}].{subkey}[{m}]",
                )
                owners.append((sub_owner, item))

        # Dict-backed block-level keys -- one owner per key.
        for subkey in _TEMPLATE_DICT_SUBKEYS:
            raw = block.get(subkey)
            if raw is None:
                continue
            if not isinstance(raw, dict):
                continue
            sub_owner = Owner(
                source_file=source.path,
                integration="template",
                block_path=f"config-block[{i}].{subkey}",
            )
            owners.append((sub_owner, raw))
    return owners


def _scan_customize(
    source: SourceInput,
    truth_set: TruthSet,
) -> list[tuple[Owner, object]]:
    """Owner per entity customization in customize.yaml.

    customize.yaml's top-level keys *are* the entity IDs
    being customized -- each key is validated as a
    reference. The attrs dict (friendly_name, icon,
    device_class, ...) is not a ref source, so the
    ``integration == "customize"`` branch in
    ``_collect_findings`` validates the key directly
    instead of running ``_walk_tree``.
    """
    parsed = source.parsed
    if not isinstance(parsed, dict):
        return []
    owners: list[tuple[Owner, object]] = []
    for i, (eid_key, attrs) in enumerate(parsed.items()):
        eid = str(eid_key)
        owner = Owner(
            source_file=source.path,
            integration="customize",
            block_path=f"config-block[{i}]",
            friendly_name=eid,
        )
        owners.append((owner, {eid: attrs}))
    return owners


def _scan_config_entries(
    source: SourceInput,
    truth_set: TruthSet,
) -> list[tuple[Owner, object]]:
    """Owner per entry in ``.storage/core.config_entries``."""
    owners: list[tuple[Owner, object]] = []
    parsed = source.parsed
    if not isinstance(parsed, dict):
        return owners
    data = parsed.get("data")
    if not isinstance(data, dict):
        return owners
    entries = data.get("entries")
    if not isinstance(entries, list):
        return owners

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        entry_id = str(entry.get("entry_id") or "")
        domain = str(entry.get("domain") or "")
        title = str(entry.get("title") or entry_id)

        owner_eid = truth_set.entity_by_unique_id.get(
            (domain, entry_id),
        )
        # Only generate an entities-page URL if the config
        # entry actually owns entities in the registry.
        # Entries like HomeKit bridges reference entities
        # from other integrations and have no owned entities,
        # so the URL would show an empty list.
        has_entities = entry_id in truth_set.config_entries_with_entities
        url = (
            f"/config/entities/?config_entry={entry_id}"
            if has_entities
            else None
        )

        owner = Owner(
            source_file=source.path,
            integration=domain,
            friendly_name=title,
            entity_id=owner_eid,
            url_path=url,
        )
        _owner_from_registry(owner, truth_set)

        # Walk both data and options -- different
        # integrations store refs in different places.
        subtree: dict[str, object] = {
            "data": entry.get("data") or {},
            "options": entry.get("options") or {},
        }
        owners.append((owner, subtree))
    return owners


def _scan_lovelace(
    source: SourceInput,
    truth_set: TruthSet,
) -> list[tuple[Owner, object]]:
    """Single owner per ``.storage/lovelace.<id>`` dashboard file."""
    parsed = source.parsed
    if not isinstance(parsed, dict):
        return []
    data = parsed.get("data")
    if not isinstance(data, dict):
        return []
    config_tree = data.get("config")
    if config_tree is None:
        return []

    title = source.extra.get("title") or source.path
    url_path = source.extra.get("url_path")
    owner = Owner(
        source_file=source.path,
        integration="lovelace",
        friendly_name=title,
        url_path=url_path,
    )
    return [(owner, config_tree)]


def _scan_generic_yaml(
    source: SourceInput,
    truth_set: TruthSet,
) -> list[tuple[Owner, object]]:
    """Generic YAML adapter with structural owner derivation.

    Handles every YAML source without a dedicated
    adapter (plants.yaml, utility_meters.yaml,
    sensor.yaml, notifications.yaml, groups.yaml,
    scenes.yaml, configuration.yaml, and any future
    integration whose YAML config we haven't learned
    about).

    Owner derivation is structural, not per-integration:

    - Top-level **dict**: each top-level key becomes an
      owner with ``block_path=config-block[N]`` and
      ``friendly_name=<key>``. Dict insertion order
      drives ``N``.
    - Top-level **list**: each list item becomes an
      owner with ``block_path=config-block[N]`` and
      ``friendly_name=item.name/alias/id`` when present,
      otherwise ``None``.
    - **Other** (scalar/empty): one owner with
      ``block_path=None`` (there's no structural block
      to index) representing the whole file.

    Integration is ``None``, so the notification omits
    the ``Integration:`` line and ``exclude_integrations``
    cannot filter these -- users reach for
    ``exclude_paths`` instead.
    """
    owners: list[tuple[Owner, object]] = []
    parsed = source.parsed

    if isinstance(parsed, dict) and parsed:
        for i, (k, v) in enumerate(parsed.items()):
            key = str(k)
            owner = Owner(
                source_file=source.path,
                block_path=f"config-block[{i}]",
                friendly_name=key,
            )
            owners.append((owner, v))
        return owners

    if isinstance(parsed, list) and parsed:
        for i, item in enumerate(parsed):
            friendly: str | None = None
            if isinstance(item, dict):
                for key_candidate in ("name", "alias", "id"):
                    candidate_val = item.get(key_candidate)
                    if candidate_val:
                        friendly = str(candidate_val)
                        break
            owner = Owner(
                source_file=source.path,
                block_path=f"config-block[{i}]",
                friendly_name=friendly,
            )
            owners.append((owner, item))
        return owners

    # Scalar, None, or otherwise -- whole-file owner with
    # no structural block to address.
    owner = Owner(
        source_file=source.path,
    )
    owners.append((owner, parsed))
    return owners


# -- Finding collection and notification building -----------------------


@dataclass
class _OwnerStats:
    """Mutable accumulator used during ref collection."""

    refs_total: int = 0
    refs_structural: int = 0
    refs_jinja: int = 0
    refs_sniff: int = 0
    refs_valid: int = 0
    refs_disabled: int = 0
    refs_broken: int = 0
    refs_service_skipped: int = 0


def _classify_ref_origin(ref: Ref) -> str:
    """Return ``'jinja'``, ``'sniff'``, or ``'structural'``."""
    if ref.context.startswith("jinja:"):
        return "jinja"
    if ref.context.startswith("sniff:"):
        return "sniff"
    return "structural"


def _collect_findings(
    config: Config,
    owner: Owner,
    tree: object,
    truth_set: TruthSet,
) -> tuple[list[Finding], _OwnerStats]:
    """Walk one owner's subtree and classify every ref.

    Returns (findings, stats). Findings are all refs the
    caller should report (broken + disabled when
    ``check_disabled_entities`` is on). Stats track
    per-owner coverage counters fed into the state
    attributes at run end.

    Dedupes by ``(kind, value, context)`` so a single
    ref position doesn't double-count. The same broken
    entity at two different context paths (e.g. a
    trigger and a condition) intentionally produces
    separate findings -- each shows *where* the broken
    reference appears. Applies the
    service-name negative truth set (drops sniff hits
    that are registered HA services). Applies the
    unified ``exclude_entities``/``exclude_entity_regex``
    on the target side.
    """
    findings: list[Finding] = []
    stats = _OwnerStats()
    seen: set[tuple[str, str, str]] = set()

    # customize.yaml is special: the subtree is a one-
    # key dict whose key is the entity ID being
    # customized. We don't walk it for refs -- the key
    # IS the ref.
    if owner.integration == "customize" and isinstance(tree, dict):
        for eid_key, _attrs in tree.items():
            eid = str(eid_key)
            if _is_entity_excluded(
                eid,
                config.exclude_entities,
                config.exclude_entity_regex,
            ):
                continue
            ref = Ref(
                kind="entity",
                value=eid,
                context="customize.key",
            )
            stats.refs_total += 1
            stats.refs_structural += 1
            if eid in truth_set.entity_ids:
                if eid in truth_set.disabled_entity_ids:
                    stats.refs_disabled += 1
                    if config.check_disabled_entities:
                        findings.append(Finding(ref=ref, disabled=True))
                else:
                    stats.refs_valid += 1
            else:
                stats.refs_broken += 1
                findings.append(Finding(ref=ref, disabled=False))
        return findings, stats

    for ref in _walk_tree(tree, [], truth_set.domains):
        key = (ref.kind, ref.value, ref.context)
        if key in seen:
            continue
        seen.add(key)

        origin = _classify_ref_origin(ref)

        # Drop sniff matches that are actually service
        # names (negative truth set). Counted separately
        # so the stat line can show the filter working.
        if (
            origin == "sniff"
            and ref.kind == "entity"
            and ref.value in truth_set.service_names
        ):
            stats.refs_service_skipped += 1
            continue

        # User-provided target exclusions apply to all
        # kinds uniformly.
        if _is_entity_excluded(
            ref.value,
            config.exclude_entities,
            config.exclude_entity_regex,
        ):
            continue

        stats.refs_total += 1
        if origin == "jinja":
            stats.refs_jinja += 1
        elif origin == "sniff":
            stats.refs_sniff += 1
        else:
            stats.refs_structural += 1

        if ref.kind == "entity":
            if ref.value in truth_set.entity_ids:
                if ref.value in truth_set.disabled_entity_ids:
                    stats.refs_disabled += 1
                    if config.check_disabled_entities:
                        findings.append(Finding(ref=ref, disabled=True))
                else:
                    stats.refs_valid += 1
            else:
                stats.refs_broken += 1
                findings.append(Finding(ref=ref, disabled=False))
        else:  # device
            if ref.value in truth_set.device_ids:
                stats.refs_valid += 1
            else:
                stats.refs_broken += 1
                findings.append(Finding(ref=ref, disabled=False))

    return findings, stats


def _build_notification_body(
    owner: Owner,
    findings: list[Finding],
) -> str:
    """Build the notification body for an owner with findings.

    Format (user-facing):

        Owner: <block-path> - <friendly-name>    (or variants;
                                                  see _owner_display_name)
        Entity: `<eid>` [(YAML-only, edit <file>)]
        Integration: <integration>               (omitted when None)
        File: `<path>`

        Broken references (N):
        - `<value>` -- <context>
        ...

        Disabled-but-existing references (M):
        - `<value>` *(disabled)* -- <context>
        ...

    ``Integration:`` is always omitted when
    ``owner.integration is None`` so the notification
    body reflects what ``exclude_integrations`` can
    filter -- users should never see an integration
    name they can't paste into the blueprint input.

    The ``(YAML-only, edit <file>)`` note is suppressed
    whenever the owner has a ``url_path`` -- in that case
    the clickable ``Owner:`` link takes the user to HA's
    UI editor (for example automations/scripts defined
    in ``automations.yaml``/``scripts.yaml`` with an
    ``id``/key), and directing them at the YAML file is
    actively misleading.
    """
    lines: list[str] = []
    header_label = md_escape(_owner_header_label(owner))
    if owner.url_path:
        header = f"Owner: [{header_label}]({owner.url_path})"
    else:
        header = f"Owner: {header_label}"
    lines.append(header)

    # ``yaml_only`` means the entity isn't backed by a
    # config entry, but HA's UI may still edit the owner
    # (e.g. entries in automations.yaml / scripts.yaml
    # with an ``id``/key -- those get a UI edit URL). When
    # an edit URL is present the Owner: link already
    # takes the user to the UI editor, so suppress the
    # "edit the YAML file" nag. The File: line below
    # still shows the path for YAML-first editors.
    show_yaml_note = owner.yaml_only and owner.url_path is None
    if owner.entity_id:
        entity_line = f"Entity: `{owner.entity_id}`"
        if show_yaml_note:
            entity_line += f" (YAML-only, edit `{owner.source_file}`)"
        lines.append(entity_line)
    elif show_yaml_note:
        lines.append(
            f"(YAML-only, edit `{owner.source_file}`)",
        )

    if owner.integration:
        lines.append(
            "Integration: "
            f"[{owner.integration}]"
            f"(/config/integrations/integration/{owner.integration})"
        )

    lines.append(f"File: `{owner.source_file}`")

    broken = [f for f in findings if not f.disabled]
    disabled = [f for f in findings if f.disabled]

    broken = [
        f
        for _, _, f in sorted(
            [
                ((f.ref.kind, f.ref.value, f.ref.context), i, f)
                for i, f in enumerate(broken)
            ]
        )
    ]
    disabled = [
        f
        for _, _, f in sorted(
            [
                ((f.ref.kind, f.ref.value, f.ref.context), i, f)
                for i, f in enumerate(disabled)
            ]
        )
    ]

    if broken:
        lines.append("")
        lines.append(f"Broken references ({len(broken)}):")
        for f in broken:
            kind_tag = "device" if f.ref.kind == "device" else ""
            suffix = f" [{kind_tag}]" if kind_tag else ""
            lines.append(
                f"- `{f.ref.value}`{suffix}  -- `{f.ref.context}`",
            )

    if disabled:
        lines.append("")
        lines.append(
            f"Disabled-but-existing references ({len(disabled)}):",
        )
        for f in disabled:
            lines.append(
                f"- `{f.ref.value}` *(disabled)*  -- `{f.ref.context}`",
            )

    return "\n".join(lines)


def _build_owner_result(
    config: Config,
    owner: Owner,
    findings: list[Finding],
    stats: _OwnerStats,
) -> OwnerResult:
    """Bundle owner + findings + stats into an OwnerResult."""
    import hashlib

    has_issue = len(findings) > 0
    # (source_file, block_path, friendly_name) is the
    # stable identity tuple. The sanitized string is a
    # human-readable prefix; the sha1 suffix guards
    # against collisions when two distinct identities
    # sanitize to the same string.
    raw_id = (
        f"{owner.source_file}\0"
        f"{owner.block_path or ''}\0"
        f"{owner.friendly_name or ''}"
    )
    suffix = hashlib.sha1(raw_id.encode("utf-8")).hexdigest()[:8]
    sanitized = _sanitize_notification_id(raw_id)
    notification_id = f"{config.notification_prefix}owner_{sanitized}_{suffix}"
    title = ""
    message = ""
    if has_issue:
        title = f"Reference watchdog: {_owner_display_name(owner)}"
        message = _build_notification_body(owner, findings)
    return OwnerResult(
        owner=owner,
        has_issue=has_issue,
        notification_id=notification_id,
        notification_title=title,
        notification_message=message,
        findings=findings,
        refs_total=stats.refs_total,
        refs_structural=stats.refs_structural,
        refs_jinja=stats.refs_jinja,
        refs_sniff=stats.refs_sniff,
        refs_valid=stats.refs_valid,
        refs_disabled=stats.refs_disabled,
        refs_broken=stats.refs_broken,
        refs_service_skipped=stats.refs_service_skipped,
    )


# Source type -> adapter dispatch. _scan_generic_yaml is the
# catch-all for anything not in this map.
_AdapterFn = Callable[
    [SourceInput, TruthSet],
    list[tuple[Owner, object]],
]
_ADAPTERS: dict[str, _AdapterFn] = {
    "automations": _scan_automations,
    "scripts": _scan_scripts,
    "template": _scan_template,
    "customize": _scan_customize,
    "config_entries": _scan_config_entries,
    "lovelace": _scan_lovelace,
    "generic_yaml": _scan_generic_yaml,
}


def _evaluate_sources(
    config: Config,
    sources: Sequence[SourceInput],
    truth_set: TruthSet,
) -> list[OwnerResult]:
    """Evaluate reference integrity for every scanned source.

    Main entry point called by the service wrapper.

    Dispatches each ``SourceInput`` to its adapter (or
    to ``_scan_generic_yaml`` if no dedicated adapter
    registered for its type), collects owners, walks
    their subtrees, applies exclusions, and returns
    one ``OwnerResult`` per owner -- including owners
    with zero findings (the service wrapper uses these
    for the ``owners_total`` / ``owners_with_refs`` /
    ``owners_without_refs`` stats).
    """
    results: list[OwnerResult] = []
    for source in sources:
        adapter = _ADAPTERS.get(source.source_type, _scan_generic_yaml)
        owners = adapter(source, truth_set)

        for owner, tree in owners:
            # Source-side entity exclusion. The owner's
            # entity_id, when available, is checked
            # against the unified entity-exclude list.
            if owner.entity_id is not None and _is_entity_excluded(
                owner.entity_id,
                config.exclude_entities,
                config.exclude_entity_regex,
            ):
                continue

            # Source-side integration exclusion. Generic
            # YAML owners have integration=None and are
            # never integration-excluded -- users reach
            # for exclude_paths for those.
            if _is_integration_excluded(
                owner.integration,
                config.exclude_integrations,
            ):
                continue

            findings, stats = _collect_findings(
                config,
                owner,
                tree,
                truth_set,
            )
            result = _build_owner_result(
                config,
                owner,
                findings,
                stats,
            )
            results.append(result)

    return results


# -- YAML source discovery ----------------------------------------------
#
# These functions handle YAML file parsing and include-
# following for source discovery. They run in HA's
# executor thread (via ``hass.async_add_executor_job``)
# because they do blocking file I/O.

# Relative path -> source_type dispatch key.
_DEDICATED_SOURCE_TYPES: dict[str, str] = {
    "automations.yaml": "automations",
    "scripts.yaml": "scripts",
    "template.yaml": "template",
    "customize.yaml": "customize",
}


_HA_YAML_TAGS = (
    "!include",
    "!include_dir_list",
    "!include_dir_named",
    "!include_dir_merge_list",
    "!include_dir_merge_named",
    "!secret",
    "!env_var",
)


def _register_yaml_tag_constructors() -> None:
    """Register placeholder constructors for HA YAML tags.

    Idempotent. Replaces HA-specific tags (``!include``,
    ``!secret``, etc.) with inert placeholder strings so
    ``yaml.safe_load`` succeeds without HA's tag handlers.
    """
    import yaml

    if getattr(
        yaml.SafeLoader,
        "_rw_tag_ctors_installed",
        False,
    ):
        return

    for tag in _HA_YAML_TAGS:
        tag_name = tag

        def _ctor(
            loader: object,
            node: object,
            _tag: str = tag_name,
        ) -> str:
            _ = loader
            if isinstance(node, yaml.ScalarNode):
                return f"<{_tag}:{node.value}>"
            return f"<{_tag}>"

        yaml.SafeLoader.add_constructor(tag, _ctor)

    yaml.SafeLoader._rw_tag_ctors_installed = True  # noqa: SLF001


def _read_yaml_file(path: str) -> object:
    """Read + parse a YAML file. Returns None on any failure."""
    import io  # noqa: UP035

    import yaml

    _register_yaml_tag_constructors()
    try:
        with io.open(path, encoding="utf-8") as f:  # noqa: UP020
            content = f.read()
    except OSError:
        return None
    try:
        return yaml.safe_load(content)
    except yaml.YAMLError:
        return None


def _read_json_file(path: str) -> object:
    """Read + parse a JSON file. Returns None on any failure."""
    import io  # noqa: UP035
    import json

    try:
        with io.open(path, encoding="utf-8") as f:  # noqa: UP020
            content = f.read()
    except OSError:
        return None
    try:
        return json.loads(content)
    except (ValueError, TypeError):
        return None


# Pre-``!include`` prefix rejects quotes and hashes to
# skip directives sitting inside comments or quoted
# string values. The optional group allows a bare
# ``!include`` at column 0.
_INCLUDE_RE = re.compile(
    r"^(?:[^#\"']*?\s)?"
    r"!include(?:_dir_(?:list|named|merge_list|merge_named))?"
    r"\s+(\S+)",
    re.MULTILINE,
)


def _extract_includes_from_text(
    text: str,
    parent_rel_path: str,
    config_dir: str,
) -> list[str]:
    """Extract include targets from raw YAML text.

    Regex-based extraction from the raw file content
    rather than the parsed tree. This is simpler and
    more reliable than walking the parsed tree for
    placeholder strings.
    """
    import os

    parent_dir = os.path.dirname(parent_rel_path)
    result: list[str] = []

    for m in _INCLUDE_RE.finditer(text):
        target = m.group(1)
        abs_target = os.path.join(
            config_dir,
            parent_dir,
            target,
        )
        if os.path.isdir(abs_target):
            try:
                entries = sorted(os.listdir(abs_target))
            except OSError:
                entries = []
            for fname in entries:
                if fname.endswith((".yaml", ".yml")):
                    rel = os.path.normpath(
                        os.path.join(
                            parent_dir,
                            target,
                            fname,
                        ),
                    )
                    result.append(rel)
        else:
            rel = os.path.normpath(
                os.path.join(parent_dir, target),
            )
            result.append(rel)

    return result


def _discover_yaml_sources(
    config_dir: str,
) -> list[tuple[str, str, object]]:
    """Discover YAML files reachable from configuration.yaml.

    BFS from ``configuration.yaml``, following
    ``!include`` and ``!include_dir_*`` directives
    recursively. Each discovered file is assigned a
    source type from ``_DEDICATED_SOURCE_TYPES`` if
    its relative path matches; otherwise
    ``generic_yaml``.

    Returns ``(rel_path, source_type, parsed)`` tuples.
    Parsed data is retained from discovery so files
    aren't parsed twice.
    """
    import io  # noqa: UP035
    import os

    visited: set[str] = set()
    queue: list[str] = ["configuration.yaml"]
    discovered: list[tuple[str, str, object]] = []

    while queue:
        rel_path = queue.pop(0)
        if rel_path in visited:
            continue
        visited.add(rel_path)

        abs_path = os.path.join(config_dir, rel_path)
        if not os.path.isfile(abs_path):
            continue
        try:
            if os.path.getsize(abs_path) == 0:
                continue
        except OSError:
            continue

        # Read raw text for include extraction, then
        # parse for ref walking.
        try:
            with io.open(abs_path, encoding="utf-8") as f:  # noqa: UP020
                raw_text = f.read()
        except OSError:
            continue

        parsed = _read_yaml_file(abs_path)
        if parsed is None:
            continue

        source_type = _DEDICATED_SOURCE_TYPES.get(
            rel_path,
            "generic_yaml",
        )
        discovered.append((rel_path, source_type, parsed))

        for inc_path in _extract_includes_from_text(
            raw_text,
            rel_path,
            config_dir,
        ):
            if inc_path not in visited:
                queue.append(inc_path)

    return discovered


# -- Source orphan detection --------------------------------------------
#
# A "source orphan" is an entity in the registry with
# ``config_entry_id is None`` whose ``object_id`` and
# ``unique_id`` are absent from the platform-appropriate
# definer pool. Definer pools are built by walking the
# parsed YAML / JSON tree of each contributing file and
# harvesting:
#
# - every mapping key (strings only, lowercased)
# - every value whose key is in ``_DEFINER_ID_KEYS``
#   (``id``, ``unique_id``, ``object_id``)
#
# Walking the parsed tree -- not raw text -- skips
# comments, ``description:`` / ``alias:`` strings, and
# consumer-side refs like ``entity_id: sensor.foo`` that
# tokenize-based extraction would otherwise bleed into
# the pool.
#
# Pool routing is still per-platform so that consumer-
# side mentions in e.g. automations.yaml can't mark a
# utility_meter orphan as "defined".


# UI-helper integrations that register entities with
# ``config_entry_id=None`` and store their definitions in
# ``.storage/<helper>`` JSON files rather than in
# ``core.config_entries``. Every file in this set that
# exists on the host is loaded into the definer pool.
# Missing files are silently skipped.
_STORAGE_HELPER_DEFINER_FILES: frozenset[str] = frozenset(
    [
        "input_boolean",
        "input_button",
        "input_number",
        "input_text",
        "input_select",
        "input_datetime",
        "counter",
        "timer",
        "person",
        "zone",
        "schedule",
        # Rare but possible on hosts where the user toggled
        # "edit in UI" for items the core integrations
        # otherwise persist to YAML.
        "automation",
        "script",
        "scene",
        "group",
    ]
)


# Platforms whose entities are created at runtime (not
# from any config file) and are excluded from orphan
# detection by default.
_SOURCE_ORPHAN_RUNTIME_PLATFORMS: frozenset[str] = frozenset(["pyscript"])


# Files that reference entities but do not define them.
# Excluded from every platform's definer pool.
_SOURCE_ORPHAN_CONSUMER_YAML: frozenset[str] = frozenset(["customize.yaml"])


# Mapping keys whose string values carry a registry
# identifier (object_id or unique_id). Values under these
# keys are added verbatim (lowercased) to the pool, so
# that non-slug identifiers such as ``aa:bb:cc:dd:ee:ff``
# match as-is.
_DEFINER_ID_KEYS: frozenset[str] = frozenset(["id", "unique_id", "object_id"])


@dataclass
class _OrphanPools:
    """Per-platform sets of identifier strings harvested from definer trees.

    Each pool is a frozenset of lowercased strings drawn
    from the parsed YAML / JSON trees of the pool's
    contributing files: every mapping key, plus every
    value whose key is in ``_DEFINER_ID_KEYS``. An
    object_id or unique_id is "defined" only when it
    appears as an exact string in the set.
    """

    automations: frozenset[str]
    scripts: frozenset[str]
    template_plus_generic: frozenset[str]
    generic: frozenset[str]


def _harvest_identifiers(node: object, out: set[str]) -> None:
    """Walk ``node``, adding keys + identifier-field values to ``out``.

    Recurses through dict / list structures. Adds:

    - lowercased string keys of every dict encountered
    - lowercased string values whose key is in
      ``_DEFINER_ID_KEYS``

    Non-string keys and non-string identifier values are
    ignored; recursion continues into the value
    regardless.
    """
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(k, str):
                key_l = k.lower()
                out.add(key_l)
                if key_l in _DEFINER_ID_KEYS and isinstance(v, str):
                    out.add(v.lower())
            _harvest_identifiers(v, out)
    elif isinstance(node, list):
        for item in node:
            _harvest_identifiers(item, out)


def _enumerate_storage_helpers(
    config_dir: str,
) -> list[tuple[str, object]]:
    """Return ``(rel_path, parsed_json)`` for each existing helper file.

    Only filenames in ``_STORAGE_HELPER_DEFINER_FILES``
    are considered. Files that don't exist or fail JSON
    parsing are silently skipped.
    """
    import os

    storage_dir = os.path.join(config_dir, ".storage")
    out: list[tuple[str, object]] = []
    if not os.path.isdir(storage_dir):
        return out
    try:
        names = sorted(os.listdir(storage_dir))
    except OSError:
        return out
    for name in names:
        if name not in _STORAGE_HELPER_DEFINER_FILES:
            continue
        abs_path = os.path.join(storage_dir, name)
        if not os.path.isfile(abs_path):
            continue
        parsed = _read_json_file(abs_path)
        if parsed is None:
            continue
        out.append((f".storage/{name}", parsed))
    return out


def _build_orphan_pools(
    yaml_sources: list[tuple[str, object]],
    storage_sources: list[tuple[str, object]],
) -> _OrphanPools:
    """Bucket definer-pool identifiers by platform.

    ``yaml_sources`` is ``(rel_path, parsed)`` for every
    YAML file discovered from ``configuration.yaml``.
    ``storage_sources`` is the parsed
    ``.storage/<helper>`` JSON collected by
    ``_enumerate_storage_helpers``.

    Files in ``_SOURCE_ORPHAN_CONSUMER_YAML`` are dropped.
    Dedicated-adapter filenames route to matching
    per-platform buckets; every other YAML goes to
    "generic". ``.storage/<helper>`` files route to the
    platform matching the filename when that platform has
    its own dedicated bucket, otherwise to "generic".

    Each bucket is populated by walking contributing
    trees via ``_harvest_identifiers``.
    """
    import os

    auto_ids: set[str] = set()
    script_ids: set[str] = set()
    template_ids: set[str] = set()
    generic_ids: set[str] = set()

    for rel, parsed in yaml_sources:
        if rel in _SOURCE_ORPHAN_CONSUMER_YAML:
            continue
        fname = os.path.basename(rel)
        if fname == "automations.yaml":
            _harvest_identifiers(parsed, auto_ids)
        elif fname == "scripts.yaml":
            _harvest_identifiers(parsed, script_ids)
        elif fname == "template.yaml":
            _harvest_identifiers(parsed, template_ids)
        else:
            _harvest_identifiers(parsed, generic_ids)

    for rel, parsed in storage_sources:
        fname = os.path.basename(rel)
        if fname == "automation":
            _harvest_identifiers(parsed, auto_ids)
        elif fname == "script":
            _harvest_identifiers(parsed, script_ids)
        else:
            _harvest_identifiers(parsed, generic_ids)

    return _OrphanPools(
        automations=frozenset(auto_ids),
        scripts=frozenset(script_ids),
        template_plus_generic=frozenset(
            template_ids | generic_ids,
        ),
        generic=frozenset(generic_ids),
    )


def _pool_for_platform(
    pools: _OrphanPools,
    platform: str,
) -> frozenset[str]:
    """Return the identifier set to search for ``platform``."""
    if platform == "automation":
        return pools.automations
    if platform == "script":
        return pools.scripts
    if platform == "template":
        return pools.template_plus_generic
    return pools.generic


@dataclass(frozen=True)
class SourceOrphan:
    """One registry entry whose definer could not be found."""

    entity_id: str
    platform: str
    unique_id: str
    disabled: bool


def _find_source_orphans(
    config: Config,
    truth_set: TruthSet,
    yaml_sources: list[tuple[str, object]],
    storage_sources: list[tuple[str, object]],
) -> list[SourceOrphan]:
    """Identify registry entries with no matching definer.

    Restricts to entries with ``config_entry_id is None``
    (UI config-flow entries have HA-managed lifecycles and
    are not our concern). Skips platforms in
    ``_SOURCE_ORPHAN_RUNTIME_PLATFORMS`` and applies
    ``exclude_entities`` / ``exclude_entity_regex``
    symmetrically.
    """
    pools = _build_orphan_pools(yaml_sources, storage_sources)

    orphans: list[SourceOrphan] = []
    for eid, entry in truth_set.registry.items():
        if entry.config_entry_id is not None:
            continue
        if entry.platform in _SOURCE_ORPHAN_RUNTIME_PLATFORMS:
            continue
        if _is_entity_excluded(
            eid,
            config.exclude_entities,
            config.exclude_entity_regex,
        ):
            continue
        object_id = eid.split(".", 1)[1].lower()
        pool = _pool_for_platform(pools, entry.platform)
        if object_id in pool:
            continue
        uid = (entry.unique_id or "").lower()
        if uid and uid in pool:
            continue
        orphans.append(
            SourceOrphan(
                entity_id=eid,
                platform=entry.platform,
                unique_id=entry.unique_id or "",
                disabled=entry.disabled,
            ),
        )
    return orphans


def _source_orphans_notification_id(config: Config) -> str:
    """Stable notification ID for the source-orphan summary."""
    return f"{config.notification_prefix}source_orphans"


def _orphan_url(platform: str) -> str:
    """Entities-page URL filtered to the orphan's integration.

    HA's entities page supports two URL filters:
    ``?config_entry=<entry_id>`` (doesn't apply here --
    orphans have no config entry by definition) and
    ``?domain=<integration>`` (filters to entities owned
    by the named integration). Other proposed filters
    like ``?search=`` and ``?entity_id=`` are not wired
    up: see
    https://github.com/orgs/home-assistant/discussions/1538.

    Using ``?domain=<platform>`` takes the user to the
    entities page showing just that integration's rows.
    They visually find the orphan and click it to open
    the per-entity settings dialog, which has a Delete
    button for registry entries no longer claimed by
    any integration.

    Direct editor URLs like ``/config/automation/edit/<id>``
    work fine for live automations but not for orphans --
    the automation was removed from the YAML, so the
    editor renders a blank-or-missing form and won't help
    the user delete the registry entry.
    """
    if not platform:
        return "/config/entities"
    return f"/config/entities?domain={platform}"


def _build_source_orphans_notification(
    config: Config,
    orphans: list[SourceOrphan],
) -> PersistentNotification:
    """One summary notification listing every orphan.

    Grouped by platform, each entity ID rendered as a
    clickable link to the entities page filtered by the
    orphan's platform integration (see ``_orphan_url``).

    When ``orphans`` is empty, an inactive notification
    is returned so a previously-active summary gets
    dismissed once the user cleans up.
    """
    nid = _source_orphans_notification_id(config)
    if not orphans:
        return PersistentNotification(
            active=False,
            notification_id=nid,
            title="",
            message="",
        )

    by_platform: dict[str, list[SourceOrphan]] = {}
    for o in orphans:
        by_platform.setdefault(o.platform or "", []).append(o)
    # Sort platforms by descending count then name, and
    # each group's entries by entity_id -- deterministic
    # output so the notification doesn't churn between
    # runs.
    plat_order = [
        plat
        for _, _, plat in sorted(
            [
                ((-len(by_platform[p]), p), i, p)
                for i, p in enumerate(by_platform.keys())
            ]
        )
    ]

    lines: list[str] = []
    lines.append(
        f"{len(orphans)} registry entries have no current"
        " definer in your configuration. Click an entity"
        " to open the filtered entities page, then open"
        " the entry and delete it."
    )
    lines.append("")
    for platform in plat_order:
        entries = [
            o
            for _, _, o in sorted(
                [
                    (o.entity_id, i, o)
                    for i, o in enumerate(by_platform[platform])
                ]
            )
        ]
        plat_label = platform or "(no platform)"
        url = _orphan_url(platform)
        lines.append(f"**{md_escape(plat_label)}** ({len(entries)}):")
        for o in entries:
            tag = " *(disabled)*" if o.disabled else ""
            lines.append(f"- [`{o.entity_id}`]({url}){tag}")
        lines.append("")

    return PersistentNotification(
        active=True,
        notification_id=nid,
        title=f"Reference watchdog: source orphans ({len(orphans)})",
        message="\n".join(lines).rstrip() + "\n",
    )


# -- Source enumeration and evaluation ----------------------------------


def _enumerate_json_sources(
    config_dir: str,
) -> list[SourceInput]:
    """Enumerate JSON storage sources (config entries, lovelace)."""
    import os

    sources: list[SourceInput] = []

    # .storage/core.config_entries
    ce_path = os.path.join(
        config_dir,
        ".storage",
        "core.config_entries",
    )
    if os.path.isfile(ce_path):
        parsed = _read_json_file(ce_path)
        if parsed is not None:
            sources.append(
                SourceInput(
                    source_type="config_entries",
                    path=".storage/core.config_entries",
                    parsed=parsed,
                ),
            )

    # .storage/lovelace.<dashboard_id>
    storage_dir = os.path.join(config_dir, ".storage")
    dashboards_index: dict[str, dict[str, str]] = {}
    idx_path = os.path.join(
        storage_dir,
        "lovelace_dashboards",
    )
    if os.path.isfile(idx_path):
        idx_parsed = _read_json_file(idx_path)
        if isinstance(idx_parsed, dict):
            idx_data = idx_parsed.get("data", {})
            if isinstance(idx_data, dict):
                idx_items = idx_data.get("items", [])
                if isinstance(idx_items, list):
                    for item in idx_items:
                        if not isinstance(item, dict):
                            continue
                        dash_id = item.get("id", "")
                        if dash_id:
                            url_path = item.get("url_path") or dash_id
                            dashboards_index[dash_id] = {
                                "title": str(
                                    item.get("title") or dash_id,
                                ),
                                "url_path": f"/{url_path}",
                            }

    try:
        storage_files = sorted(os.listdir(storage_dir))
    except OSError:
        storage_files = []
    for fname in storage_files:
        if not fname.startswith("lovelace."):
            continue
        dash_id = fname[len("lovelace.") :]
        lv_path = os.path.join(storage_dir, fname)
        if not os.path.isfile(lv_path):
            continue
        parsed = _read_json_file(lv_path)
        if parsed is None:
            continue
        extra = dashboards_index.get(
            dash_id,
            {
                "title": dash_id,
                "url_path": f"/{dash_id}",
            },
        )
        sources.append(
            SourceInput(
                source_type="lovelace",
                path=f".storage/{fname}",
                parsed=parsed,
                extra=extra,
            ),
        )

    return sources


@dataclass
class EvaluationResult:
    """Full evaluation result returned from ``run_evaluation``.

    Contains everything the service wrapper needs to
    process notifications, save state, and emit debug
    logging -- without requiring further computation
    on the main thread.
    """

    results: list[OwnerResult]
    notifications: list[PersistentNotification]
    paths_included: int
    paths_excluded: int
    owners_total: int
    owners_with_refs: int
    owners_without_refs: int
    owners_with_issues: int
    total_findings: int
    broken_entity_count: int
    broken_device_count: int
    disabled_entity_count: int
    refs_total: int
    refs_structural: int
    refs_jinja: int
    refs_sniff: int
    refs_service_skipped: int
    source_orphan_count: int
    source_orphan_candidates: int


def run_evaluation(
    config_dir: str,
    config: Config,
    truth_set: TruthSet,
    exclude_paths_list: list[str],
    max_notifications: int,
) -> EvaluationResult:
    """Run the full evaluation pipeline.

    Designed to be called from a worker thread via
    ``hass.async_add_executor_job`` so the main event
    loop stays responsive. All file I/O, YAML parsing,
    tree walking, and notification building happens here.

    The caller (service wrapper) is responsible for:
    - Building the truth set (requires HA registries,
      must run on the event loop)
    - Processing the returned notifications (HA service
      calls, must run on the event loop)
    - Saving state attributes
    """
    yaml_sources = _discover_yaml_sources(config_dir)
    json_sources = _enumerate_json_sources(config_dir)
    all_sources: list[SourceInput] = []
    for rel_path, source_type, parsed in yaml_sources:
        all_sources.append(
            SourceInput(
                source_type=source_type,
                path=rel_path,
                parsed=parsed,
            ),
        )
    all_sources.extend(json_sources)

    # Filter by exclude_paths
    included: list[SourceInput] = []
    paths_excluded = 0
    for src in all_sources:
        if _is_path_excluded(src.path, exclude_paths_list):
            paths_excluded += 1
        else:
            included.append(src)

    # Evaluate
    results = _evaluate_sources(config, included, truth_set)

    # Source-orphan detection reuses the parsed YAML trees
    # already loaded for discovery and adds parsed
    # ``.storage/<helper>`` JSON. Pool identifiers are
    # harvested structurally (mapping keys + values under
    # ``_DEFINER_ID_KEYS``), so comments and free-text
    # fields like ``description:`` don't bleed in.
    yaml_parsed = [(rel, parsed) for rel, _src, parsed in yaml_sources]
    storage_parsed = _enumerate_storage_helpers(config_dir)
    orphans = _find_source_orphans(
        config,
        truth_set,
        yaml_parsed,
        storage_parsed,
    )
    source_orphan_candidates = sum(
        [
            1
            for e in truth_set.registry.values()
            if e.config_entry_id is None
            and e.platform not in _SOURCE_ORPHAN_RUNTIME_PLATFORMS
        ]
    )

    # Build notifications.
    notifications = prepare_notifications(
        results,
        max_notifications=max_notifications,
        cap_notification_id=f"{config.notification_prefix}cap",
        cap_title="Reference watchdog: notification cap reached",
        cap_item_label="owners with broken references",
    )
    # The orphan summary sits outside the per-owner cap --
    # it's a single notification regardless of orphan
    # count. The builder emits an inactive placeholder when
    # there are no orphans so any previously-active summary
    # gets dismissed.
    notifications.append(
        _build_source_orphans_notification(config, orphans),
    )

    # Compute summary stats.
    owners_total = len(results)
    owners_with_refs = sum(1 for r in results if r.refs_total > 0)
    owners_without_refs = sum(1 for r in results if r.refs_total == 0)
    owners_with_issues = sum(1 for r in results if r.has_issue)
    total_findings = sum(len(r.findings) for r in results)
    broken_entity_count = sum(
        1
        for r in results
        for f in r.findings
        if not f.disabled and f.ref.kind == "entity"
    )
    broken_device_count = sum(
        1
        for r in results
        for f in r.findings
        if not f.disabled and f.ref.kind == "device"
    )
    disabled_entity_count = sum(
        1 for r in results for f in r.findings if f.disabled
    )
    refs_total = sum(r.refs_total for r in results)
    refs_structural = sum(r.refs_structural for r in results)
    refs_jinja = sum(r.refs_jinja for r in results)
    refs_sniff = sum(r.refs_sniff for r in results)
    refs_service_skipped = sum(r.refs_service_skipped for r in results)

    return EvaluationResult(
        results=results,
        notifications=notifications,
        paths_included=len(included),
        paths_excluded=paths_excluded,
        owners_total=owners_total,
        owners_with_refs=owners_with_refs,
        owners_without_refs=owners_without_refs,
        owners_with_issues=owners_with_issues,
        total_findings=total_findings,
        broken_entity_count=broken_entity_count,
        broken_device_count=broken_device_count,
        disabled_entity_count=disabled_entity_count,
        refs_total=refs_total,
        refs_structural=refs_structural,
        refs_jinja=refs_jinja,
        refs_sniff=refs_sniff,
        refs_service_skipped=refs_service_skipped,
        source_orphan_count=len(orphans),
        source_orphan_candidates=source_orphan_candidates,
    )
