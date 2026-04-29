# This is AI generated code
"""Bridge to zwave-js-ui's ZWAVE_API socket.io event.

Isolated shim around the zwave-js-ui HTTP/socket.io API for
managing Z-Wave priority application routes and priority SUC
return routes. Imported by the ``zwave_route_manager`` handler;
never imported by the logic module, keeping the logic module
free of socket.io and network dependencies.

``socketio`` imports are deferred into function bodies so this
module can be imported in test environments that don't have
``python-socketio`` installed. At runtime inside HA Core's
Python environment, ``socketio`` is preinstalled.

Migration target: when zwave-js-server ships schema 47 (merged
upstream 2026-03-10 but not yet released as of writing) and
``zwave-js-server-python`` gains ``async_set_priority_route`` /
``async_assign_priority_suc_return_route`` wrappers, this file is
the single switch-point -- either swap the implementation to call
the typed client (via ``hass.data``) or delete the file entirely
once HA's integration surfaces these as services.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any

NodeID = int


class RouteSpeed(Enum):
    """Z-Wave route data rate.

    Values are the human-readable strings used in the config
    YAML and the blueprint ``default_route_speed`` input. The
    zwave-js wire protocol encodes these as integers 1/2/3 --
    conversion happens in ``speed_to_wire`` / ``speed_from_wire``
    at the I/O boundary.
    """

    RATE_9600 = "9600"
    RATE_40K = "40k"
    RATE_100K = "100k"


# Wire mapping used by zwave-js core / zwave-js-ui.
# ``routeSpeed`` field values in ZWAVE_API request/response.
_SPEED_TO_WIRE: dict[RouteSpeed, int] = {
    RouteSpeed.RATE_9600: 1,
    RouteSpeed.RATE_40K: 2,
    RouteSpeed.RATE_100K: 3,
}

_WIRE_TO_SPEED: dict[int, RouteSpeed] = {
    1: RouteSpeed.RATE_9600,
    2: RouteSpeed.RATE_40K,
    3: RouteSpeed.RATE_100K,
}

# ``maxDataRate`` on getNodes() output is bits-per-second, not
# the wire enum. These are the only three values zwave-js emits.
_BPS_TO_SPEED: dict[int, RouteSpeed] = {
    9600: RouteSpeed.RATE_9600,
    40000: RouteSpeed.RATE_40K,
    100000: RouteSpeed.RATE_100K,
}


def speed_to_wire(speed: RouteSpeed) -> int:
    """Convert RouteSpeed to the 1/2/3 wire integer."""
    return _SPEED_TO_WIRE[speed]


def speed_from_wire(wire_value: int) -> RouteSpeed | None:
    """Convert a wire integer (1/2/3) to RouteSpeed, or None."""
    return _WIRE_TO_SPEED.get(wire_value)


def speed_from_bps(bps: int) -> RouteSpeed | None:
    """Convert a ``maxDataRate`` bps integer to RouteSpeed.

    Returns ``None`` for any value other than 9600, 40000, or
    100000. Callers should treat ``None`` as "unknown data rate"
    and skip the node.
    """
    return _BPS_TO_SPEED.get(bps)


# ZWAVE_API allow-list values used by this bridge. Mirror the
# entries in zwave-js-ui's ``allowedApis`` (ZwaveClient.ts).
API_GET_NODES = "getNodes"
# zwave-js-ui exposes a ``removePriorityRoute`` API but it
# returns success without actually clearing the route. The
# working path is ``setPriorityRoute`` with an empty repeater
# list, which zwave-js interprets as "no priority route" --
# getPriorityRoute afterward reports empty repeaters +
# routeKind 16 (fallback), matching the default state.
API_SET_APPLICATION_ROUTE = "setPriorityRoute"
API_ASSIGN_PRIORITY_SUC_RETURN_ROUTE = "assignPrioritySUCReturnRoute"
API_DELETE_SUC_RETURN_ROUTES = "deleteSUCReturnRoutes"
# Per-node fresh route lookups. The corresponding fields in
# ``getNodes()``'s bulk snapshot can go stale (we've observed
# them flap to ``null`` while the controller still holds the
# route). Callers that need authoritative current state should
# refresh via these per-node calls and overlay the result.
API_GET_PRIORITY_ROUTE = "getPriorityRoute"
API_GET_PRIORITY_SUC_RETURN_ROUTE = "getPrioritySUCReturnRoute"


@dataclass
class ApiResult:
    """Normalised outcome of one ZWAVE_API call.

    ``api_echo`` is ``None`` if the server response was malformed
    or if the api name was not allow-listed by zwave-js-ui. When
    ``api_echo != api_requested`` the service wrapper should
    surface an "API not available" notification distinct from an
    apply-level failure.
    """

    success: bool
    message: str
    api_echo: str | None
    result: Any


@dataclass
class NodeInfo:
    """Subset of a getNodes() entry used by the automation.

    zwave-js-ui's getNodes() returns ~60 fields per node; the
    automation only needs these.

    ``is_frequent_listening`` is ``bool | str`` because the
    underlying field is a string like ``"1000ms"`` for FLiRS
    nodes and ``False`` for everything else.

    ``is_long_range`` distinguishes Z-Wave Long Range nodes
    (star topology, no mesh repeating, priority routes not
    supported) from Classic mesh nodes. Derived from the
    ``protocol`` field in the getNodes() response
    (``0`` = Classic, ``1`` = Long Range).
    """

    node_id: NodeID
    is_routing: bool
    is_listening: bool
    is_frequent_listening: bool | str
    failed: bool
    is_long_range: bool
    max_data_rate_bps: int
    application_route: tuple[list[NodeID], RouteSpeed] | None
    priority_suc_return_route: tuple[list[NodeID], RouteSpeed] | None


def parse_node_route(
    route_dict: object | None,
) -> tuple[list[NodeID], RouteSpeed] | None:
    """Parse a zwave-js route dict into (repeaters, speed).

    Used for both ``applicationRoute`` and
    ``prioritySUCReturnRoute`` fields in the getNodes() output
    and the per-node ``getPriorityRoute`` /
    ``getPrioritySUCReturnRoute`` responses.

    Returns ``None`` for malformed, missing, or empty input.
    "Empty" explicitly includes a dict with an empty
    ``repeaters`` list: zwave-js reports cleared priority
    routes as ``{"repeaters": [], "routeSpeed": N}`` rather
    than a missing key, and we treat that as "no priority
    route" to match the controller's behavior -- a priority
    route with no repeaters is indistinguishable from the
    default direct-hop behavior.
    """
    if not isinstance(route_dict, dict):
        return None
    repeaters = route_dict.get("repeaters")
    if not isinstance(repeaters, list) or not repeaters:
        return None
    rep_list: list[NodeID] = []
    for r in repeaters:
        if not isinstance(r, int):
            return None
        rep_list.append(r)
    wire_speed = route_dict.get("routeSpeed")
    if not isinstance(wire_speed, int):
        return None
    speed = speed_from_wire(wire_speed)
    if speed is None:
        return None
    return (rep_list, speed)


def parse_node_info(raw_node: dict[str, Any]) -> NodeInfo | None:
    """Parse one getNodes() entry. Returns None if unparseable."""
    node_id = raw_node.get("id")
    if not isinstance(node_id, int):
        return None
    max_rate = raw_node.get("maxDataRate")
    if not isinstance(max_rate, int):
        return None
    return NodeInfo(
        node_id=node_id,
        is_routing=bool(raw_node.get("isRouting", False)),
        is_listening=bool(raw_node.get("isListening", False)),
        is_frequent_listening=raw_node.get(
            "isFrequentListening",
            False,
        ),
        failed=bool(raw_node.get("failed", False)),
        is_long_range=raw_node.get("protocol") == 1,
        max_data_rate_bps=max_rate,
        application_route=parse_node_route(
            raw_node.get("applicationRoute"),
        ),
        priority_suc_return_route=parse_node_route(
            raw_node.get("prioritySUCReturnRoute"),
        ),
    )


class ZwaveJsUiClient:
    """Async socket.io client for zwave-js-ui's ZWAVE_API event.

    Lifecycle:

        client = ZwaveJsUiClient("core-zwave-js", 8091)
        await client.connect()
        try:
            probe = await client.probe()
            if not probe.reachable:
                ...
            nodes = await client.get_nodes()
            ...
        finally:
            await client.disconnect()

    The client holds one socket.io connection for its lifetime.
    Callers should reuse a single client instance for all calls
    in one reconcile pass rather than reconnecting per call.
    """

    def __init__(
        self,
        host: str,
        port: int,
        token: str | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.host = host
        self.port = port
        self.token = token
        self.timeout_seconds = timeout_seconds
        self._sio: Any = None

    async def connect(self) -> None:
        """Open the socket.io connection.

        Deferred import of ``socketio`` lets the module load
        in local test envs that don't have python-socketio
        installed.
        """
        import socketio  # noqa: PLC0415

        self._sio = socketio.AsyncClient()
        url = f"http://{self.host}:{self.port}"
        if self.token:
            await self._sio.connect(
                url,
                socketio_path="/socket.io",
                auth={"token": self.token},
            )
        else:
            await self._sio.connect(
                url,
                socketio_path="/socket.io",
            )

    async def disconnect(self) -> None:
        """Close the connection. Safe to call when not connected."""
        if self._sio is not None:
            await self._sio.disconnect()
            self._sio = None

    async def call(
        self,
        api: str,
        args: list[Any],
    ) -> ApiResult:
        """Send one ZWAVE_API request and return a normalised ApiResult.

        Callers should use the typed methods below where
        available; this method is the escape hatch for the probe
        and for future apis not yet wrapped.
        """
        assert self._sio is not None, "connect() before call()"
        resp = await self._sio.call(
            "ZWAVE_API",
            {"api": api, "args": args},
            timeout=self.timeout_seconds,
        )
        if not isinstance(resp, dict):
            return ApiResult(
                success=False,
                message=f"unexpected response type: {type(resp).__name__}",
                api_echo=None,
                result=resp,
            )
        api_echo_raw = resp.get("api")
        api_echo = api_echo_raw if isinstance(api_echo_raw, str) else None
        return ApiResult(
            success=bool(resp.get("success", False)),
            message=str(resp.get("message", "")),
            api_echo=api_echo,
            result=resp.get("result"),
        )

    async def get_nodes(self) -> list[NodeInfo]:
        """Fetch and parse all nodes.

        Returns an empty list if the call fails or returns a
        non-list result. Malformed individual node entries are
        skipped silently -- caller can detect coverage gap via
        expected-vs-actual node count.

        Note: the ``application_route`` and
        ``priority_suc_return_route`` fields come from
        ``getNodes()``'s bulk snapshot, which has been observed
        to flap to ``None`` while the controller still holds
        the route. Use :meth:`get_nodes_with_fresh_routes` when
        authoritative current state matters (e.g. the route
        manager's diff).
        """
        r = await self.call(API_GET_NODES, [])
        if not r.success or not isinstance(r.result, list):
            return []
        nodes: list[NodeInfo] = []
        for raw in r.result:
            if not isinstance(raw, dict):
                continue
            ni = parse_node_info(raw)
            if ni is not None:
                nodes.append(ni)
        return nodes

    async def get_priority_route(
        self,
        node_id: NodeID,
    ) -> tuple[list[NodeID], RouteSpeed] | None:
        """Authoritative fetch of the controller->node priority route."""
        r = await self.call(API_GET_PRIORITY_ROUTE, [node_id])
        if not r.success:
            return None
        return parse_node_route(r.result)

    async def get_priority_suc_return_route(
        self,
        node_id: NodeID,
    ) -> tuple[list[NodeID], RouteSpeed] | None:
        """Authoritative fetch of the node->controller priority SUC
        return route.
        """
        r = await self.call(API_GET_PRIORITY_SUC_RETURN_ROUTE, [node_id])
        if not r.success:
            return None
        return parse_node_route(r.result)

    async def get_nodes_with_fresh_routes(
        self,
    ) -> tuple[ApiResult, list[NodeInfo]]:
        """Fetch all nodes with route fields refreshed per-node.

        Returns ``(bulk_api_result, nodes)``. The caller uses
        the ApiResult for api_echo / success checks (same
        mechanism the write-path uses); ``nodes`` is the parsed
        list from ``getNodes`` with each entry's
        ``application_route`` and ``priority_suc_return_route``
        overwritten by a per-node fresh fetch.

        The bulk ``getNodes()`` snapshot maintains its own
        cache of the priority routes that has, in observed
        practice, gone stale (e.g. reported ``None`` while the
        controller still held the route). Per-node
        ``getPriorityRoute`` and ``getPrioritySUCReturnRoute``
        hit the controller fresh, so we use them to overwrite
        the bulk snapshot's route fields.

        Z-Wave Long Range (LR) nodes are skipped -- LR is a
        direct-star topology with no mesh, so priority routes
        and priority SUC return routes do not apply. Asking
        for them is meaningless, and on some controller
        firmware versions ``getPriorityRoute`` for an LR node
        wedges the controller's serial interface.

        Per-node refreshes are bounded by a semaphore (``2``
        concurrent nodes) to avoid flooding the controller's
        command queue. The controller serializes these calls
        internally; a thundering herd has been observed to
        stall the serial interface on large meshes.
        """
        import asyncio  # noqa: PLC0415 - keep async imports local

        bulk_r = await self.call(API_GET_NODES, [])
        nodes: list[NodeInfo] = []
        if bulk_r.success and isinstance(bulk_r.result, list):
            for raw in bulk_r.result:
                if not isinstance(raw, dict):
                    continue
                ni = parse_node_info(raw)
                if ni is not None:
                    nodes.append(ni)
        if not nodes:
            return bulk_r, nodes

        sem = asyncio.Semaphore(2)

        async def _refresh(ni: NodeInfo) -> None:
            if ni.is_long_range:
                return
            async with sem:
                ar, psr = await asyncio.gather(
                    self.get_priority_route(ni.node_id),
                    self.get_priority_suc_return_route(ni.node_id),
                )
            ni.application_route = ar
            ni.priority_suc_return_route = psr

        await asyncio.gather(*[_refresh(n) for n in nodes])
        return bulk_r, nodes

    async def set_application_route(
        self,
        node_id: NodeID,
        repeaters: list[NodeID],
        speed: RouteSpeed,
    ) -> ApiResult:
        """Set the controller->node application (priority) route."""
        return await self.call(
            API_SET_APPLICATION_ROUTE,
            [node_id, list(repeaters), speed_to_wire(speed)],
        )

    async def remove_application_route(
        self,
        node_id: NodeID,
    ) -> ApiResult:
        """Clear the controller->node application route.

        Calls ``setPriorityRoute`` with an empty repeater list
        rather than ``removePriorityRoute`` -- the latter
        exists in zwave-js-ui's API surface but returns
        success without clearing, in observed practice.
        """
        return await self.call(
            API_SET_APPLICATION_ROUTE,
            [node_id, [], speed_to_wire(RouteSpeed.RATE_9600)],
        )

    async def assign_priority_suc_return_route(
        self,
        node_id: NodeID,
        repeaters: list[NodeID],
        speed: RouteSpeed,
    ) -> ApiResult:
        """Set the node->controller priority SUC return route."""
        return await self.call(
            API_ASSIGN_PRIORITY_SUC_RETURN_ROUTE,
            [node_id, list(repeaters), speed_to_wire(speed)],
        )

    async def delete_suc_return_routes(
        self,
        node_id: NodeID,
    ) -> ApiResult:
        """Delete ALL SUC return routes on a node.

        Blunt: deletes priority and custom SUC return routes
        together. zwave-js-ui has no surgical "delete priority
        SUC return route only" api. Callers relying on
        ``clear_unmanaged=true`` semantics must accept that
        user-set custom SUC return routes (UI "Return routes
        -> ADD" button) on managed nodes get cleared as
        collateral. Documented in
        ``docs/zwave_route_manager.md``.
        """
        return await self.call(
            API_DELETE_SUC_RETURN_ROUTES,
            [node_id],
        )
