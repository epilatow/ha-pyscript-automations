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
from datetime import datetime, timedelta
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
# ``homeassistant.helpers.event`` exposes the timer
# primitives ``schedule_periodic_with_jitter`` late-imports.
# The schedule helper's tests reach in via the
# ``_track_calls`` / ``_call_later_calls`` capture lists
# below (each registration appends; each unsub records its
# index).
_ha_helpers_event = types.ModuleType("homeassistant.helpers.event")
_track_calls: list[tuple[Any, Any, Any]] = []
_track_cancel_calls: list[int] = []
_call_later_calls: list[tuple[Any, Any, Any]] = []
_call_later_cancel_calls: list[int] = []


def _async_track_time_interval(
    _hass: Any,
    cb: Any,
    interval: Any,
) -> Any:
    handle_index = len(_track_calls)
    _track_calls.append((interval, cb, _hass))

    def _cancel() -> None:
        _track_cancel_calls.append(handle_index)

    return _cancel


def _async_call_later(
    _hass: Any,
    delay: Any,
    cb: Any,
) -> Any:
    handle_index = len(_call_later_calls)
    _call_later_calls.append((delay, cb, _hass))

    def _cancel() -> None:
        _call_later_cancel_calls.append(handle_index)

    return _cancel


_ha_helpers_event.async_track_time_interval = (  # type: ignore[attr-defined]
    _async_track_time_interval
)
_ha_helpers_event.async_call_later = (  # type: ignore[attr-defined]
    _async_call_later
)
sys.modules["homeassistant"] = _ha
sys.modules["homeassistant.components"] = _ha_components
sys.modules["homeassistant.components.automation"] = _ha_components_automation
sys.modules["homeassistant.const"] = _ha_const
sys.modules["homeassistant.core"] = _ha_core
sys.modules["homeassistant.helpers"] = _ha_helpers
sys.modules["homeassistant.helpers.entity_registry"] = _ha_helpers_er
sys.modules["homeassistant.helpers.event"] = _ha_helpers_event

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
    # Records each ``unsub()`` call against an already-
    # detached once-listener -- HA logs these as
    # ``Unable to remove unknown job listener`` ERROR
    # entries; tests assert this list stays empty.
    stale_unsub_calls: list[tuple[str, Callable[..., Any]]] = field(
        default_factory=list,
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
        # Mimic HA's behaviour: the listener auto-detaches
        # when the event fires; calling the returned unsub
        # AFTER that point logs ``Unable to remove unknown
        # job listener``. We surface that as a tracked event
        # so regression tests can assert it didn't happen.
        self.once_listeners.setdefault(event_type, []).append(handler)

        def _unsub() -> None:
            if handler in self.once_listeners.get(event_type, []):
                self.once_listeners[event_type].remove(handler)
            else:
                self.stale_unsub_calls.append((event_type, handler))

        return _unsub

    def fire_once(self, event_type: str, event: Any = None) -> None:
        """Dispatch every once-listener for ``event_type``, then auto-detach.

        Mirrors HA's ``Bus.async_fire`` for once-listeners:
        the listener runs synchronously and is removed
        from the bus before any task it schedules has a
        chance to run.
        """
        handlers = list(self.once_listeners.get(event_type, []))
        self.once_listeners[event_type] = []
        for handler in handlers:
            handler(event)


@dataclass
class _MockStateLike:
    """Stand-in for ``hass.states.get(...)``'s return value."""

    state: str
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class _MockStates:
    """Captures ``hass.states.async_set`` calls for inspection.

    Also provides ``hass.states.get(entity_id)`` so the
    dispatcher's automation-link lookup can find seeded
    entities. Tests stash entries via ``stub_state``.
    """

    set_calls: list[tuple[str, str, dict[str, Any]]] = field(
        default_factory=list,
    )
    stubs: dict[str, _MockStateLike] = field(default_factory=dict)

    def async_set(
        self,
        entity_id: str,
        state: str,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        self.set_calls.append((entity_id, state, dict(attributes or {})))

    def get(self, entity_id: str) -> _MockStateLike | None:
        return self.stubs.get(entity_id)

    def stub_state(
        self,
        entity_id: str,
        state: str = "on",
        **attributes: Any,
    ) -> None:
        self.stubs[entity_id] = _MockStateLike(state, dict(attributes))


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
    # Records each ``async_create_background_task`` call as
    # ``(name, coro)`` so tests can assert the entry-scoped
    # task was created (HA cancels these on entry unload).
    background_tasks: list[tuple[str, Awaitable[Any]]] = field(
        default_factory=list,
    )

    def async_create_background_task(
        self,
        _hass: Any,
        coro: Awaitable[Any],
        name: str,
    ) -> None:
        # Track for inspection, then close the coroutine to
        # avoid the ``RuntimeWarning: coroutine was never
        # awaited`` pytest emits otherwise.
        self.background_tasks.append((name, coro))
        if hasattr(coro, "close"):
            coro.close()


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

    def test_instance_id_set_so_dispatcher_can_prepend_link(self) -> None:
        # The link prefix is added by the dispatcher, not
        # the builder; the builder just stamps the
        # instance_id onto the spec so the dispatcher
        # knows which automation to look up.
        n = helpers.make_config_error_notification(
            service="trigger_entity_controller",
            service_tag="TEC",
            instance_id="automation.x",
            errors=["bad"],
        )
        assert n.instance_id == "automation.x"
        # Builder body is just the bullets -- no prefix
        # baked in.
        assert n.message == "- bad"

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
    def test_writes_common_attrs_with_default_state(self) -> None:
        hass = _MockHass()
        run_at = datetime(2024, 1, 15, 12, 0, 0)
        helpers.update_instance_state(
            hass,  # type: ignore[arg-type]
            service="device_watchdog",
            instance_id="automation.dw",
            last_run=run_at,
            runtime=1.234,
        )
        assert hass.states.set_calls == [
            (
                "blueprint_toolkit.device_watchdog_dw_state",
                "ok",
                {
                    "instance_id": "automation.dw",
                    "last_run": run_at.isoformat(),
                    "runtime": 1.23,
                },
            ),
        ]

    def test_state_value_override_for_trigger_handlers(self) -> None:
        hass = _MockHass()
        helpers.update_instance_state(
            hass,  # type: ignore[arg-type]
            service="trigger_entity_controller",
            instance_id="automation.tec",
            last_run=datetime(2024, 1, 15, 12, 0, 0),
            runtime=0.05,
            state="TURN_ON",
        )
        assert hass.states.set_calls[0][1] == "TURN_ON"

    def test_extra_attributes_merged(self) -> None:
        hass = _MockHass()
        helpers.update_instance_state(
            hass,  # type: ignore[arg-type]
            service="trigger_entity_controller",
            instance_id="automation.tec",
            last_run=datetime(2024, 1, 15, 12, 0, 0),
            runtime=0.1,
            state="TURN_ON",
            extra_attributes={
                "last_event": "TRIGGER_ON",
                "last_reason": "motion fired",
                "auto_off_at": "2024-01-15T12:05:00",
            },
        )
        attrs = hass.states.set_calls[0][2]
        assert attrs["last_event"] == "TRIGGER_ON"
        assert attrs["last_reason"] == "motion fired"
        assert attrs["auto_off_at"] == "2024-01-15T12:05:00"

    def test_runtime_rounded_to_two_decimals(self) -> None:
        hass = _MockHass()
        helpers.update_instance_state(
            hass,  # type: ignore[arg-type]
            service="zwave_route_manager",
            instance_id="automation.zrm",
            last_run=datetime(2024, 1, 15, 12, 0, 0),
            runtime=2.4567,
        )
        assert hass.states.set_calls[0][2]["runtime"] == 2.46


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

    @pytest.mark.asyncio
    async def test_prepends_automation_link_when_instance_known(self) -> None:
        hass = _MockHass()
        hass.states.stub_state(
            "automation.foo",
            "on",
            friendly_name="My Auto",
            id="1234",
        )
        await helpers.process_persistent_notifications(
            hass,  # type: ignore[arg-type]
            [
                helpers.PersistentNotification(
                    active=True,
                    notification_id="x",
                    title="t",
                    message="- bad",
                    instance_id="automation.foo",
                ),
            ],
        )
        sent = hass.services.calls[0][2]["message"]
        assert sent.startswith(
            "Automation: [My Auto](/config/automation/edit/1234)\n",
        )
        assert sent.endswith("- bad")

    @pytest.mark.asyncio
    async def test_no_prefix_when_instance_id_absent(self) -> None:
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
        assert hass.services.calls[0][2]["message"] == "m"

    @pytest.mark.asyncio
    async def test_no_prefix_when_automation_not_registered(self) -> None:
        # instance_id set on the spec but no matching state
        # in hass.states (the user invoked the service via
        # Developer Tools).
        hass = _MockHass()
        await helpers.process_persistent_notifications(
            hass,  # type: ignore[arg-type]
            [
                helpers.PersistentNotification(
                    active=True,
                    notification_id="x",
                    title="t",
                    message="m",
                    instance_id="automation.unknown",
                ),
            ],
        )
        assert hass.services.calls[0][2]["message"] == "m"

    @pytest.mark.asyncio
    async def test_md_escape_applied_to_friendly_name(self) -> None:
        hass = _MockHass()
        hass.states.stub_state(
            "automation.foo",
            "on",
            friendly_name="Office [Lights]",
            id="42",
        )
        await helpers.process_persistent_notifications(
            hass,  # type: ignore[arg-type]
            [
                helpers.PersistentNotification(
                    active=True,
                    notification_id="x",
                    title="t",
                    message="- bad",
                    instance_id="automation.foo",
                ),
            ],
        )
        assert "[Office \\[Lights\\]]" in hass.services.calls[0][2]["message"]

    @pytest.mark.asyncio
    async def test_dismiss_does_not_prepend(self) -> None:
        # Inactive specs are dismiss calls; nothing rendered.
        hass = _MockHass()
        hass.states.stub_state(
            "automation.foo",
            "on",
            friendly_name="My Auto",
            id="42",
        )
        await helpers.process_persistent_notifications(
            hass,  # type: ignore[arg-type]
            [
                helpers.PersistentNotification(
                    active=False,
                    notification_id="x",
                    title="",
                    message="",
                    instance_id="automation.foo",
                ),
            ],
        )
        # ``dismiss`` only carries notification_id; no
        # message field even possible.
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
        # via the entry-scoped background-task API so HA
        # cancels it on entry unload -- NOT via
        # ``hass.async_create_task`` which would dangle past
        # an unload that lands while the task is queued.
        assert hass.tasks == []
        assert len(entry.background_tasks) == 1
        name, _coro = entry.background_tasks[0]
        assert name.endswith("_recover_at_startup")

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

    @pytest.mark.asyncio
    async def test_started_once_listener_no_stale_unsub(self) -> None:
        # Regression for the ``Unable to remove unknown
        # job listener`` ERROR HA logs when an
        # already-detached once-listener's unsub is called
        # later. Sequence: register while HA is starting (so
        # we register the EVENT_HOMEASSISTANT_STARTED
        # once-listener), fire the event (HA auto-detaches
        # the listener), then unregister. The dispatcher
        # must have removed its bookkeeping handle when the
        # listener fired so unregister doesn't try to call
        # the stale unsub.
        hass = _MockHass(is_running=False)

        entry = _MockEntry()
        spec = _make_spec(kick=_kick_stub)
        await helpers.register_blueprint_handler(
            hass,  # type: ignore[arg-type]
            entry,  # type: ignore[arg-type]
            spec,
        )
        # The once-listener is registered.
        assert len(hass.bus.once_listeners["homeassistant_started"]) == 1

        # HA finishes starting -> fires the event. HA's bus
        # auto-detaches the listener as part of dispatch.
        hass.bus.fire_once("homeassistant_started")
        assert hass.bus.once_listeners["homeassistant_started"] == []

        # Recovery is scheduled via the entry-scoped
        # background-task API so HA cancels it on entry
        # unload (covers a corner-case race where a kick
        # task could otherwise call into the just-removed
        # blueprint service).
        assert hass.tasks == []
        assert len(entry.background_tasks) == 1
        name, _coro = entry.background_tasks[0]
        assert name.endswith("_recover_at_startup")

        # The dispatcher must have removed its stored unsub
        # for the now-detached listener.
        bucket = entry.runtime_data.handlers[spec.service]
        assert bucket["unsubs"] != [], (
            "non-once unsubs (reload + er listeners) should still be tracked"
        )
        # No unsub in the bucket should still target the
        # detached homeassistant_started listener.
        for unsub in bucket["unsubs"]:
            unsub()
        assert hass.bus.stale_unsub_calls == [], (
            "calling each remaining unsub MUST NOT trigger the stale-unsub "
            f"path; got: {hass.bus.stale_unsub_calls}"
        )

        # Restore listeners (test self-cleanup) before
        # invoking unregister, which calls all unsubs again.
        # Since we've already exhausted them above we just
        # make sure unregister itself doesn't raise.
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
        entry.background_tasks.clear()
        listener = hass.bus.listeners["automation_reloaded"][0]
        listener(_MockEvent(data={}))
        assert on_reload_calls == [True]
        # Reload-triggered recovery is entry-scoped (matches
        # the startup-recovery branch) so an entry unload
        # racing the reload cancels the in-flight task.
        assert hass.tasks == []
        assert len(entry.background_tasks) == 1
        name, _coro = entry.background_tasks[0]
        assert name == (
            "blueprint_toolkit_trigger_entity_controller_reload_recover"
        )


# --------------------------------------------------------
# Pytest plumbing: enable asyncio test mode
# --------------------------------------------------------


def pytest_collection_modifyitems(items: list[Any]) -> None:
    """Auto-mark async tests so they run without an explicit decorator."""
    for item in items:
        if asyncio.iscoroutinefunction(getattr(item, "function", None)):
            item.add_marker(pytest.mark.asyncio)


# --------------------------------------------------------
# Pure helpers (slugify + matches_pattern)
# --------------------------------------------------------
#
# ``test_helpers.py`` covers the pyscript copy of these.
# These tests cover the native ports lifted into
# ``custom_components/blueprint_toolkit/helpers.py``.


class TestSlugify:
    def test_basic(self) -> None:
        assert helpers.slugify("Hello World") == "hello_world"

    def test_collapses_runs_of_non_alphanum(self) -> None:
        assert helpers.slugify("a---b   c") == "a_b_c"

    def test_strips_leading_trailing_underscores(self) -> None:
        assert helpers.slugify("  __foo__  ") == "foo"

    def test_drops_non_ascii(self) -> None:
        # NFKD-decomposed accented chars get stripped.
        assert helpers.slugify("Café") == "cafe"
        assert helpers.slugify("naive") == "naive"

    def test_empty_returns_empty(self) -> None:
        assert helpers.slugify("") == ""

    def test_collapses_to_unknown(self) -> None:
        # Punctuation-only / emoji-only collapse to the
        # HA-fallback sentinel.
        assert helpers.slugify("!!!") == "unknown"
        assert helpers.slugify("\U0001f600") == "unknown"

    def test_lowercase(self) -> None:
        assert helpers.slugify("ABC") == "abc"


class TestMatchesPattern:
    def test_simple_match(self) -> None:
        assert helpers.matches_pattern("foo.bar", "foo")

    def test_case_insensitive(self) -> None:
        assert helpers.matches_pattern("FOO", "foo")
        assert helpers.matches_pattern("foo", "FOO")

    def test_substring_via_search(self) -> None:
        # Regex.search semantics: anchored matching not
        # required.
        assert helpers.matches_pattern("prefix-foo-suffix", "foo")

    def test_no_match(self) -> None:
        assert not helpers.matches_pattern("foo", "bar")

    def test_empty_pattern_returns_false(self) -> None:
        # Documented behaviour: callers handle "no
        # pattern means match-all" themselves if they
        # want it.
        assert not helpers.matches_pattern("anything", "")

    def test_invalid_regex_returns_false(self) -> None:
        # Unbalanced bracket -> re.error. Returns False
        # rather than raising; callers that need to
        # surface invalid-regex errors validate at
        # config-parse time.
        assert not helpers.matches_pattern("foo", "[unclosed")


# --------------------------------------------------------
# validate_and_join_regex_patterns
# --------------------------------------------------------


class TestValidateAndJoinRegexPatterns:
    """Multi-line regex-input handling for blueprint
    fields like RW's ``exclude_entity_regex`` (and DW /
    EDW's ``device_exclude_regex`` /
    ``entity_id_exclude_regex`` / ``entity_name_exclude_regex``).

    Pre-port pyscript had this in
    ``_validate_and_join_patterns``; the original RW
    native port lost it and re-implemented argparse with
    a single ``re.compile()`` -- which silently fails on
    multi-line input because the whole string (newline
    chars and all) gets fed to the regex engine. This
    suite covers the regression.
    """

    def test_single_line_passes_through(self) -> None:
        joined, errors = helpers.validate_and_join_regex_patterns(
            "sensor\\.foo",
            "exclude_entity_regex",
        )
        assert joined == "sensor\\.foo"
        assert errors == []

    def test_multiline_joined_with_pipe(self) -> None:
        # The bug the user hit: multiple patterns on
        # separate lines must combine into a single
        # alternation regex.
        joined, errors = helpers.validate_and_join_regex_patterns(
            "sensor\\.loft_humidifier_energy\nsensor\\.office_humidifier_energy",
            "exclude_entity_regex",
        )
        assert errors == []
        assert (
            joined == "sensor\\.loft_humidifier_energy"
            "|sensor\\.office_humidifier_energy"
        )

    def test_joined_pattern_actually_matches_each_line(self) -> None:
        # End-to-end: feed the joined pattern back through
        # ``matches_pattern`` and verify both inputs match.
        joined, errors = helpers.validate_and_join_regex_patterns(
            "sensor\\.loft_humidifier_energy\n"
            "sensor\\.office_humidifier_energy",
            "exclude_entity_regex",
        )
        assert errors == []
        assert helpers.matches_pattern("sensor.loft_humidifier_energy", joined)
        assert helpers.matches_pattern(
            "sensor.office_humidifier_energy", joined
        )
        # And a non-matching entity is correctly NOT
        # matched -- the bug-fix shouldn't accidentally
        # turn this into a match-everything regex.
        assert not helpers.matches_pattern("sensor.bedroom_temperature", joined)

    def test_empty_lines_skipped(self) -> None:
        joined, errors = helpers.validate_and_join_regex_patterns(
            "\n  \nfoo\n\n",
            "exclude_entity_regex",
        )
        assert joined == "foo"
        assert errors == []

    def test_invalid_pattern_per_line_error(self) -> None:
        # One invalid line drops out; valid neighbours
        # still get joined.
        joined, errors = helpers.validate_and_join_regex_patterns(
            "valid.*\n[invalid\nalso_valid",
            "exclude_entity_regex",
        )
        assert len(errors) == 1
        assert "[invalid" in errors[0]
        assert "exclude_entity_regex" in errors[0]
        # Valid lines get joined; the invalid one is
        # excluded but error surfaced.
        assert "valid" in joined
        assert "also_valid" in joined
        assert "[invalid" not in joined

    def test_match_all_pattern_rejected(self) -> None:
        # ``.*`` matches every entity; rejecting it stops
        # the user accidentally turning the field into a
        # match-everything filter.
        joined, errors = helpers.validate_and_join_regex_patterns(
            ".*",
            "exclude_entity_regex",
        )
        assert joined == ""
        assert len(errors) == 1
        assert "matches empty string" in errors[0]

    def test_match_empty_via_alternation_rejected(self) -> None:
        # ``|||||`` is the canonical "all alternatives are
        # empty" pattern -- also matches everything.
        _joined, errors = helpers.validate_and_join_regex_patterns(
            "|||||",
            "exclude_entity_regex",
        )
        assert any("matches empty string" in e for e in errors)

    def test_match_empty_via_optional_rejected(self) -> None:
        # ``a?`` matches "" too.
        _joined, errors = helpers.validate_and_join_regex_patterns(
            "a?",
            "exclude_entity_regex",
        )
        assert any("matches empty string" in e for e in errors)

    def test_empty_input_returns_empty(self) -> None:
        joined, errors = helpers.validate_and_join_regex_patterns(
            "",
            "exclude_entity_regex",
        )
        assert joined == ""
        assert errors == []

    def test_only_whitespace_returns_empty(self) -> None:
        joined, errors = helpers.validate_and_join_regex_patterns(
            "  \n\t\n   ",
            "exclude_entity_regex",
        )
        assert joined == ""
        assert errors == []


# --------------------------------------------------------
# schedule_periodic_with_jitter
# --------------------------------------------------------


def _reset_timer_capture() -> None:
    _track_calls.clear()
    _track_cancel_calls.clear()
    _call_later_calls.clear()
    _call_later_cancel_calls.clear()


class TestSchedulePeriodicWithJitter:
    def test_arms_async_call_later_with_jittered_delay(self) -> None:
        _reset_timer_capture()
        hass = _MockHass()
        entry = _MockEntry()

        async def _action(_now: Any) -> None:
            return

        helpers.schedule_periodic_with_jitter(
            hass,  # type: ignore[arg-type]
            entry,
            interval=timedelta(minutes=5),
            instance_id="automation.test_a",
            action=_action,
        )

        # Initial schedule is via async_call_later, not
        # async_track_time_interval -- the steady-state
        # tracker is armed only after the one-shot fires.
        assert len(_call_later_calls) == 1
        assert _track_calls == []

        delay, _cb, _hass = _call_later_calls[0]
        # 5 min = 300s, jitter is in [0, 300).
        assert isinstance(delay, int)
        assert 0 <= delay < 300

    def test_jitter_is_deterministic_per_instance(self) -> None:
        _reset_timer_capture()
        hass = _MockHass()
        entry = _MockEntry()

        async def _action(_now: Any) -> None:
            return

        # Two registrations with the same instance_id must
        # produce the same jitter (stable hash).
        helpers.schedule_periodic_with_jitter(
            hass,  # type: ignore[arg-type]
            entry,
            interval=timedelta(minutes=5),
            instance_id="automation.same",
            action=_action,
        )
        helpers.schedule_periodic_with_jitter(
            hass,  # type: ignore[arg-type]
            entry,
            interval=timedelta(minutes=5),
            instance_id="automation.same",
            action=_action,
        )
        assert _call_later_calls[0][0] == _call_later_calls[1][0]

    def test_jitter_differs_across_instances(self) -> None:
        _reset_timer_capture()
        hass = _MockHass()
        entry = _MockEntry()

        async def _action(_now: Any) -> None:
            return

        # 5-minute interval gives 300 distinct slots; a
        # handful of distinct instance_ids should hit
        # multiple slots. We assert >=2 distinct values to
        # confirm jitter actually varies.
        for instance_id in (
            "automation.a",
            "automation.b",
            "automation.c",
            "automation.d",
            "automation.e",
            "automation.f",
        ):
            helpers.schedule_periodic_with_jitter(
                hass,  # type: ignore[arg-type]
                entry,
                interval=timedelta(minutes=5),
                instance_id=instance_id,
                action=_action,
            )
        delays = {call[0] for call in _call_later_calls}
        assert len(delays) >= 2, (
            f"jitter should vary across instances; got {delays}"
        )

    def test_unsub_before_first_fire_cancels_call_later(self) -> None:
        _reset_timer_capture()
        hass = _MockHass()
        entry = _MockEntry()

        async def _action(_now: Any) -> None:
            return

        unsub = helpers.schedule_periodic_with_jitter(
            hass,  # type: ignore[arg-type]
            entry,
            interval=timedelta(minutes=5),
            instance_id="automation.cancel",
            action=_action,
        )
        unsub()
        # Cancelling before the one-shot fired must cancel
        # the async_call_later handle (index 0).
        assert _call_later_cancel_calls == [0]
        # And no steady-state timer should have been armed.
        assert _track_calls == []

    def test_first_fire_arms_track_time_interval(self) -> None:
        _reset_timer_capture()
        hass = _MockHass()
        entry = _MockEntry()

        invoked: list[Any] = []

        async def _action(now: Any) -> None:
            invoked.append(now)

        helpers.schedule_periodic_with_jitter(
            hass,  # type: ignore[arg-type]
            entry,
            interval=timedelta(minutes=5),
            instance_id="automation.fire",
            action=_action,
        )
        # Pull the registered one-shot callback and fire it
        # synchronously, simulating HA's
        # ``async_call_later`` dispatch.
        _delay, on_first_fire, _hass = _call_later_calls[0]
        fake_now = datetime(2026, 4, 28, 23, 0, 0)
        on_first_fire(fake_now)

        # Steady-state tracker now armed for subsequent
        # ticks. The tracked callback is a sync wrapper
        # (not ``_action`` directly) so every tick routes
        # through ``entry.async_create_background_task``
        # rather than HA's internal ``hass.async_create_task``.
        assert len(_track_calls) == 1
        interval, tracked_cb, _ = _track_calls[0]
        assert interval == timedelta(minutes=5)
        assert tracked_cb is not _action
        # First-call action scheduled via
        # ``entry.async_create_background_task`` so an entry
        # unload mid-tick cancels it.
        assert hass.tasks == []
        assert len(entry.background_tasks) == 1
        name, _coro = entry.background_tasks[0]
        assert name == "blueprint_toolkit_periodic_tick_automation.fire"

        # Firing the tracked callback (steady-state tick)
        # also routes through the entry, not hass.
        tracked_cb(datetime(2026, 4, 28, 23, 5, 0))
        assert hass.tasks == []
        assert len(entry.background_tasks) == 2

    def test_unsub_after_first_fire_cancels_track(self) -> None:
        _reset_timer_capture()
        hass = _MockHass()
        entry = _MockEntry()

        async def _action(_now: Any) -> None:
            return

        unsub = helpers.schedule_periodic_with_jitter(
            hass,  # type: ignore[arg-type]
            entry,
            interval=timedelta(minutes=5),
            instance_id="automation.late_unsub",
            action=_action,
        )
        # Fire the one-shot.
        _delay, on_first_fire, _hass = _call_later_calls[0]
        on_first_fire(datetime(2026, 4, 28, 23, 0, 0))
        # Now the steady-state tracker is the active timer.
        unsub()
        assert _track_cancel_calls == [0]
        # And the original ``async_call_later`` was NOT
        # cancelled a second time -- HA already auto-removed
        # it when it fired.
        assert _call_later_cancel_calls == []


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
