#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest", "pytest-cov", "ruff", "mypy", "python-socketio"]
# ///
# This is AI generated code
"""Tests for the zwave-js-ui bridge module.

Covers the pure parsing helpers and the ``ZwaveJsUiClient``
class. The client tests stub out ``socketio.AsyncClient`` with
``autospec=True`` so no real socket.io traffic is produced.
"""

import asyncio
import sys
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, TypeVar
from unittest.mock import AsyncMock, MagicMock, patch

REPO_ROOT = Path(__file__).parent.parent

T = TypeVar("T")


def _run(coro: Coroutine[Any, Any, T]) -> T:
    """Run a coroutine to completion -- test helper."""
    return asyncio.run(coro)


_SCRIPT_PATH = REPO_ROOT / "pyscript" / "modules" / "zwave_js_ui_bridge.py"

sys.path.insert(0, str(_SCRIPT_PATH.parent))

from conftest import CodeQualityBase  # noqa: E402
from zwave_js_ui_bridge import (  # noqa: E402
    API_ASSIGN_PRIORITY_SUC_RETURN_ROUTE,
    API_DELETE_SUC_RETURN_ROUTES,
    API_GET_NODES,
    API_SET_APPLICATION_ROUTE,
    ApiResult,
    NodeInfo,
    RouteSpeed,
    ZwaveJsUiClient,
    parse_node_info,
    parse_node_route,
    speed_by_value,
    speed_from_bps,
    speed_from_wire,
    speed_to_wire,
)


class TestSpeedConversion:
    def test_to_wire_all_values(self) -> None:
        assert speed_to_wire(RouteSpeed.RATE_9600) == 1
        assert speed_to_wire(RouteSpeed.RATE_40K) == 2
        assert speed_to_wire(RouteSpeed.RATE_100K) == 3

    def test_from_wire_all_values(self) -> None:
        assert speed_from_wire(1) == RouteSpeed.RATE_9600
        assert speed_from_wire(2) == RouteSpeed.RATE_40K
        assert speed_from_wire(3) == RouteSpeed.RATE_100K

    def test_from_wire_unknown(self) -> None:
        assert speed_from_wire(0) is None
        assert speed_from_wire(4) is None
        assert speed_from_wire(-1) is None

    def test_from_bps_all_values(self) -> None:
        assert speed_from_bps(9600) == RouteSpeed.RATE_9600
        assert speed_from_bps(40000) == RouteSpeed.RATE_40K
        assert speed_from_bps(100000) == RouteSpeed.RATE_100K

    def test_from_bps_unknown(self) -> None:
        assert speed_from_bps(0) is None
        assert speed_from_bps(50000) is None
        assert speed_from_bps(200000) is None

    def test_wire_roundtrip(self) -> None:
        for s in list(RouteSpeed):
            assert speed_from_wire(speed_to_wire(s)) == s


class TestSpeedByValue:
    """``speed_by_value`` exists so the wrapper can look up a
    ``RouteSpeed`` by its ``.value`` without iterating
    ``bridge.RouteSpeed`` from wrapper scope. Iteration over an
    imported-module enum fails under pyscript's AST evaluator
    (``TypeError: 'EvalLocalVar' object is not iterable``);
    calling this helper moves the iteration into native-Python
    scope.
    """

    def test_known_values(self) -> None:
        assert speed_by_value("9600") == RouteSpeed.RATE_9600
        assert speed_by_value("40k") == RouteSpeed.RATE_40K
        assert speed_by_value("100k") == RouteSpeed.RATE_100K

    def test_unknown_returns_none(self) -> None:
        assert speed_by_value("") is None
        assert speed_by_value("12345") is None
        assert speed_by_value("fast") is None


class TestParseNodeRoute:
    def test_valid_route(self) -> None:
        result = parse_node_route(
            {"repeaters": [50, 47], "routeSpeed": 3},
        )
        assert result == ([50, 47], RouteSpeed.RATE_100K)

    def test_empty_repeaters_parses_as_none(self) -> None:
        # zwave-js reports a cleared priority route as a dict
        # with empty repeaters; we treat that as "no priority
        # route" (matches controller behavior; a 0-hop
        # priority is indistinguishable from the default).
        assert parse_node_route({"repeaters": [], "routeSpeed": 2}) is None

    def test_none_input(self) -> None:
        assert parse_node_route(None) is None

    def test_not_a_dict(self) -> None:
        assert parse_node_route([1, 2, 3]) is None
        assert parse_node_route("hello") is None
        assert parse_node_route(42) is None

    def test_missing_repeaters(self) -> None:
        assert parse_node_route({"routeSpeed": 3}) is None

    def test_missing_route_speed(self) -> None:
        assert parse_node_route({"repeaters": [50]}) is None

    def test_non_list_repeaters(self) -> None:
        assert parse_node_route({"repeaters": 50, "routeSpeed": 3}) is None

    def test_non_int_repeater(self) -> None:
        assert (
            parse_node_route(
                {"repeaters": [50, "foo"], "routeSpeed": 3},
            )
            is None
        )

    def test_non_int_route_speed(self) -> None:
        assert (
            parse_node_route(
                {"repeaters": [50], "routeSpeed": "100k"},
            )
            is None
        )

    def test_unknown_route_speed(self) -> None:
        assert (
            parse_node_route(
                {"repeaters": [50], "routeSpeed": 99},
            )
            is None
        )


class TestParseNodeInfo:
    def _min_raw(self, **overrides: object) -> dict:
        base = {
            "id": 18,
            "maxDataRate": 100000,
            "isRouting": True,
            "isListening": True,
            "isFrequentListening": False,
            "failed": False,
        }
        base.update(overrides)
        return base

    def test_minimal_valid(self) -> None:
        ni = parse_node_info(self._min_raw())
        assert ni is not None
        assert ni.node_id == 18
        assert ni.max_data_rate_bps == 100000
        assert ni.is_routing is True
        assert ni.is_listening is True
        assert ni.is_frequent_listening is False
        assert ni.failed is False
        assert ni.is_long_range is False
        assert ni.application_route is None
        assert ni.priority_suc_return_route is None

    def test_long_range_node(self) -> None:
        # zwave-js-ui reports protocol=1 for Z-Wave Long Range
        # nodes. LR nodes use star topology and don't support
        # priority routes.
        ni = parse_node_info(self._min_raw(protocol=1))
        assert ni is not None
        assert ni.is_long_range is True

    def test_classic_mesh_node(self) -> None:
        # protocol=0 is Classic mesh. Explicitly verify the
        # non-LR case (the minimal fixture omits protocol).
        ni = parse_node_info(self._min_raw(protocol=0))
        assert ni is not None
        assert ni.is_long_range is False

    def test_missing_protocol_treated_as_classic(self) -> None:
        # Older zwave-js versions may not emit the protocol
        # field. Absence means Classic -- LR was introduced
        # alongside the field.
        raw = self._min_raw()
        assert "protocol" not in raw
        ni = parse_node_info(raw)
        assert ni is not None
        assert ni.is_long_range is False

    def test_flirs_node(self) -> None:
        # FLiRS nodes report isFrequentListening as a string
        # like "1000ms", not bool.
        ni = parse_node_info(
            self._min_raw(
                isListening=False,
                isFrequentListening="1000ms",
            ),
        )
        assert ni is not None
        assert ni.is_listening is False
        assert ni.is_frequent_listening == "1000ms"

    def test_with_application_route(self) -> None:
        ni = parse_node_info(
            self._min_raw(
                applicationRoute={"repeaters": [50], "routeSpeed": 2},
            ),
        )
        assert ni is not None
        assert ni.application_route == ([50], RouteSpeed.RATE_40K)

    def test_with_priority_suc_return_route(self) -> None:
        ni = parse_node_info(
            self._min_raw(
                prioritySUCReturnRoute={
                    "repeaters": [50],
                    "routeSpeed": 3,
                },
            ),
        )
        assert ni is not None
        assert ni.priority_suc_return_route == (
            [50],
            RouteSpeed.RATE_100K,
        )

    def test_missing_id(self) -> None:
        raw = self._min_raw()
        del raw["id"]
        assert parse_node_info(raw) is None

    def test_non_int_id(self) -> None:
        assert parse_node_info(self._min_raw(id="18")) is None

    def test_missing_max_data_rate(self) -> None:
        raw = self._min_raw()
        del raw["maxDataRate"]
        assert parse_node_info(raw) is None

    def test_unparseable_application_route_is_none(self) -> None:
        # Malformed applicationRoute should not fail node
        # parsing; it just surfaces as None so the diff logic
        # treats it as "no route set".
        ni = parse_node_info(
            self._min_raw(applicationRoute="invalid"),
        )
        assert ni is not None
        assert ni.application_route is None


def _make_client_with_mock_sio() -> tuple[ZwaveJsUiClient, MagicMock]:
    """Build a client with its ``_sio`` replaced by a mock."""
    client = ZwaveJsUiClient("core-zwave-js", 8091)
    mock_sio = MagicMock()
    mock_sio.connect = AsyncMock()
    mock_sio.disconnect = AsyncMock()
    mock_sio.call = AsyncMock()
    client._sio = mock_sio  # noqa: SLF001
    return client, mock_sio


class TestZwaveJsUiClientConnect:
    """Verify connect/disconnect and auth wiring."""

    def test_connect_without_token(self) -> None:
        async def _do() -> None:
            client = ZwaveJsUiClient("core-zwave-js", 8091)
            fake_sio = MagicMock()
            fake_sio.connect = AsyncMock()
            # Inject a stub socketio module so the deferred
            # ``import socketio`` inside connect() picks it up.
            mock_module = MagicMock()
            mock_module.AsyncClient = MagicMock(return_value=fake_sio)
            with patch.dict(sys.modules, {"socketio": mock_module}):
                await client.connect()

            assert client._sio is fake_sio  # noqa: SLF001
            fake_sio.connect.assert_awaited_once_with(
                "http://core-zwave-js:8091",
                socketio_path="/socket.io",
            )

        _run(_do())

    def test_connect_with_token(self) -> None:
        async def _do() -> None:
            client = ZwaveJsUiClient(
                "core-zwave-js",
                8091,
                token="abc123",
            )
            fake_sio = MagicMock()
            fake_sio.connect = AsyncMock()
            mock_module = MagicMock()
            mock_module.AsyncClient = MagicMock(return_value=fake_sio)
            with patch.dict(
                sys.modules,
                {"socketio": mock_module},
            ):
                await client.connect()

            fake_sio.connect.assert_awaited_once_with(
                "http://core-zwave-js:8091",
                socketio_path="/socket.io",
                auth={"token": "abc123"},
            )

        _run(_do())

    def test_disconnect(self) -> None:
        async def _do() -> None:
            client, mock_sio = _make_client_with_mock_sio()
            await client.disconnect()
            mock_sio.disconnect.assert_awaited_once()
            assert client._sio is None  # noqa: SLF001

        _run(_do())

    def test_disconnect_when_not_connected(self) -> None:
        async def _do() -> None:
            # Safe no-op.
            client = ZwaveJsUiClient("core-zwave-js", 8091)
            await client.disconnect()  # must not raise
            assert client._sio is None  # noqa: SLF001

        _run(_do())


class TestZwaveJsUiClientCall:
    def test_success_response(self) -> None:
        async def _do() -> None:
            client, mock_sio = _make_client_with_mock_sio()
            mock_sio.call.return_value = {
                "success": True,
                "message": "Success zwave api call",
                "api": "getNodes",
                "result": [{"id": 1}],
            }
            r = await client.call("getNodes", [])
            assert r.success is True
            assert r.message == "Success zwave api call"
            assert r.api_echo == "getNodes"
            assert r.result == [{"id": 1}]
            mock_sio.call.assert_awaited_once_with(
                "ZWAVE_API",
                {"api": "getNodes", "args": []},
                timeout=10.0,
            )

        _run(_do())

    def test_error_response(self) -> None:
        async def _do() -> None:
            client, mock_sio = _make_client_with_mock_sio()
            mock_sio.call.return_value = {
                "success": False,
                "message": "Unknown API",
                "api": "somethingBogus",
                "result": None,
            }
            r = await client.call("somethingBogus", [])
            assert r.success is False
            assert r.message == "Unknown API"
            assert r.api_echo == "somethingBogus"

        _run(_do())

    def test_non_dict_response(self) -> None:
        async def _do() -> None:
            client, mock_sio = _make_client_with_mock_sio()
            mock_sio.call.return_value = "bogus"
            r = await client.call("getNodes", [])
            assert r.success is False
            assert "unexpected response type" in r.message
            assert r.api_echo is None
            assert r.result == "bogus"

        _run(_do())

    def test_missing_api_field(self) -> None:
        async def _do() -> None:
            client, mock_sio = _make_client_with_mock_sio()
            mock_sio.call.return_value = {
                "success": True,
                "message": "",
                "result": None,
            }
            r = await client.call("getNodes", [])
            assert r.api_echo is None

        _run(_do())


class TestZwaveJsUiClientTypedApis:
    def test_get_nodes_parses_result(self) -> None:
        async def _do() -> None:
            client, mock_sio = _make_client_with_mock_sio()
            mock_sio.call.return_value = {
                "success": True,
                "message": "",
                "api": "getNodes",
                "result": [
                    {
                        "id": 1,
                        "maxDataRate": 100000,
                        "isRouting": True,
                        "isListening": True,
                        "isFrequentListening": False,
                        "failed": False,
                        "applicationRoute": None,
                        "prioritySUCReturnRoute": None,
                    },
                    {
                        "id": 18,
                        "maxDataRate": 100000,
                        "isRouting": True,
                        "isListening": False,
                        "isFrequentListening": "1000ms",
                        "failed": False,
                        "applicationRoute": {
                            "repeaters": [50],
                            "routeSpeed": 2,
                        },
                        "prioritySUCReturnRoute": None,
                    },
                ],
            }
            nodes = await client.get_nodes()
            assert len(nodes) == 2
            assert [n.node_id for n in nodes] == [1, 18]
            assert nodes[1].application_route == (
                [50],
                RouteSpeed.RATE_40K,
            )
            assert nodes[1].is_frequent_listening == "1000ms"

        _run(_do())

    def test_get_nodes_skips_unparseable(self) -> None:
        async def _do() -> None:
            client, mock_sio = _make_client_with_mock_sio()
            mock_sio.call.return_value = {
                "success": True,
                "message": "",
                "api": "getNodes",
                "result": [
                    {"id": 1, "maxDataRate": 100000},
                    {"garbage": True},  # missing id
                    "not a dict",  # non-dict entry
                ],
            }
            nodes = await client.get_nodes()
            assert [n.node_id for n in nodes] == [1]

        _run(_do())

    def test_get_nodes_empty_on_failure(self) -> None:
        async def _do() -> None:
            client, mock_sio = _make_client_with_mock_sio()
            mock_sio.call.return_value = {
                "success": False,
                "message": "nope",
                "api": "getNodes",
                "result": None,
            }
            assert await client.get_nodes() == []

        _run(_do())

    def test_get_nodes_with_fresh_routes_returns_tuple(self) -> None:
        # Callers use the ApiResult for api_echo / success
        # checks (same mechanism used for write APIs); verify
        # the tuple shape and that the per-node refresh
        # overwrites the bulk snapshot's route fields.
        async def _do() -> None:
            client, mock_sio = _make_client_with_mock_sio()

            def _respond(_event: str, body: dict, timeout: float) -> dict:
                _ = timeout
                api = body["api"]
                args = body["args"]
                if api == API_GET_NODES:
                    return {
                        "success": True,
                        "message": "",
                        "api": API_GET_NODES,
                        "result": [
                            {
                                "id": 18,
                                "maxDataRate": 100000,
                                "isRouting": True,
                                "isListening": True,
                                "isFrequentListening": False,
                                "failed": False,
                                # bulk snapshot shows no route
                                "applicationRoute": None,
                                "prioritySUCReturnRoute": None,
                            },
                        ],
                    }
                if api == "getPriorityRoute":
                    # per-node authoritative fetch: route IS set
                    return {
                        "success": True,
                        "message": "",
                        "api": "getPriorityRoute",
                        "result": {"repeaters": [50], "routeSpeed": 3},
                    }
                if api == "getPrioritySUCReturnRoute":
                    return {
                        "success": True,
                        "message": "",
                        "api": "getPrioritySUCReturnRoute",
                        "result": None,
                    }
                msg = f"unexpected api: {api} {args}"
                raise AssertionError(msg)

            mock_sio.call.side_effect = _respond
            bulk_r, nodes = await client.get_nodes_with_fresh_routes()
            assert isinstance(bulk_r, ApiResult)
            assert bulk_r.success is True
            assert bulk_r.api_echo == API_GET_NODES
            assert len(nodes) == 1
            # Per-node refresh overwrote the bulk None.
            assert nodes[0].application_route == (
                [50],
                RouteSpeed.RATE_100K,
            )
            assert nodes[0].priority_suc_return_route is None

        _run(_do())

    def test_get_nodes_with_fresh_routes_propagates_api_error(
        self,
    ) -> None:
        # Bulk getNodes failed: return the bad ApiResult +
        # empty nodes list so the caller can raise an
        # "API unavailable" notification on api_echo /
        # success mismatch.
        async def _do() -> None:
            client, mock_sio = _make_client_with_mock_sio()
            mock_sio.call.return_value = {
                "success": False,
                "message": "Unknown API",
                "api": API_GET_NODES,
                "result": None,
            }
            bulk_r, nodes = await client.get_nodes_with_fresh_routes()
            assert bulk_r.success is False
            assert bulk_r.api_echo == API_GET_NODES
            assert bulk_r.message == "Unknown API"
            assert nodes == []

        _run(_do())

    def test_set_application_route_args(self) -> None:
        async def _do() -> None:
            client, mock_sio = _make_client_with_mock_sio()
            mock_sio.call.return_value = {
                "success": True,
                "message": "",
                "api": API_SET_APPLICATION_ROUTE,
                "result": True,
            }
            r = await client.set_application_route(
                18,
                [50, 47],
                RouteSpeed.RATE_100K,
            )
            assert r.success is True
            mock_sio.call.assert_awaited_once_with(
                "ZWAVE_API",
                {
                    "api": API_SET_APPLICATION_ROUTE,
                    "args": [18, [50, 47], 3],
                },
                timeout=10.0,
            )

        _run(_do())

    def test_remove_application_route_args(self) -> None:
        # Clearing dispatches setPriorityRoute with empty
        # repeaters -- the documented ``removePriorityRoute``
        # API returns success without clearing in practice.
        async def _do() -> None:
            client, mock_sio = _make_client_with_mock_sio()
            mock_sio.call.return_value = {
                "success": True,
                "message": "",
                "api": API_SET_APPLICATION_ROUTE,
                "result": True,
            }
            await client.remove_application_route(18)
            mock_sio.call.assert_awaited_once_with(
                "ZWAVE_API",
                {"api": API_SET_APPLICATION_ROUTE, "args": [18, [], 1]},
                timeout=10.0,
            )

        _run(_do())

    def test_assign_priority_suc_return_route_args(
        self,
    ) -> None:
        async def _do() -> None:
            client, mock_sio = _make_client_with_mock_sio()
            mock_sio.call.return_value = {
                "success": True,
                "message": "",
                "api": API_ASSIGN_PRIORITY_SUC_RETURN_ROUTE,
                "result": True,
            }
            await client.assign_priority_suc_return_route(
                18,
                [50],
                RouteSpeed.RATE_40K,
            )
            mock_sio.call.assert_awaited_once_with(
                "ZWAVE_API",
                {
                    "api": API_ASSIGN_PRIORITY_SUC_RETURN_ROUTE,
                    "args": [18, [50], 2],
                },
                timeout=10.0,
            )

        _run(_do())

    def test_delete_suc_return_routes_args(self) -> None:
        async def _do() -> None:
            client, mock_sio = _make_client_with_mock_sio()
            mock_sio.call.return_value = {
                "success": True,
                "message": "",
                "api": API_DELETE_SUC_RETURN_ROUTES,
                "result": True,
            }
            await client.delete_suc_return_routes(18)
            mock_sio.call.assert_awaited_once_with(
                "ZWAVE_API",
                {"api": API_DELETE_SUC_RETURN_ROUTES, "args": [18]},
                timeout=10.0,
            )

        _run(_do())


class TestDataclassRoundtrip:
    def test_api_result_defaults(self) -> None:
        r = ApiResult(
            success=True,
            message="",
            api_echo="getNodes",
            result=None,
        )
        assert r.success is True

    def test_node_info_construction(self) -> None:
        ni = NodeInfo(
            node_id=1,
            is_routing=True,
            is_listening=True,
            is_frequent_listening=False,
            failed=False,
            is_long_range=False,
            max_data_rate_bps=100000,
            application_route=None,
            priority_suc_return_route=None,
        )
        assert ni.node_id == 1


class TestCodeQuality(CodeQualityBase):
    ruff_targets = [
        "pyscript/modules/zwave_js_ui_bridge.py",
        "tests/test_zwave_js_ui_bridge.py",
    ]
    mypy_targets = [
        "pyscript/modules/zwave_js_ui_bridge.py",
    ]


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _SCRIPT_PATH, REPO_ROOT)
