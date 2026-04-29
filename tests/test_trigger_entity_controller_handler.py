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
"""Unit tests for ``tec.handler``'s per-instance lifecycle code.

Covers the parts that *don't* require booting Home Assistant:
the ``_on_reload`` / ``_on_entity_remove`` /
``_on_entity_rename`` / ``_on_teardown`` mutator callbacks
fed into the BlueprintHandlerSpec, ``_apply_auto_off_at``'s
schedule + cancel sequencing against ``async_call_later``,
and ``_make_wakeup``'s synthetic-TIMER ``automation.trigger``
shape. The argparse + service layers are exercised end-to-end
by the live integration tests on a real HA instance.

Uses the same lightweight homeassistant-modules stub the
helpers tests installed (so handler.py's HA-side imports
resolve) plus a small mock ``hass`` with the ``services``,
``data``, and async-call-later surface the handler touches.
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
    BlueprintSchemaDriftBase,
    CodeQualityBase,
)


class _FrozenNow:
    value = datetime(2024, 1, 15, 12, 0, 0)


_stubs = install_homeassistant_stubs(frozen_now=_FrozenNow.value)

# Capture every async_call_later invocation; tests inspect.
_ACL_CALLS: list[tuple[float, Callable[..., Any]]] = []
_ACL_CANCEL_CALLS: list[int] = []


def _async_call_later(
    _hass: Any,
    delay: float,
    cb: Callable[..., Any],
) -> Callable[[], None]:
    handle_index = len(_ACL_CALLS)
    _ACL_CALLS.append((float(delay), cb))

    def _cancel() -> None:
        _ACL_CANCEL_CALLS.append(handle_index)

    return _cancel


_stubs.event.async_call_later = _async_call_later  # type: ignore[attr-defined]

# voluptuous import succeeds because our dev env has it
# (vendored under the test runner). If not, ImportError
# would fail the test loudly -- which is the right
# behaviour, since the handler genuinely needs it.

from custom_components.blueprint_toolkit.trigger_entity_controller import (  # noqa: E402
    handler,
)

# When this file is invoked via ``pytest.main([__file__])``
# (the script's self-invocation path) it ends up loaded
# twice -- once as ``__main__`` and once as the
# test module. ``handler`` is imported during the first
# load, so its ``async_call_later`` binding refers to the
# ``__main__``-side stub; the test methods running under
# the second load inspect the test module's own
# ``_ACL_CALLS`` capture list, which is a different
# list. Re-bind on the handler module now so both sides
# observe the same capture state.
handler.async_call_later = _async_call_later  # type: ignore[attr-defined]


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
    """Stand-in for the IntegrationData on entry.runtime_data."""

    handlers: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class _MockEntry:
    """Stand-in for HA's ConfigEntry. Carries runtime_data."""

    entry_id: str = "mock_entry"
    runtime_data: _MockRuntimeData = field(default_factory=_MockRuntimeData)


@dataclass
class _MockConfigEntries:
    """Stand-in for ``hass.config_entries``.

    The handler's ``_instances`` accessor uses
    ``hass.config_entries.async_entries(DOMAIN)`` to find
    the (single-instance) integration's entry; this mock
    returns whatever entries the test stashed.
    """

    entries: list[_MockEntry] = field(default_factory=list)

    def async_entries(self, _domain: str) -> list[_MockEntry]:
        return self.entries


@dataclass
class _MockHass:
    services: _MockServices = field(default_factory=_MockServices)
    data: dict[str, Any] = field(default_factory=dict)
    config_entries: _MockConfigEntries = field(
        default_factory=_MockConfigEntries,
    )

    def __post_init__(self) -> None:
        # Seed a single config entry so the handler's
        # ``_instances`` accessor finds runtime_data
        # without each test having to wire it up.
        self.config_entries.entries.append(_MockEntry())


def _reset_acl_state() -> None:
    """Reset async_call_later capture state.

    Called at the start of every test that inspects
    ``_ACL_CALLS``. (Pytest-asyncio's auto mode interacts
    badly with autouse fixtures that yield without doing
    work post-yield, so this is plain helper instead.)
    """
    _ACL_CALLS.clear()
    _ACL_CANCEL_CALLS.clear()


def _make_state(
    instance_id: str = "automation.test",
    auto_off_at: datetime | None = None,
    cancel_wakeup: Callable[[], None] | None = None,
) -> handler.TecInstanceState:
    return handler.TecInstanceState(
        instance_id=instance_id,
        auto_off_at=auto_off_at,
        cancel_wakeup=cancel_wakeup,
    )


# --------------------------------------------------------
# Mutator callbacks
# --------------------------------------------------------


class TestOnReload:
    def test_cancels_every_pending_wakeup(self) -> None:
        hass = _MockHass()
        cancelled: list[str] = []

        def _make_canceller(name: str) -> Callable[[], None]:
            return lambda: cancelled.append(name)

        instances = handler._instances(hass)  # type: ignore[arg-type]
        instances["automation.a"] = _make_state(
            "automation.a",
            cancel_wakeup=_make_canceller("a"),
        )
        instances["automation.b"] = _make_state(
            "automation.b",
            cancel_wakeup=None,  # nothing scheduled
        )
        instances["automation.c"] = _make_state(
            "automation.c",
            cancel_wakeup=_make_canceller("c"),
        )

        handler._on_reload(hass)  # type: ignore[arg-type]

        assert sorted(cancelled) == ["a", "c"]
        # Instances themselves stay tracked -- only the
        # cancel handles are cleared.
        assert set(instances) == {
            "automation.a",
            "automation.b",
            "automation.c",
        }
        assert all(s.cancel_wakeup is None for s in instances.values())


class TestOnEntityRemove:
    def test_drops_state_and_cancels_wakeup(self) -> None:
        hass = _MockHass()
        cancelled: list[str] = []
        instances = handler._instances(hass)  # type: ignore[arg-type]
        instances["automation.gone"] = _make_state(
            "automation.gone",
            cancel_wakeup=lambda: cancelled.append("gone"),
        )
        instances["automation.kept"] = _make_state(
            "automation.kept",
        )

        handler._on_entity_remove(hass, "automation.gone")  # type: ignore[arg-type]

        assert "automation.gone" not in instances
        assert "automation.kept" in instances
        assert cancelled == ["gone"]

    def test_no_crash_when_instance_unknown(self) -> None:
        hass = _MockHass()
        # Fresh hass.data; calling on an unknown id should not raise.
        handler._on_entity_remove(  # type: ignore[arg-type]
            hass,
            "automation.never_seen",
        )

    def test_no_crash_when_no_pending_wakeup(self) -> None:
        hass = _MockHass()
        instances = handler._instances(hass)  # type: ignore[arg-type]
        instances["automation.foo"] = _make_state(
            "automation.foo",
            cancel_wakeup=None,
        )
        handler._on_entity_remove(hass, "automation.foo")  # type: ignore[arg-type]
        assert "automation.foo" not in instances


class TestOnEntityRename:
    def test_moves_state_to_new_id(self) -> None:
        hass = _MockHass()
        instances = handler._instances(hass)  # type: ignore[arg-type]
        old_state = _make_state("automation.old")
        instances["automation.old"] = old_state

        handler._on_entity_rename(  # type: ignore[arg-type]
            hass,
            "automation.old",
            "automation.new",
        )

        assert "automation.old" not in instances
        assert instances["automation.new"] is old_state
        assert old_state.instance_id == "automation.new"

    def test_no_crash_when_unknown_old_id(self) -> None:
        hass = _MockHass()
        handler._on_entity_rename(  # type: ignore[arg-type]
            hass,
            "automation.unknown",
            "automation.new",
        )


class TestOnTeardown:
    def test_cancels_all_and_clears_map(self) -> None:
        hass = _MockHass()
        cancelled: list[str] = []
        instances = handler._instances(hass)  # type: ignore[arg-type]
        instances["automation.a"] = _make_state(
            "automation.a",
            cancel_wakeup=lambda: cancelled.append("a"),
        )
        instances["automation.b"] = _make_state(
            "automation.b",
            cancel_wakeup=lambda: cancelled.append("b"),
        )

        handler._on_teardown(hass)  # type: ignore[arg-type]

        assert sorted(cancelled) == ["a", "b"]
        assert instances == {}


# --------------------------------------------------------
# Auto-off scheduling
# --------------------------------------------------------


class TestApplyAutoOffAt:
    def test_arms_wakeup_at_correct_delay(self) -> None:
        _reset_acl_state()
        hass = _MockHass()
        state = _make_state()
        target = _FrozenNow.value + timedelta(minutes=2)

        handler._apply_auto_off_at(hass, state, target)  # type: ignore[arg-type]

        assert state.auto_off_at == target
        assert state.cancel_wakeup is not None
        # async_call_later got called once with delay=120s.
        assert len(_ACL_CALLS) == 1
        delay, _cb = _ACL_CALLS[0]
        assert delay == pytest.approx(120.0)

    def test_clears_pending_wakeup_when_auto_off_none(self) -> None:
        _reset_acl_state()
        hass = _MockHass()
        cancelled: list[bool] = []
        state = _make_state(
            cancel_wakeup=lambda: cancelled.append(True),
            auto_off_at=_FrozenNow.value + timedelta(minutes=5),
        )

        handler._apply_auto_off_at(hass, state, None)  # type: ignore[arg-type]

        assert cancelled == [True]
        assert state.auto_off_at is None
        assert state.cancel_wakeup is None
        assert _ACL_CALLS == []

    def test_replaces_prior_wakeup(self) -> None:
        _reset_acl_state()
        hass = _MockHass()
        cancelled: list[bool] = []
        state = _make_state(
            cancel_wakeup=lambda: cancelled.append(True),
        )
        target = _FrozenNow.value + timedelta(minutes=3)

        handler._apply_auto_off_at(hass, state, target)  # type: ignore[arg-type]

        # Old cancel handle fired before the new one armed.
        assert cancelled == [True]
        assert len(_ACL_CALLS) == 1

    def test_past_target_clamps_delay_to_zero(self) -> None:
        _reset_acl_state()
        hass = _MockHass()
        state = _make_state()
        target = _FrozenNow.value - timedelta(minutes=1)

        handler._apply_auto_off_at(hass, state, target)  # type: ignore[arg-type]

        assert state.auto_off_at == target
        # Delay is clamped to >=0 so async_call_later doesn't
        # get a negative number.
        assert _ACL_CALLS[0][0] == 0.0


# --------------------------------------------------------
# Wakeup closure
# --------------------------------------------------------


class TestMakeWakeup:
    @pytest.mark.asyncio
    async def test_fires_automation_trigger_with_synthetic_timer(
        self,
    ) -> None:
        hass = _MockHass()
        instances = handler._instances(hass)  # type: ignore[arg-type]
        # The wakeup closure looks up its instance by id and
        # bails if absent. Pre-arm a fake state so it
        # proceeds to the service call.
        state = _make_state(
            "automation.foo",
            auto_off_at=_FrozenNow.value + timedelta(minutes=1),
            cancel_wakeup=lambda: None,
        )
        instances["automation.foo"] = state
        wakeup = handler._make_wakeup(hass, "automation.foo")  # type: ignore[arg-type]

        await wakeup(_FrozenNow.value)

        # The closure cleared the cancel handle now that the
        # timer has fired.
        assert state.cancel_wakeup is None
        # And it dispatched automation.trigger with the
        # synthetic TIMER variables payload that the
        # handler's service layer keys on. Variables are
        # flat top-level (NOT nested under ``trigger.*``)
        # because HA's automation.trigger service strips
        # the ``trigger`` key from caller-supplied
        # variables -- see _make_wakeup docstring.
        assert hass.services.calls == [
            (
                "automation",
                "trigger",
                {
                    "entity_id": "automation.foo",
                    "skip_condition": True,
                    "variables": {
                        "trigger_entity_id": (handler._TIMER_TRIGGER_ENTITY_ID),
                        "trigger_to_state": "",
                    },
                },
            ),
        ]
        # Regression guard against re-introducing the
        # ``trigger.*`` namespace: HA strips it, so any
        # value passed there would be silently lost.
        assert "trigger" not in hass.services.calls[0][2]["variables"]

    @pytest.mark.asyncio
    async def test_does_not_propagate_caller_context(
        self,
    ) -> None:
        # Regression guard: the wakeup must NOT pass a
        # ``context=`` kwarg into ``automation.trigger``.
        # If it did, HA's automation runner would inherit
        # the caller's context (the integration setup
        # context) instead of generating a fresh per-run
        # context, which would break logbook attribution
        # of the downstream ``homeassistant.turn_off``.
        hass = _MockHass()
        instances = handler._instances(hass)  # type: ignore[arg-type]
        instances["automation.foo"] = _make_state(
            "automation.foo",
            cancel_wakeup=lambda: None,
        )
        wakeup = handler._make_wakeup(hass, "automation.foo")  # type: ignore[arg-type]

        await wakeup(_FrozenNow.value)

        assert "context" not in hass.services.kwargs[0]

    @pytest.mark.asyncio
    async def test_no_op_when_instance_state_gone(self) -> None:
        hass = _MockHass()
        wakeup = handler._make_wakeup(  # type: ignore[arg-type]
            hass,
            "automation.never_seen",
        )
        # Should be a clean no-op: no service call.
        await wakeup(_FrozenNow.value)
        assert hass.services.calls == []


# --------------------------------------------------------
# Restart-recovery kick payload shape
# --------------------------------------------------------


class TestKickForRecovery:
    @pytest.mark.asyncio
    async def test_emits_synthetic_timer_trigger(self) -> None:
        hass = _MockHass()
        await handler._async_kick_for_recovery(  # type: ignore[arg-type]
            hass,
            "automation.foo",
        )
        assert hass.services.calls == [
            (
                "automation",
                "trigger",
                {
                    "entity_id": "automation.foo",
                    "skip_condition": True,
                    "variables": {
                        "trigger_entity_id": (handler._TIMER_TRIGGER_ENTITY_ID),
                        "trigger_to_state": "",
                    },
                },
            ),
        ]
        # Regression guard against re-introducing the
        # ``trigger.*`` namespace -- see _make_wakeup
        # docstring for why HA strips it.
        assert "trigger" not in hass.services.calls[0][2]["variables"]


# --------------------------------------------------------
# Blueprint <-> schema drift
# --------------------------------------------------------
#
# Catches the failure mode where someone adds / renames a
# blueprint input but forgets to update the vol.Schema (or
# vice-versa). Pyscript-side ``test_blueprint_toolkit.py``
# had the equivalent ``TestBlueprintYamlMatchesRegistry``
# test against ``_BLUEPRINT_SERVICES``; this is the native
# replacement, currently single-port. When a second native
# port lands, hoist into a shared registry + iterate.


class TestBlueprintSchemaDrift(BlueprintSchemaDriftBase):
    """Native blueprint YAML stays in sync with the handler's vol.Schema."""

    handler = handler
    blueprint_filename = "trigger_entity_controller.yaml"


# --------------------------------------------------------
# CodeQuality
# --------------------------------------------------------


class TestCodeQuality(CodeQualityBase):
    ruff_targets = [
        "tests/test_trigger_entity_controller_handler.py",
    ]
    mypy_targets: list[str] = []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", *sys.argv[1:]]))
