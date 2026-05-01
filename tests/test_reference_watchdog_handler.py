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
from datetime import timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import pytest  # noqa: E402
from _handler_stubs import install_homeassistant_stubs  # noqa: E402
from _handler_test_base import (  # noqa: E402
    ArgparseCapture,
    FakeServiceCall,
    FrozenNow,
    MockEntry,
    MockHass,
)
from conftest import (  # noqa: E402
    BlueprintDefaultsRoundTripBase,
    BlueprintSchemaDriftBase,
    CodeQualityBase,
    HandlerArgparseGuardsBase,
)

_stubs = install_homeassistant_stubs(frozen_now=FrozenNow.value)

from custom_components.blueprint_toolkit.reference_watchdog import (  # noqa: E402, E501
    handler,
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
) -> MockHass:
    h = MockHass()
    entry = MockEntry()
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
        s = _make_state("automation.rw")
        e = object()

        handler._ensure_timer(h, e, s, 5)  # type: ignore[arg-type]

        assert len(self.calls) == 1
        assert self.calls[0]["entry"] is e
        assert self.calls[0]["interval"] == timedelta(minutes=5)
        assert self.calls[0]["instance_id"] == "automation.rw"
        assert s.armed_interval_minutes == 5
        assert s.cancel_timer is not None

    def test_same_interval_does_not_re_arm(self) -> None:
        h = _hass_with_instances({})
        s = _make_state("automation.rw")
        e = object()
        handler._ensure_timer(h, e, s, 5)  # type: ignore[arg-type]
        handler._ensure_timer(h, e, s, 5)  # type: ignore[arg-type]

        # Only one schedule call; previous timer was NOT
        # cancelled.
        assert len(self.calls) == 1
        assert self.unsub_called == []

    def test_changed_interval_re_arms(self) -> None:
        h = _hass_with_instances({})
        s = _make_state("automation.rw")
        e = object()
        handler._ensure_timer(h, e, s, 5)  # type: ignore[arg-type]
        handler._ensure_timer(h, e, s, 10)  # type: ignore[arg-type]

        # Previous unsub fired; new schedule call recorded.
        assert self.unsub_called == [0]
        assert len(self.calls) == 2
        assert self.calls[1]["interval"] == timedelta(minutes=10)
        assert s.armed_interval_minutes == 10


# --------------------------------------------------------
# Argparse: multi-line exclude_entity_regex
# --------------------------------------------------------
#
# Argparse splits multi-line ``exclude_entity_regex`` input
# on newlines and joins valid lines with ``|`` so two
# patterns on separate lines reach the service layer as a
# single alternation regex (instead of being passed verbatim
# as a multi-line string ``re.search`` would silently match
# nothing on). The split/join + per-line validation lives in
# the shared ``helpers.validate_and_join_regex_patterns``;
# parser-semantic tests for that helper live in
# ``test_helpers_lifecycle.py`` (``TestValidateAndJoinRegexPatterns``).
# This class only verifies the handler-side wiring: that
# argparse delegates to the helper and that helper-level
# errors surface as a config-error notification.


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
        self.capture = ArgparseCapture()
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

        h = MockHass()
        call = FakeServiceCall(
            _valid_argparse_payload(
                exclude_entity_regex_raw=(
                    "sensor\\.loft_humidifier_energy\n"
                    "sensor\\.office_humidifier_energy"
                ),
            ),
        )
        asyncio.run(handler._async_argparse(h, call, now=FrozenNow.value))  # type: ignore[arg-type]

        assert self.config_errors == [[]], (
            f"argparse should produce no errors; got {self.config_errors}"
        )
        assert len(self.capture.calls) == 1
        joined = self.capture.calls[0]["exclude_entity_regex"]
        assert (
            joined == "sensor\\.loft_humidifier_energy"
            "|sensor\\.office_humidifier_energy"
        )

    def test_helper_errors_emit_config_error_notification(self) -> None:
        # Wiring check: when the shared helper returns
        # errors, argparse short-circuits dispatch and
        # surfaces them as a config-error notification.
        # The exact errors (which lines fail, why) are
        # parser semantics covered by
        # ``TestValidateAndJoinRegexPatterns``.
        import asyncio

        h = MockHass()
        call = FakeServiceCall(
            _valid_argparse_payload(
                exclude_entity_regex_raw="foo\n[invalid",
            ),
        )
        asyncio.run(handler._async_argparse(h, call, now=FrozenNow.value))  # type: ignore[arg-type]

        assert self.capture.calls == [], (
            "service layer must NOT run when argparse has errors"
        )
        assert len(self.config_errors) == 1
        assert self.config_errors[0], "expected a non-empty error list"

    def test_empty_field_is_fine(self) -> None:
        import asyncio

        h = MockHass()
        call = FakeServiceCall(_valid_argparse_payload())
        asyncio.run(handler._async_argparse(h, call, now=FrozenNow.value))  # type: ignore[arg-type]

        assert self.config_errors == [[]]
        assert len(self.capture.calls) == 1
        assert self.capture.calls[0]["exclude_entity_regex"] == ""

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
        ``test_helpers_lifecycle.py`` for the full contract.
        """
        import asyncio

        spy_calls: list[tuple[Any, ...]] = []
        real = handler.validate_and_join_regex_patterns

        def _spy(*args: Any, **kwargs: Any) -> Any:
            spy_calls.append(args)
            return real(*args, **kwargs)

        handler.validate_and_join_regex_patterns = _spy  # type: ignore[assignment]
        try:
            h = MockHass()
            call = FakeServiceCall(
                _valid_argparse_payload(
                    exclude_entity_regex_raw="foo\nbar",
                ),
            )
            asyncio.run(handler._async_argparse(h, call, now=FrozenNow.value))  # type: ignore[arg-type]
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
# The schema uses ``vol.All(vol.Coerce(int), vol.Range(...))``
# for integer inputs; rejections flow through
# ``vol.MultipleInvalid`` and surface via the ``schema:``
# prefix in the emit-config-error call. These tests cover
# user-facing error messages for non-numeric and
# out-of-range integer inputs.


class TestArgparseSlugListValidation:
    def setup_method(self) -> None:
        self.capture = ArgparseCapture()
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

    def test_bad_shape_integration_rejected(self) -> None:
        # Defense-in-depth: slug-shape validation rejects
        # mis-cased / hyphenated values that HA's
        # integration-id charset would never produce.
        import asyncio

        h = MockHass()
        call = FakeServiceCall(
            _valid_argparse_payload(
                exclude_integrations_raw=["zwave-js"],
            ),
        )
        asyncio.run(handler._async_argparse(h, call, now=FrozenNow.value))  # type: ignore[arg-type]

        assert self.capture.calls == []
        assert len(self.config_errors) == 1
        joined = "\n".join(self.config_errors[0])
        assert "exclude_integrations_raw" in joined


class TestArgparseIntValidation:
    def setup_method(self) -> None:
        # Re-use the same capture pattern
        # ``TestArgparseMultilineRegex`` uses.
        self.capture = ArgparseCapture()
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

    def test_non_numeric_check_interval_minutes_rejected(self) -> None:
        import asyncio

        h = MockHass()
        call = FakeServiceCall(
            _valid_argparse_payload(
                check_interval_minutes_raw="not-a-number",
            ),
        )
        asyncio.run(handler._async_argparse(h, call, now=FrozenNow.value))  # type: ignore[arg-type]

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

    def test_out_of_range_max_source_notifications_rejected(self) -> None:
        import asyncio

        h = MockHass()
        call = FakeServiceCall(
            _valid_argparse_payload(
                max_source_notifications_raw=9999,
            ),
        )
        asyncio.run(handler._async_argparse(h, call, now=FrozenNow.value))  # type: ignore[arg-type]

        assert self.capture.calls == []
        assert len(self.config_errors) == 1
        joined = "\n".join(self.config_errors[0])
        assert "max_source_notifications_raw" in joined
        # ``vol.Range(min=0, max=1000)`` -> "value must be
        # at most 1000".
        assert "at most 1000" in joined


# --------------------------------------------------------
# Restart-recovery kick payload
# --------------------------------------------------------


class TestKickWiring:
    def test_spec_kick_variables_match(self) -> None:
        assert handler._SPEC.kick_variables == {"trigger_id": "manual"}


# --------------------------------------------------------
# Schema vs blueprint drift
# --------------------------------------------------------


class TestBlueprintSchemaDrift(BlueprintSchemaDriftBase):
    """The blueprint's ``data:`` keys must match the schema."""

    handler = handler
    blueprint_filename = "reference_watchdog.yaml"


class TestBlueprintDefaultsRoundTrip(BlueprintDefaultsRoundTripBase):
    """Blueprint input defaults must satisfy the schema."""

    handler = handler
    blueprint_filename = "reference_watchdog.yaml"
    template_defaults = {
        "instance_id": "automation.rw_default_check",
        "trigger_id": "manual",
    }


class TestArgparseGuards(HandlerArgparseGuardsBase):
    """Schema rejection / unregistered notify must short-circuit argparse."""

    handler = handler
    valid_payload = _valid_argparse_payload()


class TestCodeQuality(CodeQualityBase):
    ruff_targets = [
        "tests/test_reference_watchdog_handler.py",
    ]
    mypy_targets: list[str] = []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", *sys.argv[1:]]))
