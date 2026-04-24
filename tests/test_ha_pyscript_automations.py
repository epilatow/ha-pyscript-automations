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
"""Tests for the pyscript/ha_pyscript_automations.py bridge.

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
import types
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).parent.parent

# Path to the script under test (used for coverage)
_SCRIPT_PATH = REPO_ROOT / "pyscript" / "ha_pyscript_automations.py"

# All pyscript .py files (service wrappers + modules)
_PYSCRIPT_DIR = REPO_ROOT / "pyscript"

# Ensure pyscript/modules is importable whether run
# via pytest or directly via uv run --script.
sys.path.insert(0, str(REPO_ROOT / "pyscript" / "modules"))

from datetime import UTC  # noqa: E402

import pytest  # noqa: E402
from conftest import CodeQualityBase  # noqa: E402
from entity_defaults_watchdog import (  # noqa: E402
    DRIFT_CHECK_DEVICE_ENTITY_ID,
    DRIFT_CHECK_DEVICE_ENTITY_NAME,
    DRIFT_CHECK_ENTITY_ID,
    DevicelessEntityInfo,
)
from helpers import EntityRegistryInfo, on_interval  # noqa: E402

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
        pn_cls = env._ns["PersistentNotification"]
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
        pn_cls = env._ns["PersistentNotification"]
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
        assert "ha_pyscript_automations.py" in n.message

    def test_body_includes_remediation_hint(self) -> None:
        env = _ServiceEnv()
        n = self._call(env, missing=["x"], extras=[])
        assert "ha-pyscript-automations repository" in n.message
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
        REPO_ROOT / "blueprints" / "automation" / "ha_pyscript_automations"
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


# -- Device Watchdog mock infrastructure --------------


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


class _WatchdogEnv:
    """Loads service file and wires mocks for watchdog.

    Replaces registry helpers in the exec'd namespace
    with controllable mocks, avoiding any dependency
    on HA packages.
    """

    def __init__(
        self,
        current_time: datetime = T0_UTC,
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
            "hass": self.mock_hass,
            "persistent_notification": self.mock_pn,
        }
        exec(
            compile(src, str(_SCRIPT_PATH), "exec"),
            self._ns,
        )
        self.set_now(current_time)

        # Default mock registry responses
        self._integration_entities: dict[str, list[str]] = {}
        self._device_for_entity: dict[str, dict[str, str] | None] = {}
        self._entity_states: dict[str, tuple[str, datetime]] = {}
        self._registry_entries: dict[str, list[Any]] = {}

        # Wire mock helpers into the namespace
        self._ns["_get_integration_entities"] = (
            self._mock_get_integration_entities
        )
        self._ns["_get_device_for_entity"] = self._mock_get_device_for_entity
        self._ns["_read_entity_state"] = self._mock_read_entity_state
        self._ns["_get_all_integration_ids"] = (
            self._mock_get_all_integration_ids
        )

    def _mock_get_integration_entities(
        self,
        _hass: Any,
        integration_id: str,
    ) -> list[str]:
        return self._integration_entities.get(
            integration_id,
            [],
        )

    def _mock_get_device_for_entity(
        self,
        _hass: Any,
        entity_id: str,
    ) -> dict[str, str] | None:
        return self._device_for_entity.get(entity_id)

    def _mock_get_all_integration_ids(
        self,
        _hass: Any,
    ) -> list[str]:
        return sorted(self._integration_entities.keys())

    def _mock_read_entity_state(
        self,
        entity_id: str,
    ) -> tuple[str | None, datetime | None]:
        entry = self._entity_states.get(entity_id)
        if entry is None:
            return None, None
        return entry

    def set_now(self, dt: datetime) -> None:
        """Override datetime.now() for calls."""
        self._ns["datetime"] = _ControllableDatetime(dt)

    def remove_hass(self) -> None:
        """Remove hass from namespace to simulate missing."""
        del self._ns["hass"]

    def setup_registry_entry(
        self,
        device_id: str,
        entity_id: str,
        original_name: str,
        platform: str,
        entity_category: str | None = None,
        disabled_by: str | None = None,
    ) -> None:
        """Add a mock entity registry entry for a device."""
        cat = None
        if entity_category is not None:
            cat = types.SimpleNamespace(
                value=entity_category,
            )
        entry = types.SimpleNamespace(
            entity_id=entity_id,
            original_name=original_name,
            platform=platform,
            entity_category=cat,
            disabled_by=disabled_by,
        )
        self._registry_entries.setdefault(
            device_id,
            [],
        ).append(entry)

    def _install_entity_registry_mock(
        self,
    ) -> None:
        """Inject mock homeassistant.helpers.entity_registry.

        Call cleanup_entity_registry_mock() after the test
        to remove injected sys.modules entries.
        """
        registry_entries = self._registry_entries
        _ER_MODULES = [
            "homeassistant",
            "homeassistant.helpers",
            "homeassistant.helpers.entity_registry",
        ]
        self._er_saved: dict[str, Any] = {
            k: sys.modules.get(k) for k in _ER_MODULES
        }

        def async_get(_hass: Any) -> str:
            return "mock_ent_reg"

        def async_entries_for_device(
            _ent_reg: Any,
            device_id: str,
            include_disabled_entities: bool = False,
        ) -> list[Any]:
            return registry_entries.get(device_id, [])

        mock_er = types.ModuleType(
            "homeassistant.helpers.entity_registry",
        )
        mock_er.async_get = async_get  # type: ignore[attr-defined]
        mock_er.async_entries_for_device = async_entries_for_device  # type: ignore[attr-defined]
        sys.modules["homeassistant"] = types.ModuleType("homeassistant")
        sys.modules["homeassistant.helpers"] = types.ModuleType(
            "homeassistant.helpers",
        )
        sys.modules["homeassistant.helpers.entity_registry"] = mock_er

    def cleanup_entity_registry_mock(self) -> None:
        """Remove injected sys.modules entries."""
        for k, v in self._er_saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v

    def setup_device(
        self,
        integration: str,
        device_id: str,
        device_name: str,
        entities: dict[str, tuple[str, datetime]],
    ) -> None:
        """Wire up a device with entities.

        entities: {entity_id: (state, last_reported)}
        """
        # Add entity to integration
        current = self._integration_entities.get(
            integration,
            [],
        )
        for eid in entities:
            if eid not in current:
                current.append(eid)
            self._device_for_entity[eid] = {
                "id": device_id,
                "name": device_name,
                "default_name": device_name,
            }
        self._integration_entities[integration] = current

        # Add entity states
        for eid, (st, lc) in entities.items():
            self._entity_states[eid] = (st, lc)

    @property
    def watchdog_fn(self) -> Any:
        return self._ns["device_watchdog_blueprint_entrypoint"]

    def call(self, **kwargs: Any) -> None:
        import asyncio

        asyncio.run(self.watchdog_fn(**_dw_default_kwargs(**kwargs)))


def _dw_default_kwargs(**overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "instance_id": "auto.dw_test",
        "include_integrations_raw": ["zwave_js"],
        "exclude_integrations_raw": [],
        "device_exclude_regex_raw": "",
        "entity_id_exclude_regex_raw": "",
        "monitored_entity_domains_raw": [],
        "check_interval_minutes_raw": "1",
        "dead_device_threshold_minutes_raw": "1440",
        "enabled_checks_raw": [
            "unavailable-entities",
            "device-updates",
        ],
        "max_device_notifications_raw": "0",
        "debug_logging_raw": "false",
        "trigger_platform_raw": "time_pattern",
    }
    defaults.update(overrides)
    return defaults


class TestDeviceWatchdogHassDetection:
    """Test hass_is_global detection."""

    _NOTIF_ID = "device_watchdog_auto_dw_test__config_error"

    def test_missing_hass_notifies_and_skips(
        self,
    ) -> None:
        env = _WatchdogEnv()
        env.remove_hass()
        env.setup_device(
            "zwave_js",
            "dev1",
            "Dev",
            {"sensor.a": ("unavailable", T0_UTC)},
        )
        env.call()
        # Config error notification only, no device checks
        assert len(env.mock_pn.create_calls) == 1
        call = env.mock_pn.create_calls[0]
        assert "hass_is_global" in call["message"]
        assert call["notification_id"] == self._NOTIF_ID
        assert env.mock_pn.dismiss_calls == []

    def test_present_hass_dismisses_config_error(
        self,
    ) -> None:
        env = _WatchdogEnv()
        env.call()
        config_creates = [
            c
            for c in env.mock_pn.create_calls
            if c.get("notification_id") == self._NOTIF_ID
        ]
        assert config_creates == []
        config_dismissals = [
            c
            for c in env.mock_pn.dismiss_calls
            if c.get("notification_id") == self._NOTIF_ID
        ]
        assert len(config_dismissals) == 1


class TestDeviceWatchdogRegexValidation:
    """Test invalid regex detection."""

    def test_invalid_device_regex_notifies(self) -> None:
        env = _WatchdogEnv()
        env.call(device_exclude_regex_raw="[invalid")
        assert len(env.mock_pn.create_calls) == 1
        call = env.mock_pn.create_calls[0]
        assert "Invalid" in call["title"]
        assert "device_exclude_regex" in call["message"]
        assert "[invalid" in call["message"]

    def test_config_error_includes_automation_link(self) -> None:
        """Config-error notifications flow through
        _process_persistent_notifications like device
        notifications, so they get the same link header."""
        env = _WatchdogEnv()
        env.mock_state._attrs["auto.dw_test"] = {
            "friendly_name": "Device Watchdog 73",
            "id": "1700000000001",
        }
        env.call(device_exclude_regex_raw="[invalid")
        assert len(env.mock_pn.create_calls) == 1
        msg = env.mock_pn.create_calls[0]["message"]
        assert msg.startswith(
            "Automation: [Device Watchdog 73]"
            "(/config/automation/edit/1700000000001)\n",
        )

    def test_invalid_entity_regex_notifies(self) -> None:
        env = _WatchdogEnv()
        env.call(entity_id_exclude_regex_raw="(unclosed")
        assert len(env.mock_pn.create_calls) == 1
        msg = env.mock_pn.create_calls[0]["message"]
        assert "entity_id_exclude_regex" in msg
        assert "(unclosed" in msg

    def test_both_invalid_reports_both(self) -> None:
        env = _WatchdogEnv()
        env.call(
            device_exclude_regex_raw="[bad",
            entity_id_exclude_regex_raw="(bad",
        )
        assert len(env.mock_pn.create_calls) == 1
        msg = env.mock_pn.create_calls[0]["message"]
        assert "device_exclude_regex" in msg
        assert "entity_id_exclude_regex" in msg

    def test_invalid_regex_skips_evaluation(self) -> None:
        env = _WatchdogEnv()
        env.setup_device(
            "zwave_js",
            "dev1",
            "Dev",
            {"sensor.a": ("unavailable", T0_UTC)},
        )
        env.call(device_exclude_regex_raw="[invalid")
        # Only the config error, no device notifications
        assert len(env.mock_pn.create_calls) == 1
        assert env.mock_pn.dismiss_calls == []

    def test_valid_regex_no_error(self) -> None:
        env = _WatchdogEnv()
        env.call(
            device_exclude_regex_raw=".*test.*",
            entity_id_exclude_regex_raw="^sensor\\.bat",
        )
        config_errors = [
            c
            for c in env.mock_pn.create_calls
            if "Invalid" in c.get("title", "")
        ]
        assert config_errors == []

    def test_empty_match_regex_rejected(self) -> None:
        """Regex like '|||||' matches everything."""
        env = _WatchdogEnv()
        env.call(device_exclude_regex_raw="|||||")
        assert len(env.mock_pn.create_calls) == 1
        assert "empty string" in (env.mock_pn.create_calls[0]["message"])


class TestDeviceWatchdogIntInputValidation:
    """DW rejects non-numeric / out-of-range int inputs."""

    def test_non_numeric_check_interval_notifies(self) -> None:
        env = _WatchdogEnv()
        env.call(check_interval_minutes_raw="not-a-number")
        assert len(env.mock_pn.create_calls) == 1
        msg = env.mock_pn.create_calls[0]["message"]
        assert "check_interval_minutes" in msg
        assert "must be an integer" in msg

    def test_out_of_range_dead_threshold_notifies(self) -> None:
        env = _WatchdogEnv()
        env.call(dead_device_threshold_minutes_raw="0")
        assert len(env.mock_pn.create_calls) == 1
        msg = env.mock_pn.create_calls[0]["message"]
        assert "dead_device_threshold_minutes" in msg
        assert "must be between 1 and 10080" in msg

    def test_all_int_errors_reported(self) -> None:
        env = _WatchdogEnv()
        env.call(
            check_interval_minutes_raw="abc",
            max_device_notifications_raw="-1",
        )
        assert len(env.mock_pn.create_calls) == 1
        msg = env.mock_pn.create_calls[0]["message"]
        assert "check_interval_minutes" in msg
        assert "max_device_notifications" in msg


class TestDeviceWatchdogEnabledChecks:
    """Test enabled_checks parsing and validation."""

    def test_unknown_check_notifies_and_skips(self) -> None:
        env = _WatchdogEnv()
        env.setup_device(
            "zwave_js",
            "dev1",
            "Dev",
            {"sensor.a": ("unavailable", T0_UTC)},
        )
        env.call(enabled_checks_raw=["not-a-check"])
        config_errors = [
            c
            for c in env.mock_pn.create_calls
            if "Invalid" in c.get("title", "")
        ]
        assert len(config_errors) == 1
        assert "not-a-check" in config_errors[0]["message"]
        device_creates = [
            c
            for c in env.mock_pn.create_calls
            if c["notification_id"].startswith(
                "device_watchdog_auto_dw_test__device_",
            )
        ]
        assert device_creates == []

    def test_empty_selection_runs_all_checks(self) -> None:
        """Empty selection = all checks, including
        diagnostics (which requires the registry)."""
        env = _WatchdogEnv()
        env.setup_device(
            "zwave_js",
            "dev1",
            "Stale",
            {
                "sensor.a": (
                    "42",
                    T0_UTC - timedelta(days=2),
                ),
            },
        )
        env._install_entity_registry_mock()
        try:
            env.call(enabled_checks_raw=[])
            stale_creates = [
                c
                for c in env.mock_pn.create_calls
                if c["notification_id"]
                == "device_watchdog_auto_dw_test__device_dev1"
            ]
            assert len(stale_creates) == 1
            assert "No entity state report" in stale_creates[0]["message"]
        finally:
            env.cleanup_entity_registry_mock()

    def test_staleness_only_ignores_unavailable(self) -> None:
        env = _WatchdogEnv()
        env.setup_device(
            "zwave_js",
            "dev_u",
            "UnavailOnly",
            {"sensor.a": ("unavailable", T0_UTC)},
        )
        env.setup_device(
            "zwave_js",
            "dev_s",
            "StaleOnly",
            {
                "sensor.b": (
                    "42",
                    T0_UTC - timedelta(days=2),
                ),
            },
        )
        env.call(enabled_checks_raw=["device-updates"])
        creates = {
            c["notification_id"]: c
            for c in env.mock_pn.create_calls
            if c["notification_id"].startswith(
                "device_watchdog_auto_dw_test__device_",
            )
        }
        assert "device_watchdog_auto_dw_test__device_dev_s" in creates
        assert "device_watchdog_auto_dw_test__device_dev_u" not in creates

    def test_unavailable_only_ignores_staleness(self) -> None:
        env = _WatchdogEnv()
        env.setup_device(
            "zwave_js",
            "dev_u",
            "UnavailOnly",
            {"sensor.a": ("unavailable", T0_UTC)},
        )
        env.setup_device(
            "zwave_js",
            "dev_s",
            "StaleOnly",
            {
                "sensor.b": (
                    "42",
                    T0_UTC - timedelta(days=2),
                ),
            },
        )
        env.call(
            enabled_checks_raw=["unavailable-entities"],
        )
        creates = {
            c["notification_id"]: c
            for c in env.mock_pn.create_calls
            if c["notification_id"].startswith(
                "device_watchdog_auto_dw_test__device_",
            )
        }
        assert "device_watchdog_auto_dw_test__device_dev_u" in creates
        assert "device_watchdog_auto_dw_test__device_dev_s" not in creates


class TestDeviceWatchdogIntervalGating:
    """Test check interval gating."""

    def test_skips_when_off_interval(self) -> None:
        # T0_UTC is 12:00:00; interval 60 min, set to 12:01
        env = _WatchdogEnv(
            current_time=datetime(
                2024,
                1,
                15,
                12,
                1,
                0,
                tzinfo=UTC,
            ),
        )
        env.setup_device(
            "zwave_js",
            "dev1",
            "Dev",
            {"sensor.a": ("unavailable", T0_UTC)},
        )
        env.call(check_interval_minutes_raw="60")
        # Should not have created any notifications
        assert env.mock_pn.create_calls == []
        # Only config error dismiss (config validation
        # runs before interval gating)
        device_dismisses = [
            c
            for c in env.mock_pn.dismiss_calls
            if "config_error" not in c["notification_id"]
        ]
        assert device_dismisses == []

    def test_runs_on_interval_boundary(self) -> None:
        boundary = _boundary_time("auto.dw_test", 60, T0_UTC)
        env = _WatchdogEnv(current_time=boundary)
        env.setup_device(
            "zwave_js",
            "dev1",
            "Dev",
            {"sensor.a": ("unavailable", T0_UTC)},
        )
        env.call(check_interval_minutes_raw="60")
        assert len(env.mock_pn.create_calls) == 1

    def test_no_debug_attrs_when_gated(self) -> None:
        boundary = _boundary_time("auto.dw_test", 60, T0_UTC)
        env = _WatchdogEnv(
            current_time=boundary + timedelta(minutes=1),
        )
        env.call(check_interval_minutes_raw="60")
        key = "pyscript.auto_dw_test_state"
        assert env.mock_state.get(key) is None

    def test_manual_trigger_bypasses_interval_gate(
        self,
    ) -> None:
        """Manual UI run should always evaluate."""
        env = _WatchdogEnv(
            current_time=datetime(
                2024,
                1,
                15,
                12,
                1,
                0,
                tzinfo=UTC,
            ),
        )
        env.setup_device(
            "zwave_js",
            "dev1",
            "Dev",
            {"sensor.a": ("unavailable", T0_UTC)},
        )
        env.call(
            check_interval_minutes_raw="60",
            trigger_platform_raw="manual",
        )
        # Should have created a notification despite
        # being off the interval boundary
        assert len(env.mock_pn.create_calls) == 1


class TestDeviceWatchdogDiscovery:
    """Test device discovery from registries."""

    @staticmethod
    def _device_notifications(
        env: _WatchdogEnv,
    ) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        """Filter to device-level notifications only.

        Excludes config-error and cap notifications.
        """
        skip = ("config_error", "_cap")
        creates = [
            c
            for c in env.mock_pn.create_calls
            if not any(s in c["notification_id"] for s in skip)
        ]
        dismissals = [
            c
            for c in env.mock_pn.dismiss_calls
            if not any(s in c["notification_id"] for s in skip)
        ]
        return creates, dismissals

    def test_discovers_device_from_integration(
        self,
    ) -> None:
        env = _WatchdogEnv()
        env.setup_device(
            "zwave_js",
            "dev1",
            "Kitchen Sensor",
            {"sensor.temp": ("22.5", T0_UTC)},
        )
        env.call()
        creates, dismissals = self._device_notifications(
            env,
        )
        # Should dismiss (healthy device)
        assert len(dismissals) == 1
        assert (
            dismissals[0]["notification_id"]
            == "device_watchdog_auto_dw_test__device_dev1"
        )

    def test_deduplicates_devices(self) -> None:
        env = _WatchdogEnv()
        # Two entities on same device
        env.setup_device(
            "zwave_js",
            "dev1",
            "Multi Sensor",
            {
                "sensor.temp": ("22.5", T0_UTC),
                "sensor.humid": ("55.0", T0_UTC),
            },
        )
        env.call()
        creates, dismissals = self._device_notifications(
            env,
        )
        # One device, one dismiss
        assert len(creates) + len(dismissals) == 1

    def test_multiple_integrations(self) -> None:
        env = _WatchdogEnv()
        env.setup_device(
            "zwave_js",
            "dev1",
            "ZWave Dev",
            {"sensor.zw": ("ok", T0_UTC)},
        )
        env.setup_device(
            "matter",
            "dev2",
            "Matter Dev",
            {"sensor.mt": ("ok", T0_UTC)},
        )
        env.call(
            include_integrations_raw=["zwave_js", "matter"],
        )
        creates, dismissals = self._device_notifications(
            env,
        )
        assert len(creates) + len(dismissals) == 2

    def test_no_devices_no_notifications(self) -> None:
        env = _WatchdogEnv()
        env.call()
        creates, dismissals = self._device_notifications(
            env,
        )
        assert creates == []
        assert dismissals == []


class TestDeviceWatchdogNotifications:
    """Test notification create/dismiss behavior."""

    def test_creates_for_unavailable(self) -> None:
        env = _WatchdogEnv()
        env.setup_device(
            "zwave_js",
            "dev1",
            "Bad Device",
            {"sensor.a": ("unavailable", T0_UTC)},
        )
        env.call()
        assert len(env.mock_pn.create_calls) == 1
        call = env.mock_pn.create_calls[0]
        assert call["notification_id"] == (
            "device_watchdog_auto_dw_test__device_dev1"
        )
        assert "Bad Device" in call["title"]

    def test_dismisses_for_healthy(self) -> None:
        env = _WatchdogEnv()
        env.setup_device(
            "zwave_js",
            "dev1",
            "Good Device",
            {"sensor.a": ("22.5", T0_UTC)},
        )
        env.call()
        device_dismissals = [
            c
            for c in env.mock_pn.dismiss_calls
            if "config_error" not in c["notification_id"]
            and "_cap" not in c["notification_id"]
        ]
        assert len(device_dismissals) == 1
        assert (
            device_dismissals[0]["notification_id"]
            == "device_watchdog_auto_dw_test__device_dev1"
        )

    def test_creates_for_stale(self) -> None:
        old = T0_UTC - timedelta(hours=25)
        env = _WatchdogEnv()
        env.setup_device(
            "zwave_js",
            "dev1",
            "Stale Device",
            {"sensor.a": ("22.5", old)},
        )
        env.call()
        assert len(env.mock_pn.create_calls) == 1

    def test_mixed_healthy_and_unhealthy(self) -> None:
        old = T0_UTC - timedelta(hours=25)
        env = _WatchdogEnv()
        env.setup_device(
            "zwave_js",
            "dev1",
            "Healthy",
            {"sensor.a": ("22.5", T0_UTC)},
        )
        env.setup_device(
            "zwave_js",
            "dev2",
            "Sick",
            {"sensor.b": ("unavailable", old)},
        )
        env.call()
        assert len(env.mock_pn.create_calls) == 1
        device_dismissals = [
            c
            for c in env.mock_pn.dismiss_calls
            if "config_error" not in c["notification_id"]
            and "_cap" not in c["notification_id"]
        ]
        assert len(device_dismissals) == 1

    def test_message_has_automation_link(self) -> None:
        """When the automation exposes an ``id`` attribute,
        notifications get an ``Automation: [name](url)``
        header linking to the edit page."""
        env = _WatchdogEnv()
        env.mock_state._attrs["auto.dw_test"] = {
            "friendly_name": "Device Watchdog 73",
            "id": "1700000000001",
        }
        env.setup_device(
            "zwave_js",
            "dev1",
            "Bad Device",
            {"sensor.a": ("unavailable", T0_UTC)},
        )
        env.call()
        msg = env.mock_pn.create_calls[0]["message"]
        assert msg.startswith(
            "Automation: [Device Watchdog 73]"
            "(/config/automation/edit/1700000000001)\n",
        )

    def test_message_no_link_when_id_missing(self) -> None:
        """Without an ``id`` attribute, messages dispatch
        unchanged."""
        env = _WatchdogEnv()
        env.mock_state._attrs["auto.dw_test"] = {
            "friendly_name": "Device Watchdog 73",
        }
        env.setup_device(
            "zwave_js",
            "dev1",
            "Bad Device",
            {"sensor.a": ("unavailable", T0_UTC)},
        )
        env.call()
        msg = env.mock_pn.create_calls[0]["message"]
        assert not msg.startswith("Automation:")


class TestDeviceWatchdogDiagnostics:
    """Integration tests for diagnostic entity checking."""

    def test_disabled_diagnostic_creates_notification(
        self,
    ) -> None:
        env = _WatchdogEnv()
        env.setup_device(
            "zwave_js",
            "dev1",
            "Front Lock",
            {"sensor.a": ("ok", T0_UTC)},
        )
        env.setup_registry_entry(
            device_id="dev1",
            entity_id="sensor.last_seen",
            original_name="Last seen",
            platform="zwave_js",
            entity_category="diagnostic",
            disabled_by="user",
        )
        env._install_entity_registry_mock()
        try:
            env.call(
                enabled_checks_raw=[
                    "unavailable-entities",
                    "device-updates",
                    "disabled-diagnostics",
                ],
            )
            diag_creates = [
                c
                for c in env.mock_pn.create_calls
                if c["notification_id"].startswith(
                    "device_watchdog_auto_dw_test__diag_",
                )
            ]
            assert len(diag_creates) == 1
            assert "Last seen" in diag_creates[0]["message"]
            assert "Front Lock" in diag_creates[0]["title"]
        finally:
            env.cleanup_entity_registry_mock()

    def test_enabled_diagnostic_dismisses(
        self,
    ) -> None:
        env = _WatchdogEnv()
        env.setup_device(
            "zwave_js",
            "dev1",
            "Lock",
            {"sensor.a": ("ok", T0_UTC)},
        )
        env.setup_registry_entry(
            device_id="dev1",
            entity_id="sensor.last_seen",
            original_name="Last seen",
            platform="zwave_js",
            entity_category="diagnostic",
        )
        env._install_entity_registry_mock()
        try:
            env.call(
                enabled_checks_raw=[
                    "unavailable-entities",
                    "device-updates",
                    "disabled-diagnostics",
                ],
            )
            diag_creates = [
                c
                for c in env.mock_pn.create_calls
                if c["notification_id"].startswith(
                    "device_watchdog_auto_dw_test__diag_",
                )
            ]
            diag_dismissals = [
                c
                for c in env.mock_pn.dismiss_calls
                if c["notification_id"].startswith(
                    "device_watchdog_auto_dw_test__diag_",
                )
            ]
            assert diag_creates == []
            assert len(diag_dismissals) == 1
        finally:
            env.cleanup_entity_registry_mock()

    def test_diagnostics_not_selected_no_diag_notifs(
        self,
    ) -> None:
        """When disabled-diagnostics isn't in enabled_checks,
        no diagnostic notifications are produced."""
        env = _WatchdogEnv()
        env.setup_device(
            "zwave_js",
            "dev1",
            "Lock",
            {"sensor.a": ("ok", T0_UTC)},
        )
        env.call()  # default omits disabled-diagnostics
        diag_notifs = [
            c
            for c in (env.mock_pn.create_calls + env.mock_pn.dismiss_calls)
            if c["notification_id"].startswith(
                "device_watchdog_auto_dw_test__diag_",
            )
        ]
        assert diag_notifs == []


class TestDeviceWatchdogDebugAttrs:
    """Test debug attribute writing."""

    def test_writes_debug_attrs(self) -> None:
        env = _WatchdogEnv()
        env.setup_device(
            "zwave_js",
            "dev1",
            "Dev",
            {"sensor.a": ("22.5", T0_UTC)},
        )
        env.call()
        key = "pyscript.auto_dw_test_state"
        assert env.mock_state.get(key) == "ok"
        attrs = env.mock_state.getattr(key)
        assert "last_run" in attrs
        assert "runtime" in attrs
        assert attrs["devices"] == 1
        assert attrs["device_issues"] == 0

    def test_devices_with_issues_count(self) -> None:
        env = _WatchdogEnv()
        env.setup_device(
            "zwave_js",
            "dev1",
            "Bad",
            {"sensor.a": ("unavailable", T0_UTC)},
        )
        env.setup_device(
            "zwave_js",
            "dev2",
            "Good",
            {"sensor.b": ("22.5", T0_UTC)},
        )
        env.call()
        key = "pyscript.auto_dw_test_state"
        attrs = env.mock_state.getattr(key)
        assert attrs["devices"] == 2
        assert attrs["device_issues"] == 1

    def test_integrations_attr(self) -> None:
        env = _WatchdogEnv()
        env.setup_device(
            "zwave_js",
            "dev1",
            "Dev",
            {"sensor.a": ("22.5", T0_UTC)},
        )
        env.call(
            include_integrations_raw=["zwave_js"],
        )
        key = "pyscript.auto_dw_test_state"
        attrs = env.mock_state.getattr(key)
        assert attrs["integrations"] >= 1


class TestDeviceWatchdogDebugLogging:
    """Test debug logging behavior."""

    def test_no_logging_when_debug_false(self) -> None:
        env = _WatchdogEnv()
        env.call(debug_logging_raw="false")
        assert env.mock_log.warning_calls == []

    def test_logging_when_debug_true(self) -> None:
        env = _WatchdogEnv()
        env.setup_device(
            "zwave_js",
            "dev1",
            "Dev",
            {"sensor.a": ("22.5", T0_UTC)},
        )
        env.call(debug_logging_raw="true")
        assert len(env.mock_log.warning_calls) == 1
        msg = env.mock_log.warning_calls[0][0]
        args = env.mock_log.warning_calls[0][1]
        formatted = msg % args
        assert "[DW:" in formatted

    def test_log_includes_issue_count(self) -> None:
        env = _WatchdogEnv()
        env.setup_device(
            "zwave_js",
            "dev1",
            "Bad Dev",
            {"sensor.a": ("unavailable", T0_UTC)},
        )
        env.call(debug_logging_raw="true")
        msg = env.mock_log.warning_calls[0][0]
        args = env.mock_log.warning_calls[0][1]
        formatted = msg % args
        assert "device_issues=1" in formatted


# -- Trigger Entity Controller ---------------------


class _TecEnv:
    """Loads service file and wires mocks for TEC."""

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

    def set_now(self, dt: datetime) -> None:
        self._ns["datetime"] = _ControllableDatetime(dt)

    def set_entity_state(
        self,
        entity_id: str,
        entity_state: str,
    ) -> None:
        self.mock_state._store[entity_id] = entity_state

    @property
    def tec_fn(self) -> Any:
        return self._ns["trigger_entity_controller_blueprint_entrypoint"]

    @property
    def state_key_fn(self) -> Any:
        return self._ns["_state_key"]

    def call(self, **kwargs: Any) -> None:
        import asyncio

        asyncio.run(self.tec_fn(**_tec_defaults(**kwargs)))


def _tec_defaults(**overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "instance_id": "auto.tec_test",
        "controlled_entities_raw": ["light.hallway"],
        "trigger_entity_id": "timer",
        "trigger_to_state": "",
        "auto_off_minutes_raw": "0",
        "trigger_entities_raw": ["binary_sensor.motion"],
        "trigger_period_raw": "always",
        "trigger_forces_on_raw": "false",
        "trigger_disabling_entities_raw": [],
        "trigger_disabling_period_raw": "always",
        "auto_off_disabling_entities_raw": [],
        "notification_service": "",
        "notification_prefix_raw": "",
        "notification_suffix_raw": "",
        "notification_events_raw": [],
        "debug_logging_raw": "false",
    }
    defaults.update(overrides)
    return defaults


class TestTecServiceLoads:
    def test_exec_succeeds(self) -> None:
        _TecEnv()

    def test_function_callable(self) -> None:
        env = _TecEnv()
        assert callable(env.tec_fn)

    def test_timer_event_no_crash(self) -> None:
        env = _TecEnv()
        env.set_entity_state("light.hallway", "off")
        env.set_entity_state(
            "binary_sensor.motion",
            "off",
        )
        env.call()


class TestTecEntityValidation:
    def test_missing_entity_creates_notification(
        self,
    ) -> None:
        env = _TecEnv()
        # light.hallway not in state -> missing
        env.set_entity_state(
            "binary_sensor.motion",
            "off",
        )
        env.call()
        assert len(env.mock_pn.create_calls) == 1
        msg = env.mock_pn.create_calls[0]["message"]
        assert "light.hallway" in msg

    def test_missing_entity_no_action(self) -> None:
        env = _TecEnv()
        env.call(
            trigger_entity_id="binary_sensor.motion",
            trigger_to_state="on",
        )
        assert len(env.mock_ha.turn_on_calls) == 0

    def test_valid_entities_dismiss_notification(
        self,
    ) -> None:
        env = _TecEnv()
        env.set_entity_state("light.hallway", "off")
        env.set_entity_state(
            "binary_sensor.motion",
            "off",
        )
        env.call()
        assert len(env.mock_pn.dismiss_calls) == 1


class TestTecEntityOverlap:
    def test_controlled_and_trigger_overlap_error(
        self,
    ) -> None:
        env = _TecEnv()
        env.set_entity_state("light.hallway", "off")
        env.set_entity_state(
            "binary_sensor.motion",
            "off",
        )
        env.call(
            controlled_entities_raw=["light.hallway"],
            trigger_entities_raw=["light.hallway"],
        )
        assert len(env.mock_pn.create_calls) == 1
        msg = env.mock_pn.create_calls[0]["message"]
        assert "controlled and trigger" in msg

    def test_trigger_and_disabling_overlap_error(
        self,
    ) -> None:
        env = _TecEnv()
        env.set_entity_state("light.hallway", "off")
        env.set_entity_state(
            "binary_sensor.motion",
            "off",
        )
        env.call(
            trigger_disabling_entities_raw=[
                "binary_sensor.motion",
            ],
        )
        assert len(env.mock_pn.create_calls) == 1
        msg = env.mock_pn.create_calls[0]["message"]
        assert "trigger and disabling" in msg

    def test_disabling_overlap_allowed(self) -> None:
        """Same entity in both trigger_disabling and
        auto_off_disabling is allowed.
        """
        env = _TecEnv()
        env.set_entity_state("light.hallway", "off")
        env.set_entity_state(
            "binary_sensor.motion",
            "off",
        )
        env.set_entity_state(
            "input_boolean.occupied",
            "off",
        )
        env.call(
            trigger_disabling_entities_raw=[
                "input_boolean.occupied",
            ],
            auto_off_disabling_entities_raw=[
                "input_boolean.occupied",
            ],
        )
        assert len(env.mock_pn.dismiss_calls) == 1
        assert len(env.mock_pn.create_calls) == 0


class TestTecEventRouting:
    def test_trigger_on_turns_on(self) -> None:
        env = _TecEnv()
        env.set_entity_state("light.hallway", "off")
        env.set_entity_state(
            "binary_sensor.motion",
            "on",
        )
        env.call(
            trigger_entity_id="binary_sensor.motion",
            trigger_to_state="on",
        )
        assert len(env.mock_ha.turn_on_calls) == 1
        assert env.mock_ha.turn_on_calls[0]["entity_id"] == "light.hallway"

    def test_unavailable_state_ignored(self) -> None:
        env = _TecEnv()
        env.set_entity_state("light.hallway", "off")
        env.set_entity_state(
            "binary_sensor.motion",
            "unavailable",
        )
        env.call(
            trigger_entity_id="binary_sensor.motion",
            trigger_to_state="unavailable",
        )
        assert len(env.mock_ha.turn_on_calls) == 0
        assert len(env.mock_ha.turn_off_calls) == 0


class TestTecPreComputedBooleans:
    def test_sun_above_horizon_is_daytime(self) -> None:
        env = _TecEnv()
        env.set_entity_state("light.hallway", "off")
        env.set_entity_state(
            "binary_sensor.motion",
            "on",
        )
        env.set_entity_state(
            "sun.sun",
            "above_horizon",
        )
        env.call(
            trigger_entity_id="binary_sensor.motion",
            trigger_to_state="on",
            trigger_period_raw="night-time",
        )
        # night-time period + daytime = suppressed
        assert len(env.mock_ha.turn_on_calls) == 0

    def test_disabling_entity_suppresses(self) -> None:
        env = _TecEnv()
        env.set_entity_state("light.hallway", "off")
        env.set_entity_state(
            "binary_sensor.motion",
            "on",
        )
        env.set_entity_state(
            "input_boolean.occupied",
            "on",
        )
        env.call(
            trigger_entity_id="binary_sensor.motion",
            trigger_to_state="on",
            trigger_disabling_entities_raw=[
                "input_boolean.occupied",
            ],
            trigger_disabling_period_raw="always",
        )
        assert len(env.mock_ha.turn_on_calls) == 0


class TestTecActions:
    def test_turn_on_multiple_entities(self) -> None:
        env = _TecEnv()
        entities = [
            "light.hallway",
            "light.hallway_2",
        ]
        env.set_entity_state("light.hallway", "off")
        env.set_entity_state("light.hallway_2", "off")
        env.set_entity_state(
            "binary_sensor.motion",
            "on",
        )
        env.call(
            controlled_entities_raw=entities,
            trigger_entity_id="binary_sensor.motion",
            trigger_to_state="on",
        )
        assert len(env.mock_ha.turn_on_calls) == 2

    def test_timer_turn_off(self) -> None:
        env = _TecEnv()
        env.set_entity_state("light.hallway", "on")
        env.set_entity_state(
            "binary_sensor.motion",
            "off",
        )
        # Pre-set auto_off_at in the past
        key = env.state_key_fn("auto.tec_test")
        past = (T0 - timedelta(seconds=1)).isoformat()
        env.mock_state.set(key, "ok")
        env.mock_state.setattr(
            f"{key}.auto_off_at",
            past,
        )
        env.call()
        assert len(env.mock_ha.turn_off_calls) == 1


class TestTecNotifications:
    def test_notification_sent(self) -> None:
        env = _TecEnv()
        env.set_entity_state("light.hallway", "off")
        env.set_entity_state(
            "binary_sensor.motion",
            "on",
        )
        env.call(
            trigger_entity_id="binary_sensor.motion",
            trigger_to_state="on",
            notification_service="notify",
            notification_events_raw=["triggered-on"],
        )
        assert len(env.mock_service.call_log) == 1
        domain, svc, kwargs = env.mock_service.call_log[0]
        assert domain == "notify"
        assert svc == "notify"
        assert "Triggered on" in kwargs["message"]

    def test_no_notification_without_service(
        self,
    ) -> None:
        env = _TecEnv()
        env.set_entity_state("light.hallway", "off")
        env.set_entity_state(
            "binary_sensor.motion",
            "on",
        )
        env.call(
            trigger_entity_id="binary_sensor.motion",
            trigger_to_state="on",
            notification_service="",
            notification_events_raw=["triggered-on"],
        )
        assert len(env.mock_service.call_log) == 0


class TestTecNotificationServiceValidation:
    """TEC entrypoint validates notification_service."""

    def test_missing_service_creates_notification(
        self,
    ) -> None:
        env = _TecEnv()
        env.set_entity_state("light.hallway", "off")
        env.set_entity_state(
            "binary_sensor.motion",
            "off",
        )
        env.mock_hass.services.has_service_result = False
        env.call(notification_service="notify.gone")
        assert len(env.mock_pn.create_calls) == 1
        msg = env.mock_pn.create_calls[0]["message"]
        assert "notify.gone" in msg

    def test_missing_service_blocks_action(self) -> None:
        env = _TecEnv()
        env.set_entity_state("light.hallway", "off")
        env.set_entity_state(
            "binary_sensor.motion",
            "on",
        )
        env.mock_hass.services.has_service_result = False
        env.call(
            trigger_entity_id="binary_sensor.motion",
            trigger_to_state="on",
            notification_service="notify.gone",
            notification_events_raw=["triggered-on"],
        )
        assert env.mock_ha.turn_on_calls == []
        assert env.mock_service.call_log == []

    def test_valid_service_dismisses_notification(
        self,
    ) -> None:
        env = _TecEnv()
        env.set_entity_state("light.hallway", "off")
        env.set_entity_state(
            "binary_sensor.motion",
            "off",
        )
        env.mock_hass.services.has_service_result = True
        env.call(notification_service="notify.phone")
        assert len(env.mock_pn.dismiss_calls) == 1


class TestTecIntInputValidation:
    """TEC rejects non-numeric / out-of-range auto_off_minutes."""

    def test_non_numeric_auto_off_creates_notification(
        self,
    ) -> None:
        env = _TecEnv()
        env.call(auto_off_minutes_raw="not-a-number")
        assert len(env.mock_pn.create_calls) == 1
        msg = env.mock_pn.create_calls[0]["message"]
        assert "auto_off_minutes" in msg
        assert "must be an integer" in msg

    def test_out_of_range_auto_off_creates_notification(
        self,
    ) -> None:
        env = _TecEnv()
        env.call(auto_off_minutes_raw="999")
        assert len(env.mock_pn.create_calls) == 1
        msg = env.mock_pn.create_calls[0]["message"]
        assert "auto_off_minutes" in msg
        assert "must be between 0 and 60" in msg

    def test_invalid_int_blocks_action(self) -> None:
        env = _TecEnv()
        env.set_entity_state("light.hallway", "off")
        env.set_entity_state("binary_sensor.motion", "on")
        env.call(
            auto_off_minutes_raw="bad",
            trigger_entity_id="binary_sensor.motion",
            trigger_to_state="on",
        )
        assert env.mock_ha.turn_on_calls == []
        assert env.mock_ha.turn_off_calls == []


class TestTecStateSavedWhenNotifyRaises:
    """The reorder invariant for TEC: state saves even
    if notification dispatch raises."""

    def test_triggered_on_persisted_on_notify_failure(
        self,
    ) -> None:
        env = _TecEnv()
        env.set_entity_state("light.hallway", "off")
        env.set_entity_state(
            "binary_sensor.motion",
            "on",
        )
        env.mock_hass.services.has_service_result = True
        env.mock_service.call_exception = KeyError(
            "mobile_app_gone",
        )

        env.call(
            trigger_entity_id="binary_sensor.motion",
            trigger_to_state="on",
            auto_off_minutes_raw="2",
            notification_service="notify.mobile_app_gone",
            notification_events_raw=["triggered-on"],
        )

        # Action happened.
        assert len(env.mock_ha.turn_on_calls) == 1
        # Notification was attempted (and raised).
        assert len(env.mock_service.call_log) == 1
        # _send_notification logged the failure.
        assert len(env.mock_log.warning_calls) == 1
        # State save happened despite the notify failure
        # (the load-bearing invariant): the run's
        # ``last_action`` attribute was persisted to the
        # entity by ``_save_state``.
        key = env.state_key_fn("auto.tec_test")
        attrs = env.mock_state.getattr(key)
        assert attrs.get("last_action") == "TURN_ON"
        assert attrs.get("last_event") is not None


class TestTecStatePersistence:
    def test_auto_off_at_persisted(self) -> None:
        env = _TecEnv()
        env.set_entity_state("light.hallway", "on")
        env.set_entity_state(
            "binary_sensor.motion",
            "off",
        )
        env.call(
            trigger_entity_id="binary_sensor.motion",
            trigger_to_state="off",
            auto_off_minutes_raw="2",
        )
        key = env.state_key_fn("auto.tec_test")
        attrs = env.mock_state.getattr(key)
        raw = attrs.get("auto_off_at", "")
        assert raw != ""
        expected = T0 + timedelta(minutes=2)
        assert raw == expected.isoformat()

    def test_auto_off_at_cleared(self) -> None:
        env = _TecEnv()
        env.set_entity_state("light.hallway", "off")
        env.set_entity_state(
            "binary_sensor.motion",
            "on",
        )
        env.call(
            trigger_entity_id="binary_sensor.motion",
            trigger_to_state="on",
        )
        key = env.state_key_fn("auto.tec_test")
        attrs = env.mock_state.getattr(key)
        assert attrs.get("auto_off_at", "") == ""


class TestTecDebug:
    def test_debug_attributes_written(self) -> None:
        env = _TecEnv()
        env.set_entity_state("light.hallway", "off")
        env.set_entity_state(
            "binary_sensor.motion",
            "off",
        )
        env.call()
        key = env.state_key_fn("auto.tec_test")
        attrs = env.mock_state.getattr(key)
        assert "last_run" in attrs
        assert "last_action" in attrs
        assert "last_reason" in attrs
        assert "last_event" in attrs

    def test_debug_logging_opt_in(self) -> None:
        env = _TecEnv()
        env.set_entity_state("light.hallway", "off")
        env.set_entity_state(
            "binary_sensor.motion",
            "off",
        )
        env.call(debug_logging_raw="true")
        assert len(env.mock_log.warning_calls) == 1

    def test_debug_logging_off_by_default(self) -> None:
        env = _TecEnv()
        env.set_entity_state("light.hallway", "off")
        env.set_entity_state(
            "binary_sensor.motion",
            "off",
        )
        env.call()
        assert len(env.mock_log.warning_calls) == 0


# -- Entity Defaults Watchdog mock infrastructure ---


class _EdwEnv:
    """Loads service file and wires mocks for EDW.

    Replaces registry helpers in the exec'd namespace
    with controllable mocks, avoiding any dependency
    on HA packages.
    """

    def __init__(
        self,
        current_time: datetime = T0_UTC,
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
            "hass": self.mock_hass,
            "persistent_notification": self.mock_pn,
        }
        exec(
            compile(src, str(_SCRIPT_PATH), "exec"),
            self._ns,
        )
        self.set_now(current_time)

        # Default mock registry responses
        self._integration_entities: dict[str, list[str]] = {}
        self._device_for_entity: dict[str, dict[str, str] | None] = {}
        self._entity_info: dict[str, dict[str, object]] = {}
        self._entity_expected_ids: dict[str, str | None] = {}
        self._all_integration_ids: list[str] = []
        # Deviceless mock state: list of DevicelessEntityInfo-shaped
        # dicts plus the peer map.  Tests populate via
        # setup_deviceless_entity().
        self._deviceless_entities: list[dict[str, object]] = []
        self._deviceless_peers: dict[str, set[str]] = {}
        # Target-integration set captured from the most
        # recent _discover_deviceless_entities call.
        self.deviceless_target_integrations: Any = None

        # Wire mock helpers into the namespace
        self._ns["_get_integration_entities"] = (
            self._mock_get_integration_entities
        )
        self._ns["_get_device_for_entity"] = self._mock_get_device_for_entity
        self._ns["_get_entity_info"] = self._mock_get_entity_info
        self._ns["_compute_expected_entity_id"] = (
            self._mock_compute_expected_entity_id
        )
        self._ns["_get_all_integration_ids"] = (
            self._mock_get_all_integration_ids
        )
        self._ns["_discover_deviceless_entities"] = (
            self._mock_discover_deviceless_entities
        )

    def _mock_get_integration_entities(
        self,
        _hass: Any,
        integration_id: str,
    ) -> list[str]:
        return self._integration_entities.get(
            integration_id,
            [],
        )

    def _mock_get_device_for_entity(
        self,
        _hass: Any,
        entity_id: str,
    ) -> dict[str, str] | None:
        return self._device_for_entity.get(entity_id)

    def _mock_get_entity_info(
        self,
        _hass: Any,
        entity_id: str,
    ) -> "EntityRegistryInfo | None":
        return self._entity_info.get(entity_id)

    def _mock_compute_expected_entity_id(
        self,
        _hass: Any,
        entity_id: str,
    ) -> str | None:
        return self._entity_expected_ids.get(entity_id)

    def _mock_get_all_integration_ids(
        self,
        _hass: Any,
    ) -> list[str]:
        ids = set(self._all_integration_ids)
        ids.update(self._integration_entities.keys())
        return sorted(ids)

    def _mock_discover_deviceless_entities(
        self,
        _hass: Any,
        _domains: Any,
        target_integrations: Any = None,
    ) -> Any:
        # Record filter passed by the service wrapper so
        # tests can assert filter propagation.
        self.deviceless_target_integrations = target_integrations
        entities = [
            DevicelessEntityInfo(
                entity_id=str(e["entity_id"]),
                effective_name=str(e.get("effective_name", "")),
                platform=e.get("platform"),  # type: ignore[arg-type]
                unique_id=e.get("unique_id"),  # type: ignore[arg-type]
                from_registry=bool(e.get("from_registry", True)),
            )
            for e in self._deviceless_entities
        ]
        return (entities, self._deviceless_peers)

    def setup_deviceless_entity(
        self,
        entity_id: str,
        effective_name: str = "",
        platform: str | None = None,
        unique_id: str | None = None,
        from_registry: bool = True,
    ) -> None:
        """Add a deviceless entity the deviceless check will see.

        Also registers it as a peer for collision-suffix
        detection within its domain.
        """
        self._deviceless_entities.append(
            {
                "entity_id": entity_id,
                "effective_name": effective_name,
                "platform": platform,
                "unique_id": unique_id,
                "from_registry": from_registry,
            },
        )
        dom, obj = entity_id.split(".", 1)
        self._deviceless_peers.setdefault(dom, set()).add(obj)

    def set_now(self, dt: datetime) -> None:
        """Override datetime.now() for calls."""
        self._ns["datetime"] = _ControllableDatetime(dt)

    def remove_hass(self) -> None:
        """Remove hass to simulate missing."""
        del self._ns["hass"]

    def setup_device(
        self,
        integration: str,
        device_id: str,
        device_name: str,
        entities: dict[str, dict[str, object]],
        default_name: str | None = None,
    ) -> None:
        """Wire up a device with entities.

        entities: {entity_id: {name, original_name,
            has_entity_name, expected_entity_id}}
        default_name: device.name from the integration
            (defaults to device_name).
        """
        def_name = default_name or device_name
        current = self._integration_entities.get(
            integration,
            [],
        )
        for eid, edata in entities.items():
            if eid not in current:
                current.append(eid)
            self._device_for_entity[eid] = {
                "id": device_id,
                "name": device_name,
                "default_name": def_name,
            }
            self._entity_info[eid] = EntityRegistryInfo(
                entity_id=eid,
                name=edata.get("name"),
                original_name=edata.get(
                    "original_name",
                ),
                has_entity_name=edata.get(
                    "has_entity_name",
                    True,
                ),
                device_id=device_id,
            )
            expected_id = edata.get("expected_entity_id")
            self._entity_expected_ids[eid] = (
                str(expected_id) if expected_id else None
            )
        self._integration_entities[integration] = current

    @property
    def edw_fn(self) -> Any:
        return self._ns["entity_defaults_watchdog_blueprint_entrypoint"]

    def call(self, **kwargs: Any) -> None:
        import asyncio

        asyncio.run(self.edw_fn(**_edw_default_kwargs(**kwargs)))


def _edw_default_kwargs(
    **overrides: Any,
) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "instance_id": "auto.edw_test",
        "trigger_platform_raw": "time_pattern",
        "drift_checks_raw": [],
        "include_integrations_raw": ["zwave_js"],
        "exclude_integrations_raw": [],
        "device_exclude_regex_raw": "",
        "exclude_entities_raw": [],
        "entity_id_exclude_regex_raw": "",
        "entity_name_exclude_regex_raw": "",
        "check_interval_minutes_raw": "1",
        "max_device_notifications_raw": "0",
        "debug_logging_raw": "false",
    }
    defaults.update(overrides)
    return defaults


class TestEdwHassDetection:
    """Test hass_is_global detection for EDW."""

    def test_missing_hass_notifies(self) -> None:
        env = _EdwEnv()
        env.remove_hass()
        env.call()
        assert len(env.mock_pn.create_calls) == 1
        call = env.mock_pn.create_calls[0]
        assert "hass_is_global" in call["message"]
        assert call["notification_id"] == (
            "entity_defaults_watchdog_auto_edw_test__config_error"
        )

    def test_present_hass_no_config_error(
        self,
    ) -> None:
        env = _EdwEnv()
        env.call()
        config_errors = [
            c
            for c in env.mock_pn.create_calls
            if c.get("notification_id")
            == "entity_defaults_watchdog_config_error"
        ]
        assert config_errors == []


class TestEdwRegexValidation:
    """Test invalid regex detection for EDW."""

    def test_invalid_device_regex(self) -> None:
        env = _EdwEnv()
        env.call(device_exclude_regex_raw="[invalid")
        assert len(env.mock_pn.create_calls) == 1
        call = env.mock_pn.create_calls[0]
        assert "Invalid" in call["title"]
        assert "device_exclude_regex" in call["message"]

    def test_invalid_entity_id_regex(self) -> None:
        env = _EdwEnv()
        env.call(
            entity_id_exclude_regex_raw="(unclosed",
        )
        assert len(env.mock_pn.create_calls) == 1
        msg = env.mock_pn.create_calls[0]["message"]
        assert "entity_id_exclude_regex" in msg

    def test_invalid_entity_name_regex(self) -> None:
        env = _EdwEnv()
        env.call(
            entity_name_exclude_regex_raw="(bad",
        )
        assert len(env.mock_pn.create_calls) == 1
        msg = env.mock_pn.create_calls[0]["message"]
        assert "entity_name_exclude_regex" in msg

    def test_all_invalid_reports_all(self) -> None:
        env = _EdwEnv()
        env.call(
            device_exclude_regex_raw="[bad",
            entity_id_exclude_regex_raw="(bad",
            entity_name_exclude_regex_raw="(bad",
        )
        assert len(env.mock_pn.create_calls) == 1
        msg = env.mock_pn.create_calls[0]["message"]
        assert "device_exclude_regex" in msg
        assert "entity_id_exclude_regex" in msg
        assert "entity_name_exclude_regex" in msg


class TestEdwIntInputValidation:
    """EDW rejects non-numeric / out-of-range int inputs."""

    def test_non_numeric_check_interval_notifies(self) -> None:
        env = _EdwEnv()
        env.call(check_interval_minutes_raw="not-a-number")
        assert len(env.mock_pn.create_calls) == 1
        msg = env.mock_pn.create_calls[0]["message"]
        assert "check_interval_minutes" in msg
        assert "must be an integer" in msg

    def test_out_of_range_max_notifications_notifies(self) -> None:
        env = _EdwEnv()
        env.call(max_device_notifications_raw="9999")
        assert len(env.mock_pn.create_calls) == 1
        msg = env.mock_pn.create_calls[0]["message"]
        assert "max_device_notifications" in msg
        assert "must be between 0 and 1000" in msg


class TestEdwDevicelessE2E:
    """End-to-end deviceless drift detection."""

    def _find(
        self,
        calls: list[dict[str, Any]],
        nid: str,
    ) -> dict[str, Any]:
        matches = [c for c in calls if c.get("notification_id") == nid]
        assert len(matches) == 1, (
            f"expected 1 call for {nid}, got {len(matches)}"
        )
        return matches[0]

    def test_no_drift_emits_no_create(self) -> None:
        env = _EdwEnv()
        env.setup_deviceless_entity(
            "automation.foo",
            effective_name="Foo",
            platform="automation",
            unique_id="111",
        )
        env.call()
        create_ids = [c["notification_id"] for c in env.mock_pn.create_calls]
        assert (
            "entity_defaults_watchdog_auto_edw_test__deviceless"
            not in create_ids
        )

    def test_rename_drift_creates_notification(self) -> None:
        env = _EdwEnv()
        env.setup_deviceless_entity(
            "automation.old_name",
            effective_name="New Name",
            platform="automation",
            unique_id="1669687974816",
        )
        env.call()
        call = self._find(
            env.mock_pn.create_calls,
            "entity_defaults_watchdog_auto_edw_test__deviceless",
        )
        assert "deviceless entity drift" in call["title"]
        assert "`automation.old_name`" in call["message"]
        assert "`automation.new_name`" in call["message"]
        assert "/config/automation/edit/1669687974816" in call["message"]

    def test_deviceless_check_disabled_dismisses(self) -> None:
        env = _EdwEnv()
        env.setup_deviceless_entity(
            "automation.old",
            effective_name="New",
            platform="automation",
            unique_id="111",
        )
        env.call(drift_checks_raw=[DRIFT_CHECK_DEVICE_ENTITY_ID])
        # Deviceless notification should be dismissed, not
        # created, because the check is disabled.
        created_ids = [c["notification_id"] for c in env.mock_pn.create_calls]
        assert (
            "entity_defaults_watchdog_auto_edw_test__deviceless"
            not in created_ids
        )
        dismissed_ids = [
            c["notification_id"] for c in env.mock_pn.dismiss_calls
        ]
        assert (
            "entity_defaults_watchdog_auto_edw_test__deviceless"
            in dismissed_ids
        )

    def test_stats_written_to_state(self) -> None:
        env = _EdwEnv()
        env.setup_deviceless_entity(
            "automation.a",
            effective_name="A",
            platform="automation",
            unique_id="1",
        )
        env.setup_deviceless_entity(
            "automation.drifted_b",
            effective_name="Renamed B",
            platform="automation",
            unique_id="2",
        )
        env.call()
        attrs = env.mock_state._attrs.get(
            "pyscript.auto_edw_test_state",
            {},
        )
        assert attrs.get("deviceless_entities") == 2
        assert attrs.get("deviceless_drift") == 1
        assert attrs.get("deviceless_stale") == 0

    def test_exclude_suppresses_deviceless(self) -> None:
        env = _EdwEnv()
        env.setup_deviceless_entity(
            "automation.old",
            effective_name="New",
            platform="automation",
            unique_id="111",
        )
        env.call(exclude_entities_raw=["automation.old"])
        created_ids = [c["notification_id"] for c in env.mock_pn.create_calls]
        assert (
            "entity_defaults_watchdog_auto_edw_test__deviceless"
            not in created_ids
        )


class TestEdwDriftChecksValidation:
    """Test drift_checks unknown-value validation."""

    def test_unknown_check_notifies(self) -> None:
        env = _EdwEnv()
        env.call(drift_checks_raw=["not-a-check"])
        config_errors = [
            c
            for c in env.mock_pn.create_calls
            if "Invalid" in c.get("title", "")
        ]
        assert len(config_errors) == 1
        msg = config_errors[0]["message"]
        assert "not-a-check" in msg
        assert DRIFT_CHECK_DEVICE_ENTITY_ID in msg
        assert DRIFT_CHECK_DEVICE_ENTITY_NAME in msg
        assert DRIFT_CHECK_ENTITY_ID in msg

    def test_all_known_checks_no_error(self) -> None:
        env = _EdwEnv()
        env.call(
            drift_checks_raw=[
                DRIFT_CHECK_DEVICE_ENTITY_ID,
                DRIFT_CHECK_DEVICE_ENTITY_NAME,
                DRIFT_CHECK_ENTITY_ID,
            ],
        )
        config_errors = [
            c
            for c in env.mock_pn.create_calls
            if "Invalid" in c.get("title", "")
        ]
        assert config_errors == []

    def test_empty_selection_runs_all_checks(self) -> None:
        """Empty drift_checks = all checks active.

        One entity drifts on ID only, another on name
        only; an empty selection must surface both.
        """
        env = _EdwEnv()
        env.setup_device(
            "zwave_js",
            "dev1",
            "Dev",
            {
                "sensor.old_a": {
                    "name": None,
                    "original_name": "Temp",
                    "has_entity_name": True,
                    "expected_entity_id": "sensor.dev_temp",
                },
                "sensor.b": {
                    "name": "Custom",
                    "original_name": "Temp",
                    "has_entity_name": True,
                    "expected_entity_id": "sensor.b",
                },
            },
        )
        env.call(drift_checks_raw=[])
        device_creates = [
            c
            for c in env.mock_pn.create_calls
            if c["notification_id"]
            == "entity_defaults_watchdog_auto_edw_test__device_dev1"
        ]
        assert len(device_creates) == 1
        msg = device_creates[0]["message"]
        assert "Name overrides to clear" in msg
        assert "sensor.b" in msg
        assert "Non-default entity IDs" in msg
        assert "sensor.old_a" in msg


class TestEdwMultilineRegex:
    """Test multiline regex pattern handling."""

    def test_multiline_patterns_joined(self) -> None:
        env = _EdwEnv()
        env.setup_device(
            "zwave_js",
            "dev1",
            "Dev",
            {
                "sensor.battery": {
                    "name": None,
                    "original_name": "Battery",
                    "has_entity_name": True,
                    "expected_entity_id": ("sensor.dev_battery"),
                },
            },
        )
        env.call(
            entity_id_exclude_regex_raw=("battery\ntemp"),
        )
        # battery entity excluded by first pattern
        key = "pyscript.auto_edw_test_state"
        attrs = env.mock_state.getattr(key)
        assert attrs["device_issues"] == 0

    def test_multiline_invalid_reports_per_line(
        self,
    ) -> None:
        env = _EdwEnv()
        env.call(
            device_exclude_regex_raw=("valid.*\n[invalid\nalso_valid"),
        )
        assert len(env.mock_pn.create_calls) == 1
        msg = env.mock_pn.create_calls[0]["message"]
        assert "[invalid" in msg
        assert "valid.*" not in msg
        assert "also_valid" not in msg

    def test_empty_lines_ignored(self) -> None:
        env = _EdwEnv()
        env.setup_device(
            "zwave_js",
            "dev1",
            "Dev",
            {
                "sensor.a": {
                    "name": None,
                    "original_name": "A",
                    "has_entity_name": True,
                    "expected_entity_id": "sensor.a",
                },
            },
        )
        # Empty lines and whitespace-only lines
        env.call(
            device_exclude_regex_raw="\n  \n\n",
        )
        # No errors, no match-all rejection
        config_errors = [
            c
            for c in env.mock_pn.create_calls
            if "config_error" in c.get("notification_id", "")
        ]
        assert config_errors == []

    def test_match_all_pattern_rejected(self) -> None:
        env = _EdwEnv()
        env.call(
            device_exclude_regex_raw=".*",
        )
        assert len(env.mock_pn.create_calls) == 1
        msg = env.mock_pn.create_calls[0]["message"]
        assert "matches empty string" in msg


class TestEdwIntervalGating:
    """Test check interval gating for EDW."""

    def test_skips_when_off_interval(self) -> None:
        # One minute past this instance's boundary with
        # interval=2 lands on the opposite parity -> skip.
        boundary = _boundary_time("auto.edw_test", 2, T0_UTC)
        env = _EdwEnv(
            current_time=boundary + timedelta(minutes=1),
        )
        env.setup_device(
            "zwave_js",
            "dev1",
            "Dev",
            {
                "sensor.a": {
                    "name": "Old",
                    "original_name": "New",
                    "has_entity_name": True,
                    "expected_entity_id": "sensor.new_a",
                },
            },
        )
        env.call(check_interval_minutes_raw="2")
        assert env.mock_pn.create_calls == []
        # Only config error dismiss (config validation
        # runs before interval gating)
        device_dismisses = [
            c
            for c in env.mock_pn.dismiss_calls
            if "config_error" not in c["notification_id"]
        ]
        assert device_dismisses == []

    def test_runs_on_interval_boundary(self) -> None:
        boundary = _boundary_time("auto.edw_test", 60, T0_UTC)
        env = _EdwEnv(current_time=boundary)
        env.setup_device(
            "zwave_js",
            "dev1",
            "Dev",
            {
                "sensor.a": {
                    "name": "Old",
                    "original_name": "New",
                    "has_entity_name": True,
                    "expected_entity_id": "sensor.new_a",
                },
            },
        )
        env.call(check_interval_minutes_raw="60")
        # Should have evaluated (create or dismiss)
        total = len(env.mock_pn.create_calls) + len(env.mock_pn.dismiss_calls)
        assert total >= 1

    def test_manual_trigger_bypasses_interval(
        self,
    ) -> None:
        env = _EdwEnv(
            current_time=datetime(
                2024,
                1,
                15,
                12,
                1,
                0,
                tzinfo=UTC,
            ),
        )
        env.setup_device(
            "zwave_js",
            "dev1",
            "Dev",
            {
                "sensor.a": {
                    "name": "Old",
                    "original_name": "New",
                    "has_entity_name": True,
                    "expected_entity_id": "sensor.new_a",
                },
            },
        )
        env.call(
            check_interval_minutes_raw="60",
            trigger_platform_raw="manual",
        )
        total = len(env.mock_pn.create_calls) + len(env.mock_pn.dismiss_calls)
        assert total >= 1


class TestEdwIntegrationFiltering:
    """Test integration include/exclude filtering."""

    def test_include_filters(self) -> None:
        env = _EdwEnv()
        # Give matter device a drifted entity
        env.setup_device(
            "zwave_js",
            "dev1",
            "ZWave Dev",
            {
                "sensor.zw": {
                    "name": None,
                    "original_name": "Temp",
                    "has_entity_name": True,
                    "expected_entity_id": "sensor.zw",
                },
            },
        )
        env.setup_device(
            "matter",
            "dev2",
            "Matter Dev",
            {
                "sensor.mt_old": {
                    "name": None,
                    "original_name": "Temp",
                    "has_entity_name": True,
                    "expected_entity_id": ("sensor.matter_dev_temp"),
                },
            },
        )
        # Only include zwave_js -- matter entity
        # drift should be suppressed
        env.call(
            include_integrations_raw=["zwave_js"],
        )
        # Both devices discovered, but only
        # zwave_js entities checked for drift.
        # Matter device should get a dismiss.
        key = "pyscript.auto_edw_test_state"
        attrs = env.mock_state.getattr(key)
        assert attrs["devices"] == 2
        assert attrs["device_issues"] == 0

    def test_exclude_removes(self) -> None:
        env = _EdwEnv()
        env.setup_device(
            "zwave_js",
            "dev1",
            "ZWave Dev",
            {
                "sensor.zw_old": {
                    "name": None,
                    "original_name": "Temp",
                    "has_entity_name": True,
                    "expected_entity_id": ("sensor.zwave_dev_temp"),
                },
            },
        )
        # Include then exclude -> no drift reported
        env.call(
            include_integrations_raw=["zwave_js"],
            exclude_integrations_raw=["zwave_js"],
        )
        key = "pyscript.auto_edw_test_state"
        attrs = env.mock_state.getattr(key)
        assert attrs["device_issues"] == 0

    def test_empty_include_scans_all(self) -> None:
        env = _EdwEnv()
        env._all_integration_ids = ["zwave_js"]
        env.setup_device(
            "zwave_js",
            "dev1",
            "ZWave Dev",
            {
                "sensor.zw": {
                    "name": None,
                    "original_name": "Temp",
                    "has_entity_name": True,
                    "expected_entity_id": "sensor.zw",
                },
            },
        )
        env.call(include_integrations_raw=[])
        key = "pyscript.auto_edw_test_state"
        attrs = env.mock_state.getattr(key)
        assert attrs["devices"] == 1


class TestEdwDiscovery:
    """Test device discovery from registries."""

    def test_discovers_device(self) -> None:
        env = _EdwEnv()
        env.setup_device(
            "zwave_js",
            "dev1",
            "Kitchen Sensor",
            {
                "sensor.old_temp": {
                    "name": None,
                    "original_name": "Temperature",
                    "has_entity_name": True,
                    "expected_entity_id": ("sensor.kitchen_sensor_temperature"),
                },
            },
        )
        env.call()
        # ID drift -> notification created
        assert len(env.mock_pn.create_calls) == 1
        call = env.mock_pn.create_calls[0]
        assert "Kitchen Sensor" in call["title"]

    def test_no_devices_no_notifications(
        self,
    ) -> None:
        env = _EdwEnv()
        env.call()
        assert env.mock_pn.create_calls == []
        # Only the cap dismiss (always emitted when
        # under cap) and the deviceless dismiss (always
        # emitted when no deviceless drift).
        device_dismisses = [
            c
            for c in env.mock_pn.dismiss_calls
            if "_cap" not in c["notification_id"]
            and "config_error" not in c["notification_id"]
            and "_deviceless" not in c["notification_id"]
        ]
        assert device_dismisses == []


class TestEdwNotifications:
    """Test notification create/dismiss behavior."""

    def test_creates_for_drifted(self) -> None:
        env = _EdwEnv()
        env.setup_device(
            "zwave_js",
            "dev1",
            "Drifted Dev",
            {
                "sensor.old_name": {
                    "name": None,
                    "original_name": "Temp",
                    "has_entity_name": True,
                    "expected_entity_id": ("sensor.drifted_dev_temp"),
                },
            },
        )
        env.call()
        assert len(env.mock_pn.create_calls) == 1
        call = env.mock_pn.create_calls[0]
        assert call["notification_id"] == (
            "entity_defaults_watchdog_auto_edw_test__device_dev1"
        )
        assert "Drifted Dev" in call["title"]

    def test_duplicate_device_names(self) -> None:
        env = _EdwEnv()
        env.setup_device(
            "zwave_js",
            "dev1",
            "Doorbell",
            {
                "sensor.old_a": {
                    "name": None,
                    "original_name": "Temp",
                    "has_entity_name": True,
                    "expected_entity_id": ("sensor.doorbell_temp"),
                },
            },
        )
        env.setup_device(
            "zwave_js",
            "dev2",
            "Doorbell",
            {
                "sensor.old_b": {
                    "name": None,
                    "original_name": "Humidity",
                    "has_entity_name": True,
                    "expected_entity_id": ("sensor.doorbell_humidity"),
                },
            },
        )
        env.call()
        assert len(env.mock_pn.create_calls) == 2

    def test_dismisses_for_clean(self) -> None:
        env = _EdwEnv()
        env.setup_device(
            "zwave_js",
            "dev1",
            "Clean Dev",
            {
                "sensor.clean": {
                    "name": None,
                    "original_name": "Temp",
                    "has_entity_name": True,
                    "expected_entity_id": "sensor.clean",
                },
            },
        )
        env.call()
        device_dismisses = [
            c
            for c in env.mock_pn.dismiss_calls
            if "_cap" not in c["notification_id"]
            and "config_error" not in c["notification_id"]
            and "_deviceless" not in c["notification_id"]
        ]
        assert len(device_dismisses) == 1
        assert (
            device_dismisses[0]["notification_id"]
            == "entity_defaults_watchdog_auto_edw_test__device_dev1"
        )


class TestEdwDebugAttrs:
    """Test debug attribute writing for EDW."""

    def test_writes_debug_attrs(self) -> None:
        env = _EdwEnv()
        env.setup_device(
            "zwave_js",
            "dev1",
            "Dev",
            {
                "sensor.a": {
                    "name": None,
                    "original_name": "Temp",
                    "has_entity_name": True,
                    "expected_entity_id": "sensor.a",
                },
            },
        )
        env.call()
        key = "pyscript.auto_edw_test_state"
        assert env.mock_state.get(key) == "ok"
        attrs = env.mock_state.getattr(key)
        assert "last_run" in attrs
        assert "runtime" in attrs
        assert attrs["devices"] == 1
        assert attrs["device_issues"] == 0


class TestEdwDebugLogging:
    """Test debug logging behavior for EDW."""

    def test_no_logging_when_false(self) -> None:
        env = _EdwEnv()
        env.call(debug_logging_raw="false")
        assert env.mock_log.warning_calls == []

    def test_logging_when_true(self) -> None:
        env = _EdwEnv()
        env.setup_device(
            "zwave_js",
            "dev1",
            "Dev",
            {
                "sensor.a": {
                    "name": None,
                    "original_name": "Temp",
                    "has_entity_name": True,
                    "expected_entity_id": "sensor.a",
                },
            },
        )
        env.call(debug_logging_raw="true")
        assert len(env.mock_log.warning_calls) == 1
        msg = env.mock_log.warning_calls[0][0]
        args = env.mock_log.warning_calls[0][1]
        formatted = msg % args
        assert "[EDW:" in formatted


class _MockRegEntry:
    """Minimal stand-in for a HA entity registry entry."""

    def __init__(
        self,
        entity_id: str,
        device_id: str | None = None,
        disabled_by: str | None = None,
        name: str | None = None,
        original_name: str | None = None,
        platform: str | None = None,
        unique_id: str | None = None,
        config_entry_id: str | None = None,
    ) -> None:
        self.entity_id = entity_id
        self.device_id = device_id
        self.disabled_by = disabled_by
        self.name = name
        self.original_name = original_name
        self.platform = platform
        self.unique_id = unique_id
        self.config_entry_id = config_entry_id


class _MockEntReg:
    """Stand-in for HA's EntityRegistry exposing ``entities``."""

    def __init__(self, entries: list[_MockRegEntry]) -> None:
        self.entities = {e.entity_id: e for e in entries}


class _MockStateEntry:
    """Minimal stand-in for a HA state object."""

    def __init__(
        self,
        entity_id: str,
        friendly_name: str | None = None,
    ) -> None:
        self.entity_id = entity_id
        self.attributes: dict[str, Any] = {}
        if friendly_name is not None:
            self.attributes["friendly_name"] = friendly_name


class _MockStateMachine:
    """Stand-in for ``hass.states`` with ``async_all``."""

    def __init__(self, entries: list[_MockStateEntry]) -> None:
        self._entries = entries

    def async_all(self) -> list[_MockStateEntry]:
        return list(self._entries)


class _MockHassForDeviceless:
    """Hass mock exposing only what the deviceless
    discovery function reads.

    Carries its own entity registry reference so each test
    can build a fresh one without sharing state with
    sibling tests that run in the same process.
    """

    def __init__(
        self,
        ent_reg: _MockEntReg,
        state_entries: list[_MockStateEntry],
    ) -> None:
        self.ent_reg = ent_reg
        self.states = _MockStateMachine(state_entries)


_ER_MODULE_NAMES = (
    "homeassistant",
    "homeassistant.helpers",
    "homeassistant.helpers.entity_registry",
)


class _DiscoverDevicelessEnv:
    """Exec's ha_pyscript_automations.py and drives its
    real ``_discover_deviceless_entities``.

    Paired with the ``discover_env`` fixture, which handles
    the ``sys.modules`` save/restore around the fake
    ``homeassistant.helpers.entity_registry`` stub.

    Other _EdwEnv tests substitute the discovery function
    wholesale, so the safety-net bug lived untested. This
    env drives the real implementation.
    """

    def __init__(self) -> None:
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
            "hass": self.mock_hass,
            "persistent_notification": self.mock_pn,
        }
        exec(
            compile(src, str(_SCRIPT_PATH), "exec"),
            self._ns,
        )

    def discover(
        self,
        entries: list[_MockRegEntry],
        state_entries: list[_MockStateEntry],
        domains: frozenset[str],
        target_integrations: set[str] | None = None,
    ) -> Any:
        ent_reg = _MockEntReg(entries)
        hass_obj = _MockHassForDeviceless(ent_reg, state_entries)
        return self._ns["_discover_deviceless_entities"](
            hass_obj,
            domains,
            target_integrations,
        )


@pytest.fixture
def discover_env() -> Iterator[_DiscoverDevicelessEnv]:
    """Build a ``_DiscoverDevicelessEnv`` with an isolated
    ``homeassistant.helpers.entity_registry`` stub, then
    restore ``sys.modules`` on teardown.

    Using a fixture (not a ``try/finally`` in every test)
    guarantees restoration even if a test forgets -- the
    previous pattern put that responsibility on each test.
    """
    saved = {k: sys.modules.get(k) for k in _ER_MODULE_NAMES}

    def async_get(hass_obj: Any) -> Any:
        return hass_obj.ent_reg

    mock_er = types.ModuleType("homeassistant.helpers.entity_registry")
    mock_er.async_get = async_get  # type: ignore[attr-defined]
    sys.modules["homeassistant"] = types.ModuleType("homeassistant")
    sys.modules["homeassistant.helpers"] = types.ModuleType(
        "homeassistant.helpers",
    )
    sys.modules["homeassistant.helpers.entity_registry"] = mock_er

    try:
        yield _DiscoverDevicelessEnv()
    finally:
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


class TestDiscoverDevicelessEntities:
    """Direct tests for ``_discover_deviceless_entities``.

    Covers the registry/state-list interplay that the
    _EdwEnv tests skip by mocking the function wholesale.
    """

    DOMAINS = frozenset({"automation", "sensor", "switch"})

    def test_deviceless_registry_entry_included(
        self,
        discover_env: _DiscoverDevicelessEnv,
    ) -> None:
        entries = [
            _MockRegEntry(
                entity_id="sensor.template_grid",
                device_id=None,
                platform="template",
                unique_id="grid",
                original_name="Grid Import Power",
            ),
        ]
        entities, peers = discover_env.discover(entries, [], self.DOMAINS)
        assert len(entities) == 1
        e = entities[0]
        assert e.entity_id == "sensor.template_grid"
        assert e.platform == "template"
        assert e.from_registry is True
        # YAML-configured (no config_entry_id supplied to
        # _MockRegEntry) threads through as ``None``.
        assert e.config_entry_id is None
        assert peers["sensor"] == {"template_grid"}

    def test_config_entry_id_threaded_through(
        self,
        discover_env: _DiscoverDevicelessEnv,
    ) -> None:
        """UI-configured entities keep their config_entry_id
        so ``_deviceless_line_suffix`` can emit the
        integration link."""
        entries = [
            _MockRegEntry(
                entity_id="sensor.ui_template",
                device_id=None,
                platform="template",
                unique_id="ui",
                config_entry_id="abc123",
            ),
        ]
        entities, _ = discover_env.discover(entries, [], self.DOMAINS)
        assert entities[0].config_entry_id == "abc123"

    def test_device_attached_entry_not_flagged_as_state_only(
        self,
        discover_env: _DiscoverDevicelessEnv,
    ) -> None:
        """Regression: device-attached registry entries
        used to leak into the state-list safety net and
        surface as from_registry=False with platform=None.
        """
        entries = [
            _MockRegEntry(
                entity_id="switch.rachio_summer_schedule",
                device_id="dev_controller",
                platform="rachio",
                unique_id="sched_summer",
            ),
        ]
        states = [
            _MockStateEntry(
                entity_id="switch.rachio_summer_schedule",
                friendly_name="Summer Schedule",
            ),
        ]
        entities, _ = discover_env.discover(entries, states, self.DOMAINS)
        # Entity must not appear at all: it belongs to the
        # per-device path, not the deviceless bucket.
        assert entities == []

    def test_disabled_entry_not_flagged_as_state_only(
        self,
        discover_env: _DiscoverDevicelessEnv,
    ) -> None:
        entries = [
            _MockRegEntry(
                entity_id="sensor.disabled",
                device_id=None,
                disabled_by="user",
                platform="template",
                unique_id="dis",
            ),
        ]
        states = [
            _MockStateEntry(
                entity_id="sensor.disabled",
                friendly_name="Disabled",
            ),
        ]
        entities, _ = discover_env.discover(entries, states, self.DOMAINS)
        assert entities == []

    def test_state_only_entity_uses_safety_net(
        self,
        discover_env: _DiscoverDevicelessEnv,
    ) -> None:
        states = [
            _MockStateEntry(
                entity_id="sensor.yaml_thing",
                friendly_name="YAML Thing",
            ),
        ]
        entities, _ = discover_env.discover([], states, self.DOMAINS)
        assert len(entities) == 1
        e = entities[0]
        assert e.from_registry is False
        assert e.platform is None
        assert e.effective_name == "YAML Thing"

    def test_target_integrations_filters_registry_entries(
        self,
        discover_env: _DiscoverDevicelessEnv,
    ) -> None:
        entries = [
            _MockRegEntry(
                entity_id="automation.keep_me",
                device_id=None,
                platform="automation",
                unique_id="keep",
                original_name="Keep Me",
            ),
            _MockRegEntry(
                entity_id="sensor.rachio_schedule",
                device_id=None,
                platform="rachio",
                unique_id="sched",
                original_name="Summer Schedule",
            ),
        ]
        entities, peers = discover_env.discover(
            entries,
            [],
            self.DOMAINS,
            target_integrations={"automation"},
        )
        eids = {e.entity_id for e in entities}
        assert eids == {"automation.keep_me"}
        # Peers still include filtered-out entries so
        # collision-suffix classification stays right.
        assert "rachio_schedule" in peers["sensor"]

    def test_target_integrations_none_disables_filter(
        self,
        discover_env: _DiscoverDevicelessEnv,
    ) -> None:
        entries = [
            _MockRegEntry(
                entity_id="sensor.any",
                device_id=None,
                platform="anything",
                unique_id="x",
                original_name="Any",
            ),
        ]
        entities, _ = discover_env.discover(
            entries,
            [],
            self.DOMAINS,
            target_integrations=None,
        )
        assert len(entities) == 1

    def test_out_of_domain_entries_excluded(
        self,
        discover_env: _DiscoverDevicelessEnv,
    ) -> None:
        entries = [
            _MockRegEntry(
                entity_id="light.in_domain",
                device_id=None,
                platform="template",
                unique_id="l",
                original_name="Light",
            ),
        ]
        # light is not in our DOMAINS frozenset
        entities, _ = discover_env.discover(entries, [], self.DOMAINS)
        assert entities == []

    def test_registry_entry_without_name_uses_obj_id_fallback(
        self,
        discover_env: _DiscoverDevicelessEnv,
    ) -> None:
        """A registry entry with neither ``name`` nor
        ``original_name`` falls back to an HA-style default
        (title-cased obj_id) so downstream evaluation has
        something to slugify against.
        """
        entries = [
            _MockRegEntry(
                entity_id="sensor.old_yaml_thing",
                device_id=None,
                platform="template",
                unique_id="oyt",
                name=None,
                original_name=None,
            ),
        ]
        entities, _ = discover_env.discover(entries, [], self.DOMAINS)
        assert len(entities) == 1
        assert entities[0].effective_name == "Old Yaml Thing"

    def test_state_only_entry_without_friendly_name_uses_obj_id_fallback(
        self,
        discover_env: _DiscoverDevicelessEnv,
    ) -> None:
        """State-only entity with missing ``friendly_name``
        attribute gets the obj_id-derived default, so it
        round-trips through slugify to the same obj_id and
        is classified as non-drifting rather than silently
        skipped.
        """
        states = [_MockStateEntry(entity_id="sensor.yaml_thing")]
        entities, _ = discover_env.discover([], states, self.DOMAINS)
        assert len(entities) == 1
        assert entities[0].effective_name == "Yaml Thing"
        assert entities[0].from_registry is False

    def test_state_only_entry_empty_friendly_name_uses_obj_id_fallback(
        self,
        discover_env: _DiscoverDevicelessEnv,
    ) -> None:
        """``friendly_name=""`` (empty string) is treated
        the same as missing.
        """
        states = [
            _MockStateEntry(
                entity_id="sensor.yaml_thing",
                friendly_name="",
            ),
        ]
        entities, _ = discover_env.discover([], states, self.DOMAINS)
        assert len(entities) == 1
        assert entities[0].effective_name == "Yaml Thing"


class TestEdwDevicelessIntegrationFilterPropagation:
    """The service wrapper must hand the computed
    ``target_integrations`` set to the discovery function
    so registry-backed deviceless entries get filtered.
    """

    def test_filter_set_passed_to_discovery(self) -> None:
        env = _EdwEnv()
        env.setup_device(
            "zwave_js",
            "dev1",
            "ZW Dev",
            {
                "sensor.zw": {
                    "name": None,
                    "original_name": "Temp",
                    "has_entity_name": True,
                    "expected_entity_id": "sensor.zw",
                },
            },
        )
        env.call(
            include_integrations_raw=["zwave_js"],
        )
        # _mock_discover_deviceless_entities records the
        # kwargs it received; include-only with a single
        # integration should yield that set.
        assert env.deviceless_target_integrations == {"zwave_js"}

    def test_empty_include_passes_full_set(self) -> None:
        env = _EdwEnv()
        env._all_integration_ids = ["zwave_js", "template"]
        env.call(include_integrations_raw=[])
        # Empty include means "all integrations", so the
        # discovery filter should be the union of every
        # integration id.
        assert env.deviceless_target_integrations == {
            "zwave_js",
            "template",
        }

    def test_exclude_removes_from_filter(self) -> None:
        env = _EdwEnv()
        env._all_integration_ids = ["zwave_js", "rachio"]
        env.call(
            exclude_integrations_raw=["rachio"],
        )
        assert env.deviceless_target_integrations == {"zwave_js"}


class _FakeConfigError:
    """Minimal stand-in for zwave_route_manager.ConfigError.

    The notification helpers only read ``.location``,
    ``.entity_id``, ``.reason``, and ``.device_id`` -- a tiny
    shim avoids importing the logic module into this test
    file (already-covered by the logic-module tests).
    """

    def __init__(
        self,
        location: str,
        entity_id: str | None,
        reason: str,
        device_id: str | None = None,
    ) -> None:
        self.location = location
        self.entity_id = entity_id
        self.reason = reason
        self.device_id = device_id


class TestZrmNotifications:
    """ZRM config-error bullet formatting via the unified builder.

    ZRM flattens ConfigError objects to pre-formatted bullet
    strings (``_zrm_error_bullets``) and passes them to the
    generic ``_build_config_error_notification`` helper. Tests
    check both the per-bullet format (entity-first, optional
    device-page link) and the assembled notification.
    """

    def _build_zrm_config_notif(
        self,
        env: _ServiceEnv,
        errors: list[_FakeConfigError],
        instance_id: str = "automation.zrm_test",
    ) -> Any:
        bullets = env._ns["_zrm_error_bullets"](errors)
        return env._ns["_build_config_error_notification"](
            bullets,
            instance_id,
            "Z-Wave Route Manager",
            False,
            "",
        )

    def test_title_includes_instance_auto_name(self) -> None:
        env = _ServiceEnv()
        notif = self._build_zrm_config_notif(
            env,
            [_FakeConfigError("routes[0]", None, "something")],
            instance_id="automation.zrm_test",
        )
        # Automation name falls back to instance_id when no
        # friendly_name is available in mock state.
        assert notif.title == "automation.zrm_test: Invalid Configuration"

    def test_inactive_when_no_errors(self) -> None:
        env = _ServiceEnv()
        notif = self._build_zrm_config_notif(env, [])
        assert notif.active is False
        assert notif.message == ""

    def test_bullet_no_entity_falls_back_to_location(self) -> None:
        env = _ServiceEnv()
        notif = self._build_zrm_config_notif(
            env,
            [_FakeConfigError("(file)", None, "read failed")],
        )
        assert "- `(file)`: read failed" in notif.message

    def test_bullet_entity_first_with_device_link(self) -> None:
        env = _ServiceEnv()
        notif = self._build_zrm_config_notif(
            env,
            [
                _FakeConfigError(
                    location="routes[0].clients[2]",
                    entity_id="binary_sensor.window_front_left",
                    reason=(
                        "Device does not support routing: "
                        "configured to use Z-Wave Long Range (vs Mesh)"
                    ),
                    device_id="abc123",
                ),
            ],
        )
        assert (
            "- [`binary_sensor.window_front_left`]"
            "(/config/devices/device/abc123) "
            "(`routes[0].clients[2]`): "
            "Device does not support routing: "
            "configured to use Z-Wave Long Range (vs Mesh)"
        ) in notif.message

    def test_bullet_entity_without_device_id_unlinked(self) -> None:
        # "Entity not found" errors have entity_id but no
        # DeviceResolution, so no device_id. Render the
        # entity_id as plain backticked text -- the entity
        # isn't in HA so a device-page link wouldn't resolve.
        env = _ServiceEnv()
        notif = self._build_zrm_config_notif(
            env,
            [
                _FakeConfigError(
                    location="routes[1].clients[0]",
                    entity_id="lock.missing",
                    reason="entity not found",
                ),
            ],
        )
        bullet = "- `lock.missing` (`routes[1].clients[0]`): entity not found"
        assert bullet in notif.message
        # Make sure we didn't accidentally emit an empty-href link.
        assert "/config/devices/device/" not in notif.message

    def test_multiple_errors_all_listed(self) -> None:
        env = _ServiceEnv()
        errors = [
            _FakeConfigError(
                f"routes[0].clients[{i}]",
                f"binary_sensor.lr{i}",
                "Device does not support routing: configured to use"
                " Z-Wave Long Range (vs Mesh)",
                device_id=f"dev{i}",
            )
            for i in range(3)
        ]
        notif = self._build_zrm_config_notif(env, errors)
        # Generic builder prelude.
        assert "Configuration errors:" in notif.message
        for i in range(3):
            assert f"binary_sensor.lr{i}" in notif.message
            assert f"/config/devices/device/dev{i}" in notif.message

    def test_api_notification_title_plain(self) -> None:
        env = _ServiceEnv()
        build = env._ns["_zrm_api_notification"]
        notif = build("zrm_", "connection refused")
        assert notif.title == "Z-Wave Route Manager: API unavailable"

    def test_apply_notification_title_plain(self) -> None:
        env = _ServiceEnv()
        build = env._ns["_zrm_apply_notification"]

        class _Kind:
            value = "set_application_route"

        class _Action:
            kind = _Kind()
            node_id = 42
            client_entity_id = "lock.x"
            repeaters = [50]

        class _Result:
            message = "timeout"

        notif = build("zrm_", _Action(), _Result())
        assert notif.title == ("Z-Wave Route Manager: apply failed for node 42")

    def test_timeout_notification_id_per_attempt(self) -> None:
        # Each timeout event must produce a unique
        # notification ID keyed to the attempt that just
        # timed out, so retries don't collide and so the
        # orphan sweep (with keep_pattern="__timeout_") can
        # leave them alone.
        import importlib
        from datetime import UTC, datetime

        zrm = importlib.import_module("zwave_route_manager")
        env = _ServiceEnv()
        build = env._ns["_zrm_timeout_notification"]
        old_ts = datetime(2026, 4, 22, 1, 23, 45, tzinfo=UTC)
        notif = build(
            "zwm_",
            42,
            zrm.RouteType.PRIORITY_APP,
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

    def test_api_error_with_brackets_is_escaped(self) -> None:
        env = _ServiceEnv()
        build = env._ns["_zrm_api_notification"]
        notif = build("zrm_", "bad response: [foo]")
        assert "bad response: \\[foo\\]" in notif.message
        assert "bad response: [foo]" not in notif.message

    def test_apply_server_response_with_brackets_is_escaped(
        self,
    ) -> None:
        env = _ServiceEnv()
        build = env._ns["_zrm_apply_notification"]

        class _Kind:
            value = "set_application_route"

        class _Action:
            kind = _Kind()
            node_id = 42
            client_entity_id = "lock.x"
            repeaters: list[int] = []

        class _Result:
            message = "ack [partial]"

        notif = build("zrm_", _Action(), _Result())
        assert "Server response: ack \\[partial\\]" in notif.message


class TestZrmPathStorage:
    """Round-trip tests for the per-route storage helpers.

    These cover ``_zrm_paths_to_storage`` /
    ``_zrm_paths_from_storage`` plus the per-path serializers
    they delegate to. The route-manager service relies on
    these for both the ``pending`` and ``applied`` state-entity
    attributes (same shape, same helpers).
    """

    def _setup(self) -> tuple[Any, Any, Any]:
        env = _ServiceEnv()
        import importlib
        import sys

        sys.path.insert(
            0,
            str(REPO_ROOT / "pyscript" / "modules"),
        )
        logic = importlib.import_module("zwave_route_manager")
        bridge = importlib.import_module("zwave_js_ui_bridge")
        return env, logic, bridge

    def test_round_trip_preserves_fields(self) -> None:
        env, logic, bridge = self._setup()
        to_storage = env._ns["_zrm_paths_to_storage"]
        from_storage = env._ns["_zrm_paths_from_storage"]
        requested = datetime(2026, 4, 21, 12, 0, 0)
        confirmed = datetime(2026, 4, 21, 12, 5, 0)
        paths = {
            18: [
                logic.RouteRequest(
                    type=logic.RouteType.PRIORITY_APP,
                    repeater_node_ids=[50],
                    speed=bridge.RouteSpeed.RATE_100K,
                    requested_at=requested,
                    confirmed_at=confirmed,
                ),
                logic.RouteRequest(
                    type=logic.RouteType.PRIORITY_SUC,
                    repeater_node_ids=[50, 47],
                    speed=bridge.RouteSpeed.RATE_40K,
                    requested_at=requested,
                    confirmed_at=None,
                ),
            ],
        }
        node_to_entity = {18: "lock.front_door", 50: "sensor.ext"}
        serialized = to_storage(paths, node_to_entity)
        assert serialized["18"]["entity_id"] == "lock.front_door"
        assert serialized["18"]["paths"][0]["repeaters"] == [
            {"id": 50, "entity_id": "sensor.ext"},
        ]
        round_tripped = from_storage(serialized)
        assert set(round_tripped.keys()) == {18}
        out_paths = round_tripped[18]
        assert out_paths[0].type is logic.RouteType.PRIORITY_APP
        assert out_paths[0].repeater_node_ids == [50]
        assert out_paths[0].speed == bridge.RouteSpeed.RATE_100K
        assert out_paths[0].requested_at == requested
        assert out_paths[0].confirmed_at == confirmed
        assert out_paths[1].type is logic.RouteType.PRIORITY_SUC
        assert out_paths[1].repeater_node_ids == [50, 47]
        assert out_paths[1].confirmed_at is None

    def test_timeout_count_round_trips(self) -> None:
        env, logic, bridge = self._setup()
        to_storage = env._ns["_zrm_paths_to_storage"]
        from_storage = env._ns["_zrm_paths_from_storage"]
        requested = datetime(2026, 4, 21, 12, 0, 0)
        paths = {
            18: [
                logic.RouteRequest(
                    type=logic.RouteType.PRIORITY_APP,
                    repeater_node_ids=[50],
                    speed=bridge.RouteSpeed.RATE_100K,
                    requested_at=requested,
                    confirmed_at=None,
                    timeout_count=5,
                ),
            ],
        }
        out = from_storage(to_storage(paths, {}))[18][0]
        assert out.timeout_count == 5
        # And legacy storage without the field defaults to 0.
        legacy = {
            "18": {
                "entity_id": "",
                "paths": [
                    {
                        "type": "priority_app",
                        "repeaters": [],
                        "speed": "100k",
                        "requested_at": "",
                        "confirmed_at": "",
                    },
                ],
            },
        }
        out_legacy = from_storage(legacy)[18][0]
        assert out_legacy.timeout_count == 0

    def test_missing_timestamps_round_trip_as_none(self) -> None:
        env, logic, bridge = self._setup()
        to_storage = env._ns["_zrm_paths_to_storage"]
        from_storage = env._ns["_zrm_paths_from_storage"]
        # Observed-already-applied: requested_at is None, only
        # confirmed_at is set.
        confirmed = datetime(2026, 4, 21, 12, 0, 0)
        paths = {
            18: [
                logic.RouteRequest(
                    type=logic.RouteType.PRIORITY_APP,
                    repeater_node_ids=[50],
                    speed=bridge.RouteSpeed.RATE_100K,
                    requested_at=None,
                    confirmed_at=confirmed,
                ),
            ],
        }
        round_tripped = from_storage(to_storage(paths, {}))
        out = round_tripped[18][0]
        assert out.requested_at is None
        assert out.confirmed_at == confirmed

    def test_empty_input_yields_empty_output(self) -> None:
        env, _logic, _bridge = self._setup()
        to_storage = env._ns["_zrm_paths_to_storage"]
        from_storage = env._ns["_zrm_paths_from_storage"]
        assert to_storage({}, {}) == {}
        assert from_storage({}) == {}
        assert from_storage(None) == {}
        assert from_storage("garbage") == {}

    def test_unknown_route_type_dropped(self) -> None:
        env, _logic, _bridge = self._setup()
        from_storage = env._ns["_zrm_paths_from_storage"]
        # Future-shape entry referencing a type we don't know:
        # silently dropped rather than raising.
        stored = {
            "18": {
                "entity_id": "lock.x",
                "paths": [
                    {
                        "type": "future_route_type",
                        "repeaters": [{"id": 50, "entity_id": ""}],
                        "speed": "100k",
                        "requested_at": "",
                        "confirmed_at": "",
                    },
                ],
            },
        }
        # All the entries are dropped -> node entry omitted.
        assert from_storage(stored) == {}

    def test_repeater_legacy_int_shape_accepted(self) -> None:
        env, _logic, bridge = self._setup()
        from_storage = env._ns["_zrm_paths_from_storage"]
        # Hand-edited or older state with bare-int repeaters.
        # We accept this so a downgrade or a manual edit doesn't
        # discard otherwise-valid paths.
        stored = {
            "18": {
                "entity_id": "",
                "paths": [
                    {
                        "type": "priority_app",
                        "repeaters": [50, 47],
                        "speed": "100k",
                        "requested_at": "",
                        "confirmed_at": "",
                    },
                ],
            },
        }
        out = from_storage(stored)
        assert out[18][0].repeater_node_ids == [50, 47]
        assert out[18][0].speed == bridge.RouteSpeed.RATE_100K

    def test_clear_entry_round_trips_with_none_speed(self) -> None:
        # Pending clears carry ``speed=None`` in memory and
        # render as ``"-"`` in the state entity. Round-trip
        # verifies both directions of the storage helpers
        # agree on the invariant.
        env, logic, _bridge = self._setup()
        to_storage = env._ns["_zrm_paths_to_storage"]
        from_storage = env._ns["_zrm_paths_from_storage"]
        requested = datetime(2026, 4, 21, 12, 0, 0)
        paths = {
            18: [
                logic.RouteRequest(
                    type=logic.RouteType.PRIORITY_APP,
                    repeater_node_ids=[],
                    speed=None,
                    requested_at=requested,
                    confirmed_at=None,
                ),
            ],
        }
        serialized = to_storage(paths, {})
        assert serialized["18"]["paths"][0]["speed"] == "-"
        assert serialized["18"]["paths"][0]["repeaters"] == []
        round_tripped = from_storage(serialized)
        out = round_tripped[18][0]
        assert out.speed is None
        assert out.repeater_node_ids == []
        assert out.requested_at == requested

    def test_legacy_clear_with_real_speed_accepts_as_none(self) -> None:
        # Pre-fix stored state used RouteSpeed.RATE_9600 as a
        # placeholder for clears. Make sure we tolerate that
        # shape -- empty repeaters + any legacy speed string
        # parses back to ``speed=None``.
        env, _logic, _bridge = self._setup()
        from_storage = env._ns["_zrm_paths_from_storage"]
        stored = {
            "18": {
                "entity_id": "",
                "paths": [
                    {
                        "type": "priority_app",
                        "repeaters": [],
                        "speed": "9600",
                        "requested_at": "2026-04-21T12:00:00",
                        "confirmed_at": "",
                    },
                ],
            },
        }
        out = from_storage(stored)
        assert out[18][0].repeater_node_ids == []
        assert out[18][0].speed is None


def _rw_call(env: _WatchdogEnv, **overrides: Any) -> None:
    """Invoke the reference_watchdog service wrapper with defaults."""
    defaults: dict[str, Any] = {
        "instance_id": "auto.rw_test",
        "trigger_platform_raw": "state",
        "exclude_paths_raw": "",
        "exclude_integrations_raw": [],
        "exclude_entities_raw": [],
        "exclude_entity_regex_raw": "",
        "check_disabled_entities_raw": "false",
        "check_interval_minutes_raw": "1",
        "max_source_notifications_raw": "0",
        "debug_logging_raw": "false",
    }
    defaults.update(overrides)
    import asyncio

    asyncio.run(
        env._ns["reference_watchdog_blueprint_entrypoint"](**defaults),
    )


class TestRwIntInputValidation:
    """RW rejects non-numeric / out-of-range int inputs."""

    def test_non_numeric_check_interval_notifies(self) -> None:
        env = _WatchdogEnv()
        _rw_call(env, check_interval_minutes_raw="not-a-number")
        config_errors = [
            c
            for c in env.mock_pn.create_calls
            if "Invalid" in c.get("title", "")
        ]
        assert len(config_errors) == 1
        msg = config_errors[0]["message"]
        assert "check_interval_minutes" in msg
        assert "must be an integer" in msg

    def test_out_of_range_max_notifications_notifies(self) -> None:
        env = _WatchdogEnv()
        _rw_call(env, max_source_notifications_raw="9999")
        config_errors = [
            c
            for c in env.mock_pn.create_calls
            if "Invalid" in c.get("title", "")
        ]
        assert len(config_errors) == 1
        msg = config_errors[0]["message"]
        assert "max_source_notifications" in msg
        assert "must be between 0 and 1000" in msg


class TestCodeQuality(CodeQualityBase):
    ruff_targets = [
        "pyscript/ha_pyscript_automations.py",
        "tests/test_ha_pyscript_automations.py",
    ]
    mypy_targets = [
        "pyscript/ha_pyscript_automations.py",
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


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _SCRIPT_PATH, REPO_ROOT)
