#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "pytest",
#   "pytest-cov",
#   "ruff",
#   "mypy",
#   "PyYAML>=6",
#   "Jinja2>=3",
# ]
# ///
# This is AI generated code
"""Tests for the pyscript/blueprint_toolkit.py bridge.

Uses exec() with mock globals to test the service file
in isolation, matching how PyScript loads user scripts
at runtime.

PyScript provides no test framework for user scripts.
Instead we exec() the file into a namespace containing
mock versions of PyScript-injected globals (``@service``,
``state``, ``homeassistant``), then call the resulting
functions.  Python resolves global names at **call-time**,
so we can overwrite ``ns["datetime"]`` after exec() and
the function will see our controllable replacement.
"""

import ast
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).parent.parent

# Path to the script under test (used for coverage)
_SCRIPT_PATH = REPO_ROOT / "pyscript" / "blueprint_toolkit.py"

# All pyscript .py files (service wrappers + modules)
_PYSCRIPT_DIR = REPO_ROOT / "pyscript"

# Ensure pyscript/modules is importable whether run
# via pytest or directly via uv run --script.
sys.path.insert(0, str(REPO_ROOT / "pyscript" / "modules"))

from datetime import UTC  # noqa: E402

import pytest  # noqa: E402
from conftest import CodeQualityBase  # noqa: E402
from helpers import on_interval  # noqa: E402

T0 = datetime(2024, 1, 15, 12, 0, 0)
# Timezone-aware version for watchdog tests (pyscript's
# last_reported returns UTC-aware datetimes).
T0_UTC = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)


def _boundary_time(
    instance_id: str,
    interval_minutes: int,
    base: datetime,
) -> datetime:
    """Earliest ``base + k min`` where ``on_interval`` fires.

    Interval gating is now instance-jittered, so tests that
    need an on-boundary tick can't just use a round wall-clock
    time -- they have to find the minute within ``[base,
    base + interval)`` that matches this instance's offset.
    """
    for k in range(interval_minutes):
        candidate = base + timedelta(minutes=k)
        if on_interval(interval_minutes, candidate, instance_id):
            return candidate
    msg = (
        "no boundary found within one interval window"
        f" for {instance_id!r} at interval={interval_minutes}"
    )
    raise AssertionError(msg)


# -- Mock infrastructure ------------------------------


class _MockState:
    """Mock for PyScript's global ``state`` object.

    Supports ``get()``, ``set()``, and ``getattr()``
    backed by an in-memory dict.
    """

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._attrs: dict[str, dict[str, str]] = {}
        self._getattr_error: bool = False

    def get(self, key: str) -> str | None:
        return self._store.get(key)

    def set(self, key: str, value: str) -> None:
        self._store[key] = value

    def getattr(self, entity_id: str) -> dict[str, str]:
        if self._getattr_error:
            raise RuntimeError("state.getattr failure")
        return self._attrs.get(entity_id, {})

    def setattr(self, dotted_key: str, value: str) -> None:
        entity_id, attr = dotted_key.rsplit(".", 1)
        if entity_id not in self._attrs:
            self._attrs[entity_id] = {}
        self._attrs[entity_id][attr] = value


class _MockHA:
    """Mock for ``homeassistant`` turn_on/turn_off."""

    def __init__(self) -> None:
        self.turn_on_calls: list[dict[str, str]] = []
        self.turn_off_calls: list[dict[str, str]] = []

    def turn_on(self, **kwargs: str) -> None:
        self.turn_on_calls.append(kwargs)

    def turn_off(self, **kwargs: str) -> None:
        self.turn_off_calls.append(kwargs)


class _MockServiceObj:
    """Mock for ``@service`` and ``service.call()``.

    As a decorator (``@service``), returns the function
    unchanged.  ``call()`` records invocations for
    assertion. Tests can set ``call_exception`` to make
    the next ``call()`` raise -- used to verify that
    notification dispatch failures don't propagate.
    """

    def __init__(self) -> None:
        self.call_log: list[tuple[str, str, dict[str, Any]]] = []
        self.call_exception: Exception | None = None

    def __call__(self, fn: Any) -> Any:
        return fn

    def call(
        self,
        domain: str,
        svc: str,
        **kwargs: Any,
    ) -> None:
        self.call_log.append((domain, svc, kwargs))
        if self.call_exception is not None:
            raise self.call_exception


class _MockHassServices:
    """Mock for ``hass.services``.

    ``has_service_result`` defaults to True so existing
    tests that pass a ``notification_service`` continue
    to validate cleanly. Tests that need to simulate a
    missing service set it to False.
    """

    def __init__(self) -> None:
        self.has_service_result: bool = True
        self.has_service_calls: list[tuple[str, str]] = []

    def has_service(self, domain: str, svc: str) -> bool:
        self.has_service_calls.append((domain, svc))
        return self.has_service_result


class _MockHassConfig:
    """Mock for ``hass.config``."""

    def __init__(self) -> None:
        self.config_dir = str(REPO_ROOT)


class _MockHass:
    """Mock for the pyscript-injected ``hass`` global."""

    def __init__(self) -> None:
        self.services = _MockHassServices()
        self.config = _MockHassConfig()


class _MockLog:
    """Mock for PyScript's global ``log`` object.

    Records all warning calls for assertion.
    """

    def __init__(self) -> None:
        self.warning_calls: list[tuple[str, tuple[Any, ...]]] = []

    def warning(self, msg: str, *args: Any) -> None:
        self.warning_calls.append((msg, args))


class _ControllableDatetime:
    """Replaces ``datetime`` in the exec'd namespace.

    Delegates everything to the real ``datetime`` class
    except ``now()``, which returns a controlled value.
    """

    def __init__(self, now_value: datetime) -> None:
        self._now = now_value

    def now(self, tz: Any = None) -> datetime:
        return self._now

    def __getattr__(self, name: str) -> Any:
        return getattr(datetime, name)


class _MockPersistentNotification:
    """Mock for ``persistent_notification`` service.

    ``dismiss_calls`` filters out ``blueprint_mismatch``
    entries by default: the dispatcher emits an inactive
    mismatch on every successful tick as a cross-layer
    cleanup step, which is noise for tests focused on a
    specific service's own notifications. Tests that
    explicitly exercise the dispatcher's mismatch path
    read ``raw_dismiss_calls``.
    """

    def __init__(self) -> None:
        self.create_calls: list[dict[str, str]] = []
        self.raw_dismiss_calls: list[dict[str, str]] = []

    def create(self, **kwargs: str) -> None:
        self.create_calls.append(kwargs)

    def dismiss(self, **kwargs: str) -> None:
        self.raw_dismiss_calls.append(kwargs)

    @property
    def dismiss_calls(self) -> list[dict[str, str]]:
        return [
            c
            for c in self.raw_dismiss_calls
            if "blueprint_mismatch" not in c.get("notification_id", "")
        ]


class _ServiceEnv:
    """Loads service file via exec() and wires mocks.

    Provides controllable ``current_time`` and
    convenience methods for calling the service.
    """

    def __init__(
        self,
        current_time: datetime = T0,
    ) -> None:
        self.mock_state = _MockState()
        self.mock_ha = _MockHA()
        self.mock_service = _MockServiceObj()
        self.mock_log = _MockLog()
        self.mock_pn = _MockPersistentNotification()
        self.mock_hass = _MockHass()

        src = _SCRIPT_PATH.read_text()
        self._ns: dict[str, Any] = {
            "__builtins__": __builtins__,
            "pyscript_executor": lambda fn: fn,
            "service": self.mock_service,
            "state": self.mock_state,
            "homeassistant": self.mock_ha,
            "log": self.mock_log,
            "persistent_notification": self.mock_pn,
            "hass": self.mock_hass,
        }
        exec(
            compile(src, str(_SCRIPT_PATH), "exec"),
            self._ns,
        )
        self.set_now(current_time)

        # Seed default entity so _validate_entities passes
        self.mock_state._store["switch.test_fan"] = "off"

    def set_now(self, dt: datetime) -> None:
        """Override datetime.now() for calls."""
        self._ns["datetime"] = _ControllableDatetime(dt)

    def set_entity_state(
        self,
        entity_id: str,
        entity_state: str,
    ) -> None:
        self.mock_state._store[entity_id] = entity_state

    @property
    def state_key_fn(self) -> Any:
        """Return the _state_key helper."""
        return self._ns["_state_key"]

    @property
    def service_fn(self) -> Any:
        """Return the service function."""
        return self._ns[
            "sensor_threshold_switch_controller_blueprint_entrypoint"
        ]

    def call(self, **kwargs: Any) -> None:
        """Call the service with defaults."""
        import asyncio

        asyncio.run(self.service_fn(**_default_kwargs(**kwargs)))


def _default_kwargs(**overrides: Any) -> dict[str, Any]:
    """Default kwargs for service function calls.

    Values are strings to match how blueprints pass
    template parameters to the service.
    """
    defaults: dict[str, Any] = {
        "instance_id": "auto.test_instance",
        "target_switch_entity": "switch.test_fan",
        "sensor_value": "",
        "switch_state": "off",
        "trigger_entity": "timer",
        "trigger_threshold_raw": "5.0",
        "release_threshold_raw": "2.0",
        "sampling_window_seconds_raw": "300",
        "disable_window_seconds_raw": "10",
        "auto_off_minutes_raw": "30",
        "notification_service": "",
        "notification_prefix": "",
        "notification_suffix": "",
        "debug_logging_raw": "false",
    }
    defaults.update(overrides)
    return defaults


# -- Tests ---------------------------------------------


class TestStateKey:
    """_state_key() dot replacement."""

    @pytest.fixture()
    def env(self) -> _ServiceEnv:
        return _ServiceEnv()

    def test_replaces_dots(self, env: _ServiceEnv) -> None:
        result = env.state_key_fn("auto.test")
        assert result == "pyscript.auto_test_state"

    def test_no_dots_unchanged(self, env: _ServiceEnv) -> None:
        result = env.state_key_fn("simple")
        assert result == "pyscript.simple_state"

    def test_multiple_dots(self, env: _ServiceEnv) -> None:
        result = env.state_key_fn("a.b.c")
        assert result == "pyscript.a_b_c_state"


class TestReadEntityState:
    """_read_entity_state() pyscript state.get access.

    These tests exercise the real helper (not the watchdog
    mock override) so a rename of the ``last_reported``
    attribute path gets caught.
    """

    def test_reads_state_and_last_reported(self) -> None:
        env = _ServiceEnv()
        env.mock_state._store["sensor.foo"] = "on"
        env.mock_state._store["sensor.foo.last_reported"] = T0_UTC
        state_val, last_reported = env._ns["_read_entity_state"](
            "sensor.foo",
        )
        assert state_val == "on"
        assert last_reported == T0_UTC

    def test_missing_entity_returns_none(self) -> None:
        env = _ServiceEnv()
        state_val, last_reported = env._ns["_read_entity_state"](
            "sensor.absent",
        )
        assert state_val is None
        assert last_reported is None

    def test_missing_last_reported_returns_none(self) -> None:
        env = _ServiceEnv()
        env.mock_state._store["sensor.bar"] = "off"
        state_val, last_reported = env._ns["_read_entity_state"](
            "sensor.bar",
        )
        assert state_val == "off"
        assert last_reported is None


class TestServiceLoads:
    """Import path, decorator, and basic invocation."""

    def test_exec_succeeds(self) -> None:
        """Service file loads without error."""
        _ServiceEnv()

    def test_service_function_exists(self) -> None:
        """Decorator applied, function callable."""
        env = _ServiceEnv()
        assert callable(env.service_fn)

    def test_import_path_works(self) -> None:
        """Bug #1: module import inside function body
        resolves at call time."""
        env = _ServiceEnv()
        env.call()


class TestStateLoading:
    """Fresh / existing / malformed state JSON."""

    def test_fresh_state(self) -> None:
        """No stored state -> no crash, state saved."""
        env = _ServiceEnv()
        env.call()
        key = env.state_key_fn("auto.test_instance")
        attrs = env.mock_state.getattr(key)
        assert attrs.get("data") is not None

    def test_existing_state(self) -> None:
        """Pre-populated state is loaded and used."""
        from sensor_threshold_switch_controller import (
            State,
        )

        env = _ServiceEnv()
        key = env.state_key_fn("auto.test_instance")
        s = State(initialized=True)
        env.mock_state.setattr(
            f"{key}.data",
            json.dumps(s.to_dict()),
        )
        env.call()

    def test_malformed_json(self) -> None:
        """Bad JSON -> graceful fallback."""
        env = _ServiceEnv()
        key = env.state_key_fn("auto.test_instance")
        env.mock_state.setattr(
            f"{key}.data",
            "{not valid json",
        )
        env.call()


class TestStateSaving:
    """JSON saved, valid, and round-trips."""

    def test_saves_json(self) -> None:
        """State is persisted after each call."""
        env = _ServiceEnv()
        env.call()
        key = env.state_key_fn("auto.test_instance")
        attrs = env.mock_state.getattr(key)
        assert attrs.get("data") is not None

    def test_saved_json_valid(self) -> None:
        """Persisted state is valid JSON dict."""
        env = _ServiceEnv()
        env.call()
        key = env.state_key_fn("auto.test_instance")
        raw = env.mock_state.getattr(key).get("data", "")
        data = json.loads(raw)
        assert isinstance(data, dict)

    def test_round_trips_across_calls(self) -> None:
        """State saved by one call is loaded by next."""
        env = _ServiceEnv()
        env.call(
            sensor_value="60.0",
            trigger_entity="sensor.humidity",
        )
        key = env.state_key_fn("auto.test_instance")
        raw = env.mock_state.getattr(key).get("data", "")
        data = json.loads(raw)
        assert len(data.get("samples", [])) > 0

        # Second call loads that state successfully
        env.set_now(T0 + timedelta(seconds=10))
        env.call(
            sensor_value="70.0",
            trigger_entity="sensor.humidity",
        )


class TestFriendlyName:
    """friendly_name resolution and fallback."""

    def test_uses_friendly_name(self) -> None:
        """friendly_name attr passed to logic."""
        env = _ServiceEnv()
        env.mock_state._attrs["switch.test_fan"] = {
            "friendly_name": "Bathroom Fan",
        }
        env.call(
            sensor_value="60.0",
            trigger_entity="sensor.humidity",
            notification_service="notify.phone",
        )
        env.set_now(T0 + timedelta(seconds=10))
        env.call(
            sensor_value="70.0",
            trigger_entity="sensor.humidity",
            notification_service="notify.phone",
        )
        assert len(env.mock_service.call_log) == 1
        _, _, kw = env.mock_service.call_log[0]
        assert "Bathroom Fan" in kw["message"]

    def test_falls_back_to_entity_id(self) -> None:
        """No friendly_name -> entity_id used."""
        env = _ServiceEnv()
        env.call(
            sensor_value="60.0",
            trigger_entity="sensor.humidity",
            notification_service="notify.phone",
        )
        env.set_now(T0 + timedelta(seconds=10))
        env.call(
            sensor_value="70.0",
            trigger_entity="sensor.humidity",
            notification_service="notify.phone",
        )
        assert len(env.mock_service.call_log) == 1
        _, _, kw = env.mock_service.call_log[0]
        assert "switch.test_fan" in kw["message"]

    def test_handles_getattr_exception(self) -> None:
        """getattr failure -> graceful fallback."""
        env = _ServiceEnv()
        env.mock_state._getattr_error = True
        env.call()


class TestActionExecution:
    """TURN_ON / TURN_OFF / NONE routing."""

    def test_turn_on_calls_ha(self) -> None:
        """TURN_ON -> homeassistant.turn_on."""
        env = _ServiceEnv()
        env.call(
            sensor_value="60.0",
            trigger_entity="sensor.humidity",
        )
        env.set_now(T0 + timedelta(seconds=10))
        env.call(
            sensor_value="70.0",
            trigger_entity="sensor.humidity",
        )
        assert len(env.mock_ha.turn_on_calls) == 1

    def test_turn_off_calls_ha(self) -> None:
        """TURN_OFF -> homeassistant.turn_off."""
        from sensor_threshold_switch_controller import (
            Sample,
            State,
        )

        env = _ServiceEnv()
        key = env.state_key_fn("auto.test_instance")
        s = State(
            baseline=60.0,
            samples=[
                Sample(value=62.0, timestamp=T0),
            ],
            initialized=True,
        )
        env.mock_state.setattr(
            f"{key}.data",
            json.dumps(s.to_dict()),
        )
        env.call(
            sensor_value="61.0",
            switch_state="on",
            trigger_entity="sensor.humidity",
        )
        assert len(env.mock_ha.turn_off_calls) == 1

    def test_none_calls_nothing(self) -> None:
        """NONE -> no HA service calls."""
        env = _ServiceEnv()
        env.call()
        assert len(env.mock_ha.turn_on_calls) == 0
        assert len(env.mock_ha.turn_off_calls) == 0

    def test_correct_entity_id(self) -> None:
        """Action targets configured entity."""
        env = _ServiceEnv()
        env.call(
            sensor_value="60.0",
            trigger_entity="sensor.humidity",
        )
        env.set_now(T0 + timedelta(seconds=10))
        env.call(
            sensor_value="70.0",
            trigger_entity="sensor.humidity",
        )
        call = env.mock_ha.turn_on_calls[0]
        assert call["entity_id"] == "switch.test_fan"


class TestNotificationRouting:
    """Domain/service split, sent/not-sent, message."""

    def _trigger_spike(
        self,
        env: _ServiceEnv,
        notification_service: str = "notify.phone",
    ) -> None:
        """Two sensor calls to trigger a spike."""
        env.call(
            sensor_value="60.0",
            trigger_entity="sensor.humidity",
            notification_service=notification_service,
        )
        env.set_now(T0 + timedelta(seconds=10))
        env.call(
            sensor_value="70.0",
            trigger_entity="sensor.humidity",
            notification_service=notification_service,
        )

    def test_domain_service_split(self) -> None:
        """notification_service split on '.'."""
        env = _ServiceEnv()
        self._trigger_spike(env)
        assert len(env.mock_service.call_log) == 1
        domain, svc, _ = env.mock_service.call_log[0]
        assert domain == "notify"
        assert svc == "phone"

    def test_not_sent_when_empty(self) -> None:
        """Empty notification_service -> no call."""
        env = _ServiceEnv()
        self._trigger_spike(env, notification_service="")
        assert len(env.mock_service.call_log) == 0

    def test_sent_on_spike(self) -> None:
        """Spike with service -> notification sent."""
        env = _ServiceEnv()
        self._trigger_spike(env)
        assert len(env.mock_service.call_log) == 1

    def test_message_present(self) -> None:
        """Notification includes message text."""
        env = _ServiceEnv()
        self._trigger_spike(env)
        _, _, kw = env.mock_service.call_log[0]
        assert "message" in kw
        assert len(kw["message"]) > 0


class TestInputTypeConversion:
    """String -> float/int casts (blueprint sends strings)."""

    def test_float_thresholds(self) -> None:
        """String thresholds cast to float."""
        env = _ServiceEnv()
        env.call(
            trigger_threshold_raw="7.5",
            release_threshold_raw="3.0",
        )

    def test_int_windows(self) -> None:
        """String window values cast to int."""
        env = _ServiceEnv()
        env.call(
            sampling_window_seconds_raw="120",
            disable_window_seconds_raw="5",
            auto_off_minutes_raw="15",
        )

    def test_all_types_convert(self) -> None:
        """Full call with all string params."""
        env = _ServiceEnv()
        env.call(
            trigger_threshold_raw="10.0",
            release_threshold_raw="5.0",
            sampling_window_seconds_raw="600",
            disable_window_seconds_raw="0",
            auto_off_minutes_raw="0",
        )


class TestErrorResilience:
    """Malformed JSON, getattr/get failures."""

    def test_malformed_json_in_state(self) -> None:
        """Bad JSON in state store -> no crash."""
        env = _ServiceEnv()
        key = env.state_key_fn("auto.test_instance")
        env.mock_state.setattr(
            f"{key}.data",
            "{not valid json",
        )
        env.call()

    def test_getattr_failure(self) -> None:
        """state.getattr raises -> graceful."""
        env = _ServiceEnv()
        env.mock_state._getattr_error = True
        env.call()

    def test_missing_data_attr(self) -> None:
        """Entity exists but has no 'data' attr."""
        env = _ServiceEnv()
        key = env.state_key_fn("auto.test_instance")
        env.mock_state.set(key, "ok")
        env.call()


class TestMultiStepScenarios:
    """Full cycles through the bridge layer."""

    def test_spike_release_cycle(self) -> None:
        """Spike -> release through bridge."""
        env = _ServiceEnv()
        t = T0

        # Low reading
        env.set_now(t)
        env.call(
            sensor_value="60.0",
            trigger_entity="sensor.humidity",
            sampling_window_seconds_raw="30",
        )

        # Spike
        t += timedelta(seconds=10)
        env.set_now(t)
        env.call(
            sensor_value="70.0",
            trigger_entity="sensor.humidity",
            sampling_window_seconds_raw="30",
        )
        assert len(env.mock_ha.turn_on_calls) == 1

        # Release (old samples aged out of window)
        t += timedelta(seconds=60)
        env.set_now(t)
        env.call(
            sensor_value="61.0",
            switch_state="on",
            trigger_entity="sensor.humidity",
            sampling_window_seconds_raw="30",
        )
        assert len(env.mock_ha.turn_off_calls) == 1

    def test_auto_off_cycle(self) -> None:
        """Manual on -> timer ticks -> auto-off."""
        env = _ServiceEnv()
        t = T0

        # Startup
        env.set_now(t)
        env.call(
            switch_state="off",
            trigger_entity="switch.test_fan",
            auto_off_minutes_raw="1",
        )

        # Manual on
        t += timedelta(seconds=5)
        env.set_now(t)
        env.call(
            switch_state="on",
            trigger_entity="switch.test_fan",
            auto_off_minutes_raw="1",
        )

        # Timer before timeout (start rounded up to 12:01,
        # so 60s timeout fires at 12:02+)
        t += timedelta(seconds=55)  # 12:01:00
        env.set_now(t)
        env.call(switch_state="on", auto_off_minutes_raw="1")
        assert len(env.mock_ha.turn_off_calls) == 0

        # Timer at timeout (61s past rounded-up start)
        t += timedelta(seconds=61)  # 12:02:01
        env.set_now(t)
        env.call(switch_state="on", auto_off_minutes_raw="1")
        assert len(env.mock_ha.turn_off_calls) == 1

    def test_independent_instances(self) -> None:
        """Different instance_ids -> separate state."""
        env = _ServiceEnv()

        # Instance A: sensor reading
        env.call(
            instance_id="auto.inst_a",
            sensor_value="60.0",
            trigger_entity="sensor.humidity",
        )
        # Instance B: same reading
        env.call(
            instance_id="auto.inst_b",
            sensor_value="60.0",
            trigger_entity="sensor.humidity",
        )

        # Instance A: spike
        env.set_now(T0 + timedelta(seconds=10))
        env.call(
            instance_id="auto.inst_a",
            sensor_value="70.0",
            trigger_entity="sensor.humidity",
        )

        key_a = env.state_key_fn("auto.inst_a")
        key_b = env.state_key_fn("auto.inst_b")
        raw_a = env.mock_state.getattr(key_a).get("data", "")
        raw_b = env.mock_state.getattr(key_b).get("data", "")
        data_a = json.loads(raw_a)
        data_b = json.loads(raw_b)
        assert data_a.get("baseline") is not None
        assert data_b.get("baseline") is None


class TestDebugAttributes:
    """Layer 1: entity attributes always written."""

    def test_attrs_written_after_call(self) -> None:
        """Debug attributes present on entity after call."""
        env = _ServiceEnv()
        env.call()
        key = env.state_key_fn("auto.test_instance")
        attrs = env.mock_state.getattr(key)
        assert "last_action" in attrs
        assert "last_reason" in attrs
        assert "last_event" in attrs
        assert "last_run" in attrs
        assert "last_sensor" in attrs

    def test_last_action_value(self) -> None:
        """last_action reflects the action taken."""
        env = _ServiceEnv()
        env.call()
        key = env.state_key_fn("auto.test_instance")
        attrs = env.mock_state.getattr(key)
        assert attrs["last_action"] == "NONE"

    def test_last_action_turn_on(self) -> None:
        """last_action is TURN_ON after a spike."""
        env = _ServiceEnv()
        env.call(
            sensor_value="60.0",
            trigger_entity="sensor.humidity",
        )
        env.set_now(T0 + timedelta(seconds=10))
        env.call(
            sensor_value="70.0",
            trigger_entity="sensor.humidity",
        )
        key = env.state_key_fn("auto.test_instance")
        attrs = env.mock_state.getattr(key)
        assert attrs["last_action"] == "TURN_ON"

    def test_last_event_value(self) -> None:
        """last_event reflects the trigger type."""
        env = _ServiceEnv()
        env.call()
        key = env.state_key_fn("auto.test_instance")
        attrs = env.mock_state.getattr(key)
        assert attrs["last_event"] == "TIMER"

    def test_last_event_sensor(self) -> None:
        """last_event is SENSOR for sensor trigger."""
        env = _ServiceEnv()
        env.call(
            sensor_value="60.0",
            trigger_entity="sensor.humidity",
        )
        key = env.state_key_fn("auto.test_instance")
        attrs = env.mock_state.getattr(key)
        assert attrs["last_event"] == "SENSOR"

    def test_last_sensor_value(self) -> None:
        """last_sensor shows parsed sensor value."""
        env = _ServiceEnv()
        env.call(
            sensor_value="65.3",
            trigger_entity="sensor.humidity",
        )
        key = env.state_key_fn("auto.test_instance")
        attrs = env.mock_state.getattr(key)
        assert attrs["last_sensor"] == "65.3"

    def test_last_sensor_na_for_timer(self) -> None:
        """last_sensor is n/a for timer events."""
        env = _ServiceEnv()
        env.call()
        key = env.state_key_fn("auto.test_instance")
        attrs = env.mock_state.getattr(key)
        assert attrs["last_sensor"] == "n/a"

    def test_last_run_is_iso_timestamp(self) -> None:
        """last_run is a valid ISO timestamp."""
        env = _ServiceEnv()
        env.call()
        key = env.state_key_fn("auto.test_instance")
        attrs = env.mock_state.getattr(key)
        # Should parse without error
        datetime.fromisoformat(attrs["last_run"])

    def test_attrs_update_across_calls(self) -> None:
        """Attributes update on subsequent calls."""
        env = _ServiceEnv()
        env.call()
        key = env.state_key_fn("auto.test_instance")
        attrs1 = dict(env.mock_state.getattr(key))

        env.set_now(T0 + timedelta(seconds=60))
        env.call()
        attrs2 = dict(env.mock_state.getattr(key))
        assert attrs1["last_run"] != attrs2["last_run"]


class TestDebugLogging:
    """Layer 2: opt-in debug logging."""

    def test_no_logging_when_debug_false(self) -> None:
        """No log.warning when debug is disabled."""
        env = _ServiceEnv()
        env.call(debug_logging_raw="false")
        assert len(env.mock_log.warning_calls) == 0

    def test_logging_when_debug_true(self) -> None:
        """log.warning emitted when debug is enabled."""
        env = _ServiceEnv()
        env.call(debug_logging_raw="true")
        assert len(env.mock_log.warning_calls) == 1

    def test_logging_with_bool_true(self) -> None:
        """debug_logging_raw=True (bool) also triggers logging."""
        env = _ServiceEnv()
        env.call(debug_logging_raw=True)
        assert len(env.mock_log.warning_calls) == 1

    def test_no_logging_with_bool_false(self) -> None:
        """debug_logging_raw=False (bool) does not trigger logging."""
        env = _ServiceEnv()
        env.call(debug_logging_raw=False)
        assert len(env.mock_log.warning_calls) == 0

    def test_log_message_contains_context(self) -> None:
        """Log message includes event, action, and label."""
        env = _ServiceEnv()
        env.call(debug_logging_raw="true")
        msg, args = env.mock_log.warning_calls[0]
        formatted = msg % args
        assert "[STSC:" in formatted
        assert "TIMER" in formatted
        assert "NONE" in formatted

    def test_log_emitted_every_call(self) -> None:
        """Each call emits a log line when debug is on."""
        env = _ServiceEnv()
        env.call(debug_logging_raw="true")
        env.set_now(T0 + timedelta(seconds=30))
        env.call(debug_logging_raw="true")
        assert len(env.mock_log.warning_calls) == 2


class TestAutomationName:
    def test_returns_friendly_name(self) -> None:
        env = _ServiceEnv()
        fn = env._ns["_automation_name"]
        env.mock_state._attrs["automation.test"] = {
            "friendly_name": "My Automation",
        }
        assert fn("automation.test") == "My Automation"

    def test_falls_back_to_instance_id(self) -> None:
        env = _ServiceEnv()
        fn = env._ns["_automation_name"]
        assert fn("automation.unknown") == "automation.unknown"

    def test_empty_friendly_name_falls_back(
        self,
    ) -> None:
        env = _ServiceEnv()
        fn = env._ns["_automation_name"]
        env.mock_state._attrs["automation.test"] = {
            "friendly_name": "",
        }
        assert fn("automation.test") == "automation.test"

    def test_getattr_error_falls_back(self) -> None:
        env = _ServiceEnv()
        fn = env._ns["_automation_name"]
        env.mock_state._getattr_error = True
        assert fn("automation.test") == "automation.test"


class TestProcessPersistentNotifications:
    """_process_persistent_notifications decorates active
    notifications with an 'Automation: [name](url)' link
    when the automation entity exposes an ``id`` attribute.
    """

    def _make(
        self,
        env: _ServiceEnv,
        *,
        active: bool,
        message: str,
    ) -> Any:
        pn_cls = env._ns["helpers"].PersistentNotification
        return pn_cls(
            active=active,
            notification_id="nid",
            title="t",
            message=message,
        )

    def test_prepends_link_when_id_present(self) -> None:
        env = _ServiceEnv()
        fn = env._ns["_process_persistent_notifications"]
        env.mock_state._attrs["automation.dw_main"] = {
            "friendly_name": "Device Watchdog 73",
            "id": "1700000000001",
        }
        n = self._make(env, active=True, message="body")
        fn([n], "automation.dw_main")
        assert len(env.mock_pn.create_calls) == 1
        msg = env.mock_pn.create_calls[0]["message"]
        expected_prefix = (
            "Automation: [Device Watchdog 73]"
            "(/config/automation/edit/1700000000001)\n"
        )
        assert msg == f"{expected_prefix}body"

    def test_no_prefix_when_id_missing(self) -> None:
        env = _ServiceEnv()
        fn = env._ns["_process_persistent_notifications"]
        env.mock_state._attrs["automation.dw_main"] = {
            "friendly_name": "Device Watchdog",
        }
        n = self._make(env, active=True, message="body")
        fn([n], "automation.dw_main")
        assert env.mock_pn.create_calls[0]["message"] == "body"

    def test_no_prefix_when_id_empty_string(self) -> None:
        env = _ServiceEnv()
        fn = env._ns["_process_persistent_notifications"]
        env.mock_state._attrs["automation.dw_main"] = {
            "friendly_name": "Device Watchdog",
            "id": "",
        }
        n = self._make(env, active=True, message="body")
        fn([n], "automation.dw_main")
        assert env.mock_pn.create_calls[0]["message"] == "body"

    def test_falls_back_to_instance_id_when_no_friendly(
        self,
    ) -> None:
        env = _ServiceEnv()
        fn = env._ns["_process_persistent_notifications"]
        env.mock_state._attrs["automation.dw_main"] = {
            "id": "42",
        }
        n = self._make(env, active=True, message="body")
        fn([n], "automation.dw_main")
        msg = env.mock_pn.create_calls[0]["message"]
        assert msg.startswith(
            "Automation: [automation.dw_main](/config/automation/edit/42)\n",
        )

    def test_escapes_brackets_in_friendly_name(self) -> None:
        """Brackets in the automation name are legal but
        would break the markdown link if inlined verbatim.
        Backslash-escape them."""
        env = _ServiceEnv()
        fn = env._ns["_process_persistent_notifications"]
        env.mock_state._attrs["automation.dw_main"] = {
            "friendly_name": "Device [debug]\\ Watchdog",
            "id": "42",
        }
        n = self._make(env, active=True, message="body")
        fn([n], "automation.dw_main")
        msg = env.mock_pn.create_calls[0]["message"]
        assert msg.startswith(
            "Automation: [Device \\[debug\\]\\\\ Watchdog]"
            "(/config/automation/edit/42)\n",
        )

    def test_dismissals_unaffected(self) -> None:
        env = _ServiceEnv()
        fn = env._ns["_process_persistent_notifications"]
        env.mock_state._attrs["automation.dw_main"] = {
            "friendly_name": "Device Watchdog",
            "id": "42",
        }
        n = self._make(env, active=False, message="")
        fn([n], "automation.dw_main", {"nid"})
        # Dismissal fires with no message mutation.
        assert env.mock_pn.create_calls == []
        assert len(env.mock_pn.dismiss_calls) == 1

    def test_getattr_error_skips_decoration(self) -> None:
        env = _ServiceEnv()
        fn = env._ns["_process_persistent_notifications"]
        env.mock_state._getattr_error = True
        n = self._make(env, active=True, message="body")
        fn([n], "automation.dw_main")
        assert env.mock_pn.create_calls[0]["message"] == "body"


class TestNotificationPrefix:
    """The canonical per-instance notification prefix builder."""

    def test_slug_and_safe_id(self) -> None:
        env = _ServiceEnv()
        fn = env._ns["_notification_prefix"]
        assert (
            fn("Device Watchdog", "automation.dw_main")
            == "device_watchdog_automation_dw_main__"
        )

    def test_distinct_instances_distinct_prefixes(self) -> None:
        env = _ServiceEnv()
        fn = env._ns["_notification_prefix"]
        a = fn("Device Watchdog", "automation.inst_a")
        b = fn("Device Watchdog", "automation.inst_b")
        assert a != b
        # Same service label so both must share the service portion.
        assert a.startswith("device_watchdog_")
        assert b.startswith("device_watchdog_")

    def test_distinct_services_distinct_prefixes(self) -> None:
        env = _ServiceEnv()
        fn = env._ns["_notification_prefix"]
        a = fn("Device Watchdog", "automation.shared")
        b = fn("Reference Watchdog", "automation.shared")
        assert a != b


class TestSweepOrphanNotifications:
    """_sweep_orphan_notifications dismisses prefix-matching
    notifications that aren't in the current run's output.

    This is the fix for the 'device deleted, notification
    lingers' bug: whatever the watchdog used to emit for a
    now-gone device/node/owner no longer appears in the
    current notifications list, so the sweep appends an
    ``active=False`` entry that the dispatcher will dismiss.
    """

    def _make(
        self,
        env: _ServiceEnv,
        *,
        active: bool,
        nid: str,
    ) -> Any:
        pn_cls = env._ns["helpers"].PersistentNotification
        return pn_cls(
            active=active,
            notification_id=nid,
            title="",
            message="",
        )

    def test_orphan_appended_as_dismissal(self) -> None:
        env = _ServiceEnv()
        fn = env._ns["_sweep_orphan_notifications"]
        prefix = "device_watchdog_auto_a__"
        notifications = [
            self._make(env, active=True, nid=f"{prefix}device_dev1"),
        ]
        active_ids = {
            f"{prefix}device_dev1",
            f"{prefix}device_dev_gone",
        }
        fn(prefix, active_ids, notifications)
        # dev1 stays; dev_gone added as dismissal.
        gone = [n for n in notifications if "dev_gone" in n.notification_id]
        assert len(gone) == 1
        assert gone[0].active is False

    def test_different_instance_untouched(self) -> None:
        """Sweep for instance A must not touch instance B's IDs."""
        env = _ServiceEnv()
        fn = env._ns["_sweep_orphan_notifications"]
        prefix_a = "device_watchdog_auto_a__"
        notifications = [
            self._make(env, active=True, nid=f"{prefix_a}device_dev1"),
        ]
        active_ids = {
            f"{prefix_a}device_dev1",
            "device_watchdog_auto_b__device_dev2",
        }
        fn(prefix_a, active_ids, notifications)
        # B's id must not appear in notifications at all.
        assert all("auto_b" not in n.notification_id for n in notifications)

    def test_different_service_untouched(self) -> None:
        """Sweep for one service must not touch another's IDs."""
        env = _ServiceEnv()
        fn = env._ns["_sweep_orphan_notifications"]
        dw_prefix = "device_watchdog_auto_x__"
        notifications: list[Any] = []
        active_ids = {
            "reference_watchdog_auto_x__owner_foo",
            "entity_defaults_watchdog_auto_x__device_dev1",
        }
        fn(dw_prefix, active_ids, notifications)
        assert notifications == []

    def test_none_active_ids_is_noop(self) -> None:
        """In tests hass.data isn't populated; sweep must be safe."""
        env = _ServiceEnv()
        fn = env._ns["_sweep_orphan_notifications"]
        notifications: list[Any] = []
        fn("device_watchdog_auto_a__", None, notifications)
        assert notifications == []

    def test_current_ids_not_reemitted(self) -> None:
        """An ID already in the current list stays as-is (no
        duplicate dismissal appended)."""
        env = _ServiceEnv()
        fn = env._ns["_sweep_orphan_notifications"]
        prefix = "device_watchdog_auto_a__"
        n = self._make(env, active=True, nid=f"{prefix}device_dev1")
        notifications = [n]
        fn(prefix, {f"{prefix}device_dev1"}, notifications)
        assert notifications == [n]

    def test_keep_pattern_skips_matching_ids(self) -> None:
        # Used by the route manager to leave per-attempt
        # timeout notifications alone -- they're event-stream
        # notifications the user dismisses manually.
        env = _ServiceEnv()
        fn = env._ns["_sweep_orphan_notifications"]
        prefix = "z-wave_route_manager_auto_a__"
        active = {
            f"{prefix}timeout_30_priority_app_2026-04-22T01_23_45",
            f"{prefix}apply_42",
        }
        notifications: list[Any] = []
        fn(prefix, active, notifications, keep_pattern="__timeout_")
        # The timeout notification is exempt; apply is swept.
        dismissed_ids = {n.notification_id for n in notifications}
        assert dismissed_ids == {f"{prefix}apply_42"}


class TestBuildBlueprintMismatchNotification:
    """Three-layer dispatch: the blueprint-vs-pyscript
    mismatch notification builder. Tests the PersistentNotification
    payload directly, no HA calls involved.
    """

    def _call(
        self,
        env: _ServiceEnv,
        *,
        missing: list[str],
        extras: list[str],
        service_label: str = "TEC",
        instance_id: str = "automation.tec_test",
        blueprint_basename: str = "trigger_entity_controller.yaml",
    ) -> Any:
        fn = env._ns["_build_blueprint_mismatch_notification"]
        prefix = env._ns["_notification_prefix"](
            service_label,
            instance_id,
        )
        return fn(
            service_label=service_label,
            instance_id=instance_id,
            notif_prefix=prefix,
            blueprint_basename=blueprint_basename,
            missing=missing,
            extras=extras,
        )

    def test_notification_id_format(self) -> None:
        env = _ServiceEnv()
        n = self._call(env, missing=["foo"], extras=[])
        assert n.notification_id == (
            "tec_automation_tec_test__blueprint_mismatch"
        )

    def test_title_uses_service_label(self) -> None:
        env = _ServiceEnv()
        n = self._call(env, missing=["foo"], extras=[])
        assert n.title == "TEC: blueprint vs pyscript mismatch"

    def test_active_when_any_mismatch(self) -> None:
        env = _ServiceEnv()
        assert self._call(env, missing=["a"], extras=[]).active
        assert self._call(env, missing=[], extras=["z"]).active
        assert self._call(env, missing=["a"], extras=["z"]).active

    def test_inactive_when_no_mismatch(self) -> None:
        env = _ServiceEnv()
        n = self._call(env, missing=[], extras=[])
        assert n.active is False

    def test_missing_section_rendered(self) -> None:
        env = _ServiceEnv()
        n = self._call(env, missing=["alpha", "beta"], extras=[])
        assert "required parameters were missing" in n.message
        assert "  - alpha" in n.message
        assert "  - beta" in n.message
        assert "invalid parameters were received" not in n.message

    def test_extras_section_rendered(self) -> None:
        env = _ServiceEnv()
        n = self._call(env, missing=[], extras=["stale_raw"])
        assert "invalid parameters were received" in n.message
        assert "  - stale_raw" in n.message
        assert "required parameters were missing" not in n.message

    def test_both_sections_rendered(self) -> None:
        env = _ServiceEnv()
        n = self._call(env, missing=["a"], extras=["z"])
        assert "required parameters were missing" in n.message
        assert "  - a" in n.message
        assert "invalid parameters were received" in n.message
        assert "  - z" in n.message

    def test_body_references_both_file_basenames(self) -> None:
        env = _ServiceEnv()
        n = self._call(
            env,
            missing=["x"],
            extras=[],
            blueprint_basename="my_bp.yaml",
        )
        assert "my_bp.yaml" in n.message
        assert "blueprint_toolkit.py" in n.message

    def test_body_includes_remediation_hint(self) -> None:
        env = _ServiceEnv()
        n = self._call(env, missing=["x"], extras=[])
        assert "Blueprint Toolkit integration" in n.message
        assert "restart Home Assistant" in n.message


class TestDispatchBlueprintService:
    """Three-layer dispatch: the shape-check helper that
    every entrypoint routes through. Asserts forwarding
    behavior on matching kwargs and notification behavior
    on mismatched kwargs.
    """

    _EXPECTED = frozenset(
        [
            "instance_id",
            "alpha_raw",
            "beta_raw",
        ],
    )

    def _dispatch(
        self,
        env: _ServiceEnv,
        kwargs: dict[str, Any],
        argparse_fn: Any,
        *,
        service_label: str = "TEC",
        blueprint_basename: str = "trigger_entity_controller.yaml",
    ) -> None:
        env._ns["_BLUEPRINT_SERVICES"][service_label] = (
            blueprint_basename,
            self._EXPECTED,
            argparse_fn,
        )
        fn = env._ns["_dispatch_blueprint_service"]
        import asyncio

        asyncio.run(fn(service_label=service_label, kwargs=kwargs))

    def test_forwards_on_exact_match(self) -> None:
        env = _ServiceEnv()
        calls: list[dict[str, Any]] = []
        self._dispatch(
            env,
            {
                "instance_id": "automation.tec_test",
                "alpha_raw": "1",
                "beta_raw": "2",
            },
            lambda **kw: calls.append(kw),
        )
        assert calls == [
            {
                "instance_id": "automation.tec_test",
                "alpha_raw": "1",
                "beta_raw": "2",
            },
        ]
        assert env.mock_pn.create_calls == []

    def test_missing_key_emits_mismatch(self) -> None:
        env = _ServiceEnv()
        calls: list[dict[str, Any]] = []
        self._dispatch(
            env,
            {
                "instance_id": "automation.tec_test",
                "alpha_raw": "1",
                # beta_raw missing
            },
            lambda **kw: calls.append(kw),
        )
        assert calls == []
        assert len(env.mock_pn.create_calls) == 1
        msg = env.mock_pn.create_calls[0]["message"]
        nid = env.mock_pn.create_calls[0]["notification_id"]
        assert nid == "tec_automation_tec_test__blueprint_mismatch"
        assert "  - beta_raw" in msg

    def test_extra_key_emits_mismatch(self) -> None:
        env = _ServiceEnv()
        calls: list[dict[str, Any]] = []
        self._dispatch(
            env,
            {
                "instance_id": "automation.tec_test",
                "alpha_raw": "1",
                "beta_raw": "2",
                "ghost_raw": "9",
            },
            lambda **kw: calls.append(kw),
        )
        assert calls == []
        assert len(env.mock_pn.create_calls) == 1
        assert "  - ghost_raw" in env.mock_pn.create_calls[0]["message"]

    def test_both_missing_and_extra(self) -> None:
        env = _ServiceEnv()
        calls: list[dict[str, Any]] = []
        self._dispatch(
            env,
            {
                "instance_id": "automation.tec_test",
                "alpha_raw": "1",
                "ghost_raw": "9",
            },
            lambda **kw: calls.append(kw),
        )
        assert calls == []
        msg = env.mock_pn.create_calls[0]["message"]
        assert "  - beta_raw" in msg
        assert "  - ghost_raw" in msg

    def test_missing_instance_id_falls_back_to_unknown(
        self,
    ) -> None:
        env = _ServiceEnv()
        calls: list[dict[str, Any]] = []
        self._dispatch(
            env,
            {
                # instance_id missing
                "alpha_raw": "1",
                "beta_raw": "2",
            },
            lambda **kw: calls.append(kw),
        )
        assert calls == []
        nid = env.mock_pn.create_calls[0]["notification_id"]
        assert nid == "tec_unknown__blueprint_mismatch"

    def test_pyscript_trigger_kwargs_filtered(self) -> None:
        """pyscript's @service decorator auto-injects
        ``context``, ``trigger_type``, ``trigger_time``
        and similar trigger metadata. Those must be
        silently dropped before the shape check, and
        must not reach the argparse function.
        """
        env = _ServiceEnv()
        calls: list[dict[str, Any]] = []
        self._dispatch(
            env,
            {
                "instance_id": "automation.tec_test",
                "alpha_raw": "1",
                "beta_raw": "2",
                # Anything in pyscript's TRIGGER_KWARGS
                # set is invisible to the shape check.
                "context": "stub-context",
                "trigger_type": "time",
                "trigger_time": "2026-04-23T12:00:00",
                "old_value": "off",
                "value": "on",
                "var_name": "switch.x",
                "event_type": "state_changed",
                "payload": "{}",
                "payload_obj": {},
                "qos": 0,
                "retain": False,
                "topic": "t",
                "webhook_id": "w",
            },
            lambda **kw: calls.append(kw),
        )
        assert env.mock_pn.create_calls == []
        assert calls == [
            {
                "instance_id": "automation.tec_test",
                "alpha_raw": "1",
                "beta_raw": "2",
            },
        ]


class TestBlueprintExpectedKeys:
    """Verifies each service's ``_EXPECTED_KEYS`` constant
    matches the live signature of its ``*_blueprint_argparse``
    function. Drift between the two is the root cause the
    entrypoint's shape check is meant to catch; this test
    ensures the constant itself never drifts from the
    function it describes.

    The service registry lives in the wrapper module as
    ``_BLUEPRINT_SERVICES``; this test reads it directly
    so adding a new service registration automatically
    extends drift coverage.
    """

    def test_all_services_match(self) -> None:
        import inspect

        env = _ServiceEnv()
        registry = env._ns["_BLUEPRINT_SERVICES"]
        for label, entry in registry.items():
            _blueprint, expected_keys, argparse_fn = entry
            sig_keys = frozenset(
                inspect.signature(argparse_fn).parameters.keys(),
            )
            diff = expected_keys ^ sig_keys
            assert expected_keys == sig_keys, (
                f"{label}: _EXPECTED_KEYS drift; symmetric diff"
                f" {sorted(diff)}. Update the constant to match"
                " the argparse signature (or vice versa)."
            )


class TestBlueprintYamlMatchesRegistry:
    """Each service's blueprint YAML exposes exactly the
    expected kwargs under its ``action:`` ``data:`` block,
    and its ``action:`` targets the registered entrypoint.

    Catches drift between a blueprint's ``data:`` keys and
    the service's ``_BLUEPRINT_SERVICES`` entry that the
    dispatcher would only surface at runtime (via a
    blueprint_mismatch notification).
    """

    _BLUEPRINT_DIR = (
        REPO_ROOT / "blueprints" / "automation" / "blueprint_toolkit"
    )

    def test_all_blueprints_match_registry(self) -> None:
        import yaml

        # HA blueprints use tags like ``!input``;
        # SafeLoader rejects unknown tags. Register a
        # permissive constructor so parsing succeeds --
        # this test only cares about keys, not values.
        class _HABlueprintLoader(yaml.SafeLoader):
            pass

        def _passthrough(
            loader: yaml.SafeLoader,
            tag_suffix: str,
            node: yaml.Node,
        ) -> Any:
            if isinstance(node, yaml.ScalarNode):
                return loader.construct_scalar(node)
            if isinstance(node, yaml.SequenceNode):
                return loader.construct_sequence(node)
            if isinstance(node, yaml.MappingNode):
                return loader.construct_mapping(node)
            return None

        _HABlueprintLoader.add_multi_constructor("!", _passthrough)

        env = _ServiceEnv()
        registry = env._ns["_BLUEPRINT_SERVICES"]
        for label, (basename, expected_keys, argparse_fn) in registry.items():
            bp_path = self._BLUEPRINT_DIR / basename
            assert bp_path.exists(), (
                f"{label}: blueprint file {basename} not found at {bp_path}"
            )
            with bp_path.open() as f:
                parsed = yaml.load(f, Loader=_HABlueprintLoader)
            actions = parsed.get("actions") or parsed.get("action")
            assert actions, (
                f"{label}: {basename} has no 'actions:' or 'action:' block"
            )
            first = actions[0] if isinstance(actions, list) else actions
            yaml_keys = frozenset((first.get("data") or {}).keys())
            assert yaml_keys == expected_keys, (
                f"{label}: blueprint {basename} 'data:' keys do not match"
                " the _BLUEPRINT_SERVICES registry.\n"
                f"  only in YAML:     {sorted(yaml_keys - expected_keys)}\n"
                f"  only in registry: {sorted(expected_keys - yaml_keys)}"
            )
            # The ``action:`` line must route through the
            # entrypoint (derived from the argparse name by
            # convention).
            expected_entrypoint = argparse_fn.__name__.replace(
                "_blueprint_argparse",
                "_blueprint_entrypoint",
            )
            expected_action = f"pyscript.{expected_entrypoint}"
            action_name = first.get("action", "")
            assert action_name == expected_action, (
                f"{label}: blueprint {basename} action:"
                f" {action_name!r} does not match the registered"
                f" entrypoint {expected_action!r}"
            )


class TestModuleReloadLock:
    """Reader-writer lock for dispatcher-managed module reload.

    Readers are in-flight blueprint dispatches; writers are
    reload passes over ``_RELOAD_MODULES``. Writers must
    drain existing readers, exclude other writers, and hold
    off new readers; readers must coexist freely when no
    writer is pending or active.

    The primitives are async (``asyncio.Condition`` under the
    hood) because the production caller is a ``@service``
    entrypoint running as a task on HA's event loop. Each
    test drives them inside its own ``asyncio.run`` so the
    condition has a live loop to bind to.
    """

    def test_multiple_readers_coexist(self) -> None:
        import asyncio

        env = _ServiceEnv()
        acquire_read = env._ns["_acquire_read_lock"]
        release_read = env._ns["_release_read_lock"]
        reader_count = env._ns["_MODULE_READER_COUNT"]

        async def _body() -> None:
            await acquire_read()
            await acquire_read()
            assert reader_count[0] == 2
            await release_read()
            await release_read()
            assert reader_count[0] == 0

        asyncio.run(_body())

    def test_writer_waits_for_readers(self) -> None:
        import asyncio

        env = _ServiceEnv()
        acquire_read = env._ns["_acquire_read_lock"]
        release_read = env._ns["_release_read_lock"]
        acquire_write = env._ns["_acquire_write_lock"]
        release_write = env._ns["_release_write_lock"]
        writers_waiting = env._ns["_MODULE_WRITERS_WAITING"]

        async def _body() -> None:
            await acquire_read()

            writer_done = asyncio.Event()

            async def _writer() -> None:
                await acquire_write()
                writer_done.set()
                await release_write()

            writer = asyncio.create_task(_writer())
            # Yield enough times for the writer to land in
            # its ``cond.wait()`` call. One yield isn't enough
            # because the writer acquires the condition lock
            # first, bumps WRITERS_WAITING, then waits.
            for _ in range(10):
                await asyncio.sleep(0)
            assert not writer_done.is_set()
            assert writers_waiting[0] == 1

            await release_read()
            await asyncio.wait_for(writer_done.wait(), timeout=1.0)
            await writer

        asyncio.run(_body())

    def test_new_reader_blocks_while_writer_pending(self) -> None:
        import asyncio

        env = _ServiceEnv()
        acquire_read = env._ns["_acquire_read_lock"]
        release_read = env._ns["_release_read_lock"]
        acquire_write = env._ns["_acquire_write_lock"]
        release_write = env._ns["_release_write_lock"]
        writer_active = env._ns["_MODULE_WRITER_ACTIVE"]

        async def _body() -> None:
            await acquire_read()  # reader 1 holds the lock

            write_started = asyncio.Event()
            write_done = asyncio.Event()

            async def _writer() -> None:
                await acquire_write()
                write_started.set()
                # Yield a few times so reader 2 has a chance
                # to run and observe the active writer.
                for _ in range(5):
                    await asyncio.sleep(0)
                await release_write()
                write_done.set()

            writer = asyncio.create_task(_writer())
            # Let the writer flip WRITERS_WAITING before
            # reader 2 joins the queue.
            for _ in range(5):
                await asyncio.sleep(0)

            read2_done = asyncio.Event()

            async def _reader2() -> None:
                await acquire_read()
                read2_done.set()
                await release_read()

            reader2 = asyncio.create_task(_reader2())
            for _ in range(5):
                await asyncio.sleep(0)
            assert not read2_done.is_set()

            await release_read()  # drain reader 1
            await asyncio.wait_for(write_started.wait(), timeout=1.0)
            # While the writer is still active reader 2 must
            # stay blocked -- writer exclusion, not just
            # waiting-flag exclusion.
            assert not read2_done.is_set()
            assert writer_active[0] is True
            await asyncio.wait_for(write_done.wait(), timeout=1.0)
            await asyncio.wait_for(read2_done.wait(), timeout=1.0)
            await writer
            await reader2

        asyncio.run(_body())

    def test_two_writers_are_mutually_exclusive(self) -> None:
        """Writer-vs-writer exclusion.

        With only WRITER_WAITING (the pre-fix version of this
        lock), two concurrent writers both saw the flag as
        already-set, both proceeded past their wait condition,
        and mutated ``sys.modules`` concurrently -- and a
        reader woken by the first writer's release could run
        alongside the second still-active writer.

        The fixed lock tracks WRITER_ACTIVE separately so
        writers fully exclude each other.
        """
        import asyncio

        env = _ServiceEnv()
        acquire_write = env._ns["_acquire_write_lock"]
        release_write = env._ns["_release_write_lock"]
        writer_active = env._ns["_MODULE_WRITER_ACTIVE"]

        async def _body() -> None:
            entered: list[int] = []
            concurrent_seen: list[bool] = [False]

            async def _writer(tag: int) -> None:
                await acquire_write()
                entered.append(tag)
                # If two writers coexist here at any moment,
                # we'd see len(entered) > 1 before either
                # releases. Yield control a few times to give
                # any racing writer a chance to land.
                for _ in range(5):
                    if len(entered) > 1:
                        concurrent_seen[0] = True
                        break
                    await asyncio.sleep(0)
                # Pop so the next arrival starts from a
                # clean slate; otherwise a slow test could
                # misread the final list as two coexisting
                # writers when really they were sequential.
                entered.remove(tag)
                await release_write()

            w1 = asyncio.create_task(_writer(1))
            w2 = asyncio.create_task(_writer(2))
            await asyncio.wait_for(
                asyncio.gather(w1, w2),
                timeout=1.0,
            )
            assert concurrent_seen[0] is False
            assert writer_active[0] is False

        asyncio.run(_body())


class TestMaybeReloadChangedModules:
    """mtime-gated reload of tracked modules.

    Tests load a real stub module via ``importlib.import_module``
    so ``sys.modules`` has the correct spec/name entry that
    ``importlib.reload`` looks up. ``_RELOAD_MODULES`` is
    extended for the duration of each test to include the
    stub name.
    """

    def _make_stub(
        self,
        env: _ServiceEnv,
        tmp_path: Path,
        mod_name: str,
        body: str,
    ) -> Path:
        """Write ``body`` to a module file, import it, and
        register it in the env's ``_RELOAD_MODULES``."""
        fake = tmp_path / f"{mod_name}.py"
        fake.write_text(body)
        if str(tmp_path) not in sys.path:
            sys.path.insert(0, str(tmp_path))
        import importlib  # noqa: PLC0415

        importlib.import_module(mod_name)
        env._ns["_RELOAD_MODULES"] = (
            *env._ns["_RELOAD_MODULES"],
            mod_name,
        )
        return fake

    def _cleanup_stub(self, tmp_path: Path, mod_name: str) -> None:
        sys.modules.pop(mod_name, None)
        tp = str(tmp_path)
        if tp in sys.path:
            sys.path.remove(tp)

    def test_no_change_is_noop(self, tmp_path: Path) -> None:
        import asyncio

        env = _ServiceEnv()
        cache = env._ns["_MODULE_MTIMES"]
        maybe_reload = env._ns["_maybe_reload_changed_modules"]
        mod_name = "stub_noop_mod"

        fake = self._make_stub(env, tmp_path, mod_name, "VALUE = 1\n")
        try:
            pre_mod = sys.modules[mod_name]
            cache[mod_name] = os.stat(fake).st_mtime
            asyncio.run(maybe_reload())
            # Same module object (not reloaded) and same value.
            assert sys.modules[mod_name] is pre_mod
            assert sys.modules[mod_name].VALUE == 1
        finally:
            self._cleanup_stub(tmp_path, mod_name)

    def test_mtime_advance_triggers_reload(
        self,
        tmp_path: Path,
    ) -> None:
        import asyncio

        env = _ServiceEnv()
        cache = env._ns["_MODULE_MTIMES"]
        maybe_reload = env._ns["_maybe_reload_changed_modules"]
        mod_name = "stub_bump_mod"

        fake = self._make_stub(env, tmp_path, mod_name, "VALUE = 1\n")
        try:
            cache[mod_name] = os.stat(fake).st_mtime
            fake.write_text("VALUE = 2\n")
            # Bump mtime well past the cached value so the
            # filesystem's 1-second mtime granularity can't
            # mask the advance on a fast test run.
            os.utime(
                fake,
                (os.stat(fake).st_atime, cache[mod_name] + 10),
            )
            asyncio.run(maybe_reload())
            assert sys.modules[mod_name].VALUE == 2
            assert cache[mod_name] >= os.stat(fake).st_mtime
        finally:
            self._cleanup_stub(tmp_path, mod_name)

    def test_module_missing_from_sys_modules_is_skipped(self) -> None:
        import asyncio

        env = _ServiceEnv()
        cache = env._ns["_MODULE_MTIMES"]
        maybe_reload = env._ns["_maybe_reload_changed_modules"]
        reload_modules = env._ns["_RELOAD_MODULES"]

        # Pick a tracked name that's not in sys.modules for
        # this test run and confirm maybe_reload is a no-op.
        probe = "stub_not_in_sys_modules"
        assert probe not in sys.modules
        env._ns["_RELOAD_MODULES"] = (*reload_modules, probe)
        cache[probe] = 0.0
        asyncio.run(maybe_reload())  # must not raise
        # Entry stays at 0.0 since there was no module to stat.
        assert cache[probe] == 0.0


class TestStscEntityValidation:
    def test_missing_entity_creates_notification(
        self,
    ) -> None:
        env = _ServiceEnv()
        # Remove the default entity so validation fails
        del env.mock_state._store["switch.test_fan"]
        env.call()
        assert len(env.mock_pn.create_calls) == 1
        msg = env.mock_pn.create_calls[0]["message"]
        assert "switch.test_fan" in msg

    def test_missing_entity_no_action(self) -> None:
        env = _ServiceEnv()
        del env.mock_state._store["switch.test_fan"]
        env.call()
        assert len(env.mock_ha.turn_on_calls) == 0
        assert len(env.mock_ha.turn_off_calls) == 0

    def test_valid_entity_dismisses_notification(
        self,
    ) -> None:
        env = _ServiceEnv()
        env.call()
        assert len(env.mock_pn.dismiss_calls) == 1

    def test_notification_title_uses_friendly_name(
        self,
    ) -> None:
        env = _ServiceEnv()
        env.mock_state._attrs["auto.test_instance"] = {
            "friendly_name": "My Fan Controller"
        }
        del env.mock_state._store["switch.test_fan"]
        env.call()
        title = env.mock_pn.create_calls[0]["title"]
        assert "My Fan Controller" in title

    def test_unsupported_domain_creates_notification(
        self,
    ) -> None:
        env = _ServiceEnv()
        env.set_entity_state("sensor.temperature", "22")
        env.call(
            target_switch_entity="sensor.temperature",
        )
        assert len(env.mock_pn.create_calls) == 1
        msg = env.mock_pn.create_calls[0]["message"]
        assert "does not support on/off" in msg

    def test_unsupported_domain_no_action(self) -> None:
        env = _ServiceEnv()
        env.set_entity_state("sensor.temperature", "22")
        env.call(
            target_switch_entity="sensor.temperature",
        )
        assert len(env.mock_ha.turn_on_calls) == 0


class TestValidateEntitiesDomainChecks:
    """Test _validate_entities domain validation."""

    def test_controllable_domain_accepted(
        self,
    ) -> None:
        env = _ServiceEnv()
        fn = env._ns["_validate_entities"]
        et = env._ns["EntityType"]
        env.set_entity_state("light.test", "off")
        errors = fn(["light.test"], et.CONTROLLABLE)
        assert errors == []

    def test_non_controllable_domain_rejected(
        self,
    ) -> None:
        env = _ServiceEnv()
        fn = env._ns["_validate_entities"]
        et = env._ns["EntityType"]
        env.set_entity_state("sensor.temp", "22")
        errors = fn(["sensor.temp"], et.CONTROLLABLE)
        assert len(errors) == 1
        assert "does not support on/off" in errors[0]

    def test_binary_domain_accepted(self) -> None:
        env = _ServiceEnv()
        fn = env._ns["_validate_entities"]
        et = env._ns["EntityType"]
        env.set_entity_state(
            "binary_sensor.motion",
            "off",
        )
        errors = fn(
            ["binary_sensor.motion"],
            et.BINARY,
        )
        assert errors == []

    def test_non_binary_domain_rejected(self) -> None:
        env = _ServiceEnv()
        fn = env._ns["_validate_entities"]
        et = env._ns["EntityType"]
        env.set_entity_state("sensor.temp", "22")
        errors = fn(["sensor.temp"], et.BINARY)
        assert len(errors) == 1
        assert "not a binary entity" in errors[0]

    def test_missing_entity_skips_domain_check(
        self,
    ) -> None:
        """A missing entity produces one 'does not exist'
        error, not an additional domain error."""
        env = _ServiceEnv()
        fn = env._ns["_validate_entities"]
        et = env._ns["EntityType"]
        # sensor.gone does not exist in mock state
        errors = fn(["sensor.gone"], et.CONTROLLABLE)
        assert len(errors) == 1
        assert "does not exist" in errors[0]

    def test_any_checks_existence_only(self) -> None:
        """EntityType.ANY skips domain checks."""
        env = _ServiceEnv()
        fn = env._ns["_validate_entities"]
        et = env._ns["EntityType"]
        env.set_entity_state("sensor.temp", "22")
        errors = fn(["sensor.temp"], et.ANY)
        assert errors == []


class TestValidateNotificationService:
    """_validate_notification_service unit tests."""

    def test_empty_returns_no_errors(self) -> None:
        env = _ServiceEnv()
        fn = env._ns["_validate_notification_service"]
        env.mock_hass.services.has_service_result = False
        assert fn("") == []
        # Empty input must not even consult hass.
        assert env.mock_hass.services.has_service_calls == []

    def test_existing_returns_no_errors(self) -> None:
        env = _ServiceEnv()
        fn = env._ns["_validate_notification_service"]
        env.mock_hass.services.has_service_result = True
        assert fn("notify.mobile_app_phone") == []

    def test_missing_returns_error(self) -> None:
        env = _ServiceEnv()
        fn = env._ns["_validate_notification_service"]
        env.mock_hass.services.has_service_result = False
        errors = fn("notify.mobile_app_gone")
        assert len(errors) == 1
        assert "notify.mobile_app_gone" in errors[0]
        assert "does not exist" in errors[0]

    def test_normalizes_bare_service_name(self) -> None:
        """Bare 'mobile_app_x' becomes notify.mobile_app_x."""
        env = _ServiceEnv()
        fn = env._ns["_validate_notification_service"]
        env.mock_hass.services.has_service_result = True
        fn("mobile_app_x")
        assert env.mock_hass.services.has_service_calls == [
            ("notify", "mobile_app_x"),
        ]

    def test_handles_missing_hass(self) -> None:
        """No hass in namespace -> NameError caught, no error."""
        env = _ServiceEnv()
        fn = env._ns["_validate_notification_service"]
        del env._ns["hass"]
        assert fn("notify.mobile_app_phone") == []


class TestSendNotificationHelper:
    """_send_notification helper unit tests."""

    def test_dispatches_qualified_name(self) -> None:
        env = _ServiceEnv()
        fn = env._ns["_send_notification"]
        fn("notify.mobile_app_phone", "hello", "[TAG]")
        assert env.mock_service.call_log == [
            (
                "notify",
                "mobile_app_phone",
                {"message": "hello"},
            ),
        ]

    def test_dispatches_bare_name_with_prefix(self) -> None:
        """Bare 'mobile_app_x' is normalized to notify."""
        env = _ServiceEnv()
        fn = env._ns["_send_notification"]
        fn("mobile_app_x", "hi", "[TAG]")
        assert env.mock_service.call_log == [
            (
                "notify",
                "mobile_app_x",
                {"message": "hi"},
            ),
        ]

    def test_empty_service_is_noop(self) -> None:
        env = _ServiceEnv()
        fn = env._ns["_send_notification"]
        fn("", "hello", "[TAG]")
        assert env.mock_service.call_log == []

    def test_empty_message_is_noop(self) -> None:
        env = _ServiceEnv()
        fn = env._ns["_send_notification"]
        fn("notify.phone", "", "[TAG]")
        assert env.mock_service.call_log == []

    def test_swallows_keyerror(self) -> None:
        """A KeyError from service.call must not propagate."""
        env = _ServiceEnv()
        fn = env._ns["_send_notification"]
        env.mock_service.call_exception = KeyError("phone")
        # Must return normally (no raise).
        fn("notify.phone", "hello", "[STSC: foo]")
        # Helper logged the failure.
        assert len(env.mock_log.warning_calls) == 1
        msg, args = env.mock_log.warning_calls[0]
        formatted = msg % args
        assert "[STSC: foo]" in formatted
        assert "notify.phone" in formatted

    def test_swallows_runtime_error(self) -> None:
        """Non-KeyError exceptions are also swallowed."""
        env = _ServiceEnv()
        fn = env._ns["_send_notification"]
        env.mock_service.call_exception = RuntimeError(
            "transient",
        )
        fn("notify.phone", "hello", "[TAG]")
        assert len(env.mock_log.warning_calls) == 1


class TestStscNotificationServiceValidation:
    """STSC entrypoint validates notification_service."""

    def test_missing_service_creates_notification(
        self,
    ) -> None:
        env = _ServiceEnv()
        env.mock_hass.services.has_service_result = False
        env.call(notification_service="notify.gone")
        assert len(env.mock_pn.create_calls) == 1
        msg = env.mock_pn.create_calls[0]["message"]
        assert "notify.gone" in msg
        assert "does not exist" in msg

    def test_missing_service_blocks_action(self) -> None:
        env = _ServiceEnv()
        env.mock_hass.services.has_service_result = False
        env.call(
            sensor_value="60.0",
            trigger_entity="sensor.humidity",
            notification_service="notify.gone",
        )
        # Validation failed -> early return -> no action,
        # no service.call dispatched.
        assert env.mock_ha.turn_on_calls == []
        assert env.mock_ha.turn_off_calls == []
        assert env.mock_service.call_log == []

    def test_valid_service_dismisses_notification(
        self,
    ) -> None:
        env = _ServiceEnv()
        env.mock_hass.services.has_service_result = True
        env.call(notification_service="notify.phone")
        assert len(env.mock_pn.dismiss_calls) == 1
        assert env.mock_pn.create_calls == []

    def test_empty_service_skips_validation(
        self,
    ) -> None:
        """Empty notification_service is valid."""
        env = _ServiceEnv()
        env.mock_hass.services.has_service_result = False
        env.call(notification_service="")
        # Default config-error path: dismiss only.
        assert env.mock_pn.create_calls == []


class TestStscIntInputValidation:
    """STSC rejects non-numeric / out-of-range int inputs."""

    def test_non_numeric_sampling_window_creates_notification(
        self,
    ) -> None:
        env = _ServiceEnv()
        env.call(sampling_window_seconds_raw="not-a-number")
        assert len(env.mock_pn.create_calls) == 1
        msg = env.mock_pn.create_calls[0]["message"]
        assert "sampling_window_seconds" in msg
        assert "must be an integer" in msg

    def test_out_of_range_auto_off_creates_notification(self) -> None:
        env = _ServiceEnv()
        env.call(auto_off_minutes_raw="9999")
        assert len(env.mock_pn.create_calls) == 1
        msg = env.mock_pn.create_calls[0]["message"]
        assert "auto_off_minutes" in msg
        assert "must be between 0 and 1440" in msg

    def test_invalid_int_blocks_action(self) -> None:
        env = _ServiceEnv()
        env.call(disable_window_seconds_raw="-5")
        # Parse error -> early return -> no action.
        assert env.mock_ha.turn_on_calls == []
        assert env.mock_ha.turn_off_calls == []


class TestStscStateSavedWhenNotifyRaises:
    """The reorder invariant: state saves even if
    notification dispatch raises.

    This is the load-bearing regression test for the
    bath fan flap incident on 2026-04-13.
    """

    def test_release_path_saves_state_on_notify_failure(
        self,
    ) -> None:
        from sensor_threshold_switch_controller import (
            Sample,
            State,
        )

        env = _ServiceEnv()
        env.mock_hass.services.has_service_result = True
        env.mock_service.call_exception = KeyError(
            "mobile_app_gone",
        )

        # Pre-seed state with an active baseline so the
        # release path fires.
        key = env.state_key_fn("auto.test_instance")
        s = State(
            baseline=60.0,
            samples=[Sample(value=62.0, timestamp=T0)],
            initialized=True,
        )
        env.mock_state.setattr(
            f"{key}.data",
            json.dumps(s.to_dict()),
        )

        # Sensor reading drops below release threshold.
        env.call(
            sensor_value="61.0",
            switch_state="on",
            trigger_entity="sensor.humidity",
            notification_service="notify.mobile_app_gone",
        )

        # Action happened.
        assert len(env.mock_ha.turn_off_calls) == 1
        # Notification was attempted (and raised).
        assert len(env.mock_service.call_log) == 1
        # _send_notification logged the failure.
        assert len(env.mock_log.warning_calls) == 1
        # State save still happened: baseline cleared.
        raw = env.mock_state.getattr(key).get("data", "")
        data = json.loads(raw)
        assert data["baseline"] is None


class TestImportBan:
    """Ban ``from <our_module> import X`` in wrapper + logic modules.

    The dispatcher's reload mechanism (``_maybe_reload_changed_modules``)
    propagates edits by reloading module objects in place. Code that
    binds individual symbols via ``from X import Y`` keeps a stale
    reference to the pre-reload object after a reload, defeating the
    refresh. ``import X`` + ``X.Y`` attribute access resolves through
    the (mutated-in-place) module object on each call, so it stays
    fresh.

    Imports inside ``if TYPE_CHECKING:`` blocks are allowed -- they
    never execute at runtime and have no staleness risk.
    """

    _RELOAD_MODULES = frozenset(
        {
            "helpers",
            "sensor_threshold_switch_controller",
        }
    )

    def _scan_paths(self) -> list[Path]:
        return [
            REPO_ROOT / "pyscript" / "blueprint_toolkit.py",
            *sorted((REPO_ROOT / "pyscript" / "modules").glob("*.py")),
        ]

    def _is_under_type_checking(
        self,
        node: ast.AST,
        ancestors: list[ast.AST],
    ) -> bool:
        for parent in ancestors:
            if (
                isinstance(parent, ast.If)
                and isinstance(parent.test, ast.Name)
                and parent.test.id == "TYPE_CHECKING"
            ):
                return True
        return False

    def test_no_from_imports_for_reloadable_modules(self) -> None:
        offenders: list[str] = []
        for path in self._scan_paths():
            tree = ast.parse(path.read_text())
            stack: list[tuple[ast.AST, list[ast.AST]]] = [
                (tree, []),
            ]
            while stack:
                node, ancestors = stack.pop()
                if (
                    isinstance(node, ast.ImportFrom)
                    and node.module in self._RELOAD_MODULES
                    and not self._is_under_type_checking(node, ancestors)
                ):
                    rel = path.relative_to(REPO_ROOT)
                    offenders.append(
                        f"{rel}:{node.lineno} 'from {node.module}"
                        f" import ...' (use 'import {node.module}'"
                        " + attribute access instead)"
                    )
                next_ancestors = [*ancestors, node]
                for child in ast.iter_child_nodes(node):
                    stack.append((child, next_ancestors))
        assert not offenders, (
            "from-imports of reloadable modules block dispatcher-managed"
            " reload from taking effect:\n  " + "\n  ".join(offenders)
        )


class TestCodeQuality(CodeQualityBase):
    ruff_targets = [
        "pyscript/blueprint_toolkit.py",
        "tests/test_blueprint_toolkit.py",
    ]
    mypy_targets = [
        "pyscript/blueprint_toolkit.py",
    ]


class TestPyScriptCompatibility:
    """Guard against PyScript AST evaluator limitations.

    PyScript evaluates ALL pyscript files -- both service
    wrappers (pyscript/*.py) and logic modules
    (pyscript/modules/*.py) -- with a custom AST
    interpreter, not standard Python. Even though some
    logic modules run via ``@pyscript_executor`` (native
    Python in a worker thread), they may also be imported
    directly by the wrapper, so all code must be
    compatible with the AST evaluator.

    Known limitations (pyscript 1.7.0):
      - @classmethod / @property unsupported
      - lambda closures cannot capture local variables
      - generator expressions (x for x in ...) unsupported
      - match/case unsupported
      - yield / yield from unsupported
      - all pyscript functions are async, so dunder methods
        (__eq__, __str__, etc.) defined in pyscript don't work
      - print() is intercepted (use log.* instead)
      - bare open() is not available (use io.open())
      - local variable annotations are evaluated at runtime
        (TYPE_CHECKING-only names cause NameError)

    These tests scan all pyscript files to prevent
    regressions.

    IMPORTANT -- paired evaluator sanity tests:
    When adding a new ``test_no_*`` guard here, add a
    matching negative test to
    ``TestHarnessSanity`` in
    ``tests/test_pyscript_eval_compat.py`` that feeds
    the banned construct through the real PyScript
    AstEval and asserts it actually raises.  That
    pairing is what makes this suite self-healing:
    if a future pyscript release starts accepting a
    construct we ban, the harness test fails and
    signals that the static ban can be removed.
    ``@staticmethod`` was one such case -- banned
    defensively but actually accepted by pyscript
    1.7.0; the ban was removed once the harness
    pairing confirmed it.
    """

    @staticmethod
    def _pyscript_files() -> list[Path]:
        """Return all .py files under pyscript/."""
        return sorted(_PYSCRIPT_DIR.rglob("*.py"))

    @staticmethod
    def _service_files() -> list[Path]:
        """Return service wrapper files (not modules).

        These run directly under the AST evaluator and
        have the strictest constraints.
        """
        return [
            p for p in _PYSCRIPT_DIR.glob("*.py") if p.name != "__init__.py"
        ]

    def test_no_classmethod_decorator(self) -> None:
        """@classmethod is not supported by PyScript."""
        for path in self._pyscript_files():
            src = path.read_text()
            tree = ast.parse(src, str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef):
                    continue
                for dec in node.decorator_list:
                    name = ""
                    if isinstance(dec, ast.Name):
                        name = dec.id
                    assert name != "classmethod", (
                        f"{path.name}:{node.lineno}"
                        f" @classmethod on {node.name}()"
                        " -- PyScript cannot call"
                        " classmethods. Use a module-"
                        "level function instead."
                    )

    def test_no_property_decorator(self) -> None:
        """@property is not supported by PyScript."""
        for path in self._pyscript_files():
            src = path.read_text()
            tree = ast.parse(src, str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef):
                    continue
                for dec in node.decorator_list:
                    name = ""
                    if isinstance(dec, ast.Name):
                        name = dec.id
                    assert name != "property", (
                        f"{path.name}:{node.lineno}"
                        f" @property on {node.name}()"
                        " -- PyScript does not support"
                        " built-in decorators."
                    )

    def test_no_lambda_in_pyscript_files(self) -> None:
        """Lambda closures break under the AST evaluator.

        Lambda functions are compiled to native Python via
        @pyscript_compile, which cannot capture variables
        from the enclosing pyscript scope.
        """
        for path in self._pyscript_files():
            src = path.read_text()
            tree = ast.parse(src, str(path))
            for node in ast.walk(tree):
                assert not isinstance(node, ast.Lambda), (
                    f"{path.name}:{node.lineno}"  # type: ignore[union-attr]
                    " lambda expression -- PyScript"
                    " lambdas cannot capture local"
                    " variables. Use a module-level"
                    " function instead."
                )

    def test_no_generator_expressions(self) -> None:
        """Generator expressions are not implemented."""
        for path in self._pyscript_files():
            src = path.read_text()
            tree = ast.parse(src, str(path))
            for node in ast.walk(tree):
                assert not isinstance(node, ast.GeneratorExp), (
                    f"{path.name}:{node.lineno}"  # type: ignore[union-attr]
                    " generator expression -- not"
                    " implemented in PyScript. Use"
                    " a list comprehension instead."
                )

    def test_no_yield_in_pyscript_files(self) -> None:
        """yield / yield from are not supported."""
        for path in self._pyscript_files():
            src = path.read_text()
            tree = ast.parse(src, str(path))
            for node in ast.walk(tree):
                assert not isinstance(
                    node,
                    (ast.Yield, ast.YieldFrom),
                ), (
                    f"{path.name}:{node.lineno}"  # type: ignore[union-attr]
                    " yield -- not supported in"
                    " PyScript."
                )

    def test_no_match_case(self) -> None:
        """match/case is not implemented in PyScript."""
        for path in self._pyscript_files():
            src = path.read_text()
            # ast.Match exists only in Python 3.10+
            match_cls = getattr(ast, "Match", None)
            if match_cls is None:
                return
            tree = ast.parse(src, str(path))
            for node in ast.walk(tree):
                assert not isinstance(node, match_cls), (
                    f"{path.name}:{node.lineno}"  # type: ignore[union-attr]
                    " match/case -- not implemented"
                    " in PyScript. Use if/elif/else."
                )

    def test_no_sort_key_in_pyscript_files(
        self,
    ) -> None:
        """sort(key=func) breaks under PyScript.

        PyScript wraps all function calls as coroutines,
        so sort(key=func) compares coroutine objects
        instead of return values. Use tuple-based sorting
        instead: [(key, item) for item in items].sort().
        """
        for path in self._pyscript_files():
            src = path.read_text()
            tree = ast.parse(src, str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                # Check .sort(key=...) and sorted(key=...)
                func = node.func
                is_sort = False
                if isinstance(func, ast.Attribute):
                    is_sort = func.attr in (
                        "sort",
                        "sorted",
                    )
                elif isinstance(func, ast.Name):
                    is_sort = func.id == "sorted"
                if not is_sort:
                    continue
                for kw in node.keywords:
                    assert kw.arg != "key", (
                        f"{path.name}:{node.lineno}"
                        " sort(key=func) -- PyScript"
                        " wraps key function calls as"
                        " coroutines, breaking"
                        " comparison. Use tuple-based"
                        " sorting instead."
                    )

    def test_no_print_in_pyscript_files(self) -> None:
        """print() is intercepted by PyScript; use log.*."""
        for path in self._pyscript_files():
            src = path.read_text()
            # Simple regex: print( at start of line or
            # after whitespace, ignoring comments/strings
            # is hard -- just flag obvious calls.
            for i, line in enumerate(src.splitlines(), start=1):
                stripped = line.lstrip()
                if stripped.startswith("#"):
                    continue
                assert not re.match(r".*\bprint\s*\(", stripped), (
                    f"{path.name}:{i} print() call"
                    " -- PyScript intercepts print()."
                    " Use log.warning/info/error."
                )

    def test_no_bare_open_in_pyscript_files(self) -> None:
        """Bare open() is not available under the AST evaluator.

        Use ``io.open()`` instead. PyScript removes the
        ``open`` builtin from the process namespace.
        """
        for path in self._pyscript_files():
            src = path.read_text()
            tree = ast.parse(src, str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if isinstance(func, ast.Name) and func.id == "open":
                    raise AssertionError(
                        f"{path.name}:{node.lineno}"
                        " bare open() call -- not"
                        " available under PyScript's"
                        " AST evaluator. Use io.open()"
                        " instead."
                    )

    def test_no_type_checking_names_in_local_annotations(
        self,
    ) -> None:
        """TYPE_CHECKING names in local annotations fail at runtime.

        PyScript's AST evaluator evaluates function-body
        variable annotations at runtime, unlike standard
        Python (which skips them per PEP 526). Any local
        annotation that references a name only imported
        under ``if TYPE_CHECKING:`` raises ``NameError``.

        Function signature annotations (parameters, return
        types) are fine -- they're evaluated lazily or
        written as string literals.
        """
        for path in self._pyscript_files():
            src = path.read_text()
            tree = ast.parse(src, str(path))

            # Collect names defined under TYPE_CHECKING.
            tc_names: set[str] = set()
            for node in ast.walk(tree):
                if not isinstance(node, ast.If):
                    continue
                test = node.test
                if not (
                    isinstance(test, ast.Name) and test.id == "TYPE_CHECKING"
                ):
                    continue
                for child in ast.walk(node):
                    if isinstance(child, ast.ClassDef):
                        tc_names.add(child.name)
                    elif isinstance(child, ast.ImportFrom):
                        for alias in child.names:
                            name = alias.asname or alias.name
                            tc_names.add(name)
                    elif isinstance(
                        child,
                        ast.AnnAssign,
                    ) and isinstance(child.target, ast.Name):
                        tc_names.add(child.target.id)

            if not tc_names:
                continue

            # Walk function bodies for AnnAssign nodes
            # that reference TYPE_CHECKING-only names.
            for func_node in ast.walk(tree):
                if not isinstance(
                    func_node,
                    ast.FunctionDef,
                ):
                    continue
                # Collect names imported at runtime
                # within this function -- those are
                # safe even if they share a name with
                # a TYPE_CHECKING import.
                runtime_names: set[str] = set()
                for stmt in ast.walk(func_node):
                    if isinstance(stmt, ast.ImportFrom):
                        for alias in stmt.names:
                            name = alias.asname or alias.name
                            runtime_names.add(name)
                    elif isinstance(stmt, ast.Import):
                        for alias in stmt.names:
                            name = alias.asname or alias.name
                            runtime_names.add(name)
                flaggable = tc_names - runtime_names
                if not flaggable:
                    continue
                for child in ast.walk(func_node):
                    if not isinstance(child, ast.AnnAssign):
                        continue
                    # Collect all Name references in the
                    # annotation subtree.
                    for name_node in ast.walk(
                        child.annotation,
                    ):
                        if not isinstance(
                            name_node,
                            ast.Name,
                        ):
                            continue
                        if name_node.id in flaggable:
                            raise AssertionError(
                                f"{path.name}:{child.lineno}"
                                f" local annotation uses"
                                f" TYPE_CHECKING name"
                                f" '{name_node.id}'."
                                f" PyScript evaluates"
                                f" local annotations at"
                                f" runtime. Remove the"
                                f" annotation or quote it."
                            )

    def test_no_future_annotations_import(self) -> None:
        """``from __future__ import annotations`` is a silent no-op.

        Standard Python 3.7+ treats this future import as a
        directive to make all annotations strings (PEP 563),
        deferring evaluation. PyScript's AST evaluator does
        not implement that behaviour -- the import parses
        cleanly but annotations are still evaluated at
        class-body / module-level / function-definition
        time. Including the import masks the real
        requirement (explicit string quotes around
        otherwise-unevaluable annotations), and creates
        false confidence that a broken annotation is safe.

        Verified empirically 2026-04-23: adding
        ``from __future__ import annotations`` to a
        pyscript module with ``bridge.X | None`` class-body
        annotations did not prevent the
        ``EvalLocalVar | NoneType`` failure on HA.
        """
        for path in self._pyscript_files():
            src = path.read_text()
            tree = ast.parse(src, str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom):
                    continue
                if node.module != "__future__":
                    continue
                for alias in node.names:
                    assert alias.name != "annotations", (
                        f"{path.name}:{node.lineno}"
                        " 'from __future__ import"
                        " annotations' -- PyScript does"
                        " not implement PEP 563, so this"
                        " is a no-op that masks real"
                        " annotation failures. Quote the"
                        " offending annotations"
                        " individually instead."
                    )

    def test_no_attribute_union_annotation_in_modules(self) -> None:
        """Ban ``name.Attr | X`` annotations in logic modules.

        Logic modules (``pyscript/modules/*.py``) are loaded
        by pyscript's own module loader, which wraps the
        imported module in an ``EvalLocalVar``. At class-
        body / module-level / function-definition time,
        pyscript evaluates annotation expressions. A
        ``name.Attr | Other`` annotation where ``name`` is
        an imported module evaluates
        ``EvalLocalVar.__or__(Other)`` -- which raises
        ``TypeError: unsupported operand type(s) for |:
        'EvalLocalVar' and ...``.

        Workaround: write the annotation as a string
        literal, e.g. ``"bridge.X | None"``. PyScript's
        AST evaluator passes string annotations through
        without evaluation (same behaviour as
        PEP 563 when honoured).

        Bare-name annotations (``RouteSpeed | None``
        imported via ``from zwave_js_ui_bridge import
        RouteSpeed``) hit the same failure because the
        imported name is also an ``EvalLocalVar``; but
        that pattern is already blocked by
        ``test_no_from_imports_for_reloadable_modules``.
        This ban specifically catches the
        ``module.Attr | X`` form that survives that
        earlier ban.

        Verified empirically 2026-04-23:
        ``bridge.RouteSpeed | None`` at line 50 of
        ``pyscript/modules/zwave_route_manager.py`` raised
        ``TypeError`` on every HA load attempt,
        cascading to saturate HA core's event loop.
        """
        modules_dir = _PYSCRIPT_DIR / "modules"

        def _annotation_violates(ann: ast.AST | None) -> ast.BinOp | None:
            """Find a ``X.Y | Z`` BinOp inside an annotation, or None."""
            if ann is None:
                return None
            for sub in ast.walk(ann):
                if not isinstance(sub, ast.BinOp):
                    continue
                if not isinstance(sub.op, ast.BitOr):
                    continue
                for operand in (sub.left, sub.right):
                    if isinstance(operand, ast.Attribute) and isinstance(
                        operand.value,
                        ast.Name,
                    ):
                        return sub
            return None

        for path in sorted(modules_dir.glob("*.py")):
            src = path.read_text()
            tree = ast.parse(src, str(path))
            # Every AnnAssign (class body / module level)
            # plus function signature annotations
            # (parameters, return type) get scanned.
            for node in ast.walk(tree):
                if isinstance(node, ast.AnnAssign):
                    offender = _annotation_violates(node.annotation)
                    if offender is not None:
                        raise AssertionError(
                            f"{path.name}:{node.lineno}"
                            " annotation uses"
                            " ``module.Attr | X`` form --"
                            " PyScript's AST evaluator"
                            " wraps imported modules in"
                            " EvalLocalVar, which does"
                            " not implement ``|``. Quote"
                            " the annotation as a string"
                            " literal instead (e.g."
                            ' ``"bridge.X | None"``).'
                        )
                if isinstance(node, ast.FunctionDef):
                    for arg in node.args.args:
                        offender = _annotation_violates(arg.annotation)
                        if offender is not None:
                            raise AssertionError(
                                f"{path.name}:{arg.lineno}"
                                f" parameter '{arg.arg}' uses"
                                " ``module.Attr | X`` -- quote"
                                " the annotation as a string"
                                " literal."
                            )
                    ret_offender = _annotation_violates(node.returns)
                    if ret_offender is not None:
                        raise AssertionError(
                            f"{path.name}:{node.lineno}"
                            f" return type of '{node.name}()'"
                            " uses ``module.Attr | X`` --"
                            " quote the annotation as a"
                            " string literal."
                        )

    def test_no_module_attribute_iteration(self) -> None:
        """Ban ``for X in <reloadable>.Attr:`` iteration.

        Pyscript's AST evaluator wraps values pulled from an
        imported-module attribute in ``EvalLocalVar``. The
        resulting object supports ``getattr`` and ``()`` calls
        but not the iterator protocol -- iterating it raises
        ``TypeError: 'EvalLocalVar' object is not iterable``.

        This pattern typically appears when the wrapper
        iterates an enum defined in a logic module, e.g.::

            for rt in zrm.RouteType:
                if rt.value == wanted:
                    return rt

        The fix is to expose a lookup helper in the module
        itself (e.g. ``<module>.lookup_by_value(...)``) so
        the iteration happens in the module's own native-
        Python scope; the wrapper then calls the helper via
        module attribute, which pyscript handles correctly.

        Verified empirically 2026-04-24: the wrapper's
        ``_zrm_path_from_storage`` raised this error on
        every reconcile that tried to hydrate stored state.
        """
        # Reloadable modules whose attributes are unsafe to
        # iterate from wrapper scope. Same set
        # ``TestImportBan._RELOAD_MODULES`` covers for the
        # ``from X import Y`` ban.
        reloadable = frozenset(
            {
                "helpers",
                "sensor_threshold_switch_controller",
            }
        )

        for path in self._pyscript_files():
            src = path.read_text()
            tree = ast.parse(src, str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.For):
                    continue
                tgt = node.iter
                if not isinstance(tgt, ast.Attribute):
                    continue
                if not isinstance(tgt.value, ast.Name):
                    continue
                mod_name = tgt.value.id
                if mod_name not in reloadable:
                    continue
                rel = path.relative_to(REPO_ROOT)
                raise AssertionError(
                    f"{rel}:{node.lineno} iterates"
                    f" ``{mod_name}.{tgt.attr}`` -- PyScript's"
                    " AST evaluator wraps imported-module"
                    " attributes in EvalLocalVar, which raises"
                    " ``TypeError: 'EvalLocalVar' object is not"
                    " iterable``. Add a lookup helper in the"
                    f" ``{mod_name}`` module and call it via"
                    f" ``{mod_name}.<helper>(...)`` from the"
                    " wrapper."
                )

    def test_no_enum_identity_comparison_against_module_member(
        self,
    ) -> None:
        """Ban ``X == <reloadable>.Enum.MEMBER`` comparisons.

        pyscript re-creates enum instances across the
        AST / native-Python boundary (``@pyscript_executor``
        workers). Two enum members with the same ``.value``
        compare unequal because ``Enum.__eq__`` uses identity,
        and the instances are from different module loads.
        Accessing the class attribute (``zrm.RouteActionKind``)
        also routes through pyscript's EvalLocalVar wrapping,
        compounding the identity mismatch.

        Compare by ``.value`` string instead (or build a
        ``{value: member}`` dict inside the module and look up
        via a helper).

        Verified empirically 2026-04-24: the ZRM apply
        dispatcher's
        ``if kind == zrm.RouteActionKind.CLEAR_PRIORITY_SUC_RETURN_ROUTES:``
        always fell through to the "unknown RouteActionKind"
        branch, so every CLEAR / SET action landed as an
        apply-failure notification instead of actually being
        sent to the bridge.
        """
        reloadable = frozenset(
            {
                "helpers",
                "sensor_threshold_switch_controller",
            }
        )

        def _deep_attr_module(node: ast.AST) -> str | None:
            """If ``node`` is ``<name>.<attr>.<attr>`` rooted
            at a reloadable module, return the module name.
            """
            if not isinstance(node, ast.Attribute):
                return None
            inner = node.value
            # Walk outward through Attribute chains (class.member,
            # or deeper).
            while isinstance(inner, ast.Attribute):
                inner = inner.value
            if not isinstance(inner, ast.Name):
                return None
            return inner.id

        for path in self._pyscript_files():
            src = path.read_text()
            tree = ast.parse(src, str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Compare):
                    continue
                if not any(isinstance(op, ast.Eq) for op in node.ops):
                    continue
                # Check every side of the comparison for a
                # ``module.Class.MEMBER`` reference.
                sides: list[ast.AST] = [node.left, *node.comparators]
                for side in sides:
                    mod_name = _deep_attr_module(side)
                    if mod_name is None or mod_name not in reloadable:
                        continue
                    # Require at least two levels deep
                    # (``module.Class.MEMBER``) to avoid false
                    # positives on plain ``module.constant``
                    # comparisons. Enum-member access always
                    # has that shape.
                    depth = 0
                    cur: ast.AST = side
                    while isinstance(cur, ast.Attribute):
                        depth += 1
                        cur = cur.value
                    if depth < 2:
                        continue
                    rel = path.relative_to(REPO_ROOT)
                    raise AssertionError(
                        f"{rel}:{node.lineno} compares against"
                        f" ``{ast.unparse(side)}`` -- pyscript"
                        " re-creates enum instances across the"
                        " AST / native-Python boundary and the"
                        " class attribute goes through"
                        " EvalLocalVar, so enum-identity"
                        " comparisons always return False."
                        " Compare by ``.value`` string instead"
                        " (e.g. ``kind.value =="
                        ' "set_application_route"``).'
                    )


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _SCRIPT_PATH, REPO_ROOT)
