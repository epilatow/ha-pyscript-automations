#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pytest",
#     "pytest-asyncio",
#     "pytest-cov",
#     "ruff",
#     "mypy",
# ]
# ///
# This is AI generated code
"""Unit tests for the shared blueprint_toolkit helpers.

Covers the pure-Python pieces of
``custom_components/blueprint_toolkit/helpers.py``
(``parse_entity_registry_update``,
``make_config_error_notification``,
``PersistentNotification``) and the lifecycle
dispatcher (``register_blueprint_handler`` /
``unregister_blueprint_handler``) using a lightweight
mock ``hass`` -- enough surface to exercise:

- service-name registration + idempotent re-register
- conditional listener wiring based on which spec
  hooks are populated
- restart-recovery scheduling (immediate vs
  ``EVENT_HOMEASSISTANT_STARTED`` deferral)
- unregister teardown (service removal, listener
  unsubscribe, on_teardown invocation)

Real HA instance tests live separately; this file
catches regressions in the pure logic + the dispatch
shape without booting Home Assistant.
"""

from __future__ import annotations

import asyncio
import sys
import types
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import pytest  # noqa: E402
from conftest import CodeQualityBase  # noqa: E402

# Stub the ``homeassistant`` modules the lifecycle
# helpers late-import. Only constants + a noop
# ``callback`` decorator are needed; the strings have
# to match what our ``_MockBus`` keys on. Done at module
# load (before helpers' first call) via sys.modules so
# monkeypatch fixtures aren't needed per-test.
_ha = types.ModuleType("homeassistant")
_ha_components = types.ModuleType("homeassistant.components")
_ha_components_automation = types.ModuleType(
    "homeassistant.components.automation",
)
_ha_components_automation.EVENT_AUTOMATION_RELOADED = "automation_reloaded"  # type: ignore[attr-defined]
_ha_components_automation.DATA_COMPONENT = "automation_data_component"  # type: ignore[attr-defined]
_ha_const = types.ModuleType("homeassistant.const")
_ha_const.EVENT_HOMEASSISTANT_STARTED = "homeassistant_started"  # type: ignore[attr-defined]
_ha_core = types.ModuleType("homeassistant.core")
_ha_core.callback = lambda f: f  # type: ignore[attr-defined]
_ha_helpers = types.ModuleType("homeassistant.helpers")
_ha_helpers_er = types.ModuleType(
    "homeassistant.helpers.entity_registry",
)
_ha_helpers_er.EVENT_ENTITY_REGISTRY_UPDATED = "entity_registry_updated"  # type: ignore[attr-defined]
sys.modules["homeassistant"] = _ha
sys.modules["homeassistant.components"] = _ha_components
sys.modules["homeassistant.components.automation"] = _ha_components_automation
sys.modules["homeassistant.const"] = _ha_const
sys.modules["homeassistant.core"] = _ha_core
sys.modules["homeassistant.helpers"] = _ha_helpers
sys.modules["homeassistant.helpers.entity_registry"] = _ha_helpers_er

from custom_components.blueprint_toolkit import helpers  # noqa: E402
from custom_components.blueprint_toolkit.const import DOMAIN  # noqa: E402

# --------------------------------------------------------
# Mock hass surface
# --------------------------------------------------------


@dataclass
class _MockServices:
    registered: dict[tuple[str, str], Callable[..., Any]] = field(
        default_factory=dict,
    )
    calls: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)

    def has_service(self, domain: str, name: str) -> bool:
        return (domain, name) in self.registered

    def async_register(
        self,
        domain: str,
        name: str,
        handler: Callable[..., Any],
    ) -> None:
        self.registered[(domain, name)] = handler

    def async_remove(self, domain: str, name: str) -> None:
        self.registered.pop((domain, name), None)

    async def async_call(
        self,
        domain: str,
        name: str,
        data: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> None:
        self.calls.append((domain, name, dict(data or {})))


@dataclass
class _MockBus:
    listeners: dict[str, list[Callable[..., Any]]] = field(
        default_factory=dict,
    )
    once_listeners: dict[str, list[Callable[..., Any]]] = field(
        default_factory=dict,
    )

    def async_listen(
        self,
        event_type: str,
        handler: Callable[..., Any],
    ) -> Callable[[], None]:
        self.listeners.setdefault(event_type, []).append(handler)

        def _unsub() -> None:
            if handler in self.listeners.get(event_type, []):
                self.listeners[event_type].remove(handler)

        return _unsub

    def async_listen_once(
        self,
        event_type: str,
        handler: Callable[..., Any],
    ) -> Callable[[], None]:
        self.once_listeners.setdefault(event_type, []).append(handler)
        return lambda: None


@dataclass
class _MockStates:
    """Captures ``hass.states.async_set`` calls for inspection."""

    set_calls: list[tuple[str, str, dict[str, Any]]] = field(
        default_factory=list,
    )

    def async_set(
        self,
        entity_id: str,
        state: str,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        self.set_calls.append((entity_id, state, dict(attributes or {})))


@dataclass
class _MockHass:
    services: _MockServices = field(default_factory=_MockServices)
    bus: _MockBus = field(default_factory=_MockBus)
    states: _MockStates = field(default_factory=_MockStates)
    data: dict[str, Any] = field(default_factory=dict)
    is_running: bool = True
    tasks: list[Awaitable[Any]] = field(default_factory=list)

    def async_create_task(self, coro: Awaitable[Any]) -> None:
        # Track for inspection, then close so we don't
        # leak a coroutine that was never awaited
        # (RuntimeWarning under pytest). Tests only need
        # to know the task was scheduled, not its result.
        self.tasks.append(coro)
        if hasattr(coro, "close"):
            coro.close()


@dataclass
class _MockRuntimeData:
    """Stand-in for the IntegrationData on entry.runtime_data."""

    handlers: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class _MockEntry:
    """Stand-in for HA's ConfigEntry. Carries runtime_data."""

    entry_id: str = "mock_entry"
    runtime_data: _MockRuntimeData = field(default_factory=_MockRuntimeData)


def _service_handler_stub() -> Callable[[Any, Any], Awaitable[None]]:
    async def _h(_hass: Any, _call: Any) -> None:
        return

    return _h


async def _kick_stub(_hass: Any, _entity_id: str) -> None:
    return


def _make_spec(**overrides: Any) -> helpers.BlueprintHandlerSpec:
    defaults: dict[str, Any] = {
        "service": "trigger_entity_controller",
        "service_tag": "TEC",
        "service_name": "Trigger Entity Controller",
        "blueprint_path": "blueprint_toolkit/foo.yaml",
        "service_handler": _service_handler_stub(),
    }
    defaults.update(overrides)
    return helpers.BlueprintHandlerSpec(**defaults)


# --------------------------------------------------------
# Pure helpers (no hass needed)
# --------------------------------------------------------


class TestParseEntityRegistryUpdate:
    def test_remove_action_returns_tuple(self) -> None:
        result = helpers.parse_entity_registry_update(
            {
                "action": "remove",
                "entity_id": "automation.foo",
                "old_entity_id": "automation.foo",
            },
        )
        assert result == ("remove", "automation.foo", "automation.foo")

    def test_update_with_rename_returns_distinct_ids(self) -> None:
        result = helpers.parse_entity_registry_update(
            {
                "action": "update",
                "entity_id": "automation.bar",
                "old_entity_id": "automation.foo",
            },
        )
        assert result == ("update", "automation.foo", "automation.bar")

    def test_non_automation_returns_none(self) -> None:
        assert (
            helpers.parse_entity_registry_update(
                {
                    "action": "update",
                    "entity_id": "light.kitchen",
                    "old_entity_id": "light.kitchen",
                },
            )
            is None
        )

    def test_missing_action_returns_none(self) -> None:
        assert (
            helpers.parse_entity_registry_update(
                {"entity_id": "automation.foo"},
            )
            is None
        )

    def test_old_id_falls_back_to_new_id_when_absent(self) -> None:
        result = helpers.parse_entity_registry_update(
            {"action": "create", "entity_id": "automation.foo"},
        )
        assert result == ("create", "automation.foo", "automation.foo")


_EXPECTED_CFG_ERR_ID = (
    "blueprint_toolkit_trigger_entity_controller__automation.x__config_error"
)


class TestMakeConfigErrorNotification:
    def test_with_errors_is_active(self) -> None:
        n = helpers.make_config_error_notification(
            service="trigger_entity_controller",
            service_tag="TEC",
            instance_id="automation.x",
            errors=["bad", "worse"],
        )
        assert n.active is True
        assert n.notification_id == _EXPECTED_CFG_ERR_ID
        assert "TEC config error" in n.title
        assert "automation.x" in n.title
        assert "- bad" in n.message
        assert "- worse" in n.message

    def test_empty_errors_yields_dismiss_spec(self) -> None:
        n = helpers.make_config_error_notification(
            service="trigger_entity_controller",
            service_tag="TEC",
            instance_id="automation.x",
            errors=[],
        )
        assert n.active is False
        assert n.notification_id == _EXPECTED_CFG_ERR_ID
        assert n.title == ""
        assert n.message == ""

    def test_link_prefix_when_name_and_yaml_id_provided(self) -> None:
        n = helpers.make_config_error_notification(
            service="trigger_entity_controller",
            service_tag="TEC",
            instance_id="automation.x",
            errors=["bad"],
            instance_name="My Auto",
            instance_yaml_id="1234567890",
        )
        assert n.message.startswith(
            "Automation: [My Auto](/config/automation/edit/1234567890)\n",
        )

    def test_no_link_prefix_when_yaml_id_missing(self) -> None:
        n = helpers.make_config_error_notification(
            service="trigger_entity_controller",
            service_tag="TEC",
            instance_id="automation.x",
            errors=["bad"],
            instance_name="My Auto",
            instance_yaml_id=None,
        )
        assert "Automation:" not in n.message
        assert n.message == "- bad"

    def test_md_escape_applied_to_friendly_name(self) -> None:
        # ``[`` / ``]`` in the user-typed friendly name
        # would otherwise pair with the ``](`` of the
        # link target and corrupt the rendered link.
        n = helpers.make_config_error_notification(
            service="trigger_entity_controller",
            service_tag="TEC",
            instance_id="automation.x",
            errors=["bad"],
            instance_name="Office [Lights]",
            instance_yaml_id="42",
        )
        assert "[Office \\[Lights\\]]" in n.message

    def test_md_escape_applied_to_error_bullets(self) -> None:
        # vol.Invalid messages can echo the offending
        # input value back, which can contain ``[`` /
        # ``]`` (e.g. bad list literal in YAML). Escape
        # so those don't render as a markdown link.
        n = helpers.make_config_error_notification(
            service="trigger_entity_controller",
            service_tag="TEC",
            instance_id="automation.x",
            errors=[
                "expected list, got '[evil](http://evil)'",
            ],
        )
        assert "\\[evil\\](http://evil)" in n.message
        assert "[evil](" not in n.message


class TestPersistentNotificationDataclass:
    def test_active_create_spec(self) -> None:
        n = helpers.PersistentNotification(
            active=True,
            notification_id="x",
            title="t",
            message="m",
        )
        assert (n.active, n.notification_id, n.title, n.message) == (
            True,
            "x",
            "t",
            "m",
        )


# --------------------------------------------------------
# Per-instance diagnostic state
# --------------------------------------------------------


class TestInstanceStateEntityId:
    def test_strips_automation_prefix(self) -> None:
        assert (
            helpers.instance_state_entity_id(
                "trigger_entity_controller",
                "automation.foo_bar",
            )
            == "blueprint_toolkit.trigger_entity_controller_foo_bar_state"
        )

    def test_passes_through_non_automation_id(self) -> None:
        # Belt-and-suspenders: a caller passing a bare slug
        # without the ``automation.`` prefix still gets a
        # well-formed entity_id.
        assert (
            helpers.instance_state_entity_id(
                "trigger_entity_controller",
                "foo_bar",
            )
            == "blueprint_toolkit.trigger_entity_controller_foo_bar_state"
        )


class TestUpdateInstanceState:
    def test_writes_state_with_attributes(self) -> None:
        hass = _MockHass()

        run_at = datetime(2024, 1, 15, 12, 0, 0)
        off_at = datetime(2024, 1, 15, 12, 5, 0)
        helpers.update_instance_state(
            hass,  # type: ignore[arg-type]
            service="trigger_entity_controller",
            instance_id="automation.foo_bar",
            last_event="TRIGGER_ON",
            last_action="TURN_ON",
            last_run=run_at,
            last_reason="motion fired",
            auto_off_at=off_at,
        )
        assert hass.states.set_calls == [
            (
                ("blueprint_toolkit.trigger_entity_controller_foo_bar_state"),
                "TURN_ON",
                {
                    "instance_id": "automation.foo_bar",
                    "last_event": "TRIGGER_ON",
                    "last_run": run_at.isoformat(),
                    "last_reason": "motion fired",
                    "auto_off_at": off_at.isoformat(),
                },
            ),
        ]

    def test_auto_off_at_none_serialises_as_none(self) -> None:
        hass = _MockHass()

        helpers.update_instance_state(
            hass,  # type: ignore[arg-type]
            service="trigger_entity_controller",
            instance_id="automation.foo",
            last_event="TIMER",
            last_action="TURN_OFF",
            last_run=datetime(2024, 1, 15, 12, 0, 0),
        )
        attrs = hass.states.set_calls[0][2]
        assert attrs["auto_off_at"] is None

    def test_extra_attributes_merged(self) -> None:
        hass = _MockHass()

        helpers.update_instance_state(
            hass,  # type: ignore[arg-type]
            service="zwave_route_manager",
            instance_id="automation.zrm",
            last_event="EVALUATE",
            last_action="APPLIED",
            last_run=datetime(2024, 1, 15, 12, 0, 0),
            extra_attributes={
                "applied_routes": 17,
                "pending_routes": 1,
            },
        )
        attrs = hass.states.set_calls[0][2]
        assert attrs["applied_routes"] == 17
        assert attrs["pending_routes"] == 1


# --------------------------------------------------------
# process_persistent_notifications dispatcher
# --------------------------------------------------------


class TestProcessPersistentNotifications:
    @pytest.mark.asyncio
    async def test_active_creates(self) -> None:
        hass = _MockHass()

        await helpers.process_persistent_notifications(
            hass,  # type: ignore[arg-type]
            [
                helpers.PersistentNotification(
                    active=True,
                    notification_id="x",
                    title="t",
                    message="m",
                ),
            ],
        )
        assert hass.services.calls == [
            (
                "persistent_notification",
                "create",
                {"notification_id": "x", "title": "t", "message": "m"},
            ),
        ]

    @pytest.mark.asyncio
    async def test_inactive_dismisses(self) -> None:
        hass = _MockHass()

        await helpers.process_persistent_notifications(
            hass,  # type: ignore[arg-type]
            [
                helpers.PersistentNotification(
                    active=False,
                    notification_id="x",
                    title="",
                    message="",
                ),
            ],
        )
        assert hass.services.calls == [
            (
                "persistent_notification",
                "dismiss",
                {"notification_id": "x"},
            ),
        ]


# --------------------------------------------------------
# Lifecycle: register_blueprint_handler
# --------------------------------------------------------


class TestRegisterBlueprintHandler:
    @pytest.mark.asyncio
    async def test_minimal_spec_only_registers_service(self) -> None:
        hass = _MockHass(is_running=True)

        entry = _MockEntry()
        spec = _make_spec()  # all hooks default to None
        await helpers.register_blueprint_handler(
            hass,  # type: ignore[arg-type]
            entry,  # type: ignore[arg-type]
            spec,
        )
        assert (DOMAIN, spec.service) in hass.services.registered
        # No reload listener wired (no kick, no on_reload)
        assert "automation_reloaded" not in hass.bus.listeners
        # No entity-registry listener wired
        assert "entity_registry_updated" not in hass.bus.listeners
        # No restart-recovery task scheduled
        assert hass.tasks == []

    @pytest.mark.asyncio
    async def test_kick_only_wires_reload_listener_and_recovery(
        self,
    ) -> None:
        hass = _MockHass(is_running=True)

        entry = _MockEntry()
        spec = _make_spec(kick=_kick_stub)
        await helpers.register_blueprint_handler(
            hass,  # type: ignore[arg-type]
            entry,  # type: ignore[arg-type]
            spec,
        )
        # Reload listener wired (kick is enough)
        assert len(hass.bus.listeners.get("automation_reloaded", [])) == 1
        # Restart-recovery scheduled (hass already running)
        assert len(hass.tasks) == 1

    @pytest.mark.asyncio
    async def test_kick_when_not_running_defers_to_started_event(
        self,
    ) -> None:
        hass = _MockHass(is_running=False)

        entry = _MockEntry()
        spec = _make_spec(kick=_kick_stub)
        await helpers.register_blueprint_handler(
            hass,  # type: ignore[arg-type]
            entry,  # type: ignore[arg-type]
            spec,
        )
        # Recovery is deferred -- no task created right
        # away; instead a one-shot listener on
        # EVENT_HOMEASSISTANT_STARTED is registered.
        assert hass.tasks == []
        assert (
            len(hass.bus.once_listeners.get("homeassistant_started", [])) == 1
        )

    @pytest.mark.asyncio
    async def test_entity_registry_listener_only_when_remove_or_rename(
        self,
    ) -> None:
        hass = _MockHass(is_running=True)

        entry = _MockEntry()
        # Only on_entity_remove provided -- ER listener
        # should still be wired.
        spec = _make_spec(
            on_entity_remove=lambda _h, _e: None,
        )
        await helpers.register_blueprint_handler(
            hass,  # type: ignore[arg-type]
            entry,  # type: ignore[arg-type]
            spec,
        )
        assert len(hass.bus.listeners.get("entity_registry_updated", [])) == 1

    @pytest.mark.asyncio
    async def test_idempotent_under_re_register(self) -> None:
        hass = _MockHass(is_running=True)

        entry = _MockEntry()
        spec = _make_spec(kick=_kick_stub)
        await helpers.register_blueprint_handler(
            hass,  # type: ignore[arg-type]
            entry,  # type: ignore[arg-type]
            spec,
        )
        await helpers.register_blueprint_handler(
            hass,  # type: ignore[arg-type]
            entry,  # type: ignore[arg-type]
            spec,
        )
        # Service still registered once (re-register
        # removed prior entry first), and listener
        # count is still 1 (prior unsub fired before
        # re-listening).
        assert (DOMAIN, spec.service) in hass.services.registered
        assert len(hass.bus.listeners.get("automation_reloaded", [])) == 1


# --------------------------------------------------------
# Lifecycle: unregister_blueprint_handler
# --------------------------------------------------------


class TestUnregisterBlueprintHandler:
    @pytest.mark.asyncio
    async def test_removes_service(self) -> None:
        hass = _MockHass(is_running=True)

        entry = _MockEntry()
        spec = _make_spec()
        await helpers.register_blueprint_handler(
            hass,  # type: ignore[arg-type]
            entry,  # type: ignore[arg-type]
            spec,
        )
        assert (DOMAIN, spec.service) in hass.services.registered
        await helpers.unregister_blueprint_handler(
            hass,  # type: ignore[arg-type]
            entry,  # type: ignore[arg-type]
            spec,
        )
        assert (DOMAIN, spec.service) not in hass.services.registered

    @pytest.mark.asyncio
    async def test_unsubscribes_listeners(self) -> None:
        hass = _MockHass(is_running=True)

        entry = _MockEntry()
        spec = _make_spec(
            kick=_kick_stub,
            on_entity_remove=lambda _h, _e: None,
        )
        await helpers.register_blueprint_handler(
            hass,  # type: ignore[arg-type]
            entry,  # type: ignore[arg-type]
            spec,
        )
        assert len(hass.bus.listeners["automation_reloaded"]) == 1
        assert len(hass.bus.listeners["entity_registry_updated"]) == 1
        await helpers.unregister_blueprint_handler(
            hass,  # type: ignore[arg-type]
            entry,  # type: ignore[arg-type]
            spec,
        )
        assert hass.bus.listeners["automation_reloaded"] == []
        assert hass.bus.listeners["entity_registry_updated"] == []

    @pytest.mark.asyncio
    async def test_calls_on_teardown_when_set(self) -> None:
        hass = _MockHass(is_running=True)

        entry = _MockEntry()
        called: list[bool] = []
        spec = _make_spec(
            on_teardown=lambda _h: called.append(True),
        )
        await helpers.register_blueprint_handler(
            hass,  # type: ignore[arg-type]
            entry,  # type: ignore[arg-type]
            spec,
        )
        await helpers.unregister_blueprint_handler(
            hass,  # type: ignore[arg-type]
            entry,  # type: ignore[arg-type]
            spec,
        )
        assert called == [True]

    @pytest.mark.asyncio
    async def test_no_crash_when_on_teardown_absent(self) -> None:
        hass = _MockHass(is_running=True)

        entry = _MockEntry()
        spec = _make_spec()  # no on_teardown
        await helpers.register_blueprint_handler(
            hass,  # type: ignore[arg-type]
            entry,  # type: ignore[arg-type]
            spec,
        )
        # Should not raise.
        await helpers.unregister_blueprint_handler(
            hass,  # type: ignore[arg-type]
            entry,  # type: ignore[arg-type]
            spec,
        )


# --------------------------------------------------------
# End-to-end listener dispatch
# --------------------------------------------------------
#
# Drive a synthetic ``EVENT_ENTITY_REGISTRY_UPDATED`` /
# ``EVENT_AUTOMATION_RELOADED`` event through the
# listener the dispatcher registered, and verify the
# per-spec mutator callbacks are called with the right
# arguments. Catches regressions in the
# parse-then-dispatch wiring inside
# ``register_blueprint_handler``.


@dataclass
class _MockEvent:
    data: dict[str, Any]


class TestListenerDispatch:
    @pytest.mark.asyncio
    async def test_remove_event_calls_on_entity_remove(self) -> None:
        hass = _MockHass(is_running=True)

        entry = _MockEntry()
        seen_removes: list[str] = []
        spec = _make_spec(
            on_entity_remove=lambda _h, eid: seen_removes.append(eid),
        )
        await helpers.register_blueprint_handler(
            hass,  # type: ignore[arg-type]
            entry,  # type: ignore[arg-type]
            spec,
        )
        listener = hass.bus.listeners["entity_registry_updated"][0]
        listener(
            _MockEvent(
                data={
                    "action": "remove",
                    "entity_id": "automation.foo",
                    "old_entity_id": "automation.foo",
                },
            ),
        )
        assert seen_removes == ["automation.foo"]

    @pytest.mark.asyncio
    async def test_rename_event_calls_on_entity_rename(self) -> None:
        hass = _MockHass(is_running=True)

        entry = _MockEntry()
        seen_renames: list[tuple[str, str]] = []
        spec = _make_spec(
            on_entity_rename=lambda _h, old, new: seen_renames.append(
                (old, new),
            ),
        )
        await helpers.register_blueprint_handler(
            hass,  # type: ignore[arg-type]
            entry,  # type: ignore[arg-type]
            spec,
        )
        listener = hass.bus.listeners["entity_registry_updated"][0]
        listener(
            _MockEvent(
                data={
                    "action": "update",
                    "entity_id": "automation.bar",
                    "old_entity_id": "automation.foo",
                },
            ),
        )
        assert seen_renames == [("automation.foo", "automation.bar")]

    @pytest.mark.asyncio
    async def test_non_automation_event_is_ignored(self) -> None:
        hass = _MockHass(is_running=True)

        entry = _MockEntry()
        seen: list[str] = []
        spec = _make_spec(
            on_entity_remove=lambda _h, eid: seen.append(eid),
        )
        await helpers.register_blueprint_handler(
            hass,  # type: ignore[arg-type]
            entry,  # type: ignore[arg-type]
            spec,
        )
        listener = hass.bus.listeners["entity_registry_updated"][0]
        listener(
            _MockEvent(
                data={
                    "action": "remove",
                    "entity_id": "light.kitchen",
                    "old_entity_id": "light.kitchen",
                },
            ),
        )
        assert seen == []

    @pytest.mark.asyncio
    async def test_disable_style_update_does_not_dispatch(self) -> None:
        # Mirrors what HA fires when an automation's
        # ``disabled_by`` field is toggled: action=update
        # but old_id == new_id (no rename). The dispatcher
        # must skip both the rename and remove paths.
        hass = _MockHass(is_running=True)

        entry = _MockEntry()
        seen_removes: list[str] = []
        seen_renames: list[tuple[str, str]] = []
        spec = _make_spec(
            on_entity_remove=lambda _h, eid: seen_removes.append(eid),
            on_entity_rename=lambda _h, old, new: seen_renames.append(
                (old, new),
            ),
        )
        await helpers.register_blueprint_handler(
            hass,  # type: ignore[arg-type]
            entry,  # type: ignore[arg-type]
            spec,
        )
        listener = hass.bus.listeners["entity_registry_updated"][0]
        listener(
            _MockEvent(
                data={
                    "action": "update",
                    "entity_id": "automation.foo",
                    "old_entity_id": "automation.foo",
                    "changes": {"disabled_by": "user"},
                },
            ),
        )
        assert seen_removes == []
        assert seen_renames == []

    @pytest.mark.asyncio
    async def test_reload_event_calls_on_reload_then_recovery(self) -> None:
        hass = _MockHass(is_running=True)

        entry = _MockEntry()
        on_reload_calls: list[bool] = []
        spec = _make_spec(
            kick=_kick_stub,
            on_reload=lambda _h: on_reload_calls.append(True),
        )
        await helpers.register_blueprint_handler(
            hass,  # type: ignore[arg-type]
            entry,  # type: ignore[arg-type]
            spec,
        )
        # The is_running=True branch already scheduled an
        # initial recovery task at registration. Reset
        # before exercising the listener so we count only
        # the reload-triggered one.
        hass.tasks.clear()
        listener = hass.bus.listeners["automation_reloaded"][0]
        listener(_MockEvent(data={}))
        assert on_reload_calls == [True]
        assert len(hass.tasks) == 1


# --------------------------------------------------------
# Pytest plumbing: enable asyncio test mode
# --------------------------------------------------------


def pytest_collection_modifyitems(items: list[Any]) -> None:
    """Auto-mark async tests so they run without an explicit decorator."""
    for item in items:
        if asyncio.iscoroutinefunction(getattr(item, "function", None)):
            item.add_marker(pytest.mark.asyncio)


# --------------------------------------------------------
# CodeQuality
# --------------------------------------------------------


class TestCodeQuality(CodeQualityBase):
    ruff_targets = [
        "tests/test_helpers_lifecycle.py",
        "custom_components/blueprint_toolkit/helpers.py",
    ]
    mypy_targets: list[str] = [
        "custom_components/blueprint_toolkit/helpers.py",
    ]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", *sys.argv[1:]]))
