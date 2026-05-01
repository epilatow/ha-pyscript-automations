#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pytest",
#     "pytest-asyncio",
#     "pytest-cov",
#     "ruff",
#     "mypy",
#     "voluptuous",
#     "PyYAML",
# ]
# ///
# This is AI generated code
"""Unit tests for ``zwave_route_manager.handler``'s lifecycle code.

Covers parts that don't require booting Home Assistant:
the ``_on_reload`` / ``_on_entity_remove`` /
``_on_entity_rename`` / ``_on_teardown`` mutator
callbacks fed into the BlueprintHandlerSpec,
``_ensure_timer``'s schedule + re-arm sequencing,
``_async_kick_for_recovery``'s synthetic-manual
``automation.trigger`` shape, and the schema vs blueprint
drift check. The argparse + service layers are exercised
end-to-end on a real HA host.

Uses the same lightweight homeassistant-modules stub the
TEC handler tests installed (so ``handler.py``'s HA-side
imports resolve) plus a small mock ``hass``.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import pytest  # noqa: E402
from _handler_stubs import install_homeassistant_stubs  # noqa: E402
from conftest import (  # noqa: E402
    BlueprintDefaultsRoundTripBase,
    BlueprintSchemaDriftBase,
    CodeQualityBase,
    HandlerArgparseGuardsBase,
)


class _FrozenNow:
    value = datetime(2026, 4, 27, 12, 0, 0)


_stubs = install_homeassistant_stubs(frozen_now=_FrozenNow.value)

# Capture every async_track_time_interval invocation; tests
# inspect.
_ATI_CALLS: list[tuple[timedelta, Callable[..., Any]]] = []
_ATI_CANCEL_CALLS: list[int] = []


def _async_track_time_interval(
    _hass: Any,
    cb: Callable[..., Any],
    interval: timedelta,
) -> Callable[[], None]:
    handle_index = len(_ATI_CALLS)
    _ATI_CALLS.append((interval, cb))

    def _cancel() -> None:
        _ATI_CANCEL_CALLS.append(handle_index)

    return _cancel


_stubs.event.async_track_time_interval = (  # type: ignore[attr-defined]
    _async_track_time_interval
)

from custom_components.blueprint_toolkit.helpers import (  # noqa: E402
    make_config_error_notification,
)
from custom_components.blueprint_toolkit.zwave_route_manager import (  # noqa: E402
    bridge,
    handler,
    logic,
)

# Re-bind so the handler module sees the test module's
# capture lists (see TEC handler test for the same dance).
handler.async_track_time_interval = _async_track_time_interval  # type: ignore[attr-defined]


# --------------------------------------------------------
# Mock hass surface
# --------------------------------------------------------


@dataclass
class _MockServices:
    # ``calls`` records ``(domain, name, data)``; ``kwargs``
    # records the keyword args (``context=``, ``blocking=``)
    # for the matching index.
    calls: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    kwargs: list[dict[str, Any]] = field(default_factory=list)

    async def async_call(
        self,
        domain: str,
        name: str,
        data: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self.calls.append((domain, name, dict(data or {})))
        self.kwargs.append(dict(kwargs))


@dataclass
class _MockRuntimeData:
    handlers: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class _MockEntry:
    runtime_data: _MockRuntimeData = field(default_factory=_MockRuntimeData)


@dataclass
class _MockConfigEntries:
    entries: list[_MockEntry] = field(default_factory=list)

    def async_entries(self, _domain: str) -> list[_MockEntry]:
        return list(self.entries)


@dataclass
class _MockHass:
    services: _MockServices = field(default_factory=_MockServices)
    config_entries: _MockConfigEntries = field(
        default_factory=_MockConfigEntries
    )


def _reset_capture_state() -> None:
    _ATI_CALLS.clear()
    _ATI_CANCEL_CALLS.clear()


def _make_state(
    instance_id: str = "automation.zrm_test",
    *,
    armed_interval_minutes: int = 0,
    cancel_timer: Callable[[], None] | None = None,
) -> handler.ZrmInstanceState:
    return handler.ZrmInstanceState(
        instance_id=instance_id,
        armed_interval_minutes=armed_interval_minutes,
        cancel_timer=cancel_timer,
    )


def _hass_with_instances(
    instances: dict[str, handler.ZrmInstanceState],
) -> _MockHass:
    h = _MockHass()
    entry = _MockEntry()
    entry.runtime_data.handlers["zwave_route_manager"] = {
        "instances": instances,
        "unsubs": [],
    }
    h.config_entries.entries.append(entry)
    return h


# --------------------------------------------------------
# Mutator callbacks
# --------------------------------------------------------


class TestOnReload:
    def test_cancels_pending_timers(self) -> None:
        _reset_capture_state()
        canceled: list[int] = []

        def _cancel() -> None:
            canceled.append(1)

        s1 = _make_state("automation.a", armed_interval_minutes=5)
        s1.cancel_timer = _cancel
        s2 = _make_state("automation.b", armed_interval_minutes=10)
        # No timer on s2; should not raise.
        h = _hass_with_instances({"automation.a": s1, "automation.b": s2})

        handler._on_reload(h)  # type: ignore[arg-type]

        assert canceled == [1]
        assert s1.cancel_timer is None
        assert s1.armed_interval_minutes == 0
        assert s2.cancel_timer is None
        # State entries themselves remain.
        bucket = h.config_entries.entries[0].runtime_data.handlers[
            "zwave_route_manager"
        ]
        assert set(bucket["instances"]) == {"automation.a", "automation.b"}


class TestOnEntityRemove:
    def test_drops_state_and_cancels_timer(self) -> None:
        _reset_capture_state()
        canceled: list[int] = []

        def _cancel() -> None:
            canceled.append(1)

        s = _make_state("automation.a", armed_interval_minutes=5)
        s.cancel_timer = _cancel
        h = _hass_with_instances(
            {"automation.a": s, "automation.b": _make_state("automation.b")}
        )

        handler._on_entity_remove(h, "automation.a")  # type: ignore[arg-type]

        assert canceled == [1]
        bucket = h.config_entries.entries[0].runtime_data.handlers[
            "zwave_route_manager"
        ]
        assert set(bucket["instances"]) == {"automation.b"}

    def test_unknown_id_is_noop(self) -> None:
        _reset_capture_state()
        h = _hass_with_instances({"automation.a": _make_state("automation.a")})
        # Should not raise.
        handler._on_entity_remove(h, "automation.unknown")  # type: ignore[arg-type]


class TestOnEntityRename:
    def test_moves_state_to_new_id(self) -> None:
        _reset_capture_state()
        s = _make_state("automation.old")
        h = _hass_with_instances({"automation.old": s})

        handler._on_entity_rename(h, "automation.old", "automation.new")  # type: ignore[arg-type]

        bucket = h.config_entries.entries[0].runtime_data.handlers[
            "zwave_route_manager"
        ]
        assert "automation.old" not in bucket["instances"]
        assert bucket["instances"]["automation.new"] is s
        assert s.instance_id == "automation.new"

    def test_unknown_old_id_is_noop(self) -> None:
        _reset_capture_state()
        h = _hass_with_instances({"automation.a": _make_state("automation.a")})
        handler._on_entity_rename(h, "automation.unknown", "automation.x")  # type: ignore[arg-type]


class TestOnTeardown:
    def test_cancels_all_and_clears(self) -> None:
        _reset_capture_state()
        canceled: list[int] = []
        s1 = _make_state("automation.a", armed_interval_minutes=5)
        s1.cancel_timer = lambda: canceled.append(1)
        s2 = _make_state("automation.b", armed_interval_minutes=10)
        s2.cancel_timer = lambda: canceled.append(2)
        h = _hass_with_instances({"automation.a": s1, "automation.b": s2})

        handler._on_teardown(h)  # type: ignore[arg-type]

        assert sorted(canceled) == [1, 2]
        bucket = h.config_entries.entries[0].runtime_data.handlers[
            "zwave_route_manager"
        ]
        assert bucket["instances"] == {}


# --------------------------------------------------------
# Periodic timer arming
# --------------------------------------------------------


class TestEnsureTimer:
    def test_first_call_arms(self) -> None:
        _reset_capture_state()
        s = _make_state("automation.a")
        h = _hass_with_instances({"automation.a": s})

        handler._ensure_timer(h, s, 5)  # type: ignore[arg-type]

        assert len(_ATI_CALLS) == 1
        interval, _cb = _ATI_CALLS[0]
        assert interval == timedelta(minutes=5)
        assert s.armed_interval_minutes == 5
        assert s.cancel_timer is not None

    def test_same_interval_does_not_re_arm(self) -> None:
        _reset_capture_state()
        s = _make_state("automation.a")
        h = _hass_with_instances({"automation.a": s})

        handler._ensure_timer(h, s, 5)  # type: ignore[arg-type]
        handler._ensure_timer(h, s, 5)  # type: ignore[arg-type]

        assert len(_ATI_CALLS) == 1

    def test_changed_interval_re_arms(self) -> None:
        _reset_capture_state()
        s = _make_state("automation.a")
        h = _hass_with_instances({"automation.a": s})

        handler._ensure_timer(h, s, 5)  # type: ignore[arg-type]
        handler._ensure_timer(h, s, 10)  # type: ignore[arg-type]

        assert len(_ATI_CALLS) == 2
        assert _ATI_CALLS[1][0] == timedelta(minutes=10)
        # First timer was canceled.
        assert _ATI_CANCEL_CALLS == [0]
        assert s.armed_interval_minutes == 10


# --------------------------------------------------------
# Restart-recovery kick payload
# --------------------------------------------------------


class TestKickForRecovery:
    def test_emits_manual_trigger(self) -> None:
        h = _MockHass()

        import asyncio

        asyncio.run(
            handler._async_kick_for_recovery(h, "automation.zrm")  # type: ignore[arg-type]
        )

        assert len(h.services.calls) == 1
        domain, name, data = h.services.calls[0]
        assert (domain, name) == ("automation", "trigger")
        assert data["entity_id"] == "automation.zrm"
        assert data["skip_condition"] is True
        # Flat top-level variable, NOT under ``trigger.*`` --
        # HA's automation.trigger service strips the
        # ``trigger`` key. See _make_periodic_callback for
        # the full reasoning.
        assert data["variables"] == {"trigger_id": "manual"}
        assert "trigger" not in data["variables"]

    def test_does_not_propagate_caller_context(self) -> None:
        # Regression guard: ``automation.trigger`` must NOT
        # carry a ``context=`` kwarg. If it did, HA's
        # automation runner would inherit the caller's
        # context (the integration setup context) instead
        # of generating a fresh per-run context, which
        # would break logbook attribution of the
        # downstream service calls inside the blueprint's
        # action.
        import asyncio

        h = _MockHass()
        asyncio.run(
            handler._async_kick_for_recovery(h, "automation.zrm")  # type: ignore[arg-type]
        )
        assert len(h.services.kwargs) == 1
        assert "context" not in h.services.kwargs[0]


class TestPeriodicCallback:
    def test_does_not_propagate_caller_context(self) -> None:
        # Same regression guard for the integration-owned
        # periodic timer's ``automation.trigger`` call.
        import asyncio

        s = _make_state("automation.zrm")
        h = _hass_with_instances({"automation.zrm": s})

        cb = handler._make_periodic_callback(h, "automation.zrm")  # type: ignore[arg-type]
        asyncio.run(cb(_FrozenNow.value))

        assert len(h.services.kwargs) == 1
        assert "context" not in h.services.kwargs[0]
        # And ``trigger_id`` must be "periodic" so the
        # service handler can distinguish integration-fired
        # ticks from manual invocations (dev tools, the
        # restart-recovery and reload-recovery kicks). The
        # variable is flat (NOT nested under ``trigger.*``);
        # HA's automation.trigger service strips that key.
        _domain, _name, data = h.services.calls[0]
        assert data["variables"] == {"trigger_id": "periodic"}
        assert "trigger" not in data["variables"]

    def test_no_op_when_instance_state_gone(self) -> None:
        # If the automation has been removed between
        # scheduling and firing, the timer must drop the
        # tick silently rather than fire automation.trigger
        # against an entity HA no longer knows about.
        import asyncio

        h = _hass_with_instances({})
        cb = handler._make_periodic_callback(h, "automation.never_seen")  # type: ignore[arg-type]
        asyncio.run(cb(_FrozenNow.value))
        assert h.services.calls == []

    def test_callback_swallows_automation_trigger_failure(self) -> None:
        """A failing ``automation.trigger`` (e.g. the
        automation entity was deleted between scheduling
        and firing) must not propagate out of the timer
        callback. Defence-in-depth: a single failed tick is
        a self-healing transient -- the next tick fires
        anyway.
        """
        import asyncio

        s = _make_state("automation.zrm")
        h = _hass_with_instances({"automation.zrm": s})

        async def _raise(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("automation gone")

        h.services.async_call = _raise  # type: ignore[assignment]
        cb = handler._make_periodic_callback(h, "automation.zrm")  # type: ignore[arg-type]

        # Should not raise.
        asyncio.run(cb(_FrozenNow.value))


# --------------------------------------------------------
# Notification builders
# --------------------------------------------------------


class TestConfigErrorBullet:
    """``_format_config_error`` renders one ConfigError as one bullet body."""

    def test_no_entity_falls_back_to_location(self) -> None:
        err = logic.ConfigError(
            location="(file)",
            entity_id=None,
            reason="read failed",
        )
        assert handler._format_config_error(err) == "`(file)`: read failed"

    def test_entity_with_device_link(self) -> None:
        err = logic.ConfigError(
            location="routes[0].clients[2]",
            entity_id="binary_sensor.window_front_left",
            device_id="abc123",
            reason=(
                "Device does not support routing: "
                "configured to use Z-Wave Long Range (vs Mesh)"
            ),
        )
        # ``[``/``]`` in the YAML location are md-escaped --
        # they're rendered inside backticks so the escaping is
        # cosmetic-only, but ``_format_config_error`` runs
        # ``md_escape`` on every interpolated string regardless.
        assert handler._format_config_error(err) == (
            "[`binary_sensor.window_front_left`]"
            "(/config/devices/device/abc123) "
            "(`routes\\[0\\].clients\\[2\\]`): "
            "Device does not support routing: "
            "configured to use Z-Wave Long Range (vs Mesh)"
        )

    def test_entity_without_device_id_unlinked(self) -> None:
        # "Entity not found" errors carry an entity_id but no
        # DeviceResolution, so device_id is None. The bullet
        # renders entity_id as plain backticked text -- the
        # entity isn't in HA so a device-page link wouldn't
        # resolve.
        err = logic.ConfigError(
            location="routes[1].clients[0]",
            entity_id="lock.missing",
            reason="entity not found",
        )
        bullet = handler._format_config_error(err)
        expected = (
            "`lock.missing` (`routes\\[1\\].clients\\[0\\]`): entity not found"
        )
        assert bullet == expected
        assert "/config/devices/device/" not in bullet


class TestConfigErrorNotification:
    """End-to-end: ZRM bullets -> ``make_config_error_notification``.

    Exercises the integration of ``_format_config_error`` with the
    shared helpers.make_config_error_notification builder that
    ``emit_config_error`` (and thus ``_emit_config_error`` on the
    handler) ultimately dispatches.
    """

    def _build(
        self,
        errors: list[logic.ConfigError],
        instance_id: str = "automation.zrm_test",
    ) -> Any:
        bullets = [handler._format_config_error(e) for e in errors]
        return make_config_error_notification(
            service=handler._SERVICE,
            instance_id=instance_id,
            errors=bullets,
        )

    def test_inactive_when_no_errors(self) -> None:
        notif = self._build([])
        assert notif.active is False
        assert notif.message == ""

    def test_title_is_bare_category(self) -> None:
        # The dispatcher prepends ``<friendly_name>: `` so
        # the spec carries only the category descriptor.
        notif = self._build(
            [
                logic.ConfigError(
                    location="routes[0]", entity_id=None, reason="x"
                )
            ],
            instance_id="automation.zrm_test",
        )
        assert notif.title == "Config Error"

    def test_multiple_errors_all_listed(self) -> None:
        errors = [
            logic.ConfigError(
                location=f"routes[0].clients[{i}]",
                entity_id=f"binary_sensor.lr{i}",
                device_id=f"dev{i}",
                reason=(
                    "Device does not support routing:"
                    " configured to use Z-Wave Long Range (vs Mesh)"
                ),
            )
            for i in range(3)
        ]
        notif = self._build(errors)
        for i in range(3):
            assert f"binary_sensor.lr{i}" in notif.message
            assert f"/config/devices/device/dev{i}" in notif.message
        assert notif.message.count("- ") == 3


def _make_action(
    *,
    kind: logic.RouteActionKind = logic.RouteActionKind.SET_APPLICATION_ROUTE,
    node_id: int = 42,
    repeaters: list[int] | None = None,
    client_entity_id: str = "lock.x",
) -> logic.RouteAction:
    return logic.RouteAction(
        kind=kind,
        node_id=node_id,
        repeaters=list(repeaters or []),
        route_speed=None,
        client_entity_id=client_entity_id,
    )


def _make_api_result(message: str) -> bridge.ApiResult:
    return bridge.ApiResult(
        success=False,
        message=message,
        api_echo=None,
        result=None,
    )


class TestApiNotification:
    def test_title_plain(self) -> None:
        notif = handler._api_notification(
            "zrm__", "automation.zrm", "connection refused"
        )
        assert notif.title == "API unavailable"

    def test_brackets_escaped(self) -> None:
        notif = handler._api_notification(
            "zrm__", "automation.zrm", "bad response: [foo]"
        )
        assert "bad response: \\[foo\\]" in notif.message
        assert "bad response: [foo]" not in notif.message

    def test_inactive_when_error_empty(self) -> None:
        # Empty error string yields an inactive notification --
        # ``api_unavailable`` notifications are dismissed by
        # passing through an empty error rather than building
        # a separate dismiss spec.
        notif = handler._api_notification("zrm__", "automation.zrm", "")
        assert notif.active is False


class TestApplyNotification:
    def test_title_includes_node_id(self) -> None:
        notif = handler._apply_notification(
            "zrm__",
            "automation.zrm",
            _make_action(node_id=42),
            _make_api_result("timeout"),
        )
        assert notif.title == "Apply failed for node 42"

    def test_server_response_brackets_escaped(self) -> None:
        notif = handler._apply_notification(
            "zrm__",
            "automation.zrm",
            _make_action(),
            _make_api_result("ack [partial]"),
        )
        assert "Server response: ack \\[partial\\]" in notif.message
        assert "Server response: ack [partial]" not in notif.message

    def test_id_keyed_to_node(self) -> None:
        notif = handler._apply_notification(
            "zrm__",
            "automation.zrm",
            _make_action(node_id=17),
            _make_api_result("oops"),
        )
        assert notif.notification_id == "zrm__apply_17"


class TestTimeoutNotification:
    """Per-attempt timeout-id stability + body content."""

    def test_id_per_attempt(self) -> None:
        from datetime import UTC
        from datetime import datetime as _datetime

        old_ts = _datetime(2026, 4, 22, 1, 23, 45, tzinfo=UTC)
        notif = handler._timeout_notification(
            "zwm__",
            "automation.zrm",
            42,
            logic.RouteType.PRIORITY_APP,
            old_ts,
            3,
            24,
        )
        assert "timeout_42_priority_app" in notif.notification_id
        assert "2026-04-22T01_23_45" in notif.notification_id
        # User-facing message names the route type, the retry
        # count, and the way to stop further retries.
        assert "priority_app" in notif.message
        assert "timeout #3" in notif.message
        assert "Remove the device from the YAML config" in notif.message


# --------------------------------------------------------
# Schema vs blueprint drift
# --------------------------------------------------------


class TestBlueprintSchemaDrift(BlueprintSchemaDriftBase):
    """The blueprint's ``data:`` keys must match the schema."""

    handler = handler
    blueprint_filename = "zwave_route_manager.yaml"


class TestBlueprintDefaultsRoundTrip(BlueprintDefaultsRoundTripBase):
    """Blueprint input defaults must satisfy the schema."""

    handler = handler
    blueprint_filename = "zwave_route_manager.yaml"
    template_defaults = {
        "instance_id": "automation.zrm_default_check",
        "trigger_id": "manual",
    }


class TestArgparseGuards(HandlerArgparseGuardsBase):
    """Schema rejection must short-circuit argparse.

    ZRM has no ``notification_service`` field, so the
    unregistered-notify guard test from the base skips.
    """

    handler = handler


class TestCodeQuality(CodeQualityBase):
    ruff_targets = [
        "tests/test_zwave_route_manager_handler.py",
    ]
    mypy_targets: list[str] = []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", *sys.argv[1:]]))
