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

T0 = datetime(2024, 1, 15, 12, 0, 0)
# Timezone-aware version for watchdog tests (pyscript's
# last_changed returns UTC-aware datetimes).
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
    assertion.
    """

    def __init__(self) -> None:
        self.call_log: list[tuple[str, str, dict[str, Any]]] = []

    def __call__(self, fn: Any) -> Any:
        return fn

    def call(
        self,
        domain: str,
        svc: str,
        **kwargs: Any,
    ) -> None:
        self.call_log.append((domain, svc, kwargs))


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

        src = _SCRIPT_PATH.read_text()
        self._ns: dict[str, Any] = {
            "__builtins__": __builtins__,
            "service": self.mock_service,
            "state": self.mock_state,
            "homeassistant": self.mock_ha,
            "log": self.mock_log,
            "persistent_notification": self.mock_pn,
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
        "debug_raw": "false",
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
        env.call(debug_raw="false")
        assert len(env.mock_log.warning_calls) == 0

    def test_logging_when_debug_true(self) -> None:
        """log.warning emitted when debug is enabled."""
        env = _ServiceEnv()
        env.call(debug_raw="true")
        assert len(env.mock_log.warning_calls) == 1

    def test_logging_with_bool_true(self) -> None:
        """debug_raw=True (bool) also triggers logging."""
        env = _ServiceEnv()
        env.call(debug_raw=True)
        assert len(env.mock_log.warning_calls) == 1

    def test_no_logging_with_bool_false(self) -> None:
        """debug_raw=False (bool) does not trigger logging."""
        env = _ServiceEnv()
        env.call(debug_raw=False)
        assert len(env.mock_log.warning_calls) == 0

    def test_log_message_contains_context(self) -> None:
        """Log message includes event, action, and label."""
        env = _ServiceEnv()
        env.call(debug_raw="true")
        msg, args = env.mock_log.warning_calls[0]
        formatted = msg % args
        assert "[STSC:" in formatted
        assert "TIMER" in formatted
        assert "NONE" in formatted

    def test_log_emitted_every_call(self) -> None:
        """Each call emits a log line when debug is on."""
        env = _ServiceEnv()
        env.call(debug_raw="true")
        env.set_now(T0 + timedelta(seconds=30))
        env.call(debug_raw="true")
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

        src = _SCRIPT_PATH.read_text()
        self._ns: dict[str, Any] = {
            "__builtins__": __builtins__,
            "service": self.mock_service,
            "state": self.mock_state,
            "homeassistant": self.mock_ha,
            "log": self.mock_log,
            "hass": "mock_hass_obj",
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

        # Wire mock helpers into the namespace
        self._ns["_get_integration_entities"] = (
            self._mock_get_integration_entities
        )
        self._ns["_get_device_for_entity"] = self._mock_get_device_for_entity
        self._ns["_read_entity_state"] = self._mock_read_entity_state

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

    def setup_device(
        self,
        integration: str,
        device_id: str,
        device_name: str,
        entities: dict[str, tuple[str, datetime]],
    ) -> None:
        """Wire up a device with entities.

        entities: {entity_id: (state, last_changed)}
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
        "monitored_integrations_raw": ["zwave_js"],
        "device_exclude_regex_raw": "",
        "entity_exclude_regex_raw": "",
        "monitored_entity_domains_raw": [],
        "check_interval_minutes_raw": "1",
        "dead_device_threshold_minutes_raw": "1440",
        "debug_output_raw": "false",
    }
    defaults.update(overrides)
    return defaults


class TestDeviceWatchdogHassDetection:
    """Test hass_is_global detection."""

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
        assert call["notification_id"] == ("device_watchdog_config_error")
        assert env.mock_pn.dismiss_calls == []

    def test_present_hass_no_config_error(self) -> None:
        env = _WatchdogEnv()
        env.call()
        config_errors = [
            c
            for c in env.mock_pn.create_calls
            if c.get("notification_id") == "device_watchdog_config_error"
        ]
        assert config_errors == []


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

    def test_invalid_entity_regex_notifies(self) -> None:
        env = _WatchdogEnv()
        env.call(entity_exclude_regex_raw="(unclosed")
        assert len(env.mock_pn.create_calls) == 1
        msg = env.mock_pn.create_calls[0]["message"]
        assert "entity_exclude_regex" in msg
        assert "(unclosed" in msg

    def test_both_invalid_reports_both(self) -> None:
        env = _WatchdogEnv()
        env.call(
            device_exclude_regex_raw="[bad",
            entity_exclude_regex_raw="(bad",
        )
        assert len(env.mock_pn.create_calls) == 1
        msg = env.mock_pn.create_calls[0]["message"]
        assert "device_exclude_regex" in msg
        assert "entity_exclude_regex" in msg

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
            entity_exclude_regex_raw="^sensor\\.bat",
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
        assert env.mock_pn.dismiss_calls == []

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


class TestDeviceWatchdogDiscovery:
    """Test device discovery from registries."""

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
        # Should dismiss (healthy device)
        assert len(env.mock_pn.dismiss_calls) == 1
        assert (
            env.mock_pn.dismiss_calls[0]["notification_id"]
            == "device_watchdog_dev1"
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
        # One device, one dismiss
        total = len(env.mock_pn.create_calls) + len(env.mock_pn.dismiss_calls)
        assert total == 1

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
            monitored_integrations_raw=["zwave_js", "matter"],
        )
        total = len(env.mock_pn.create_calls) + len(env.mock_pn.dismiss_calls)
        assert total == 2

    def test_no_devices_no_notifications(self) -> None:
        env = _WatchdogEnv()
        env.call()
        assert env.mock_pn.create_calls == []
        assert env.mock_pn.dismiss_calls == []


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
        assert len(env.mock_pn.dismiss_calls) == 1
        assert (
            env.mock_pn.dismiss_calls[0]["notification_id"]
            == "device_watchdog_dev1"
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
        assert len(env.mock_pn.dismiss_calls) == 1


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
        assert attrs["devices_checked"] == 1
        assert attrs["devices_with_issues"] == 0

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
        assert attrs["devices_checked"] == 2
        assert attrs["devices_with_issues"] == 1

    def test_integrations_attr(self) -> None:
        env = _WatchdogEnv()
        env.call(
            monitored_integrations_raw=["zwave_js", "matter"],
        )
        key = "pyscript.auto_dw_test_state"
        attrs = env.mock_state.getattr(key)
        assert "zwave_js" in attrs["integrations"]


class TestDeviceWatchdogDebugLogging:
    """Test debug logging behavior."""

    def test_no_logging_when_debug_false(self) -> None:
        env = _WatchdogEnv()
        env.call(debug_output_raw="false")
        assert env.mock_log.warning_calls == []

    def test_logging_when_debug_true(self) -> None:
        env = _WatchdogEnv()
        env.setup_device(
            "zwave_js",
            "dev1",
            "Dev",
            {"sensor.a": ("22.5", T0_UTC)},
        )
        env.call(debug_output_raw="true")
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
        env.call(debug_output_raw="true")
        msg = env.mock_log.warning_calls[0][0]
        args = env.mock_log.warning_calls[0][1]
        formatted = msg % args
        assert "issues=1" in formatted
        assert "Bad Dev" in formatted


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

    PyScript evaluates service wrappers (pyscript/*.py) with
    a custom AST interpreter, not standard Python.  Imported
    modules (pyscript/modules/*.py) run under standard Python
    import, but some constructs still fail at the boundary.

    Known limitations (pyscript 1.7.0):
      - @classmethod / @staticmethod / @property unsupported
      - lambda closures cannot capture local variables
      - generator expressions (x for x in ...) unsupported
      - match/case unsupported
      - yield / yield from unsupported
      - all pyscript functions are async, so dunder methods
        (__eq__, __str__, etc.) defined in pyscript don't work
      - print() is intercepted (use log.* instead)

    These tests scan source files to prevent regressions.
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

    def test_no_staticmethod_decorator(self) -> None:
        """@staticmethod is not supported by PyScript."""
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
                    assert name != "staticmethod", (
                        f"{path.name}:{node.lineno}"
                        f" @staticmethod on {node.name}()"
                        " -- PyScript does not support"
                        " built-in decorators."
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

    def test_no_lambda_in_service_wrappers(self) -> None:
        """Lambda closures break under the AST evaluator.

        Lambda functions are compiled to native Python via
        @pyscript_compile, which cannot capture variables
        from the enclosing pyscript scope.
        """
        for path in self._service_files():
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

    def test_no_yield_in_service_wrappers(self) -> None:
        """yield / yield from are not supported."""
        for path in self._service_files():
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

    def test_no_print_in_service_wrappers(self) -> None:
        """print() is intercepted by PyScript; use log.*."""
        for path in self._service_files():
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


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _SCRIPT_PATH, REPO_ROOT)
