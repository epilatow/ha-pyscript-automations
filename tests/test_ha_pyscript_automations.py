#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest", "pytest-cov", "ruff", "mypy"]
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
import re
import sys
import types
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
)
from helpers import EntityRegistryInfo  # noqa: E402

T0 = datetime(2024, 1, 15, 12, 0, 0)
# Timezone-aware version for watchdog tests (pyscript's
# last_reported returns UTC-aware datetimes).
T0_UTC = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)


# ── Mock infrastructure ──────────────────────────────


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
    the next ``call()`` raise — used to verify that
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
        return self._ns["sensor_threshold_switch_controller"]

    def call(self, **kwargs: Any) -> None:
        """Call the service with defaults."""
        self.service_fn(**_default_kwargs(**kwargs))


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


# ── Tests ─────────────────────────────────────────────


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
            key + ".data",
            json.dumps(s.to_dict()),
        )
        env.call()

    def test_malformed_json(self) -> None:
        """Bad JSON -> graceful fallback."""
        env = _ServiceEnv()
        key = env.state_key_fn("auto.test_instance")
        env.mock_state.setattr(
            key + ".data",
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
            key + ".data",
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
            key + ".data",
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
        assert msg == expected_prefix + "body"

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
            key + ".data",
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


# ── Device Watchdog mock infrastructure ──────────────


class _MockPersistentNotification:
    """Mock for ``persistent_notification`` service."""

    def __init__(self) -> None:
        self.create_calls: list[dict[str, str]] = []
        self.dismiss_calls: list[dict[str, str]] = []

    def create(self, **kwargs: str) -> None:
        self.create_calls.append(kwargs)

    def dismiss(self, **kwargs: str) -> None:
        self.dismiss_calls.append(kwargs)


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
        return self._ns["device_watchdog"]

    def call(self, **kwargs: Any) -> None:
        self.watchdog_fn(**_dw_default_kwargs(**kwargs))


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

    _NOTIF_ID = "device_watchdog_config_error_auto_dw_test"

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
            if c["notification_id"].startswith("device_watchdog_dev")
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
                if c["notification_id"] == "device_watchdog_dev1"
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
            if c["notification_id"].startswith("device_watchdog_dev")
        }
        assert "device_watchdog_dev_s" in creates
        assert "device_watchdog_dev_u" not in creates

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
            if c["notification_id"].startswith("device_watchdog_dev")
        }
        assert "device_watchdog_dev_u" in creates
        assert "device_watchdog_dev_s" not in creates


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
        env = _WatchdogEnv(
            current_time=datetime(
                2024,
                1,
                15,
                12,
                0,
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
        assert len(env.mock_pn.create_calls) == 1

    def test_no_debug_attrs_when_gated(self) -> None:
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
        assert dismissals[0]["notification_id"] == "device_watchdog_dev1"

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
        assert call["notification_id"] == ("device_watchdog_dev1")
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
        assert device_dismissals[0]["notification_id"] == "device_watchdog_dev1"

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
                    "dw_diag_",
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
                    "dw_diag_",
                )
            ]
            diag_dismissals = [
                c
                for c in env.mock_pn.dismiss_calls
                if c["notification_id"].startswith(
                    "dw_diag_",
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
            if c["notification_id"].startswith("dw_diag_")
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


# ── Trigger Entity Controller ─────────────────────


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
        return self._ns["trigger_entity_controller"]

    @property
    def state_key_fn(self) -> Any:
        return self._ns["_state_key"]

    def call(self, **kwargs: Any) -> None:
        self.tec_fn(**_tec_defaults(**kwargs))


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
            key + ".auto_off_at",
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


# ── Entity Defaults Watchdog mock infrastructure ───


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
        return self._ns["entity_defaults_watchdog"]

    def call(self, **kwargs: Any) -> None:
        self.edw_fn(**_edw_default_kwargs(**kwargs))


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
            "entity_defaults_watchdog_config_error_auto_edw_test"
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

    def test_all_known_checks_no_error(self) -> None:
        env = _EdwEnv()
        env.call(
            drift_checks_raw=[
                DRIFT_CHECK_DEVICE_ENTITY_ID,
                DRIFT_CHECK_DEVICE_ENTITY_NAME,
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
            if c["notification_id"] == "entity_defaults_watchdog_dev1"
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
        # Odd minute with interval=2 -> skip
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
        env = _EdwEnv(
            current_time=datetime(
                2024,
                1,
                15,
                12,
                0,
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
        # Only include zwave_js — matter entity
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
        # under cap)
        device_dismisses = [
            c
            for c in env.mock_pn.dismiss_calls
            if "_cap" not in c["notification_id"]
            and "config_error" not in c["notification_id"]
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
        assert call["notification_id"] == ("entity_defaults_watchdog_dev1")
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
        ]
        assert len(device_dismisses) == 1
        assert (
            device_dismisses[0]["notification_id"]
            == "entity_defaults_watchdog_dev1"
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

    PyScript evaluates ALL pyscript files — both service
    wrappers (pyscript/*.py) and logic modules
    (pyscript/modules/*.py) — with a custom AST
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

    IMPORTANT — paired evaluator sanity tests:
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
    ``@staticmethod`` was one such case — banned
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
        types) are fine — they're evaluated lazily or
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
                # within this function — those are
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


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _SCRIPT_PATH, REPO_ROOT)
