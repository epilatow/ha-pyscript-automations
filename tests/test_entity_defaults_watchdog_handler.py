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
"""Unit tests for ``entity_defaults_watchdog.handler``.

Covers the parts that don't require booting HA: mutator
callbacks, ``_ensure_timer`` re-arm sequencing,
``_async_kick_for_recovery`` payload shape, periodic-
callback context-propagation regression tests, argparse
field validation (``drift_checks`` cross-validation,
multi-line regex helper delegation, schema-level int
rejection), and the blueprint <-> schema drift check. The
service layer's full build-and-apply loop is exercised
in ``test_entity_defaults_watchdog_integration.py``
against the pytest-HACC harness.
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
    value = datetime(2026, 4, 28, 23, 0, 0)


_stubs = install_homeassistant_stubs(frozen_now=_FrozenNow.value)

from custom_components.blueprint_toolkit.entity_defaults_watchdog import (  # noqa: E402, E501
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
    instance_id: str = "automation.edw_test",
    *,
    armed_interval_minutes: int = 0,
    cancel_timer: Callable[[], None] | None = None,
) -> handler.EdwInstanceState:
    return handler.EdwInstanceState(
        instance_id=instance_id,
        armed_interval_minutes=armed_interval_minutes,
        cancel_timer=cancel_timer,
    )


def _hass_with_instances(
    instances: dict[str, handler.EdwInstanceState],
) -> _MockHass:
    h = _MockHass()
    entry = _MockEntry()
    entry.runtime_data.handlers["entity_defaults_watchdog"] = {
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
            "entity_defaults_watchdog"
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
            "entity_defaults_watchdog"
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
            "entity_defaults_watchdog"
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
            "entity_defaults_watchdog"
        ]
        assert bucket["instances"] == {}


# --------------------------------------------------------
# _ensure_timer
# --------------------------------------------------------


class TestEnsureTimer:
    def setup_method(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.unsub_called: list[int] = []

        def _fake_schedule(
            _hass: Any,
            entry: Any,
            *,
            interval: timedelta,
            instance_id: str,
            action: Any,
        ) -> Callable[[], None]:
            handle_index = len(self.calls)
            self.calls.append(
                {
                    "entry": entry,
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
        s = _make_state("automation.edw")
        e = object()

        handler._ensure_timer(h, e, s, 5)  # type: ignore[arg-type]

        assert len(self.calls) == 1
        assert self.calls[0]["entry"] is e
        assert self.calls[0]["interval"] == timedelta(minutes=5)
        assert self.calls[0]["instance_id"] == "automation.edw"
        assert s.armed_interval_minutes == 5
        assert s.cancel_timer is not None

    def test_same_interval_does_not_re_arm(self) -> None:
        h = _hass_with_instances({})
        s = _make_state("automation.edw")
        e = object()
        handler._ensure_timer(h, e, s, 5)  # type: ignore[arg-type]
        handler._ensure_timer(h, e, s, 5)  # type: ignore[arg-type]

        assert len(self.calls) == 1
        assert self.unsub_called == []

    def test_changed_interval_re_arms(self) -> None:
        h = _hass_with_instances({})
        s = _make_state("automation.edw")
        e = object()
        handler._ensure_timer(h, e, s, 5)  # type: ignore[arg-type]
        handler._ensure_timer(h, e, s, 10)  # type: ignore[arg-type]

        assert self.unsub_called == [0]
        assert len(self.calls) == 2
        assert self.calls[1]["interval"] == timedelta(minutes=10)
        assert s.armed_interval_minutes == 10


# --------------------------------------------------------
# Argparse harness
# --------------------------------------------------------


@dataclass
class _ArgparseCapture:
    """Records the kwargs passed into ``_async_service_layer``."""

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
        "instance_id": "automation.edw_test",
        "trigger_id": "manual",
        "drift_checks_raw": [],
        "include_integrations_raw": [],
        "exclude_integrations_raw": [],
        "device_exclude_regex_raw": "",
        "exclude_entities_raw": [],
        "entity_id_exclude_regex_raw": "",
        "entity_name_exclude_regex_raw": "",
        "check_interval_minutes_raw": 5,
        "max_device_notifications_raw": 0,
        "debug_logging_raw": False,
    }
    payload.update(overrides)
    return payload


class _ArgparseHarness:
    """Shared setup/teardown for argparse-only tests.

    Subclasses inherit ``setup_method`` / ``teardown_method``
    so each test gets a fresh ``_ArgparseCapture`` and a
    fresh ``config_errors`` capture list. The handler-side
    ``_async_service_layer`` and ``_emit_config_error``
    references are restored on teardown so cross-test
    pollution is impossible.
    """

    def setup_method(self) -> None:
        self.capture = _ArgparseCapture()
        self._real_service_layer = handler._async_service_layer
        handler._async_service_layer = self.capture  # type: ignore[assignment]
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


# --------------------------------------------------------
# Argparse: drift_checks cross-validation
# --------------------------------------------------------


class TestArgparseDriftChecks(_ArgparseHarness):
    def test_empty_defaults_to_all_checks(self) -> None:
        import asyncio

        h = _MockHass()
        call = _FakeServiceCall(_valid_argparse_payload(drift_checks_raw=[]))
        asyncio.run(handler._async_argparse(h, call, now=_FrozenNow.value))  # type: ignore[arg-type]

        assert self.config_errors == [[]]
        assert len(self.capture.calls) == 1
        # Empty input -> CHECK_ALL forwarded to the service
        # layer (mirrors the blueprint description that
        # documents empty-means-all).
        from custom_components.blueprint_toolkit.entity_defaults_watchdog import (  # noqa: E402, E501, PLC0415
            logic,
        )

        assert self.capture.calls[0]["drift_checks"] == logic.CHECK_ALL

    def test_unknown_value_emits_error(self) -> None:
        import asyncio

        h = _MockHass()
        call = _FakeServiceCall(
            _valid_argparse_payload(
                drift_checks_raw=["device-entity-id", "bogus-check"],
            ),
        )
        asyncio.run(handler._async_argparse(h, call, now=_FrozenNow.value))  # type: ignore[arg-type]

        assert self.capture.calls == [], (
            "service layer must NOT run when drift_checks has unknowns"
        )
        assert len(self.config_errors) == 1
        joined = "\n".join(self.config_errors[0])
        assert "drift_checks" in joined
        assert "bogus-check" in joined

    def test_valid_subset_passes_through(self) -> None:
        import asyncio

        h = _MockHass()
        call = _FakeServiceCall(
            _valid_argparse_payload(
                drift_checks_raw=["device-entity-id"],
            ),
        )
        asyncio.run(handler._async_argparse(h, call, now=_FrozenNow.value))  # type: ignore[arg-type]

        assert self.config_errors == [[]]
        assert self.capture.calls[0]["drift_checks"] == frozenset(
            {"device-entity-id"}
        )


# --------------------------------------------------------
# Argparse: multi-line regex fields
# --------------------------------------------------------
#
# EDW has THREE multi-line regex inputs
# (device_exclude_regex, entity_id_exclude_regex,
# entity_name_exclude_regex). Each is split on newlines and
# joined with ``|`` so two patterns on separate lines reach
# the service layer as a single alternation regex. The
# split/join + per-line validation lives in the shared
# ``helpers.validate_and_join_regex_patterns``;
# parser-semantic tests for the helper itself live in
# ``test_helpers_lifecycle.py``
# (``TestValidateAndJoinRegexPatterns``). This class only
# verifies the handler-side wiring: that argparse delegates
# to the helper for every regex field and that helper-level
# errors surface as a config-error notification.


class TestArgparseMultilineRegex(_ArgparseHarness):
    def test_all_three_regex_fields_join_with_pipe(self) -> None:
        import asyncio

        h = _MockHass()
        call = _FakeServiceCall(
            _valid_argparse_payload(
                device_exclude_regex_raw="^Stale-Hub\nold-hub$",
                entity_id_exclude_regex_raw="sensor\\.foo\nsensor\\.bar",
                entity_name_exclude_regex_raw="^Custom .*\nKeep this",
            ),
        )
        asyncio.run(handler._async_argparse(h, call, now=_FrozenNow.value))  # type: ignore[arg-type]

        assert self.config_errors == [[]]
        assert len(self.capture.calls) == 1
        kw = self.capture.calls[0]
        assert kw["device_exclude_regex"] == "^Stale-Hub|old-hub$"
        assert kw["entity_id_exclude_regex"] == "sensor\\.foo|sensor\\.bar"
        assert kw["entity_name_exclude_regex"] == "^Custom .*|Keep this"

    def test_helper_errors_emit_config_error_notification(self) -> None:
        # Wiring check: when the shared helper returns
        # errors, argparse short-circuits dispatch and
        # surfaces them as a config-error notification.
        # The exact errors (which lines fail, why) are
        # parser semantics covered by
        # ``TestValidateAndJoinRegexPatterns``.
        import asyncio

        h = _MockHass()
        call = _FakeServiceCall(
            _valid_argparse_payload(
                entity_id_exclude_regex_raw="foo\n[invalid",
            ),
        )
        asyncio.run(handler._async_argparse(h, call, now=_FrozenNow.value))  # type: ignore[arg-type]

        assert self.capture.calls == [], (
            "service layer must NOT run when argparse has errors"
        )
        assert len(self.config_errors) == 1
        assert self.config_errors[0], "expected a non-empty error list"

    def test_all_empty_fields_pass_through_clean(self) -> None:
        import asyncio

        h = _MockHass()
        call = _FakeServiceCall(_valid_argparse_payload())
        asyncio.run(handler._async_argparse(h, call, now=_FrozenNow.value))  # type: ignore[arg-type]

        assert self.config_errors == [[]]
        assert len(self.capture.calls) == 1
        kw = self.capture.calls[0]
        assert kw["device_exclude_regex"] == ""
        assert kw["entity_id_exclude_regex"] == ""
        assert kw["entity_name_exclude_regex"] == ""

    def test_argparse_delegates_to_shared_regex_helper(self) -> None:
        """Lock in that argparse delegates regex parsing to
        ``helpers.validate_and_join_regex_patterns``.

        Why this matters: re-implementing multi-line regex
        parsing inline would silently lose the helper's
        guarantees (per-line ``re.compile`` validation,
        ``.*``-rejection, alternation join, empty-line
        drop). If a future refactor moves off the helper,
        this test fires and forces the maintainer to
        choose: (a) restore the call-through, or (b)
        re-implement equivalent guarantees inline -- see
        ``TestValidateAndJoinRegexPatterns`` in
        ``test_helpers_lifecycle.py`` for the full
        contract.

        EDW has three regex fields, so argparse should call
        the helper at least once per non-empty field; we
        only assert the spy was called (and let
        TestArgparseMultilineRegex above cover the
        per-field output shape).
        """
        import asyncio

        spy_calls: list[tuple[Any, ...]] = []
        real = handler.validate_and_join_regex_patterns

        def _spy(*args: Any, **kwargs: Any) -> Any:
            spy_calls.append(args)
            return real(*args, **kwargs)

        handler.validate_and_join_regex_patterns = _spy  # type: ignore[assignment]
        try:
            h = _MockHass()
            call = _FakeServiceCall(
                _valid_argparse_payload(
                    device_exclude_regex_raw="foo\nbar",
                    entity_id_exclude_regex_raw="baz",
                    entity_name_exclude_regex_raw="qux",
                ),
            )
            asyncio.run(handler._async_argparse(h, call, now=_FrozenNow.value))  # type: ignore[arg-type]
        finally:
            handler.validate_and_join_regex_patterns = real  # type: ignore[assignment]

        assert spy_calls, (
            "argparse must call helpers.validate_and_join_regex_patterns "
            "-- see this test's docstring for the contract"
        )


# --------------------------------------------------------
# Argparse: int-input rejection (schema-level)
# --------------------------------------------------------
#
# Schema-level validation:
# ``vol.All(vol.Coerce(int), vol.Range(min=..., max=...))``
# rejects non-numeric and out-of-range integers; rejections
# flow through ``vol.MultipleInvalid`` and surface as a
# config-error notification carrying the offending field
# name (the ``schema:`` prefix the helper prepends).


class TestArgparseSlugListValidation(_ArgparseHarness):
    def test_bad_shape_integration_rejected(self) -> None:
        # Defense-in-depth: slug-shape validation rejects
        # mis-cased / hyphenated values that HA's
        # integration-id charset would never produce.
        import asyncio

        h = _MockHass()
        call = _FakeServiceCall(
            _valid_argparse_payload(
                include_integrations_raw=["zwave-js"],
            ),
        )
        asyncio.run(handler._async_argparse(h, call, now=_FrozenNow.value))  # type: ignore[arg-type]

        assert self.capture.calls == []
        assert len(self.config_errors) == 1
        joined = "\n".join(self.config_errors[0])
        assert "include_integrations_raw" in joined


class TestArgparseIntValidation(_ArgparseHarness):
    def test_non_numeric_check_interval_minutes_rejected(self) -> None:
        import asyncio

        h = _MockHass()
        call = _FakeServiceCall(
            _valid_argparse_payload(
                check_interval_minutes_raw="not-a-number",
            ),
        )
        asyncio.run(handler._async_argparse(h, call, now=_FrozenNow.value))  # type: ignore[arg-type]

        assert self.capture.calls == [], (
            "service layer must NOT run when schema rejects an input"
        )
        assert len(self.config_errors) == 1
        joined = "\n".join(self.config_errors[0])
        assert "check_interval_minutes_raw" in joined
        # ``vol.Coerce(int)`` produces this phrasing on
        # bad-int input; if voluptuous changes the message
        # in the future this assertion may need a softer
        # match.
        assert "expected int" in joined

    def test_out_of_range_max_device_notifications_rejected(self) -> None:
        import asyncio

        h = _MockHass()
        call = _FakeServiceCall(
            _valid_argparse_payload(
                max_device_notifications_raw=9999,
            ),
        )
        asyncio.run(handler._async_argparse(h, call, now=_FrozenNow.value))  # type: ignore[arg-type]

        assert self.capture.calls == []
        assert len(self.config_errors) == 1
        joined = "\n".join(self.config_errors[0])
        assert "max_device_notifications_raw" in joined
        # ``vol.Range(min=0, max=1000)`` -> "value must be
        # at most 1000".
        assert "at most 1000" in joined


# --------------------------------------------------------
# Restart-recovery kick payload
# --------------------------------------------------------


class TestKickForRecovery:
    def test_emits_manual_trigger(self) -> None:
        import asyncio

        h = _MockHass()
        asyncio.run(
            handler._async_kick_for_recovery(h, "automation.edw")  # type: ignore[arg-type]
        )

        assert len(h.services.calls) == 1
        domain, name, data = h.services.calls[0]
        assert (domain, name) == ("automation", "trigger")
        assert data["entity_id"] == "automation.edw"
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
            handler._async_kick_for_recovery(h, "automation.edw")  # type: ignore[arg-type]
        )
        assert len(h.services.kwargs) == 1
        assert "context" not in h.services.kwargs[0]


class TestPeriodicCallback:
    def test_does_not_propagate_caller_context(self) -> None:
        # Same regression guard for the integration-owned
        # periodic timer's ``automation.trigger`` call.
        import asyncio

        s = _make_state("automation.edw")
        h = _hass_with_instances({"automation.edw": s})

        cb = handler._make_periodic_callback(h, "automation.edw")  # type: ignore[arg-type]
        asyncio.run(cb(_FrozenNow.value))

        assert len(h.services.kwargs) == 1
        assert "context" not in h.services.kwargs[0]
        # ``trigger_id`` must be "periodic" so the service
        # handler can distinguish integration-fired ticks
        # from manual invocations. Flat (NOT under
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

    def test_callback_swallows_automation_trigger_failure(self) -> None:
        """A failing ``automation.trigger`` (e.g. the
        automation entity was deleted between scheduling
        and firing) must not propagate out of the timer
        callback. Defence-in-depth: a single failed tick is
        a self-healing transient -- the next tick fires
        anyway.
        """
        import asyncio

        s = _make_state("automation.edw")
        h = _hass_with_instances({"automation.edw": s})

        async def _raise(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("automation gone")

        h.services.async_call = _raise  # type: ignore[assignment]
        cb = handler._make_periodic_callback(h, "automation.edw")  # type: ignore[arg-type]

        # Should not raise.
        asyncio.run(cb(_FrozenNow.value))


# --------------------------------------------------------
# Schema vs blueprint drift
# --------------------------------------------------------


class TestBlueprintSchemaDrift(BlueprintSchemaDriftBase):
    """The blueprint's ``data:`` keys must match the schema."""

    handler = handler
    blueprint_filename = "entity_defaults_watchdog.yaml"


class TestBlueprintDefaultsRoundTrip(BlueprintDefaultsRoundTripBase):
    """Blueprint input defaults must satisfy the schema."""

    handler = handler
    blueprint_filename = "entity_defaults_watchdog.yaml"
    template_defaults = {
        "instance_id": "automation.edw_default_check",
        "trigger_id": "manual",
    }


class TestArgparseGuards(HandlerArgparseGuardsBase):
    """Schema rejection / unregistered notify must short-circuit argparse."""

    handler = handler
    valid_payload = _valid_argparse_payload()


class TestCodeQuality(CodeQualityBase):
    ruff_targets = [
        "custom_components/blueprint_toolkit/entity_defaults_watchdog/handler.py",
        "tests/test_entity_defaults_watchdog_handler.py",
    ]
    mypy_targets: list[str] = []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", *sys.argv[1:]]))
