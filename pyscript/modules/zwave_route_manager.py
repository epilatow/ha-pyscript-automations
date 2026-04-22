# This is AI generated code
"""Logic for declarative Z-Wave priority route management.

Pure functions over dataclasses. No PyScript-injected globals,
no HA registries, no socket.io. The service wrapper is
responsible for:

- Reading the config file from disk
- Building the entity->device resolution map from HA registries
- Calling ``zwave_js_ui_bridge.ZwaveJsUiClient`` for I/O
- Persisting the pending map in the state entity

This module owns:

- YAML parsing (``parse_config``)
- Entity-to-node resolution + speed precedence (``resolve_entities``)
- Diff-and-plan (``diff_and_plan``) -- produces ``RouteAction`` list
  and updated pending map

Two Z-Wave routes managed per configured client:

- **Application route** (controller -> node) -- what the UI calls
  "Priority route". Wire api: ``setPriorityRoute``.
- **Priority SUC return route** (node -> controller). Wire api:
  ``assignPrioritySUCReturnRoute``.

See ``docs/zwave_route_manager.md`` for user-facing docs.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

from zwave_js_ui_bridge import (
    NodeID,
    NodeInfo,
    RouteSpeed,
    speed_from_bps,
)

# -- Config dataclasses ------------------------------------------


@dataclass
class ClientSpec:
    """One client entry in a route's clients list.

    ``route_speed = None`` means "inherit from the enclosing
    RouteEntry, then from the blueprint default, then from the
    auto resolver".
    """

    entity_id: str
    route_speed: RouteSpeed | None = None


@dataclass
class RouteEntry:
    """One repeater + its clients, from the YAML config."""

    repeater_entity_id: str
    route_speed: RouteSpeed | None = None
    clients: list[ClientSpec] = field(default_factory=list)


@dataclass
class Config:
    """Parsed YAML config."""

    routes: list[RouteEntry] = field(default_factory=list)


@dataclass(kw_only=True)
class ConfigError:
    """One parsing or validation error.

    ``location`` is a YAML-path-ish string for user diagnostics
    (``"routes[0].clients[2].entity"``). ``entity_id`` is set
    when the error relates to a specific entity. ``device_id``
    is the HA device-registry UUID for the offending entity,
    set when we have a resolved DeviceResolution; used by the
    service wrapper to render the entity name as a clickable
    link to the HA device page in the notification.

    ``kw_only=True`` keeps the field order grouped by intent
    (location-then-entity-then-reason) while letting
    ``device_id`` carry a default without forcing later fields
    to default too.
    """

    location: str
    entity_id: str | None
    device_id: str | None = None
    reason: str


# -- Runtime resolution dataclasses ------------------------------


@dataclass
class DeviceResolution:
    """Resolved Z-Wave device info.

    Built by the service wrapper from HA's entity/device
    registries plus a single ``NodeInfo`` from the bridge's
    ``get_nodes()`` call. Used only as an input to
    ``resolve_entities``.

    For non-controller devices: looked up via entity_id.
    For the controller: passed separately as the ``controller``
    arg to ``resolve_entities``.
    """

    entity_id: str
    device_id: str
    node_id: NodeID
    is_routing: bool
    is_listening: bool
    is_frequent_listening: bool | str
    failed: bool
    is_long_range: bool
    max_data_rate_bps: int


@dataclass
class ResolvedRoute:
    """A fully-resolved route ready to diff and apply.

    ``repeater_node_ids`` is always a 1-element list in v1 (single
    hop). The data structure accommodates future N-hop support
    without a shape change.

    ``speed_is_auto`` is ``True`` when the route speed was picked
    by the auto-resolver (no explicit override at client, entry,
    or file level). In that case, the diff considers any node-
    reported speed to be "applied" as long as the repeaters
    match -- some devices silently negotiate the actual route
    speed down (e.g. Kwikset locks settling at 40k even when
    100k is requested), and we don't want that to trap the
    reconcile in a perpetual pending loop. When the user
    specifies an explicit speed they opt into strict matching.
    """

    client_entity_id: str
    client_node_id: NodeID
    repeater_node_ids: list[NodeID]
    route_speed: RouteSpeed
    # Parallel to ``repeater_node_ids`` -- the YAML-configured
    # entity ID for each repeater hop. Carried through purely
    # so the service wrapper can annotate stored state with
    # human-readable names; not used for diff or apply logic.
    repeater_entity_ids: list[str] = field(default_factory=list)
    speed_is_auto: bool = False


# -- Route types ------------------------------------------------


# Z-Wave routing primer (relevant to what this module manages):
#
# Z-Wave uses *source routing*: the originator of a frame writes
# the full hop sequence into the frame header. Intermediate
# repeaters are dumb forwarders -- they do not maintain routing
# tables for other nodes, they just look at the header, find
# their slot, and transmit to whichever node the header names
# as the next hop. Repeaters must therefore be always-listening
# (line-powered). Classic Z-Wave caps a path at 4 repeaters.
#
# Because each direction is source-routed, knowledge of paths
# lives on the originator's end -- and the two directions store
# independent routes:
#
#   Controller -> node (outbound)
#     "Priority application route" / "priority route". Lives in
#     the controller's memory. Written via ``setPriorityRoute``.
#     No node cooperation required.
#
#   Node -> controller / SUC (inbound)
#     "Priority SUC return route". Lives in the *node's* memory.
#     Written via ``assignPrioritySUCReturnRoute``. Requires
#     reaching the node, so battery/sleepy nodes queue the
#     command on the controller until they wake up. This
#     asymmetry is why the outbound half can land immediately
#     while the inbound half sits pending for hours or days --
#     each direction has its own independent state machine.
#
# Beyond the priority slots, both ends also keep auto-populated
# fallback slots (cached return routes, Last Working Route,
# etc.). This module only manages the *priority* routes: they
# are the preferred slot each end tries first, and the only
# slots the user is meant to configure directly.
#
# ``RouteType`` enumerates the route slots we currently write.
# New slots (e.g. non-priority SUC returns, node-to-node
# association returns) would extend the enum; the rest of the
# per-type machinery -- resolution, diff, storage -- already
# treats each type independently.
class RouteType(Enum):
    # Values are stable strings persisted in stored state;
    # don't rename them.
    PRIORITY_APP = "priority_app"  # controller -> node
    PRIORITY_SUC = "priority_suc"  # node -> controller (SUC)


# The route types this module currently writes. Iteration order
# is the order routes get evaluated per node, and the order
# they appear in serialized state lists.
MANAGED_ROUTE_TYPES: list[RouteType] = [
    RouteType.PRIORITY_APP,
    RouteType.PRIORITY_SUC,
]


# -- Action + route-state dataclasses ---------------------------


class RouteActionKind(Enum):
    """One of the four bridge operations our reconcile may emit.

    Naming follows a uniform ``SET_<direction>`` / ``CLEAR_<direction>``
    pattern. The underlying zwave-js-ui wire APIs are inconsistent
    (``setPriorityRoute`` / ``assignPrioritySUCReturnRoute`` /
    ``deleteSUCReturnRoutes``); the bridge translates between
    our internal kinds and those wire names.

    ``CLEAR_PRIORITY_SUC_RETURN_ROUTES`` API: there's no zwave-js-ui
    entry point that deletes only the priority SUC return route, so this
    clears all SUC return routes on the node (priority + custom).
    Documented in ``docs/zwave_route_manager.md``.
    """

    SET_APPLICATION_ROUTE = "set_application_route"
    CLEAR_APPLICATION_ROUTE = "clear_application_route"
    SET_PRIORITY_SUC_RETURN_ROUTE = "set_priority_suc_return_route"
    CLEAR_PRIORITY_SUC_RETURN_ROUTES = "clear_priority_suc_return_routes"


@dataclass
class RouteAction:
    """One reconcile action to execute via the bridge.

    ``repeaters`` and ``route_speed`` are only meaningful for
    the two "set" kinds; ignored for "remove"/"delete".
    ``client_entity_id`` is empty string for unmanaged-cleanup
    actions (the node is not in the user's config).
    """

    kind: RouteActionKind
    node_id: NodeID
    repeaters: list[NodeID]
    route_speed: RouteSpeed | None
    client_entity_id: str


@dataclass
class RouteRequest:
    """One configured route for a node, in either pending or
    applied state depending on which bucket holds it.

    Interpretation of the fields:

    ``repeater_node_ids``
        The target repeater list. An empty list means this
        request is a *clear* (tell the controller / node to
        drop its priority route, returning it to the default
        state). A non-empty list means this request is a
        *set* (assign the priority route to these repeaters).
        Our YAML schema requires a repeater, so clears are
        only generated internally by the clear-unmanaged
        branch.

    ``speed``
        Route speed for set requests. ``None`` for clears
        (speed is meaningless when the action is "drop the
        priority route" -- the controller / node returns to
        its default fallback behaviour, which has no
        negotiated rate).

    ``requested_at``
        The time we most recently sent the command for this
        route. ``None`` when we observed the route as applied
        on first sight (no prior tracking existed).

    ``confirmed_at``
        The time we first observed the route as applied.
        ``None`` while the route is in flight. When the route
        transitions pending -> applied in a single reconcile,
        ``requested_at`` and ``confirmed_at`` together give
        the landing latency. Carried forward across reconciles
        as long as the route stays applied to the same target.
        Not set on clear requests -- clears fall out of
        ``new_pending`` on completion rather than moving to
        ``new_applied``.

    ``timeout_count``
        Number of times this route's pending interval has
        elapsed without the route landing. Each timeout
        re-issues the command and bumps the count; carried
        forward into the applied entry once a set lands.
        Survives ticks (and incidentally restarts) but resets
        to ``0`` if the YAML changes the desired route's
        repeaters or speed.

    A ``RouteRequest`` in ``new_pending`` always has
    ``requested_at`` set and ``confirmed_at`` ``None``; in
    ``new_applied`` it always has ``confirmed_at`` set and
    non-empty ``repeater_node_ids``.
    """

    type: RouteType
    repeater_node_ids: list[NodeID]
    speed: RouteSpeed | None
    requested_at: datetime | None = None
    confirmed_at: datetime | None = None
    timeout_count: int = 0


@dataclass
class ReconcileResult:
    """Output of ``diff_and_plan``. All fields are pure data.

    ``new_pending`` / ``new_applied`` contain at most one
    ``RouteRequest`` per (node_id, RouteType). A node with a
    half-applied state appears in *both* dicts -- its applied
    type in ``new_applied``, its in-flight type in
    ``new_pending``. Nodes with nothing to track for either
    type appear in neither.

    Clear requests (``RouteRequest`` with empty
    ``repeater_node_ids``) appear in ``new_pending`` only;
    once the clear lands, the entry drops out entirely
    rather than moving to ``new_applied``.

    ``new_timeouts`` records each pending route that just
    crossed its timeout threshold this reconcile (covers both
    sets and clears). Each entry carries the (node_id,
    route_type), the ``requested_at`` of the attempt that
    just timed out (used as the unique notification ID for
    the event), and the new ``timeout_count`` after the bump.
    The reconcile also re-issues a fresh action for each
    timed-out route, so ``new_pending`` carries a fresh
    ``RouteRequest`` with ``requested_at=now`` and the bumped
    count.
    """

    actions: list[RouteAction] = field(default_factory=list)
    new_pending: dict[NodeID, list[RouteRequest]] = field(default_factory=dict)
    new_applied: dict[NodeID, list[RouteRequest]] = field(default_factory=dict)
    new_timeouts: list[tuple[NodeID, RouteType, datetime, int]] = field(
        default_factory=list,
    )


# -- Parsing -----------------------------------------------------


_ROUTE_SPEED_STRINGS: dict[str, RouteSpeed | None] = {
    "auto": None,
    "9600": RouteSpeed.RATE_9600,
    "40k": RouteSpeed.RATE_40K,
    "100k": RouteSpeed.RATE_100K,
}

# Accept bare-integer YAML values (``route_speed: 9600``) as an
# alternative to the string form. Maps bps -> RouteSpeed.
_ROUTE_SPEED_INTS: dict[int, RouteSpeed] = {
    9600: RouteSpeed.RATE_9600,
    40000: RouteSpeed.RATE_40K,
    100000: RouteSpeed.RATE_100K,
}


def parse_route_speed_value(
    raw: object,
    location: str,
) -> tuple[RouteSpeed | None, ConfigError | None]:
    """Parse a ``route_speed`` value from the YAML.

    Returns (resolved_speed_or_none, error_or_none). A missing
    value (``raw is None``, e.g. key absent) is not an error --
    it means "inherit". An invalid value IS an error.

    Accepts strings ``"auto" | "9600" | "40k" | "100k"`` and
    bare integer bps ``9600 | 40000 | 100000``. YAML treats
    unquoted ``9600`` as an integer, so accepting both forms
    avoids a surprise for users typing the obvious value.

    "auto" parses to ``None`` -- semantically equivalent to
    "inherit" since auto resolution happens after inheritance
    collapses to None at the bottom of the precedence stack.
    """
    if raw is None:
        return None, None
    # Check bool before int -- in Python, bool is a subclass of
    # int, and ``True == 1``, ``False == 0`` would coincidentally
    # round-trip through the int mapping. Reject explicitly.
    if isinstance(raw, bool):
        return None, ConfigError(
            location=location,
            entity_id=None,
            reason=("route_speed must be a string or bps integer; got bool"),
        )
    if isinstance(raw, int):
        if raw in _ROUTE_SPEED_INTS:
            return _ROUTE_SPEED_INTS[raw], None
        allowed_ints = ", ".join([str(k) for k in sorted(_ROUTE_SPEED_INTS)])
        return None, ConfigError(
            location=location,
            entity_id=None,
            reason=(
                f"route_speed integer must be one of {allowed_ints}; got {raw}"
            ),
        )
    if not isinstance(raw, str):
        return None, ConfigError(
            location=location,
            entity_id=None,
            reason=f"route_speed must be a string; got {type(raw).__name__}",
        )
    if raw not in _ROUTE_SPEED_STRINGS:
        allowed = ", ".join(sorted(_ROUTE_SPEED_STRINGS.keys()))
        return None, ConfigError(
            location=location,
            entity_id=None,
            reason=f"route_speed must be one of {allowed}; got {raw!r}",
        )
    return _ROUTE_SPEED_STRINGS[raw], None


def _is_entity_id(s: object) -> bool:
    """Shallow entity-id shape check: non-empty domain.object_id string."""
    if not isinstance(s, str):
        return False
    if "." not in s:
        return False
    domain, _, rest = s.partition(".")
    return bool(domain) and bool(rest)


def _parse_client(
    raw: object,
    location: str,
) -> tuple[list[ClientSpec], list[ConfigError]]:
    """Parse one entry in a ``clients`` list.

    Accepts three shapes:

    - A bare string: ``lock.front_door``
    - A dict with ``entity:`` + optional ``route_speed:``
    - A dict with ``entities:`` list + optional ``route_speed:``
      (group override: the speed applies to every listed entity)

    Returns a list of ClientSpec (one for a singleton, many for
    a group) plus any errors. Each emitted ClientSpec carries
    the per-entry override.
    """
    if isinstance(raw, str):
        if not _is_entity_id(raw):
            return [], [
                ConfigError(
                    location=location,
                    entity_id=raw,
                    reason="expected entity ID (domain.object_id)",
                ),
            ]
        return [ClientSpec(entity_id=raw, route_speed=None)], []

    if not isinstance(raw, dict):
        return [], [
            ConfigError(
                location=location,
                entity_id=None,
                reason=(
                    "client entry must be a string or mapping; got "
                    + type(raw).__name__
                ),
            ),
        ]

    has_entity = "entity" in raw
    has_entities = "entities" in raw
    errors: list[ConfigError] = []

    if has_entity and has_entities:
        errors.append(
            ConfigError(
                location=location,
                entity_id=None,
                reason=(
                    "client entry cannot have both 'entity' and 'entities'"
                ),
            ),
        )
        return [], errors

    if not has_entity and not has_entities:
        errors.append(
            ConfigError(
                location=location,
                entity_id=None,
                reason=("client entry must have 'entity' or 'entities'"),
            ),
        )
        return [], errors

    speed, speed_err = parse_route_speed_value(
        raw.get("route_speed"),
        f"{location}.route_speed",
    )
    if speed_err is not None:
        errors.append(speed_err)
        # Continue with speed=None; the caller will emit more
        # errors if further validation fails, consistent with
        # "return all errors at once" policy.

    clients: list[ClientSpec] = []
    if has_entity:
        ent = raw.get("entity")
        if not _is_entity_id(ent):
            errors.append(
                ConfigError(
                    location=f"{location}.entity",
                    entity_id=ent if isinstance(ent, str) else None,
                    reason="'entity' must be an entity ID",
                ),
            )
        else:
            # ent is a valid entity_id str (_is_entity_id verified).
            assert isinstance(ent, str)
            clients.append(ClientSpec(entity_id=ent, route_speed=speed))
    else:
        ents_raw = raw.get("entities")
        if not isinstance(ents_raw, list):
            errors.append(
                ConfigError(
                    location=f"{location}.entities",
                    entity_id=None,
                    reason=(
                        "'entities' must be a list; got "
                        f"{type(ents_raw).__name__}"
                    ),
                ),
            )
        else:
            for k, ent in enumerate(ents_raw):
                ent_loc = f"{location}.entities[{k}]"
                if not _is_entity_id(ent):
                    errors.append(
                        ConfigError(
                            location=ent_loc,
                            entity_id=(ent if isinstance(ent, str) else None),
                            reason="expected entity ID",
                        ),
                    )
                    continue
                assert isinstance(ent, str)
                clients.append(
                    ClientSpec(entity_id=ent, route_speed=speed),
                )

    return clients, errors


def parse_config(yaml_text: str) -> tuple[Config, list[ConfigError]]:
    """Parse the YAML config string.

    Returns a (partial) Config plus any errors encountered.
    Callers should treat any non-empty error list as "abort
    reconcile, raise persistent notification(s)".

    Empty input (empty string, missing ``routes`` key, empty
    file) is not an error -- returns an empty Config.
    """
    import yaml  # noqa: PLC0415 - deferred; pyscript AST compat

    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as e:
        return Config(), [
            ConfigError(
                location="(root)",
                entity_id=None,
                reason=f"YAML parse error: {e}",
            ),
        ]

    if data is None:
        return Config(), []

    if not isinstance(data, dict):
        return Config(), [
            ConfigError(
                location="(root)",
                entity_id=None,
                reason=(
                    f"top-level must be a mapping; got {type(data).__name__}"
                ),
            ),
        ]

    routes_raw = data.get("routes")
    if routes_raw is None:
        return Config(), []

    if not isinstance(routes_raw, list):
        return Config(), [
            ConfigError(
                location="routes",
                entity_id=None,
                reason=(
                    f"'routes' must be a list; got {type(routes_raw).__name__}"
                ),
            ),
        ]

    config = Config()
    errors: list[ConfigError] = []

    for i, entry_raw in enumerate(routes_raw):
        entry_loc = f"routes[{i}]"

        if not isinstance(entry_raw, dict):
            errors.append(
                ConfigError(
                    location=entry_loc,
                    entity_id=None,
                    reason=(
                        "route entry must be a mapping; got "
                        f"{type(entry_raw).__name__}"
                    ),
                ),
            )
            continue

        repeater = entry_raw.get("repeater")
        if not _is_entity_id(repeater):
            errors.append(
                ConfigError(
                    location=f"{entry_loc}.repeater",
                    entity_id=(repeater if isinstance(repeater, str) else None),
                    reason="'repeater' must be an entity ID",
                ),
            )
            continue
        assert isinstance(repeater, str)

        entry_speed, speed_err = parse_route_speed_value(
            entry_raw.get("route_speed"),
            f"{entry_loc}.route_speed",
        )
        if speed_err is not None:
            errors.append(speed_err)

        clients_raw = entry_raw.get("clients")
        if clients_raw is None:
            clients_raw = []
        if not isinstance(clients_raw, list):
            errors.append(
                ConfigError(
                    location=f"{entry_loc}.clients",
                    entity_id=None,
                    reason=(
                        "'clients' must be a list; got "
                        f"{type(clients_raw).__name__}"
                    ),
                ),
            )
            continue

        clients: list[ClientSpec] = []
        for j, raw_client in enumerate(clients_raw):
            client_loc = f"{entry_loc}.clients[{j}]"
            parsed, errs = _parse_client(raw_client, client_loc)
            clients.extend(parsed)
            errors.extend(errs)

        config.routes.append(
            RouteEntry(
                repeater_entity_id=repeater,
                route_speed=entry_speed,
                clients=clients,
            ),
        )

    return config, errors


# -- Entity resolution + speed precedence ------------------------


# Keyed by the enum's string ``.value`` rather than the enum
# instance. PyScript's AST evaluator re-creates RouteSpeed
# enum instances when values cross between AST-evaluated code
# and native-Python (@pyscript_executor) code, which breaks
# enum-identity-based dict lookups. String keys sidestep the
# issue and hash identically regardless of import context.
_SPEED_ORDINAL: dict[str, int] = {
    RouteSpeed.RATE_9600.value: 0,
    RouteSpeed.RATE_40K.value: 1,
    RouteSpeed.RATE_100K.value: 2,
}


def _min_speed(
    speeds: list[RouteSpeed | None],
) -> RouteSpeed | None:
    """Slowest speed across ``speeds``, or None if indeterminate.

    Returns ``None`` if the list is empty OR if any entry is
    ``None``. Auto-resolution requires knowing every hop's
    max rate; a single unknown hop invalidates the result
    because an over-optimistic pick would fail to transmit.
    """
    if not speeds:
        return None
    # Generator expressions are banned under PyScript's AST
    # evaluator; use a list comprehension + any() on the list.
    if any([s is None for s in speeds]):
        return None
    # All non-None by the check above. Help mypy: narrow via
    # explicit cast-by-reconstruction.
    concrete: list[RouteSpeed] = [s for s in speeds if s is not None]
    best = concrete[0]
    for s in concrete[1:]:
        if _SPEED_ORDINAL[s.value] < _SPEED_ORDINAL[best.value]:
            best = s
    return best


def resolve_speed(
    client_speed: RouteSpeed | None,
    entry_speed: RouteSpeed | None,
    default_speed: RouteSpeed | None,
    auto_fallback: RouteSpeed | None,
) -> RouteSpeed | None:
    """Apply the route_speed precedence chain.

    Most-specific wins. If nothing is explicit, fall back to
    the auto-resolved minimum across hops. Returns ``None``
    only if ``auto_fallback`` is also None -- i.e., no explicit
    speed anywhere AND auto resolution was indeterminate.
    """
    for candidate in (client_speed, entry_speed, default_speed):
        if candidate is not None:
            return candidate
    return auto_fallback


def resolve_entities(
    config: Config,
    default_route_speed: RouteSpeed | None,
    entity_to_resolution: dict[str, DeviceResolution],
    controller: DeviceResolution,
) -> tuple[list[ResolvedRoute], list[ConfigError]]:
    """Resolve config entries to concrete ResolvedRoutes.

    Errors surface when:

    - A configured entity_id is not in ``entity_to_resolution``
      (entity doesn't exist, or is not on a zwave_js device).
    - The repeater device is not routing-capable
      (``isRouting=False``), not always-listening
      (``isListening=False``; battery/FLiRS can't repeat), or is
      marked failed by the controller.
    - A client device is marked failed.
    - Auto speed resolution fails because one or more hops
      report an unknown ``maxDataRate``.

    Each error collects as much context as possible (location,
    entity_id, reason) for the notification body. Errors are
    per-entry: one bad client in a RouteEntry does not drop
    sibling clients.
    """
    resolved: list[ResolvedRoute] = []
    errors: list[ConfigError] = []

    controller_speed = speed_from_bps(controller.max_data_rate_bps)

    for i, entry in enumerate(config.routes):
        entry_loc = f"routes[{i}]"

        repeater = entity_to_resolution.get(entry.repeater_entity_id)
        if repeater is None:
            errors.append(
                ConfigError(
                    location=f"{entry_loc}.repeater",
                    entity_id=entry.repeater_entity_id,
                    reason=(
                        "entity not found, is disabled, or is not on a "
                        "Z-Wave device"
                    ),
                ),
            )
            continue

        if repeater.is_long_range:
            errors.append(
                ConfigError(
                    location=f"{entry_loc}.repeater",
                    entity_id=entry.repeater_entity_id,
                    reason=(
                        "Device does not support routing: configured "
                        "to use Z-Wave Long Range (vs Mesh)"
                    ),
                    device_id=repeater.device_id,
                ),
            )
            continue

        if repeater.failed:
            errors.append(
                ConfigError(
                    location=f"{entry_loc}.repeater",
                    entity_id=entry.repeater_entity_id,
                    reason=("device is marked failed by the Z-Wave controller"),
                    device_id=repeater.device_id,
                ),
            )
            continue

        if not repeater.is_routing:
            errors.append(
                ConfigError(
                    location=f"{entry_loc}.repeater",
                    entity_id=entry.repeater_entity_id,
                    reason="device is not routing-capable",
                    device_id=repeater.device_id,
                ),
            )
            continue

        if not repeater.is_listening:
            errors.append(
                ConfigError(
                    location=f"{entry_loc}.repeater",
                    entity_id=entry.repeater_entity_id,
                    reason=(
                        "device is not always-listening (battery/FLiRS) -- "
                        "cannot act as a repeater"
                    ),
                    device_id=repeater.device_id,
                ),
            )
            continue

        repeater_speed = speed_from_bps(repeater.max_data_rate_bps)

        for j, client in enumerate(entry.clients):
            client_loc = f"{entry_loc}.clients[{j}]"
            source = entity_to_resolution.get(client.entity_id)
            if source is None:
                errors.append(
                    ConfigError(
                        location=client_loc,
                        entity_id=client.entity_id,
                        reason=(
                            "entity not found, is disabled, or is not on "
                            "a Z-Wave device"
                        ),
                    ),
                )
                continue
            if source.is_long_range:
                errors.append(
                    ConfigError(
                        location=client_loc,
                        entity_id=client.entity_id,
                        reason=(
                            "Device does not support routing: configured "
                            "to use Z-Wave Long Range (vs Mesh)"
                        ),
                        device_id=source.device_id,
                    ),
                )
                continue
            if source.failed:
                errors.append(
                    ConfigError(
                        location=client_loc,
                        entity_id=client.entity_id,
                        reason=(
                            "device is marked failed by the Z-Wave controller"
                        ),
                        device_id=source.device_id,
                    ),
                )
                continue

            source_speed = speed_from_bps(source.max_data_rate_bps)
            auto_fallback = _min_speed(
                [source_speed, repeater_speed, controller_speed],
            )
            final_speed = resolve_speed(
                client.route_speed,
                entry.route_speed,
                default_route_speed,
                auto_fallback,
            )
            if final_speed is None:
                errors.append(
                    ConfigError(
                        location=client_loc,
                        entity_id=client.entity_id,
                        reason=(
                            "could not resolve route_speed: no explicit "
                            "value given and one or more hops report an "
                            "unknown maxDataRate"
                        ),
                        device_id=source.device_id,
                    ),
                )
                continue

            # True when no explicit speed was specified at any
            # level of the precedence stack. The diff then
            # accepts any node-reported speed as long as the
            # repeaters match.
            is_auto = (
                client.route_speed is None
                and entry.route_speed is None
                and default_route_speed is None
            )

            resolved.append(
                ResolvedRoute(
                    client_entity_id=client.entity_id,
                    client_node_id=source.node_id,
                    repeater_node_ids=[repeater.node_id],
                    repeater_entity_ids=[entry.repeater_entity_id],
                    route_speed=final_speed,
                    speed_is_auto=is_auto,
                ),
            )

    return resolved, errors


# -- Diff + plan -------------------------------------------------


def _route_tuple(
    r: ResolvedRoute,
) -> tuple[list[NodeID], RouteSpeed]:
    """Return the comparable (repeaters, speed) tuple for diff."""
    return (list(r.repeater_node_ids), r.route_speed)


def _routes_equal(
    a: tuple[list[NodeID], RouteSpeed | None] | None,
    b: tuple[list[NodeID], RouteSpeed | None] | None,
) -> bool:
    """Compare two route tuples by value.

    Accepts ``None`` as a speed (used for pending clears,
    whose speed is meaningless). Avoids RouteSpeed enum-
    identity comparison: PyScript's AST evaluator may produce
    RouteSpeed instances that compare unequal by ``==`` across
    the AST / native-Python boundary even when their ``.value``
    strings match. Comparing by ``.value`` + the repeaters
    list is stable.
    """
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    ar, as_ = a
    br, bs = b
    if list(ar) != list(br):
        return False
    if as_ is None and bs is None:
        return True
    if as_ is None or bs is None:
        return False
    return as_.value == bs.value


def _current_matches_desired(
    current: tuple[list[NodeID], RouteSpeed] | None,
    desired: tuple[list[NodeID], RouteSpeed],
    speed_is_auto: bool,
) -> bool:
    """Is the node's current state an acceptable match for desired?

    When ``speed_is_auto`` is False (user specified an explicit
    speed somewhere in the precedence chain) this is strict
    equality -- we want the exact speed the user asked for.

    When ``speed_is_auto`` is True, only the repeater list
    needs to match. The actual ``routeSpeed`` that ends up on
    the node may differ from what we wrote because some
    devices silently negotiate down (e.g. locks settling at
    40k even when 100k was requested, despite
    ``maxDataRate: 100000``). The user didn't demand a
    specific speed, so the negotiated speed is fine --
    otherwise the diff loops in pending forever.
    """
    if current is None:
        return False
    if not speed_is_auto:
        return _routes_equal(current, desired)
    cur_reps, _cur_speed = current
    des_reps, _des_speed = desired
    return list(cur_reps) == list(des_reps)


def _current_route_for_type(
    ni: NodeInfo,
    route_type: RouteType,
) -> tuple[list[NodeID], RouteSpeed] | None:
    """Return the node's current route tuple for ``route_type``."""
    if route_type == RouteType.PRIORITY_APP:
        return ni.application_route
    return ni.priority_suc_return_route


def _action_kind_for_type(route_type: RouteType) -> RouteActionKind:
    """Return the bridge action kind that writes ``route_type``."""
    if route_type == RouteType.PRIORITY_APP:
        return RouteActionKind.SET_APPLICATION_ROUTE
    return RouteActionKind.SET_PRIORITY_SUC_RETURN_ROUTE


def _clear_action_kind_for_type(route_type: RouteType) -> RouteActionKind:
    """Return the bridge action kind that clears ``route_type``."""
    if route_type == RouteType.PRIORITY_APP:
        return RouteActionKind.CLEAR_APPLICATION_ROUTE
    return RouteActionKind.CLEAR_PRIORITY_SUC_RETURN_ROUTES


def type_for_action_kind(kind: RouteActionKind) -> RouteType:
    """Return the route direction a given action operates on.

    Inverse of :func:`_action_kind_for_type` /
    :func:`_clear_action_kind_for_type`: both the set and clear
    kinds for a direction collapse back to the same RouteType.
    Public because the service wrapper needs this to classify
    failed apply results back to a route direction.
    """
    if kind == RouteActionKind.SET_APPLICATION_ROUTE:
        return RouteType.PRIORITY_APP
    if kind == RouteActionKind.CLEAR_APPLICATION_ROUTE:
        return RouteType.PRIORITY_APP
    return RouteType.PRIORITY_SUC


def _find_path(
    paths: list[RouteRequest],
    route_type: RouteType,
) -> RouteRequest | None:
    """Return the path of ``route_type`` from ``paths``, if any."""
    for p in paths:
        if p.type == route_type:
            return p
    return None


def diff_and_plan(
    desired: list[ResolvedRoute],
    nodes: dict[NodeID, NodeInfo],
    pending: dict[NodeID, list[RouteRequest]],
    applied: dict[NodeID, list[RouteRequest]],
    now: datetime,
    pending_timeout: timedelta,
    clear_unmanaged: bool,
) -> ReconcileResult:
    """Diff desired routes against observed ``nodes`` state.

    Precondition: ``desired`` is already fully resolved --
    ``resolve_entities`` returned it with no errors for this set.
    Each ResolvedRoute's ``client_node_id`` must be a key in
    ``nodes``; if not, the node is silently skipped (it vanished
    from the mesh between resolve and diff).

    Each (node, route_type) pair is evaluated independently. A
    node whose outbound route matches but whose return route
    doesn't ends up in both ``new_applied`` (for PRIORITY_APP)
    and ``new_pending`` (for PRIORITY_SUC). ``new_pending``
    represents "what pending state would look like if every
    action succeeds"; the service wrapper filters based on
    actual apply results.

    ``pending_timeout`` doubles as the retry interval: a route
    that's been pending longer than this is re-issued with a
    fresh ``requested_at`` and a bumped ``timeout_count``, and
    the timeout event is appended to ``new_timeouts`` so the
    service wrapper can emit a one-shot notification keyed to
    the attempt that just timed out.
    """
    result = ReconcileResult()
    desired_by_node: dict[NodeID, ResolvedRoute] = {
        r.client_node_id: r for r in desired
    }
    desired_node_ids = set(desired_by_node.keys())

    for node_id, route in desired_by_node.items():
        ni = nodes.get(node_id)
        if ni is None:
            # Node vanished between resolve and diff. Skip --
            # next reconcile will treat it fresh.
            continue

        desired_tuple = _route_tuple(route)
        prior_pending_paths = pending.get(node_id, [])
        prior_applied_paths = applied.get(node_id, [])
        node_pending: list[RouteRequest] = []
        node_applied: list[RouteRequest] = []

        for route_type in MANAGED_ROUTE_TYPES:
            current = _current_route_for_type(ni, route_type)
            matches = _current_matches_desired(
                current,
                desired_tuple,
                route.speed_is_auto,
            )
            prior_pending = _find_path(prior_pending_paths, route_type)
            prior_applied = _find_path(prior_applied_paths, route_type)

            if matches:
                # Applied. Determine timestamps + carry-forward
                # debug fields:
                # - requested_at: from the prior pending entry
                #   (pending -> applied transition) or, failing
                #   that, from the prior applied entry (carry-
                #   forward). ``None`` when we first observed it
                #   already applied.
                # - confirmed_at: preserved from prior applied if
                #   we saw it there before; otherwise ``now``
                #   (first time we noticed this route applied).
                # - timeout_count: carries from the prior entry
                #   so the applied record shows how many retries
                #   it took to land.
                if prior_pending is not None:
                    requested_at: datetime | None = prior_pending.requested_at
                    timeout_count = prior_pending.timeout_count
                elif prior_applied is not None:
                    requested_at = prior_applied.requested_at
                    timeout_count = prior_applied.timeout_count
                else:
                    requested_at = None
                    timeout_count = 0
                confirmed_at = (
                    prior_applied.confirmed_at
                    if prior_applied is not None
                    and prior_applied.confirmed_at is not None
                    else now
                )
                node_applied.append(
                    RouteRequest(
                        type=route_type,
                        repeater_node_ids=list(route.repeater_node_ids),
                        speed=route.route_speed,
                        requested_at=requested_at,
                        confirmed_at=confirmed_at,
                        timeout_count=timeout_count,
                    ),
                )
                continue

            # Not matching. Three cases:
            #  1. prior_pending matches desired and is within
            #     the timeout window -> carry forward unchanged.
            #  2. prior_pending matches desired but the timeout
            #     window has elapsed -> re-issue the command,
            #     bump timeout_count, signal a timeout event.
            #  3. Stale or no prior -> emit a fresh action.
            prior_desired = (
                (prior_pending.repeater_node_ids, prior_pending.speed)
                if prior_pending is not None
                else None
            )
            if prior_pending is not None and _routes_equal(
                prior_desired,
                desired_tuple,
            ):
                timed_out = (
                    prior_pending.requested_at is not None
                    and now - prior_pending.requested_at > pending_timeout
                )
                if not timed_out:
                    # Case 1: still within the window.
                    node_pending.append(prior_pending)
                    continue
                # Case 2: re-issue, bump timeout_count, signal
                # the event so the service wrapper can emit a
                # one-shot notification keyed to the attempt
                # that just timed out.
                new_count = prior_pending.timeout_count + 1
                result.actions.append(
                    RouteAction(
                        kind=_action_kind_for_type(route_type),
                        node_id=node_id,
                        repeaters=list(route.repeater_node_ids),
                        route_speed=route.route_speed,
                        client_entity_id=route.client_entity_id,
                    ),
                )
                node_pending.append(
                    RouteRequest(
                        type=route_type,
                        repeater_node_ids=list(route.repeater_node_ids),
                        speed=route.route_speed,
                        requested_at=now,
                        confirmed_at=None,
                        timeout_count=new_count,
                    ),
                )
                # The notification ID uses the OLD requested_at
                # (the attempt that just timed out) so each
                # retry generates a unique, persistent
                # notification.
                assert prior_pending.requested_at is not None
                result.new_timeouts.append(
                    (
                        node_id,
                        route_type,
                        prior_pending.requested_at,
                        new_count,
                    ),
                )
                continue

            # Case 3: stale or no prior. Emit a fresh action
            # for this route type with timeout_count=0.
            result.actions.append(
                RouteAction(
                    kind=_action_kind_for_type(route_type),
                    node_id=node_id,
                    repeaters=list(route.repeater_node_ids),
                    route_speed=route.route_speed,
                    client_entity_id=route.client_entity_id,
                ),
            )
            node_pending.append(
                RouteRequest(
                    type=route_type,
                    repeater_node_ids=list(route.repeater_node_ids),
                    speed=route.route_speed,
                    requested_at=now,
                    confirmed_at=None,
                ),
            )

        if node_pending:
            result.new_pending[node_id] = node_pending
        if node_applied:
            result.new_applied[node_id] = node_applied

    if clear_unmanaged:
        unmanaged_ids = sorted(set(nodes.keys()) - desired_node_ids)
        for node_id in unmanaged_ids:
            ni = nodes[node_id]
            prior_pending_paths = pending.get(node_id, [])
            node_pending_clears: list[RouteRequest] = []

            for route_type in MANAGED_ROUTE_TYPES:
                current = _current_route_for_type(ni, route_type)
                if current is None:
                    # Already cleared; nothing to do, nothing
                    # to track.
                    continue

                prior_pending = _find_path(
                    prior_pending_paths,
                    route_type,
                )
                # A valid carry-forward is a prior pending
                # *clear* (empty repeaters). A prior pending
                # set means the node was previously managed
                # and now isn't -- we drop the stale set and
                # emit a fresh clear.
                prior_is_clear = (
                    prior_pending is not None
                    and not prior_pending.repeater_node_ids
                )
                if prior_is_clear:
                    assert prior_pending is not None
                    timed_out = (
                        prior_pending.requested_at is not None
                        and now - prior_pending.requested_at > pending_timeout
                    )
                    if not timed_out:
                        # Still within the window; carry
                        # forward, don't re-emit.
                        node_pending_clears.append(prior_pending)
                        continue
                    # Timed out: re-issue + bump count +
                    # signal timeout event, keyed to the
                    # attempt that just timed out.
                    new_count = prior_pending.timeout_count + 1
                    result.actions.append(
                        RouteAction(
                            kind=_clear_action_kind_for_type(route_type),
                            node_id=node_id,
                            repeaters=[],
                            route_speed=None,
                            client_entity_id="",
                        ),
                    )
                    node_pending_clears.append(
                        RouteRequest(
                            type=route_type,
                            repeater_node_ids=[],
                            speed=None,
                            requested_at=now,
                            confirmed_at=None,
                            timeout_count=new_count,
                        ),
                    )
                    assert prior_pending.requested_at is not None
                    result.new_timeouts.append(
                        (
                            node_id,
                            route_type,
                            prior_pending.requested_at,
                            new_count,
                        ),
                    )
                    continue

                # No prior clear-pending (or prior was a
                # stale set). Emit a fresh clear.
                result.actions.append(
                    RouteAction(
                        kind=_clear_action_kind_for_type(route_type),
                        node_id=node_id,
                        repeaters=[],
                        route_speed=None,
                        client_entity_id="",
                    ),
                )
                node_pending_clears.append(
                    RouteRequest(
                        type=route_type,
                        repeater_node_ids=[],
                        speed=None,
                        requested_at=now,
                        confirmed_at=None,
                    ),
                )

            if node_pending_clears:
                result.new_pending[node_id] = node_pending_clears

    return result
