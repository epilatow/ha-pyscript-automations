#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest", "pytest-cov", "ruff", "mypy", "pyyaml"]
# ///
# This is AI generated code
"""Tests for the zwave_route_manager logic module."""

import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
_SCRIPT_PATH = REPO_ROOT / "pyscript" / "modules" / "zwave_route_manager.py"

sys.path.insert(0, str(_SCRIPT_PATH.parent))

from conftest import CodeQualityBase  # noqa: E402
from zwave_js_ui_bridge import (  # noqa: E402
    NodeInfo,
    RouteSpeed,
)
from zwave_route_manager import (  # noqa: E402
    ClientSpec,
    Config,
    ConfigError,
    DeviceResolution,
    ResolvedRoute,
    RouteAction,
    RouteActionKind,
    RouteEntry,
    RouteRequest,
    RouteType,
    diff_and_plan,
    parse_config,
    parse_route_speed_value,
    resolve_entities,
    resolve_speed,
    type_for_action_kind,
)

NOW = datetime(2026, 4, 20, 12, 0, 0)
PENDING_TIMEOUT = timedelta(hours=24)


# -- Helpers -----------------------------------------------------


def _dev(
    entity_id: str,
    node_id: int,
    *,
    is_routing: bool = True,
    is_listening: bool = True,
    is_frequent_listening: bool | str = False,
    failed: bool = False,
    is_long_range: bool = False,
    max_data_rate_bps: int = 100000,
) -> DeviceResolution:
    return DeviceResolution(
        entity_id=entity_id,
        device_id=f"dev_{entity_id}",
        node_id=node_id,
        is_routing=is_routing,
        is_listening=is_listening,
        is_frequent_listening=is_frequent_listening,
        failed=failed,
        is_long_range=is_long_range,
        max_data_rate_bps=max_data_rate_bps,
    )


def _controller(max_rate: int = 100000) -> DeviceResolution:
    return _dev(
        "sensor.controller",
        node_id=1,
        max_data_rate_bps=max_rate,
    )


def _node(
    node_id: int,
    *,
    is_routing: bool = True,
    is_listening: bool = True,
    is_frequent_listening: bool | str = False,
    failed: bool = False,
    is_long_range: bool = False,
    max_rate: int = 100000,
    application_route: tuple[list[int], RouteSpeed] | None = None,
    priority_suc_return_route: tuple[list[int], RouteSpeed] | None = None,
) -> NodeInfo:
    return NodeInfo(
        node_id=node_id,
        is_routing=is_routing,
        is_listening=is_listening,
        is_frequent_listening=is_frequent_listening,
        failed=failed,
        is_long_range=is_long_range,
        max_data_rate_bps=max_rate,
        application_route=application_route,
        priority_suc_return_route=priority_suc_return_route,
    )


# -- parse_route_speed_value -------------------------------------


class TestParseRouteSpeedValue:
    def test_none_input_is_not_error(self) -> None:
        speed, err = parse_route_speed_value(None, "route_speed")
        assert speed is None
        assert err is None

    def test_auto_parses_to_none(self) -> None:
        speed, err = parse_route_speed_value("auto", "route_speed")
        assert speed is None
        assert err is None

    def test_9600(self) -> None:
        speed, err = parse_route_speed_value("9600", "route_speed")
        assert speed == RouteSpeed.RATE_9600
        assert err is None

    def test_40k(self) -> None:
        speed, err = parse_route_speed_value("40k", "route_speed")
        assert speed == RouteSpeed.RATE_40K
        assert err is None

    def test_100k(self) -> None:
        speed, err = parse_route_speed_value("100k", "route_speed")
        assert speed == RouteSpeed.RATE_100K
        assert err is None

    def test_unknown_string(self) -> None:
        speed, err = parse_route_speed_value("fast", "x")
        assert speed is None
        assert err is not None
        assert err.location == "x"
        assert "route_speed must be one of" in err.reason

    def test_bps_int_accepted(self) -> None:
        # YAML parses unquoted 9600 as int. Accept that form.
        speed, err = parse_route_speed_value(9600, "x")
        assert speed == RouteSpeed.RATE_9600
        assert err is None
        speed, err = parse_route_speed_value(40000, "x")
        assert speed == RouteSpeed.RATE_40K
        assert err is None
        speed, err = parse_route_speed_value(100000, "x")
        assert speed == RouteSpeed.RATE_100K
        assert err is None

    def test_unknown_int(self) -> None:
        speed, err = parse_route_speed_value(12345, "x")
        assert speed is None
        assert err is not None
        assert "integer must be one of" in err.reason

    def test_bool_rejected(self) -> None:
        # bool is an int subclass; without a special check,
        # True would map to 1 silently. Ensure we reject.
        speed, err = parse_route_speed_value(True, "x")
        assert speed is None
        assert err is not None

    def test_non_string_or_int(self) -> None:
        speed, err = parse_route_speed_value([1, 2], "x")
        assert speed is None
        assert err is not None
        assert "must be a string" in err.reason


# -- parse_config ------------------------------------------------


class TestParseConfig:
    def test_empty_string(self) -> None:
        cfg, errs = parse_config("")
        assert cfg == Config()
        assert errs == []

    def test_routes_key_missing(self) -> None:
        cfg, errs = parse_config("defaults:\n  foo: bar\n")
        assert cfg == Config()
        assert errs == []

    def test_top_level_not_mapping(self) -> None:
        cfg, errs = parse_config("- 1\n- 2\n")
        assert cfg == Config()
        assert len(errs) == 1
        assert errs[0].location == "(root)"
        assert "mapping" in errs[0].reason

    def test_routes_not_list(self) -> None:
        cfg, errs = parse_config("routes: foo\n")
        assert cfg == Config()
        assert len(errs) == 1
        assert errs[0].location == "routes"

    def test_yaml_syntax_error(self) -> None:
        cfg, errs = parse_config("routes:\n  - repeater: [unbalanced\n")
        assert cfg == Config()
        assert len(errs) == 1
        assert "YAML parse error" in errs[0].reason

    def test_single_bare_client(self) -> None:
        cfg, errs = parse_config(
            """
            routes:
              - repeater: sensor.ext_node_status
                clients:
                  - lock.front_door
            """
        )
        assert errs == []
        assert len(cfg.routes) == 1
        e = cfg.routes[0]
        assert e.repeater_entity_id == "sensor.ext_node_status"
        assert e.route_speed is None
        assert e.clients == [
            ClientSpec(entity_id="lock.front_door", route_speed=None),
        ]

    def test_route_speed_entry_level(self) -> None:
        cfg, errs = parse_config(
            """
            routes:
              - repeater: sensor.ext
                route_speed: 40k
                clients:
                  - lock.a
            """
        )
        assert errs == []
        assert cfg.routes[0].route_speed == RouteSpeed.RATE_40K

    def test_singleton_override(self) -> None:
        cfg, errs = parse_config(
            """
            routes:
              - repeater: sensor.ext
                clients:
                  - entity: lock.a
                    route_speed: 9600
            """
        )
        assert errs == []
        assert cfg.routes[0].clients == [
            ClientSpec(
                entity_id="lock.a",
                route_speed=RouteSpeed.RATE_9600,
            ),
        ]

    def test_group_override_expands(self) -> None:
        cfg, errs = parse_config(
            """
            routes:
              - repeater: sensor.ext
                clients:
                  - entities:
                      - lock.a
                      - lock.b
                    route_speed: 40k
            """
        )
        assert errs == []
        assert cfg.routes[0].clients == [
            ClientSpec(entity_id="lock.a", route_speed=RouteSpeed.RATE_40K),
            ClientSpec(entity_id="lock.b", route_speed=RouteSpeed.RATE_40K),
        ]

    def test_mixed_client_shapes(self) -> None:
        cfg, errs = parse_config(
            """
            routes:
              - repeater: sensor.ext
                route_speed: 100k
                clients:
                  - lock.bare
                  - entity: sensor.singleton
                    route_speed: 40k
                  - entities: [sensor.g1, sensor.g2]
                    route_speed: 9600
            """
        )
        assert errs == []
        clients = cfg.routes[0].clients
        assert len(clients) == 4
        assert clients[0] == ClientSpec(
            entity_id="lock.bare",
            route_speed=None,
        )
        assert clients[1] == ClientSpec(
            entity_id="sensor.singleton",
            route_speed=RouteSpeed.RATE_40K,
        )
        assert clients[2].route_speed == RouteSpeed.RATE_9600
        assert clients[3].route_speed == RouteSpeed.RATE_9600

    def test_entity_and_entities_both_set(self) -> None:
        _, errs = parse_config(
            """
            routes:
              - repeater: sensor.ext
                clients:
                  - entity: lock.a
                    entities: [lock.b]
            """
        )
        assert len(errs) == 1
        assert "both 'entity' and 'entities'" in errs[0].reason

    def test_neither_entity_nor_entities(self) -> None:
        _, errs = parse_config(
            """
            routes:
              - repeater: sensor.ext
                clients:
                  - route_speed: 40k
            """
        )
        assert len(errs) == 1
        assert "must have 'entity' or 'entities'" in errs[0].reason

    def test_invalid_repeater(self) -> None:
        _, errs = parse_config(
            """
            routes:
              - repeater: "not an entity id"
                clients: []
            """
        )
        assert any("'repeater' must be an entity ID" in e.reason for e in errs)

    def test_invalid_entity_in_group(self) -> None:
        cfg, errs = parse_config(
            """
            routes:
              - repeater: sensor.ext
                clients:
                  - entities: [lock.good, "bad", sensor.also_good]
                    route_speed: 40k
            """
        )
        # Bad entity flagged; valid ones still emitted.
        assert len(errs) == 1
        assert errs[0].entity_id == "bad"
        client_ids = [c.entity_id for c in cfg.routes[0].clients]
        assert client_ids == ["lock.good", "sensor.also_good"]

    def test_bad_route_speed_continues_parse(self) -> None:
        cfg, errs = parse_config(
            """
            routes:
              - repeater: sensor.ext
                route_speed: fast
                clients:
                  - lock.a
            """
        )
        assert len(errs) == 1
        # Entry still emitted; speed falls back to None.
        assert cfg.routes[0].route_speed is None
        assert cfg.routes[0].clients[0].entity_id == "lock.a"


# -- resolve_speed -----------------------------------------------


class TestResolveSpeed:
    def test_client_wins(self) -> None:
        r = resolve_speed(
            RouteSpeed.RATE_40K,
            RouteSpeed.RATE_100K,
            RouteSpeed.RATE_9600,
            RouteSpeed.RATE_100K,
        )
        assert r == RouteSpeed.RATE_40K

    def test_entry_used_when_client_none(self) -> None:
        r = resolve_speed(
            None,
            RouteSpeed.RATE_100K,
            RouteSpeed.RATE_9600,
            RouteSpeed.RATE_40K,
        )
        assert r == RouteSpeed.RATE_100K

    def test_default_used_when_client_entry_none(self) -> None:
        r = resolve_speed(
            None,
            None,
            RouteSpeed.RATE_40K,
            RouteSpeed.RATE_100K,
        )
        assert r == RouteSpeed.RATE_40K

    def test_auto_fallback_used_when_all_none(self) -> None:
        r = resolve_speed(None, None, None, RouteSpeed.RATE_40K)
        assert r == RouteSpeed.RATE_40K

    def test_all_none_returns_none(self) -> None:
        assert resolve_speed(None, None, None, None) is None


# -- resolve_entities --------------------------------------------


def _single_route_config(
    repeater_id: str = "sensor.ext",
    client_id: str = "lock.a",
    entry_speed: RouteSpeed | None = None,
    client_speed: RouteSpeed | None = None,
) -> Config:
    return Config(
        routes=[
            RouteEntry(
                repeater_entity_id=repeater_id,
                route_speed=entry_speed,
                clients=[
                    ClientSpec(
                        entity_id=client_id,
                        route_speed=client_speed,
                    ),
                ],
            ),
        ],
    )


class TestResolveEntities:
    def test_happy_path(self) -> None:
        cfg = _single_route_config()
        dmap = {
            "sensor.ext": _dev("sensor.ext", 50),
            "lock.a": _dev(
                "lock.a",
                18,
                is_listening=False,
                is_frequent_listening="1000ms",
            ),
        }
        resolved, errs = resolve_entities(
            cfg,
            default_route_speed=None,
            entity_to_resolution=dmap,
            controller=_controller(),
        )
        assert errs == []
        assert resolved == [
            ResolvedRoute(
                client_entity_id="lock.a",
                client_node_id=18,
                repeater_node_ids=[50],
                repeater_entity_ids=["sensor.ext"],
                route_speed=RouteSpeed.RATE_100K,
                speed_is_auto=True,
            ),
        ]

    def test_repeater_not_found(self) -> None:
        cfg = _single_route_config()
        dmap = {"lock.a": _dev("lock.a", 18)}
        _, errs = resolve_entities(
            cfg,
            None,
            dmap,
            _controller(),
        )
        assert len(errs) == 1
        assert errs[0].entity_id == "sensor.ext"
        assert "not found" in errs[0].reason

    def test_client_not_found(self) -> None:
        cfg = _single_route_config()
        dmap = {"sensor.ext": _dev("sensor.ext", 50)}
        _, errs = resolve_entities(
            cfg,
            None,
            dmap,
            _controller(),
        )
        assert len(errs) == 1
        assert errs[0].entity_id == "lock.a"

    def test_repeater_battery_rejected(self) -> None:
        cfg = _single_route_config()
        dmap = {
            "sensor.ext": _dev(
                "sensor.ext",
                50,
                is_listening=False,
                is_frequent_listening="1000ms",
            ),
            "lock.a": _dev("lock.a", 18),
        }
        _, errs = resolve_entities(
            cfg,
            None,
            dmap,
            _controller(),
        )
        assert len(errs) == 1
        assert "always-listening" in errs[0].reason

    def test_repeater_non_routing_rejected(self) -> None:
        cfg = _single_route_config()
        dmap = {
            "sensor.ext": _dev("sensor.ext", 50, is_routing=False),
            "lock.a": _dev("lock.a", 18),
        }
        _, errs = resolve_entities(
            cfg,
            None,
            dmap,
            _controller(),
        )
        assert len(errs) == 1
        assert "not routing-capable" in errs[0].reason

    def test_repeater_failed_rejected(self) -> None:
        cfg = _single_route_config()
        dmap = {
            "sensor.ext": _dev("sensor.ext", 50, failed=True),
            "lock.a": _dev("lock.a", 18),
        }
        _, errs = resolve_entities(
            cfg,
            None,
            dmap,
            _controller(),
        )
        assert len(errs) == 1
        assert "failed" in errs[0].reason

    def test_client_failed_rejected(self) -> None:
        cfg = _single_route_config()
        dmap = {
            "sensor.ext": _dev("sensor.ext", 50),
            "lock.a": _dev("lock.a", 18, failed=True),
        }
        _, errs = resolve_entities(
            cfg,
            None,
            dmap,
            _controller(),
        )
        assert len(errs) == 1
        assert "failed" in errs[0].reason

    def test_client_long_range_rejected(self) -> None:
        # LR nodes can't have priority routes. The error
        # carries device_id so the notification can link to
        # the HA device page.
        cfg = _single_route_config()
        dmap = {
            "sensor.ext": _dev("sensor.ext", 50),
            "lock.a": _dev(
                "lock.a",
                18,
                is_routing=False,
                is_long_range=True,
            ),
        }
        _, errs = resolve_entities(cfg, None, dmap, _controller())
        assert len(errs) == 1
        assert "Long Range" in errs[0].reason
        assert errs[0].entity_id == "lock.a"
        assert errs[0].device_id == "dev_lock.a"

    def test_client_long_range_preferred_over_failed(self) -> None:
        # LR nodes are often reported as failed AND isRouting=False.
        # The LR message is strictly more informative, so it wins.
        cfg = _single_route_config()
        dmap = {
            "sensor.ext": _dev("sensor.ext", 50),
            "lock.a": _dev(
                "lock.a",
                18,
                is_routing=False,
                failed=True,
                is_long_range=True,
            ),
        }
        _, errs = resolve_entities(cfg, None, dmap, _controller())
        assert len(errs) == 1
        assert "Long Range" in errs[0].reason

    def test_repeater_long_range_rejected(self) -> None:
        cfg = _single_route_config()
        dmap = {
            "sensor.ext": _dev(
                "sensor.ext",
                50,
                is_routing=False,
                is_long_range=True,
            ),
            "lock.a": _dev("lock.a", 18),
        }
        _, errs = resolve_entities(cfg, None, dmap, _controller())
        assert len(errs) == 1
        assert "Long Range" in errs[0].reason
        assert errs[0].entity_id == "sensor.ext"
        assert errs[0].device_id == "dev_sensor.ext"

    def test_multiple_long_range_clients_all_reported(self) -> None:
        # Whack-a-mole prevention: a single pass surfaces
        # every non-routable client, so the user can remove
        # them all in one config edit.
        cfg = Config(
            routes=[
                RouteEntry(
                    repeater_entity_id="sensor.ext",
                    clients=[
                        ClientSpec(entity_id="lock.a"),
                        ClientSpec(entity_id="sensor.lr1"),
                        ClientSpec(entity_id="sensor.lr2"),
                        ClientSpec(entity_id="sensor.lr3"),
                    ],
                ),
            ],
        )
        dmap = {
            "sensor.ext": _dev("sensor.ext", 50),
            "lock.a": _dev("lock.a", 18),
            "sensor.lr1": _dev(
                "sensor.lr1",
                256,
                is_routing=False,
                is_long_range=True,
            ),
            "sensor.lr2": _dev(
                "sensor.lr2",
                257,
                is_routing=False,
                is_long_range=True,
            ),
            "sensor.lr3": _dev(
                "sensor.lr3",
                258,
                is_routing=False,
                is_long_range=True,
            ),
        }
        resolved, errs = resolve_entities(cfg, None, dmap, _controller())
        assert len(errs) == 3
        assert {e.entity_id for e in errs} == {
            "sensor.lr1",
            "sensor.lr2",
            "sensor.lr3",
        }
        assert all("Long Range" in e.reason for e in errs)
        # The valid client is still resolved.
        assert len(resolved) == 1
        assert resolved[0].client_entity_id == "lock.a"

    def test_explicit_speed_overrides_auto(self) -> None:
        cfg = _single_route_config(entry_speed=RouteSpeed.RATE_40K)
        dmap = {
            "sensor.ext": _dev("sensor.ext", 50),
            "lock.a": _dev("lock.a", 18),
        }
        resolved, errs = resolve_entities(
            cfg,
            None,
            dmap,
            _controller(),
        )
        assert errs == []
        assert resolved[0].route_speed == RouteSpeed.RATE_40K

    def test_client_speed_wins_over_entry(self) -> None:
        cfg = _single_route_config(
            entry_speed=RouteSpeed.RATE_100K,
            client_speed=RouteSpeed.RATE_9600,
        )
        dmap = {
            "sensor.ext": _dev("sensor.ext", 50),
            "lock.a": _dev("lock.a", 18),
        }
        resolved, _ = resolve_entities(
            cfg,
            None,
            dmap,
            _controller(),
        )
        assert resolved[0].route_speed == RouteSpeed.RATE_9600

    def test_default_used_when_nothing_else(self) -> None:
        cfg = _single_route_config()
        dmap = {
            "sensor.ext": _dev("sensor.ext", 50),
            "lock.a": _dev("lock.a", 18),
        }
        resolved, _ = resolve_entities(
            cfg,
            default_route_speed=RouteSpeed.RATE_40K,
            entity_to_resolution=dmap,
            controller=_controller(),
        )
        assert resolved[0].route_speed == RouteSpeed.RATE_40K

    def test_auto_picks_min_across_hops(self) -> None:
        cfg = _single_route_config()
        dmap = {
            # Extender at 40k (the bottleneck).
            "sensor.ext": _dev(
                "sensor.ext",
                50,
                max_data_rate_bps=40000,
            ),
            # Lock and controller at 100k.
            "lock.a": _dev("lock.a", 18, max_data_rate_bps=100000),
        }
        resolved, _ = resolve_entities(
            cfg,
            None,
            dmap,
            _controller(),
        )
        assert resolved[0].route_speed == RouteSpeed.RATE_40K
        # No explicit speed anywhere -> auto flag set so the
        # diff tolerates any node-reported speed.
        assert resolved[0].speed_is_auto is True

    def test_explicit_speed_sets_not_auto(self) -> None:
        cfg = _single_route_config(client_speed=RouteSpeed.RATE_40K)
        dmap = {
            "sensor.ext": _dev("sensor.ext", 50),
            "lock.a": _dev("lock.a", 18),
        }
        resolved, _ = resolve_entities(
            cfg,
            None,
            dmap,
            _controller(),
        )
        assert resolved[0].speed_is_auto is False

    def test_entry_speed_sets_not_auto(self) -> None:
        cfg = _single_route_config(entry_speed=RouteSpeed.RATE_40K)
        dmap = {
            "sensor.ext": _dev("sensor.ext", 50),
            "lock.a": _dev("lock.a", 18),
        }
        resolved, _ = resolve_entities(
            cfg,
            None,
            dmap,
            _controller(),
        )
        assert resolved[0].speed_is_auto is False

    def test_default_speed_sets_not_auto(self) -> None:
        cfg = _single_route_config()
        dmap = {
            "sensor.ext": _dev("sensor.ext", 50),
            "lock.a": _dev("lock.a", 18),
        }
        resolved, _ = resolve_entities(
            cfg,
            default_route_speed=RouteSpeed.RATE_40K,
            entity_to_resolution=dmap,
            controller=_controller(),
        )
        assert resolved[0].speed_is_auto is False

    def test_auto_indeterminate_with_unknown_rate(self) -> None:
        cfg = _single_route_config()
        dmap = {
            "sensor.ext": _dev(
                "sensor.ext",
                50,
                max_data_rate_bps=12345,
            ),
            "lock.a": _dev("lock.a", 18),
        }
        _, errs = resolve_entities(
            cfg,
            None,
            dmap,
            _controller(),
        )
        assert len(errs) == 1
        assert "unknown maxDataRate" in errs[0].reason

    def test_explicit_speed_ok_even_with_unknown_rate(self) -> None:
        # User took responsibility by specifying the speed.
        cfg = _single_route_config(client_speed=RouteSpeed.RATE_40K)
        dmap = {
            "sensor.ext": _dev(
                "sensor.ext",
                50,
                max_data_rate_bps=12345,
            ),
            "lock.a": _dev("lock.a", 18),
        }
        resolved, errs = resolve_entities(
            cfg,
            None,
            dmap,
            _controller(),
        )
        assert errs == []
        assert resolved[0].route_speed == RouteSpeed.RATE_40K

    def test_group_expansion_preserves_per_client_override(
        self,
    ) -> None:
        cfg = Config(
            routes=[
                RouteEntry(
                    repeater_entity_id="sensor.ext",
                    route_speed=RouteSpeed.RATE_100K,
                    clients=[
                        ClientSpec(
                            entity_id="lock.a",
                            route_speed=RouteSpeed.RATE_40K,
                        ),
                        ClientSpec(
                            entity_id="lock.b",
                            route_speed=RouteSpeed.RATE_40K,
                        ),
                        ClientSpec(
                            entity_id="lock.c",
                            route_speed=None,
                        ),
                    ],
                ),
            ],
        )
        dmap = {
            "sensor.ext": _dev("sensor.ext", 50),
            "lock.a": _dev("lock.a", 18),
            "lock.b": _dev("lock.b", 19),
            "lock.c": _dev("lock.c", 20),
        }
        resolved, _ = resolve_entities(
            cfg,
            None,
            dmap,
            _controller(),
        )
        speeds = [r.route_speed for r in resolved]
        assert speeds == [
            RouteSpeed.RATE_40K,  # per-client override
            RouteSpeed.RATE_40K,  # per-client override
            RouteSpeed.RATE_100K,  # inherits entry speed
        ]


# -- diff_and_plan -----------------------------------------------


def _single_resolved(
    client_node_id: int = 18,
    repeater_ids: list[int] | None = None,
    speed: RouteSpeed = RouteSpeed.RATE_100K,
    entity_id: str = "lock.a",
    speed_is_auto: bool = False,
) -> ResolvedRoute:
    return ResolvedRoute(
        client_entity_id=entity_id,
        client_node_id=client_node_id,
        repeater_node_ids=repeater_ids or [50],
        route_speed=speed,
        speed_is_auto=speed_is_auto,
    )


class TestDiffAndPlan:
    def test_empty_no_actions(self) -> None:
        result = diff_and_plan(
            desired=[],
            nodes={},
            pending={},
            applied={},
            now=NOW,
            pending_timeout=PENDING_TIMEOUT,
            clear_unmanaged=False,
        )
        assert result.actions == []
        assert result.new_pending == {}
        assert result.new_applied == {}

    def test_fresh_apply_emits_both_actions(self) -> None:
        desired = [_single_resolved()]
        nodes = {
            18: _node(18),  # no current routes
            50: _node(50),
        }
        result = diff_and_plan(
            desired=desired,
            nodes=nodes,
            pending={},
            applied={},
            now=NOW,
            pending_timeout=PENDING_TIMEOUT,
            clear_unmanaged=False,
        )
        kinds = [a.kind for a in result.actions]
        assert kinds == [
            RouteActionKind.SET_APPLICATION_ROUTE,
            RouteActionKind.SET_PRIORITY_SUC_RETURN_ROUTE,
        ]
        assert all(a.node_id == 18 for a in result.actions)
        assert all(a.repeaters == [50] for a in result.actions)
        assert result.new_applied == {}
        assert 18 in result.new_pending

    def test_match_is_applied_no_action(self) -> None:
        desired_speed = RouteSpeed.RATE_100K
        desired = [
            _single_resolved(speed=desired_speed),
        ]
        nodes = {
            18: _node(
                18,
                application_route=([50], desired_speed),
                priority_suc_return_route=([50], desired_speed),
            ),
            50: _node(50),
        }
        result = diff_and_plan(
            desired=desired,
            nodes=nodes,
            pending={},
            applied={},
            now=NOW,
            pending_timeout=PENDING_TIMEOUT,
            clear_unmanaged=False,
        )
        assert result.actions == []
        assert {p.type for p in result.new_applied[18]} == {
            RouteType.PRIORITY_APP,
            RouteType.PRIORITY_SUC,
        }
        assert result.new_pending == {}

    def test_auto_accepts_any_speed_when_repeaters_match(
        self,
    ) -> None:
        # Desired was resolved from auto -> 100k, but the node
        # ended up reporting 40k after negotiation. Should be
        # counted as applied, no action, no pending.
        desired = [
            _single_resolved(
                speed=RouteSpeed.RATE_100K,
                speed_is_auto=True,
            ),
        ]
        nodes = {
            18: _node(
                18,
                application_route=([50], RouteSpeed.RATE_40K),
                priority_suc_return_route=(
                    [50],
                    RouteSpeed.RATE_40K,
                ),
            ),
            50: _node(50),
        }
        result = diff_and_plan(
            desired=desired,
            nodes=nodes,
            pending={},
            applied={},
            now=NOW,
            pending_timeout=PENDING_TIMEOUT,
            clear_unmanaged=False,
        )
        assert result.actions == []
        assert {p.type for p in result.new_applied[18]} == {
            RouteType.PRIORITY_APP,
            RouteType.PRIORITY_SUC,
        }
        assert result.new_pending == {}

    def test_auto_still_mismatches_when_repeaters_differ(
        self,
    ) -> None:
        # Auto tolerance doesn't extend to repeaters -- if the
        # node is routing through a different repeater than
        # desired, we still need to correct it.
        desired = [
            _single_resolved(
                repeater_ids=[47],
                speed=RouteSpeed.RATE_100K,
                speed_is_auto=True,
            ),
        ]
        nodes = {
            18: _node(
                18,
                application_route=([50], RouteSpeed.RATE_100K),
                priority_suc_return_route=(
                    [50],
                    RouteSpeed.RATE_100K,
                ),
            ),
            47: _node(47),
            50: _node(50),
        }
        result = diff_and_plan(
            desired=desired,
            nodes=nodes,
            pending={},
            applied={},
            now=NOW,
            pending_timeout=PENDING_TIMEOUT,
            clear_unmanaged=False,
        )
        assert len(result.actions) == 2  # both AR and PSR
        assert result.new_applied == {}
        assert 18 in result.new_pending

    def test_explicit_speed_mismatches_on_speed_only(
        self,
    ) -> None:
        # User specified 100k explicitly; node is at 40k. Keep
        # trying, don't auto-accept the negotiation.
        desired = [
            _single_resolved(
                speed=RouteSpeed.RATE_100K,
                speed_is_auto=False,
            ),
        ]
        nodes = {
            18: _node(
                18,
                application_route=([50], RouteSpeed.RATE_40K),
                priority_suc_return_route=(
                    [50],
                    RouteSpeed.RATE_40K,
                ),
            ),
            50: _node(50),
        }
        result = diff_and_plan(
            desired=desired,
            nodes=nodes,
            pending={},
            applied={},
            now=NOW,
            pending_timeout=PENDING_TIMEOUT,
            clear_unmanaged=False,
        )
        assert len(result.actions) == 2
        assert 18 in result.new_pending

    def test_partial_match_only_missing_emits(self) -> None:
        desired = [_single_resolved(speed=RouteSpeed.RATE_100K)]
        nodes = {
            # Application route already correct, PSR missing.
            18: _node(
                18,
                application_route=([50], RouteSpeed.RATE_100K),
                priority_suc_return_route=None,
            ),
            50: _node(50),
        }
        result = diff_and_plan(
            desired=desired,
            nodes=nodes,
            pending={},
            applied={},
            now=NOW,
            pending_timeout=PENDING_TIMEOUT,
            clear_unmanaged=False,
        )
        kinds = [a.kind for a in result.actions]
        assert kinds == [
            RouteActionKind.SET_PRIORITY_SUC_RETURN_ROUTE,
        ]

    def test_pending_suppresses_reapply(self) -> None:
        desired = [_single_resolved()]
        nodes = {18: _node(18), 50: _node(50)}
        pending = {
            18: [
                RouteRequest(
                    type=t,
                    repeater_node_ids=[50],
                    speed=RouteSpeed.RATE_100K,
                    requested_at=NOW - timedelta(minutes=5),
                )
                for t in (RouteType.PRIORITY_APP, RouteType.PRIORITY_SUC)
            ],
        }
        result = diff_and_plan(
            desired=desired,
            nodes=nodes,
            pending=pending,
            applied={},
            now=NOW,
            pending_timeout=PENDING_TIMEOUT,
            clear_unmanaged=False,
        )
        assert result.actions == []
        assert result.new_timeouts == []
        # Both prior paths carried forward unchanged.
        carried = result.new_pending[18]
        assert len(carried) == 2
        for orig, kept in zip(pending[18], carried, strict=True):
            assert orig is kept

    def test_pending_timeout_reissues_and_records_event(self) -> None:
        desired = [_single_resolved()]
        nodes = {18: _node(18), 50: _node(50)}
        old_requested = NOW - timedelta(hours=25)
        pending = {
            18: [
                RouteRequest(
                    type=RouteType.PRIORITY_APP,
                    repeater_node_ids=[50],
                    speed=RouteSpeed.RATE_100K,
                    requested_at=old_requested,
                    timeout_count=2,
                ),
                RouteRequest(
                    type=RouteType.PRIORITY_SUC,
                    repeater_node_ids=[50],
                    speed=RouteSpeed.RATE_100K,
                    requested_at=old_requested,
                    timeout_count=0,
                ),
            ],
        }
        result = diff_and_plan(
            desired=desired,
            nodes=nodes,
            pending=pending,
            applied={},
            now=NOW,
            pending_timeout=PENDING_TIMEOUT,
            clear_unmanaged=False,
        )
        # Both directions re-issued.
        assert len(result.actions) == 2
        # One timeout event per timed-out direction, each
        # carrying the OLD requested_at and the bumped count.
        events_by_type = {(t, c): r for (_, t, r, c) in result.new_timeouts}
        assert (RouteType.PRIORITY_APP, 3) in events_by_type
        assert (RouteType.PRIORITY_SUC, 1) in events_by_type
        assert events_by_type[(RouteType.PRIORITY_APP, 3)] == old_requested
        # The carried-forward pending entries have fresh
        # requested_at and bumped timeout_count.
        new_paths = {p.type: p for p in result.new_pending[18]}
        assert new_paths[RouteType.PRIORITY_APP].requested_at == NOW
        assert new_paths[RouteType.PRIORITY_APP].timeout_count == 3
        assert new_paths[RouteType.PRIORITY_SUC].requested_at == NOW
        assert new_paths[RouteType.PRIORITY_SUC].timeout_count == 1

    def test_stale_pending_triggers_reapply(self) -> None:
        # User changed config to a different repeater; the
        # pending entry's target no longer matches desired.
        desired = [
            _single_resolved(repeater_ids=[47], speed=RouteSpeed.RATE_40K),
        ]
        nodes = {18: _node(18), 47: _node(47), 50: _node(50)}
        stale_pending = [
            RouteRequest(
                type=t,
                repeater_node_ids=[50],
                speed=RouteSpeed.RATE_100K,
                requested_at=NOW - timedelta(minutes=5),
            )
            for t in (RouteType.PRIORITY_APP, RouteType.PRIORITY_SUC)
        ]
        result = diff_and_plan(
            desired=desired,
            nodes=nodes,
            pending={18: stale_pending},
            applied={},
            now=NOW,
            pending_timeout=PENDING_TIMEOUT,
            clear_unmanaged=False,
        )
        assert len(result.actions) == 2
        assert all(a.repeaters == [47] for a in result.actions)
        new_paths = result.new_pending[18]
        # Fresh paths, not the stale ones.
        assert all(p not in stale_pending for p in new_paths)
        for p in new_paths:
            assert p.repeater_node_ids == [47]
            assert p.speed == RouteSpeed.RATE_40K
            assert p.requested_at == NOW

    def test_clear_unmanaged_off_leaves_other_nodes(self) -> None:
        desired = [_single_resolved(client_node_id=18)]
        nodes = {
            18: _node(18),
            50: _node(50),
            # Node 47 has a route we didn't configure.
            47: _node(
                47,
                application_route=([50], RouteSpeed.RATE_40K),
                priority_suc_return_route=([50], RouteSpeed.RATE_100K),
            ),
        }
        result = diff_and_plan(
            desired=desired,
            nodes=nodes,
            pending={},
            applied={},
            now=NOW,
            pending_timeout=PENDING_TIMEOUT,
            clear_unmanaged=False,
        )
        # Only actions for node 18; node 47 left alone.
        for a in result.actions:
            assert a.node_id == 18
        # No clear-pending entries when clear_unmanaged=False.
        assert 47 not in result.new_pending

    def test_clear_unmanaged_on_emits_remove_delete(self) -> None:
        desired = [_single_resolved(client_node_id=18)]
        nodes = {
            18: _node(
                18,
                application_route=([50], RouteSpeed.RATE_100K),
                priority_suc_return_route=([50], RouteSpeed.RATE_100K),
            ),
            50: _node(50),
            47: _node(
                47,
                application_route=([50], RouteSpeed.RATE_40K),
                priority_suc_return_route=([50], RouteSpeed.RATE_100K),
            ),
        }
        result = diff_and_plan(
            desired=desired,
            nodes=nodes,
            pending={},
            applied={},
            now=NOW,
            pending_timeout=PENDING_TIMEOUT,
            clear_unmanaged=True,
        )
        kinds = [(a.kind, a.node_id) for a in result.actions]
        assert (
            RouteActionKind.CLEAR_APPLICATION_ROUTE,
            47,
        ) in kinds
        assert (
            RouteActionKind.CLEAR_PRIORITY_SUC_RETURN_ROUTES,
            47,
        ) in kinds
        # Pending clears are tracked per direction (empty
        # repeaters marks them as clears).
        pending_47 = result.new_pending[47]
        assert {p.type for p in pending_47} == {
            RouteType.PRIORITY_APP,
            RouteType.PRIORITY_SUC,
        }
        for p in pending_47:
            assert p.repeater_node_ids == []
            assert p.requested_at == NOW
            assert p.timeout_count == 0
            # Clears carry no speed; it is meaningless for
            # "drop the priority route" actions.
            assert p.speed is None
        # Clears do not enter applied.
        assert 47 not in result.new_applied

    def test_clear_unmanaged_skips_empty_nodes(self) -> None:
        desired: list[ResolvedRoute] = []
        nodes = {
            50: _node(50),  # repeater with no routes set
            99: _node(99),  # leaf with no routes set
        }
        result = diff_and_plan(
            desired=desired,
            nodes=nodes,
            pending={},
            applied={},
            now=NOW,
            pending_timeout=PENDING_TIMEOUT,
            clear_unmanaged=True,
        )
        assert result.actions == []
        assert result.new_pending == {}

    def test_clear_unmanaged_partial_only_emits_present_fields(
        self,
    ) -> None:
        # Node has only application route set, not PSR.
        # Only CLEAR_APPLICATION_ROUTE should be emitted,
        # and only PRIORITY_APP enters pending clears.
        nodes = {
            47: _node(
                47,
                application_route=([50], RouteSpeed.RATE_100K),
                priority_suc_return_route=None,
            ),
            50: _node(50),
        }
        result = diff_and_plan(
            desired=[],
            nodes=nodes,
            pending={},
            applied={},
            now=NOW,
            pending_timeout=PENDING_TIMEOUT,
            clear_unmanaged=True,
        )
        kinds = [a.kind for a in result.actions]
        assert kinds == [RouteActionKind.CLEAR_APPLICATION_ROUTE]
        pending_47 = result.new_pending[47]
        assert [p.type for p in pending_47] == [RouteType.PRIORITY_APP]
        assert pending_47[0].repeater_node_ids == []

    def test_clear_unmanaged_suppresses_reissue_within_window(
        self,
    ) -> None:
        # A prior pending-clear (empty repeaters) within the
        # timeout window must not re-emit the DELETE -- that
        # was the original bug where sleepy-node clears
        # stacked up in the SendQueue.
        nodes = {
            47: _node(
                47,
                application_route=None,
                priority_suc_return_route=([50], RouteSpeed.RATE_100K),
            ),
            50: _node(50),
        }
        prior_clear = RouteRequest(
            type=RouteType.PRIORITY_SUC,
            repeater_node_ids=[],
            speed=None,
            requested_at=NOW - timedelta(minutes=5),
        )
        result = diff_and_plan(
            desired=[],
            nodes=nodes,
            pending={47: [prior_clear]},
            applied={},
            now=NOW,
            pending_timeout=PENDING_TIMEOUT,
            clear_unmanaged=True,
        )
        assert result.actions == []
        # Prior clear carried forward unchanged.
        assert result.new_pending[47][0] is prior_clear
        assert result.new_timeouts == []

    def test_clear_unmanaged_reissues_on_timeout(self) -> None:
        nodes = {
            47: _node(
                47,
                application_route=None,
                priority_suc_return_route=([50], RouteSpeed.RATE_100K),
            ),
            50: _node(50),
        }
        old_requested = NOW - timedelta(hours=25)
        prior_clear = RouteRequest(
            type=RouteType.PRIORITY_SUC,
            repeater_node_ids=[],
            speed=None,
            requested_at=old_requested,
            timeout_count=1,
        )
        result = diff_and_plan(
            desired=[],
            nodes=nodes,
            pending={47: [prior_clear]},
            applied={},
            now=NOW,
            pending_timeout=PENDING_TIMEOUT,
            clear_unmanaged=True,
        )
        assert len(result.actions) == 1
        assert (
            result.actions[0].kind
            == RouteActionKind.CLEAR_PRIORITY_SUC_RETURN_ROUTES
        )
        # Timeout event keyed to the old requested_at.
        assert result.new_timeouts == [
            (47, RouteType.PRIORITY_SUC, old_requested, 2),
        ]
        # Fresh pending entry with bumped count.
        fresh = result.new_pending[47][0]
        assert fresh.repeater_node_ids == []
        assert fresh.requested_at == NOW
        assert fresh.timeout_count == 2

    def test_clear_completed_drops_from_pending(self) -> None:
        # Prior clear-pending + current route now None:
        # the clear landed, don't track anything further
        # (and don't move the clear into applied).
        nodes = {
            47: _node(
                47,
                application_route=None,
                priority_suc_return_route=None,
            ),
            50: _node(50),
        }
        prior_clear = RouteRequest(
            type=RouteType.PRIORITY_SUC,
            repeater_node_ids=[],
            speed=None,
            requested_at=NOW - timedelta(minutes=5),
        )
        result = diff_and_plan(
            desired=[],
            nodes=nodes,
            pending={47: [prior_clear]},
            applied={},
            now=NOW,
            pending_timeout=PENDING_TIMEOUT,
            clear_unmanaged=True,
        )
        assert result.actions == []
        assert 47 not in result.new_pending
        assert 47 not in result.new_applied

    def test_node_vanished_between_resolve_and_diff(self) -> None:
        # ResolvedRoute points at a node that isn't in
        # nodes anymore.
        desired = [_single_resolved(client_node_id=99)]
        nodes = {50: _node(50)}
        result = diff_and_plan(
            desired=desired,
            nodes=nodes,
            pending={},
            applied={},
            now=NOW,
            pending_timeout=PENDING_TIMEOUT,
            clear_unmanaged=False,
        )
        assert result.actions == []
        assert result.new_applied == {}
        assert result.new_pending == {}


class TestDiffAndPlanPerType:
    """Per-type route tracking covering half-applied and
    carry-forward behaviour that's hard to express in
    TestDiffAndPlan without being awkward."""

    def test_half_applied_splits_buckets(self) -> None:
        # AR matches but PSR doesn't -- typical sleepy-node case.
        desired = [_single_resolved(speed=RouteSpeed.RATE_100K)]
        nodes = {
            18: _node(
                18,
                application_route=([50], RouteSpeed.RATE_100K),
                priority_suc_return_route=None,
            ),
            50: _node(50),
        }
        result = diff_and_plan(
            desired=desired,
            nodes=nodes,
            pending={},
            applied={},
            now=NOW,
            pending_timeout=PENDING_TIMEOUT,
            clear_unmanaged=False,
        )
        # AR ended up in applied, PSR in pending.
        assert [p.type for p in result.new_applied[18]] == [
            RouteType.PRIORITY_APP,
        ]
        assert [p.type for p in result.new_pending[18]] == [
            RouteType.PRIORITY_SUC,
        ]
        assert [a.kind for a in result.actions] == [
            RouteActionKind.SET_PRIORITY_SUC_RETURN_ROUTE,
        ]

    def test_per_type_staleness_only_re_emits_changed_type(
        self,
    ) -> None:
        # User changed AR-effective config (different speed).
        # PSR prior is still aligned with current desired --
        # keep waiting on it rather than re-sending.
        desired = [_single_resolved(speed=RouteSpeed.RATE_100K)]
        nodes = {18: _node(18), 50: _node(50)}
        prior_pending = [
            RouteRequest(
                type=RouteType.PRIORITY_APP,
                repeater_node_ids=[50],
                speed=RouteSpeed.RATE_40K,  # stale -- different speed
                requested_at=NOW - timedelta(minutes=5),
            ),
            RouteRequest(
                type=RouteType.PRIORITY_SUC,
                repeater_node_ids=[50],
                speed=RouteSpeed.RATE_100K,  # still matches desired
                requested_at=NOW - timedelta(minutes=5),
            ),
        ]
        result = diff_and_plan(
            desired=desired,
            nodes=nodes,
            pending={18: prior_pending},
            applied={},
            now=NOW,
            pending_timeout=PENDING_TIMEOUT,
            clear_unmanaged=False,
        )
        # Only the AR action re-emitted.
        assert [a.kind for a in result.actions] == [
            RouteActionKind.SET_APPLICATION_ROUTE,
        ]
        # Pending now has a fresh AR and the carried-forward PSR.
        by_type = {p.type: p for p in result.new_pending[18]}
        assert by_type[RouteType.PRIORITY_APP].speed == RouteSpeed.RATE_100K
        assert by_type[RouteType.PRIORITY_APP].requested_at == NOW
        assert by_type[RouteType.PRIORITY_SUC] is prior_pending[1]

    def test_both_types_applied_lands_in_applied_only(self) -> None:
        desired = [_single_resolved(speed=RouteSpeed.RATE_100K)]
        nodes = {
            18: _node(
                18,
                application_route=([50], RouteSpeed.RATE_100K),
                priority_suc_return_route=([50], RouteSpeed.RATE_100K),
            ),
            50: _node(50),
        }
        result = diff_and_plan(
            desired=desired,
            nodes=nodes,
            pending={},
            applied={},
            now=NOW,
            pending_timeout=PENDING_TIMEOUT,
            clear_unmanaged=False,
        )
        assert 18 not in result.new_pending
        types = {p.type for p in result.new_applied[18]}
        assert types == {RouteType.PRIORITY_APP, RouteType.PRIORITY_SUC}

    def test_pending_to_applied_carries_requested_at(self) -> None:
        # The half that was pending last run is applied now.
        # confirmed_at should be now; requested_at should be
        # preserved from the pending record, so we can compute
        # how long the route took to land.
        sent_at = NOW - timedelta(hours=2)
        desired = [_single_resolved(speed=RouteSpeed.RATE_100K)]
        nodes = {
            18: _node(
                18,
                application_route=([50], RouteSpeed.RATE_100K),
                priority_suc_return_route=([50], RouteSpeed.RATE_100K),
            ),
            50: _node(50),
        }
        prior_pending = [
            RouteRequest(
                type=RouteType.PRIORITY_APP,
                repeater_node_ids=[50],
                speed=RouteSpeed.RATE_100K,
                requested_at=sent_at,
            ),
        ]
        result = diff_and_plan(
            desired=desired,
            nodes=nodes,
            pending={18: prior_pending},
            applied={},
            now=NOW,
            pending_timeout=PENDING_TIMEOUT,
            clear_unmanaged=False,
        )
        applied_by_type = {p.type: p for p in result.new_applied[18]}
        ar = applied_by_type[RouteType.PRIORITY_APP]
        assert ar.requested_at == sent_at
        assert ar.confirmed_at == NOW
        # The PSR type had no prior pending -- observed applied
        # for the first time, so requested_at is None.
        psr = applied_by_type[RouteType.PRIORITY_SUC]
        assert psr.requested_at is None
        assert psr.confirmed_at == NOW

    def test_applied_carries_forward_confirmed_at(self) -> None:
        # Route was applied last run and is still applied.
        # confirmed_at should not reset to now.
        original_confirmed_at = NOW - timedelta(days=3)
        original_sent_at = NOW - timedelta(days=3, hours=1)
        desired = [_single_resolved(speed=RouteSpeed.RATE_100K)]
        nodes = {
            18: _node(
                18,
                application_route=([50], RouteSpeed.RATE_100K),
                priority_suc_return_route=([50], RouteSpeed.RATE_100K),
            ),
            50: _node(50),
        }
        prior_applied = [
            RouteRequest(
                type=RouteType.PRIORITY_APP,
                repeater_node_ids=[50],
                speed=RouteSpeed.RATE_100K,
                requested_at=original_sent_at,
                confirmed_at=original_confirmed_at,
            ),
        ]
        result = diff_and_plan(
            desired=desired,
            nodes=nodes,
            pending={},
            applied={18: prior_applied},
            now=NOW,
            pending_timeout=PENDING_TIMEOUT,
            clear_unmanaged=False,
        )
        applied_by_type = {p.type: p for p in result.new_applied[18]}
        ar = applied_by_type[RouteType.PRIORITY_APP]
        assert ar.requested_at == original_sent_at
        assert ar.confirmed_at == original_confirmed_at


class TestRouteActionEquality:
    """Lightweight checks on the action kinds used elsewhere."""

    def test_route_action_kinds_unique(self) -> None:
        kinds = [k.value for k in RouteActionKind]
        assert len(kinds) == len(set(kinds))

    def test_route_action_repr_contains_kind(self) -> None:
        a = RouteAction(
            kind=RouteActionKind.SET_APPLICATION_ROUTE,
            node_id=18,
            repeaters=[50],
            route_speed=RouteSpeed.RATE_100K,
            client_entity_id="lock.a",
        )
        assert "SET_APPLICATION_ROUTE" in repr(a.kind)


class TestTypeForActionKind:
    """Every action kind maps back to exactly one RouteType."""

    def test_set_application_route_maps_to_priority_app(self) -> None:
        assert (
            type_for_action_kind(RouteActionKind.SET_APPLICATION_ROUTE)
            == RouteType.PRIORITY_APP
        )

    def test_clear_application_route_maps_to_priority_app(self) -> None:
        # Regression: pre-rename this was REMOVE_APPLICATION_ROUTE,
        # and the inline classifier in the service wrapper mis-
        # mapped it to PRIORITY_SUC, causing failed app-route
        # clears to bleed pending entries across directions.
        assert (
            type_for_action_kind(RouteActionKind.CLEAR_APPLICATION_ROUTE)
            == RouteType.PRIORITY_APP
        )

    def test_set_priority_suc_maps_to_priority_suc(self) -> None:
        assert (
            type_for_action_kind(
                RouteActionKind.SET_PRIORITY_SUC_RETURN_ROUTE,
            )
            == RouteType.PRIORITY_SUC
        )

    def test_clear_priority_suc_return_routes_maps_to_priority_suc(
        self,
    ) -> None:
        assert (
            type_for_action_kind(
                RouteActionKind.CLEAR_PRIORITY_SUC_RETURN_ROUTES
            )
            == RouteType.PRIORITY_SUC
        )


class TestConfigError:
    def test_fields(self) -> None:
        e = ConfigError(
            location="routes[0]",
            entity_id="lock.a",
            reason="not found",
        )
        assert e.location == "routes[0]"
        assert e.entity_id == "lock.a"
        assert e.reason == "not found"


class TestCodeQuality(CodeQualityBase):
    ruff_targets = [
        "pyscript/modules/zwave_route_manager.py",
        "tests/test_zwave_route_manager.py",
    ]
    mypy_targets = [
        "pyscript/modules/zwave_route_manager.py",
    ]


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _SCRIPT_PATH, REPO_ROOT)
