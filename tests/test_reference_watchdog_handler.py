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
#     "Jinja2>=3",
# ]
# ///
# This is AI generated code
"""Unit tests for ``reference_watchdog.handler``'s lifecycle code.

Covers the parts that don't require booting HA: mutator
callbacks, ``_ensure_timer`` re-arm sequencing,
``_async_kick_for_recovery`` payload shape, periodic-
callback context-propagation regression tests, and the
blueprint <-> schema drift check. The argparse + service
layers are exercised end-to-end on a real HA host.
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
from conftest import CodeQualityBase  # noqa: E402


class _FrozenNow:
    value = datetime(2026, 4, 28, 23, 0, 0)


_stubs = install_homeassistant_stubs(frozen_now=_FrozenNow.value)

from custom_components.blueprint_toolkit.reference_watchdog import (  # noqa: E402, E501
    handler,
)

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


def _make_state(
    instance_id: str = "automation.rw_test",
    *,
    armed_interval_minutes: int = 0,
    cancel_timer: Callable[[], None] | None = None,
) -> handler.RwInstanceState:
    return handler.RwInstanceState(
        instance_id=instance_id,
        armed_interval_minutes=armed_interval_minutes,
        cancel_timer=cancel_timer,
    )


def _hass_with_instances(
    instances: dict[str, handler.RwInstanceState],
) -> _MockHass:
    h = _MockHass()
    entry = _MockEntry()
    entry.runtime_data.handlers["reference_watchdog"] = {
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
        canceled: list[int] = []

        s1 = _make_state(
            "automation.a",
            armed_interval_minutes=5,
            cancel_timer=lambda: canceled.append(1),
        )
        s2 = _make_state("automation.b", armed_interval_minutes=10)
        h = _hass_with_instances({"automation.a": s1, "automation.b": s2})

        handler._on_reload(h)  # type: ignore[arg-type]

        assert canceled == [1]
        assert s1.cancel_timer is None
        assert s1.armed_interval_minutes == 0
        assert s2.cancel_timer is None
        bucket = h.config_entries.entries[0].runtime_data.handlers[
            "reference_watchdog"
        ]
        assert set(bucket["instances"]) == {"automation.a", "automation.b"}


class TestOnEntityRemove:
    def test_drops_state_and_cancels_timer(self) -> None:
        canceled: list[int] = []
        s = _make_state(
            "automation.a",
            armed_interval_minutes=5,
            cancel_timer=lambda: canceled.append(1),
        )
        h = _hass_with_instances(
            {"automation.a": s, "automation.b": _make_state("automation.b")}
        )

        handler._on_entity_remove(h, "automation.a")  # type: ignore[arg-type]

        assert canceled == [1]
        bucket = h.config_entries.entries[0].runtime_data.handlers[
            "reference_watchdog"
        ]
        assert set(bucket["instances"]) == {"automation.b"}

    def test_unknown_id_is_noop(self) -> None:
        h = _hass_with_instances({"automation.a": _make_state("automation.a")})
        # Should not raise.
        handler._on_entity_remove(h, "automation.unknown")  # type: ignore[arg-type]


class TestOnEntityRename:
    def test_moves_state_to_new_id(self) -> None:
        s = _make_state("automation.old")
        h = _hass_with_instances({"automation.old": s})

        handler._on_entity_rename(h, "automation.old", "automation.new")  # type: ignore[arg-type]

        bucket = h.config_entries.entries[0].runtime_data.handlers[
            "reference_watchdog"
        ]
        assert "automation.old" not in bucket["instances"]
        assert bucket["instances"]["automation.new"] is s
        assert s.instance_id == "automation.new"

    def test_unknown_old_id_is_noop(self) -> None:
        h = _hass_with_instances({})
        # Should not raise.
        handler._on_entity_rename(h, "automation.x", "automation.y")  # type: ignore[arg-type]


class TestOnTeardown:
    def test_cancels_all_and_clears(self) -> None:
        canceled: list[int] = []
        s1 = _make_state(
            "automation.a", cancel_timer=lambda: canceled.append(1)
        )
        s2 = _make_state(
            "automation.b", cancel_timer=lambda: canceled.append(2)
        )
        h = _hass_with_instances({"automation.a": s1, "automation.b": s2})

        handler._on_teardown(h)  # type: ignore[arg-type]

        assert sorted(canceled) == [1, 2]
        bucket = h.config_entries.entries[0].runtime_data.handlers[
            "reference_watchdog"
        ]
        assert bucket["instances"] == {}


# --------------------------------------------------------
# _ensure_timer
# --------------------------------------------------------


class TestEnsureTimer:
    def setup_method(self) -> None:
        # Capture the args ``schedule_periodic_with_jitter``
        # is called with, return a stub unsub.
        self.calls: list[dict[str, Any]] = []
        self.unsub_called: list[int] = []

        def _fake_schedule(
            _hass: Any,
            *,
            interval: timedelta,
            instance_id: str,
            action: Any,
        ) -> Callable[[], None]:
            handle_index = len(self.calls)
            self.calls.append(
                {
                    "interval": interval,
                    "instance_id": instance_id,
                    "action": action,
                }
            )

            def _unsub() -> None:
                self.unsub_called.append(handle_index)

            return _unsub

        self._real_schedule = handler.schedule_periodic_with_jitter
        handler.schedule_periodic_with_jitter = _fake_schedule  # type: ignore[assignment]

    def teardown_method(self) -> None:
        handler.schedule_periodic_with_jitter = self._real_schedule  # type: ignore[assignment]

    def test_first_call_arms(self) -> None:
        h = _hass_with_instances({})
        s = _make_state("automation.rw")

        handler._ensure_timer(h, s, 5)  # type: ignore[arg-type]

        assert len(self.calls) == 1
        assert self.calls[0]["interval"] == timedelta(minutes=5)
        assert self.calls[0]["instance_id"] == "automation.rw"
        assert s.armed_interval_minutes == 5
        assert s.cancel_timer is not None

    def test_same_interval_does_not_re_arm(self) -> None:
        h = _hass_with_instances({})
        s = _make_state("automation.rw")
        handler._ensure_timer(h, s, 5)  # type: ignore[arg-type]
        handler._ensure_timer(h, s, 5)  # type: ignore[arg-type]

        # Only one schedule call; previous timer was NOT
        # cancelled.
        assert len(self.calls) == 1
        assert self.unsub_called == []

    def test_changed_interval_re_arms(self) -> None:
        h = _hass_with_instances({})
        s = _make_state("automation.rw")
        handler._ensure_timer(h, s, 5)  # type: ignore[arg-type]
        handler._ensure_timer(h, s, 10)  # type: ignore[arg-type]

        # Previous unsub fired; new schedule call recorded.
        assert self.unsub_called == [0]
        assert len(self.calls) == 2
        assert self.calls[1]["interval"] == timedelta(minutes=10)
        assert s.armed_interval_minutes == 10


# --------------------------------------------------------
# Argparse: multi-line exclude_entity_regex
# --------------------------------------------------------
#
# Regression guard for the user-reported bug where
# multi-line ``exclude_entity_regex`` input silently
# matched nothing because the whole string (with literal
# ``\n`` chars) was fed to ``re.search``. Argparse must
# split on newlines and join valid lines with ``|`` --
# delegated to ``helpers.validate_and_join_regex_patterns``.
# Unit tests for the helper itself live in
# ``test_helpers_lifecycle.py``; this class verifies
# argparse actually wires the helper in.


@dataclass
class _ArgparseCapture:
    """Records the kwargs passed into ``_async_service_layer``.

    Lets us assert what the post-argparse joined
    ``exclude_entity_regex`` looks like without booting
    the full service-layer executor path.
    """

    calls: list[dict[str, Any]] = field(default_factory=list)

    async def __call__(self, _hass: Any, _call: Any, **kwargs: Any) -> None:
        self.calls.append(kwargs)


class _FakeServiceCall:
    """Bare-minimum ServiceCall shape ``_async_argparse`` reads."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.context = None


def _valid_argparse_payload(**overrides: Any) -> dict[str, Any]:
    """Return a schema-valid raw payload with optional overrides."""
    payload = {
        "instance_id": "automation.rw_test",
        "trigger_id": "manual",
        "exclude_paths_raw": "",
        "exclude_integrations_raw": [],
        "exclude_entities_raw": [],
        "exclude_entity_regex_raw": "",
        "check_disabled_entities_raw": False,
        "check_interval_minutes_raw": 60,
        "max_source_notifications_raw": 0,
        "debug_logging_raw": False,
    }
    payload.update(overrides)
    return payload


class TestArgparseMultilineRegex:
    def setup_method(self) -> None:
        # Replace the service-layer entry point with a
        # capture stub so argparse runs in isolation.
        self.capture = _ArgparseCapture()
        self._real_service_layer = handler._async_service_layer
        handler._async_service_layer = self.capture  # type: ignore[assignment]
        # Replace the emit-config-error helper with a
        # capture-list so we can inspect the per-instance
        # error notifications without booting HA's
        # persistent_notification surface.
        self.config_errors: list[list[str]] = []

        async def _capture_errors(
            _hass: Any,
            _instance_id: str,
            errors: list[str],
        ) -> None:
            self.config_errors.append(errors)

        self._real_emit = handler._emit_config_error
        handler._emit_config_error = _capture_errors  # type: ignore[assignment]

    def teardown_method(self) -> None:
        handler._async_service_layer = self._real_service_layer  # type: ignore[assignment]
        handler._emit_config_error = self._real_emit  # type: ignore[assignment]

    def test_multiline_regex_joined_with_pipe(self) -> None:
        # The bug the user hit: two patterns on separate
        # lines must reach the service layer as a single
        # alternation regex.
        import asyncio

        h = _MockHass()
        call = _FakeServiceCall(
            _valid_argparse_payload(
                exclude_entity_regex_raw=(
                    "sensor\\.loft_humidifier_energy\n"
                    "sensor\\.office_humidifier_energy"
                ),
            ),
        )
        asyncio.run(handler._async_argparse(h, call))  # type: ignore[arg-type]

        assert self.config_errors == [[]], (
            f"argparse should produce no errors; got {self.config_errors}"
        )
        assert len(self.capture.calls) == 1
        joined = self.capture.calls[0]["exclude_entity_regex"]
        assert (
            joined == "sensor\\.loft_humidifier_energy"
            "|sensor\\.office_humidifier_energy"
        )

    def test_invalid_regex_per_line_surfaces_error(self) -> None:
        # ``[invalid`` fails ``re.compile``; ``foo`` is a
        # legitimate valid pattern. The bad line surfaces
        # in the error list while the good one would have
        # been used had argparse continued -- but since
        # any error short-circuits the dispatch, the
        # service layer doesn't run at all.
        import asyncio

        h = _MockHass()
        call = _FakeServiceCall(
            _valid_argparse_payload(
                exclude_entity_regex_raw="foo\n[invalid",
            ),
        )
        asyncio.run(handler._async_argparse(h, call))  # type: ignore[arg-type]

        assert self.capture.calls == [], (
            "service layer must NOT run when argparse has errors"
        )
        assert len(self.config_errors) == 1
        errors = self.config_errors[0]
        # The invalid line surfaces; the valid line does
        # not (no error needed for it).
        joined_msg = "\n".join(errors)
        assert "[invalid" in joined_msg
        assert "exclude_entity_regex" in joined_msg

    def test_match_all_pattern_rejected(self) -> None:
        import asyncio

        h = _MockHass()
        call = _FakeServiceCall(
            _valid_argparse_payload(exclude_entity_regex_raw=".*"),
        )
        asyncio.run(handler._async_argparse(h, call))  # type: ignore[arg-type]

        assert self.capture.calls == []
        assert len(self.config_errors) == 1
        assert any("matches empty string" in e for e in self.config_errors[0])

    def test_empty_field_is_fine(self) -> None:
        import asyncio

        h = _MockHass()
        call = _FakeServiceCall(_valid_argparse_payload())
        asyncio.run(handler._async_argparse(h, call))  # type: ignore[arg-type]

        assert self.config_errors == [[]]
        assert len(self.capture.calls) == 1
        assert self.capture.calls[0]["exclude_entity_regex"] == ""


# --------------------------------------------------------
# Restart-recovery kick payload
# --------------------------------------------------------


class TestKickForRecovery:
    def test_emits_manual_trigger(self) -> None:
        import asyncio

        h = _MockHass()
        asyncio.run(
            handler._async_kick_for_recovery(h, "automation.rw")  # type: ignore[arg-type]
        )

        assert len(h.services.calls) == 1
        domain, name, data = h.services.calls[0]
        assert (domain, name) == ("automation", "trigger")
        assert data["entity_id"] == "automation.rw"
        assert data["skip_condition"] is True
        # Flat top-level variable, NOT under ``trigger.*``
        # -- HA's automation.trigger service strips the
        # ``trigger`` key.
        assert data["variables"] == {"trigger_id": "manual"}
        assert "trigger" not in data["variables"]

    def test_does_not_propagate_caller_context(self) -> None:
        # Regression guard: ``automation.trigger`` MUST NOT
        # carry a ``context=`` kwarg. If it did, HA's
        # automation runner would inherit the caller's
        # context (the integration setup context) instead
        # of generating a fresh per-run context, which
        # would break logbook attribution of the
        # downstream service calls.
        import asyncio

        h = _MockHass()
        asyncio.run(
            handler._async_kick_for_recovery(h, "automation.rw")  # type: ignore[arg-type]
        )
        assert len(h.services.kwargs) == 1
        assert "context" not in h.services.kwargs[0]


class TestPeriodicCallback:
    def test_does_not_propagate_caller_context(self) -> None:
        # Same regression guard for the integration-owned
        # periodic timer's ``automation.trigger`` call.
        import asyncio

        s = _make_state("automation.rw")
        h = _hass_with_instances({"automation.rw": s})

        cb = handler._make_periodic_callback(h, "automation.rw")  # type: ignore[arg-type]
        asyncio.run(cb(_FrozenNow.value))

        assert len(h.services.kwargs) == 1
        assert "context" not in h.services.kwargs[0]
        # And ``trigger_id`` must be "periodic" so the
        # service handler can distinguish integration-fired
        # ticks from manual invocations. Flat (NOT under
        # ``trigger.*``); HA strips that key.
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


# --------------------------------------------------------
# Schema vs blueprint drift
# --------------------------------------------------------

import voluptuous as vol  # noqa: E402
import yaml  # noqa: E402


class _PermissiveBlueprintLoader(yaml.SafeLoader):
    """SafeLoader that ignores the ``!input`` tag.

    Blueprint YAML uses ``!input <name>`` to interpolate
    inputs at load time; under PyYAML's SafeLoader that's
    an unrecognised tag and load() raises. The test only
    cares about the keys, so the tag value just becomes a
    marker.
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
                "reference_watchdog.yaml"
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
        "tests/test_reference_watchdog_handler.py",
    ]
    mypy_targets: list[str] = []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", *sys.argv[1:]]))
