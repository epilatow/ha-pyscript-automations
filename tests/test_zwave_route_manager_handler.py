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
import types
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import pytest  # noqa: E402
from conftest import CodeQualityBase  # noqa: E402

# --------------------------------------------------------
# homeassistant.* stubs so the handler module imports
# --------------------------------------------------------

_ha = types.ModuleType("homeassistant")
_ha_components = types.ModuleType("homeassistant.components")
_ha_components_automation = types.ModuleType(
    "homeassistant.components.automation",
)
_ha_components_automation.EVENT_AUTOMATION_RELOADED = (  # type: ignore[attr-defined]
    "automation_reloaded"
)
_ha_components_automation.DATA_COMPONENT = (  # type: ignore[attr-defined]
    "automation_data_component"
)
_ha_config_entries = types.ModuleType("homeassistant.config_entries")
_ha_config_entries.ConfigEntry = type(  # type: ignore[attr-defined]
    "ConfigEntry", (), {}
)
_ha_const = types.ModuleType("homeassistant.const")
_ha_const.EVENT_HOMEASSISTANT_STARTED = (  # type: ignore[attr-defined]
    "homeassistant_started"
)


def _noop_decorator(f: Any) -> Any:
    return f


_ha_core = types.ModuleType("homeassistant.core")
_ha_core.callback = _noop_decorator  # type: ignore[attr-defined]
_ha_core.HomeAssistant = type(  # type: ignore[attr-defined]
    "HomeAssistant", (), {}
)
_ha_core.ServiceCall = type("ServiceCall", (), {})  # type: ignore[attr-defined]
_ha_core.Context = type("Context", (), {})  # type: ignore[attr-defined]
_ha_core.Event = type("Event", (), {})  # type: ignore[attr-defined]
_ha_helpers = types.ModuleType("homeassistant.helpers")
_ha_helpers_cv = types.ModuleType(
    "homeassistant.helpers.config_validation",
)
_ha_helpers_cv.entity_id = lambda v: str(v)  # type: ignore[attr-defined]
_ha_helpers_cv.boolean = lambda v: bool(v)  # type: ignore[attr-defined]
_ha_helpers_cv.ensure_list = lambda v: (  # type: ignore[attr-defined]
    list(v) if v else []
)
_ha_helpers_dr = types.ModuleType("homeassistant.helpers.device_registry")
_ha_helpers_dr.async_get = lambda _hass: None  # type: ignore[attr-defined]
_ha_helpers_er = types.ModuleType(
    "homeassistant.helpers.entity_registry",
)
_ha_helpers_er.EVENT_ENTITY_REGISTRY_UPDATED = (  # type: ignore[attr-defined]
    "entity_registry_updated"
)
_ha_helpers_er.async_get = lambda _hass: None  # type: ignore[attr-defined]
_ha_helpers_event = types.ModuleType("homeassistant.helpers.event")
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


_ha_helpers_event.async_track_time_interval = (  # type: ignore[attr-defined]
    _async_track_time_interval
)
# async_call_later isn't imported by the ZRM handler, but
# helpers.py touches it; install a no-op stub.
_ha_helpers_event.async_call_later = (  # type: ignore[attr-defined]
    lambda _h, _d, _c: lambda: None
)
_ha_util = types.ModuleType("homeassistant.util")
_ha_util_dt = types.ModuleType("homeassistant.util.dt")


class _FrozenNow:
    value = datetime(2026, 4, 27, 12, 0, 0)


def _now() -> datetime:
    return _FrozenNow.value


def _utcnow() -> datetime:
    return _FrozenNow.value


_ha_util_dt.now = _now  # type: ignore[attr-defined]
_ha_util_dt.utcnow = _utcnow  # type: ignore[attr-defined]
sys.modules["homeassistant"] = _ha
sys.modules["homeassistant.components"] = _ha_components
sys.modules["homeassistant.components.automation"] = _ha_components_automation
sys.modules["homeassistant.config_entries"] = _ha_config_entries
sys.modules["homeassistant.const"] = _ha_const
sys.modules["homeassistant.core"] = _ha_core
sys.modules["homeassistant.helpers"] = _ha_helpers
sys.modules["homeassistant.helpers.config_validation"] = _ha_helpers_cv
sys.modules["homeassistant.helpers.device_registry"] = _ha_helpers_dr
sys.modules["homeassistant.helpers.entity_registry"] = _ha_helpers_er
sys.modules["homeassistant.helpers.event"] = _ha_helpers_event
sys.modules["homeassistant.util"] = _ha_util
sys.modules["homeassistant.util.dt"] = _ha_util_dt

from custom_components.blueprint_toolkit.zwave_route_manager import (  # noqa: E402
    handler,
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
        assert data["variables"] == {"trigger": {"id": "manual"}}

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
        # And the trigger.id must be "periodic" so the
        # service handler can distinguish integration-fired
        # ticks from manual / ha_start invocations.
        _domain, _name, data = h.services.calls[0]
        assert data["variables"] == {"trigger": {"id": "periodic"}}

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


# --------------------------------------------------------
# Schema vs blueprint drift
# --------------------------------------------------------

import voluptuous as vol  # noqa: E402
import yaml  # noqa: E402


class _PermissiveBlueprintLoader(yaml.SafeLoader):
    """SafeLoader that ignores the ``!input`` tag.

    Blueprint YAML uses ``!input <name>`` to interpolate
    inputs at load time; under PyYAML's SafeLoader that's an
    unrecognised tag and load() raises. The test only cares
    about the keys, so the tag value just becomes a marker.
    """


def _passthrough_tag(_loader: Any, _suffix: str, node: Any) -> Any:
    if isinstance(node, yaml.ScalarNode):
        return f"!input:{node.value}"
    return None


_PermissiveBlueprintLoader.add_multi_constructor("!input", _passthrough_tag)


def _required_keys(schema: vol.Schema) -> set[str]:
    return {
        str(k.schema)
        for k in schema.schema  # type: ignore[attr-defined]
        if isinstance(k, vol.Required)
    }


class TestBlueprintSchemaDrift:
    """The blueprint's ``data:`` keys must match the schema."""

    def _load_blueprint(self) -> dict[str, Any]:
        bp_path = (
            REPO_ROOT
            / "custom_components"
            / "blueprint_toolkit"
            / (
                "bundled/blueprints/automation/blueprint_toolkit/"
                "zwave_route_manager.yaml"
            )
        )
        text = bp_path.read_text()
        loaded = yaml.load(text, Loader=_PermissiveBlueprintLoader)
        assert isinstance(loaded, dict)
        return loaded

    def test_yaml_data_keys_match_schema_required_keys(self) -> None:
        bp = self._load_blueprint()
        actions = bp["actions"]
        assert isinstance(actions, list) and actions
        action = actions[0]
        data_keys = set(action["data"].keys())
        schema_keys = _required_keys(handler._SCHEMA)
        assert data_keys == schema_keys, (
            f"blueprint vs schema mismatch:\n"
            f"  in blueprint, not schema: {sorted(data_keys - schema_keys)}\n"
            f"  in schema, not blueprint: {sorted(schema_keys - data_keys)}"
        )

    def test_blueprint_action_targets_registered_service(self) -> None:
        bp = self._load_blueprint()
        action = bp["actions"][0]
        assert action["action"] == f"blueprint_toolkit.{handler._SERVICE}"


class TestCodeQuality(CodeQualityBase):
    ruff_targets = [
        "tests/test_zwave_route_manager_handler.py",
    ]
    mypy_targets: list[str] = []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", *sys.argv[1:]]))
