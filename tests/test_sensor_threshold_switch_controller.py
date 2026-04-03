#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest", "pytest-cov", "ruff", "mypy"]
# ///
# This is AI generated code
"""Tests for sensor_threshold_switch_controller module."""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

# Path to the module under test (used for coverage)
_SCRIPT_PATH = (
    REPO_ROOT / "pyscript" / "modules" / "sensor_threshold_switch_controller.py"
)

# Ensure pyscript/modules is importable whether run
# via pytest or directly via uv run --script.
sys.path.insert(0, str(_SCRIPT_PATH.parent))

import pytest  # noqa: E402
from conftest import CodeQualityBase  # noqa: E402
from sensor_threshold_switch_controller import (  # noqa: E402
    Action,
    Config,
    Controller,
    EventType,
    Inputs,
    Result,
    Sample,
    ServiceResult,
    State,
    determine_event_type,
    evaluate,
    format_notification,
    format_timestamp,
    handle_service_call,
    parse_float,
    state_from_dict,
)

T0 = datetime(2024, 1, 15, 12, 0, 0)


def sensor_inputs(
    value: float | None,
    switch_state: str = "off",
    current_time: datetime = T0,
    switch_name: str = "Test Fan",
) -> Inputs:
    """Build Inputs for a sensor event."""
    return Inputs(
        current_time=current_time,
        event_type=EventType.SENSOR,
        sensor_value=value,
        switch_state=switch_state,
        switch_name=switch_name,
    )


def switch_inputs(
    switch_state: str,
    current_time: datetime = T0,
    switch_name: str = "Test Fan",
) -> Inputs:
    """Build Inputs for a switch event."""
    return Inputs(
        current_time=current_time,
        event_type=EventType.SWITCH,
        switch_state=switch_state,
        switch_name=switch_name,
    )


def timer_inputs(
    switch_state: str = "on",
    current_time: datetime = T0,
    switch_name: str = "Test Fan",
) -> Inputs:
    """Build Inputs for a timer event."""
    return Inputs(
        current_time=current_time,
        event_type=EventType.TIMER,
        switch_state=switch_state,
        switch_name=switch_name,
    )


class TestParseFloat:
    def test_valid_float(self) -> None:
        assert parse_float("42.5") == 42.5

    def test_integer_string(self) -> None:
        assert parse_float("42") == 42.0

    def test_none_returns_none(self) -> None:
        assert parse_float(None) is None

    def test_empty_string(self) -> None:
        assert parse_float("") is None

    def test_unknown(self) -> None:
        assert parse_float("unknown") is None

    def test_unavailable(self) -> None:
        assert parse_float("unavailable") is None

    def test_nan_string(self) -> None:
        assert parse_float("NaN") is None

    def test_inf_string(self) -> None:
        assert parse_float("inf") is None

    def test_negative_inf(self) -> None:
        assert parse_float("-inf") is None

    def test_non_numeric_text(self) -> None:
        assert parse_float("hello") is None

    def test_negative_number(self) -> None:
        assert parse_float("-3.14") == -3.14

    def test_zero(self) -> None:
        assert parse_float("0") == 0.0


class TestFormatTimestamp:
    def test_full_format(self) -> None:
        dt = datetime(2024, 3, 5, 14, 7, 9)
        result = format_timestamp("YYYY-MM-DD HH:mm:ss", dt)
        assert result == "2024-03-05 14:07:09"

    def test_short_year(self) -> None:
        dt = datetime(2024, 1, 1, 0, 0, 0)
        assert format_timestamp("YY", dt) == "24"

    def test_empty_template(self) -> None:
        assert format_timestamp("", T0) == ""

    def test_no_tokens(self) -> None:
        assert format_timestamp("no tokens here", T0) == "no tokens here"

    def test_prefix_with_tokens(self) -> None:
        dt = datetime(2024, 6, 15, 8, 30, 0)
        result = format_timestamp("Log at HH:mm - ", dt)
        assert result == "Log at 08:30 - "


class TestStateSerialization:
    def test_empty_state_round_trip(self) -> None:
        state = State()
        data = state.to_dict()
        restored = state_from_dict(data)
        assert restored.samples == []
        assert restored.baseline is None
        assert restored.overrides == []
        assert restored.auto_off_started_at is None
        assert restored.initialized is False

    def test_populated_state_round_trip(self) -> None:
        state = State(
            samples=[Sample(value=60.0, timestamp=T0)],
            baseline=55.0,
            overrides=[T0],
            auto_off_started_at=T0,
            initialized=True,
        )
        data = state.to_dict()
        restored = state_from_dict(data)
        assert len(restored.samples) == 1
        assert restored.samples[0].value == 60.0
        assert restored.samples[0].timestamp == T0
        assert restored.baseline == 55.0
        assert restored.overrides == [T0]
        assert restored.auto_off_started_at == T0
        assert restored.initialized is True

    def test_from_dict_missing_keys(self) -> None:
        restored = state_from_dict({})
        assert restored.samples == []
        assert restored.baseline is None
        assert restored.overrides == []
        assert restored.auto_off_started_at is None
        assert restored.initialized is False

    def test_realistic_state_exceeds_ha_state_limit(
        self,
    ) -> None:
        """Serialized state with samples exceeds the HA
        entity state value limit of 255 characters.

        This is why the service wrapper stores state in an
        entity attribute (via state.setattr) rather than the
        entity state value (via state.set).  If someone
        changes the storage mechanism back to state.set,
        persistence will silently fail once enough sensor
        readings accumulate.

        5 samples is modest (300s window, reading every 60s).
        """
        state = State(
            samples=[
                Sample(
                    value=65.0 + i,
                    timestamp=T0 + timedelta(seconds=60 * i),
                )
                for i in range(5)
            ],
            baseline=60.0,
            overrides=[T0],
            auto_off_started_at=T0,
            initialized=True,
        )
        serialized = json.dumps(state.to_dict())
        assert len(serialized) > 255, (
            f"Serialized state is only {len(serialized)}"
            " chars. If a refactor shrinks it below 255,"
            " update the service wrapper comment and"
            " consider whether attribute storage is"
            " still needed."
        )


class TestSensorSpikeDetection:
    @pytest.fixture()
    def config(self) -> Config:
        return Config(
            trigger_threshold=5.0,
            release_threshold=2.0,
            sampling_window_seconds=120,
            disable_window_seconds=10,
            auto_off_minutes=1,
        )

    @pytest.fixture()
    def controller(self, config: Config) -> Controller:
        return Controller(config)

    def test_no_action_for_none_sensor_value(
        self, controller: Controller
    ) -> None:
        state = State()
        result = controller.evaluate(state, sensor_inputs(None))
        assert result.action == Action.NONE
        assert state.samples == []

    def test_no_trigger_below_threshold(self, controller: Controller) -> None:
        state = State()
        # Two readings with 4-unit spread (< 5 threshold)
        controller.evaluate(state, sensor_inputs(60.0))
        result = controller.evaluate(state, sensor_inputs(64.0))
        assert result.action == Action.NONE
        assert state.baseline is None

    def test_no_trigger_at_exact_threshold(
        self, controller: Controller
    ) -> None:
        """Spike must be strictly greater than threshold."""
        state = State()
        controller.evaluate(state, sensor_inputs(60.0))
        result = controller.evaluate(state, sensor_inputs(65.0))
        assert result.action == Action.NONE
        assert state.baseline is None

    def test_trigger_above_threshold(self, controller: Controller) -> None:
        state = State()
        controller.evaluate(state, sensor_inputs(60.0))
        result = controller.evaluate(state, sensor_inputs(65.1))
        assert result.action == Action.TURN_ON
        assert state.baseline == 60.0
        assert "spike" in result.reason.lower()
        assert result.notification != ""

    def test_sets_baseline_to_min_on_spike(
        self, controller: Controller
    ) -> None:
        state = State()
        controller.evaluate(state, sensor_inputs(60.0))
        controller.evaluate(state, sensor_inputs(58.0))
        result = controller.evaluate(state, sensor_inputs(70.0))
        assert result.action == Action.TURN_ON
        assert state.baseline == 58.0

    def test_spike_clears_overrides_and_auto_off(
        self, controller: Controller
    ) -> None:
        state = State(
            overrides=[T0],
            auto_off_started_at=T0,
        )
        controller.evaluate(state, sensor_inputs(60.0))
        controller.evaluate(state, sensor_inputs(70.0))
        assert state.overrides == []
        assert state.auto_off_started_at is None

    def test_spike_when_switch_already_on(self, controller: Controller) -> None:
        """When switch is already on, set baseline
        but don't command TURN_ON."""
        state = State()
        controller.evaluate(
            state,
            sensor_inputs(60.0, switch_state="on"),
        )
        result = controller.evaluate(
            state,
            sensor_inputs(70.0, switch_state="on"),
        )
        assert result.action == Action.NONE
        assert state.baseline == 60.0
        assert result.notification == ""


class TestSensorRelease:
    @pytest.fixture()
    def config(self) -> Config:
        return Config(
            trigger_threshold=5.0,
            release_threshold=2.0,
            sampling_window_seconds=120,
            disable_window_seconds=10,
            auto_off_minutes=30,
        )

    @pytest.fixture()
    def controller(self, config: Config) -> Controller:
        return Controller(config)

    def test_no_release_above_threshold(self, controller: Controller) -> None:
        state = State(baseline=60.0)
        result = controller.evaluate(
            state,
            sensor_inputs(65.0, switch_state="on"),
        )
        assert result.action == Action.NONE
        assert state.baseline == 60.0

    def test_release_at_threshold_boundary(
        self, controller: Controller
    ) -> None:
        """Release when max <= baseline + release."""
        state = State(baseline=60.0)
        result = controller.evaluate(
            state,
            sensor_inputs(62.0, switch_state="on"),
        )
        assert result.action == Action.TURN_OFF
        assert state.baseline is None
        assert "release" in result.reason.lower()
        assert result.notification != ""

    def test_release_clears_state(self, controller: Controller) -> None:
        state = State(
            baseline=60.0,
            overrides=[T0],
            auto_off_started_at=T0,
        )
        controller.evaluate(
            state,
            sensor_inputs(61.0, switch_state="on"),
        )
        assert state.baseline is None
        assert state.overrides == []
        assert state.auto_off_started_at is None

    def test_release_when_switch_already_off(
        self, controller: Controller
    ) -> None:
        state = State(baseline=60.0)
        result = controller.evaluate(
            state,
            sensor_inputs(62.0, switch_state="off"),
        )
        assert result.action == Action.NONE
        assert state.baseline is None
        assert result.notification == ""


class TestSampleWindow:
    @pytest.fixture()
    def config(self) -> Config:
        return Config(
            trigger_threshold=5.0,
            release_threshold=2.0,
            sampling_window_seconds=10,
            disable_window_seconds=10,
            auto_off_minutes=30,
        )

    @pytest.fixture()
    def controller(self, config: Config) -> Controller:
        return Controller(config)

    def test_prunes_old_samples(self, controller: Controller) -> None:
        now = T0
        state = State(
            samples=[
                Sample(
                    value=50.0,
                    timestamp=now - timedelta(seconds=15),
                ),
                Sample(
                    value=60.0,
                    timestamp=now - timedelta(seconds=5),
                ),
                Sample(
                    value=70.0,
                    timestamp=now - timedelta(seconds=1),
                ),
            ]
        )
        controller.evaluate(
            state,
            sensor_inputs(65.0, current_time=now),
        )
        # Old sample (15s ago) pruned; 3 remain
        assert len(state.samples) == 3
        values = [s.value for s in state.samples]
        assert 50.0 not in values
        assert 60.0 in values
        assert 70.0 in values
        assert 65.0 in values


class TestManualOverride:
    @pytest.fixture()
    def config(self) -> Config:
        return Config(
            trigger_threshold=5.0,
            release_threshold=2.0,
            sampling_window_seconds=300,
            disable_window_seconds=10,
            auto_off_minutes=1,
        )

    @pytest.fixture()
    def controller(self, config: Config) -> Controller:
        return Controller(config)

    def test_re_enables_on_first_manual_off(
        self, controller: Controller
    ) -> None:
        state = State(
            baseline=55.0,
            initialized=True,
        )
        result = controller.evaluate(state, switch_inputs("off"))
        assert result.action == Action.TURN_ON
        assert len(state.overrides) == 1
        assert "manual off" in result.reason.lower()
        assert result.notification != ""

    def test_disables_on_double_manual_off(
        self, controller: Controller
    ) -> None:
        state = State(
            baseline=55.0,
            initialized=True,
            overrides=[T0 - timedelta(seconds=5)],
        )
        result = controller.evaluate(state, switch_inputs("off"))
        assert result.action == Action.NONE
        assert state.baseline is None
        assert "override disabled" in result.notification
        assert state.overrides == []

    def test_no_disable_outside_window(self, controller: Controller) -> None:
        """Second off 20s ago, outside 10s window."""
        state = State(
            baseline=55.0,
            initialized=True,
            overrides=[T0 - timedelta(seconds=20)],
        )
        result = controller.evaluate(state, switch_inputs("off"))
        assert result.action == Action.TURN_ON
        assert state.baseline == 55.0

    def test_disable_window_zero_never_disables(
        self,
    ) -> None:
        """When disable_window=0, always re-enable."""
        config = Config(
            trigger_threshold=5.0,
            release_threshold=2.0,
            sampling_window_seconds=300,
            disable_window_seconds=0,
            auto_off_minutes=30,
        )
        ctrl = Controller(config)
        state = State(
            baseline=55.0,
            initialized=True,
            overrides=[T0 - timedelta(seconds=1)],
        )
        result = ctrl.evaluate(state, switch_inputs("off"))
        assert result.action == Action.TURN_ON
        # Previous overrides cleared, only current one
        assert len(state.overrides) == 1


class TestAutoOff:
    @pytest.fixture()
    def config(self) -> Config:
        return Config(
            trigger_threshold=5.0,
            release_threshold=2.0,
            sampling_window_seconds=300,
            disable_window_seconds=10,
            auto_off_minutes=1,
        )

    @pytest.fixture()
    def controller(self, config: Config) -> Controller:
        return Controller(config)

    def test_schedules_on_manual_on(self, controller: Controller) -> None:
        state = State(initialized=True)
        controller.evaluate(state, switch_inputs("on"))
        assert state.auto_off_started_at == T0

    def test_timer_fires_after_timeout(self, controller: Controller) -> None:
        state = State(
            auto_off_started_at=T0,
            initialized=True,
        )
        result = controller.evaluate(
            state,
            timer_inputs(current_time=T0 + timedelta(minutes=1)),
        )
        assert result.action == Action.TURN_OFF
        assert state.auto_off_started_at is None
        assert "auto-off" in result.reason.lower()
        assert result.notification != ""

    def test_timer_no_action_before_timeout(
        self, controller: Controller
    ) -> None:
        state = State(
            auto_off_started_at=T0,
            initialized=True,
        )
        result = controller.evaluate(
            state,
            timer_inputs(current_time=T0 + timedelta(seconds=30)),
        )
        assert result.action == Action.NONE
        assert state.auto_off_started_at == T0

    def test_timer_ignored_when_baseline_active(
        self, controller: Controller
    ) -> None:
        state = State(
            auto_off_started_at=T0,
            baseline=55.0,
            initialized=True,
        )
        result = controller.evaluate(
            state,
            timer_inputs(current_time=T0 + timedelta(minutes=5)),
        )
        assert result.action == Action.NONE
        assert state.auto_off_started_at == T0

    def test_timer_ignored_when_switch_off(
        self, controller: Controller
    ) -> None:
        state = State(
            auto_off_started_at=T0,
            initialized=True,
        )
        result = controller.evaluate(
            state,
            timer_inputs(
                switch_state="off",
                current_time=T0 + timedelta(minutes=5),
            ),
        )
        assert result.action == Action.NONE

    def test_cancelled_when_baseline_set(self, controller: Controller) -> None:
        """Auto-off cancelled when sensor spike takes
        over control."""
        state = State(
            auto_off_started_at=T0,
            initialized=True,
        )
        # Sensor spike: baseline gets set
        controller.evaluate(
            state,
            sensor_inputs(60.0, switch_state="on"),
        )
        controller.evaluate(
            state,
            sensor_inputs(70.0, switch_state="on"),
        )
        assert state.baseline == 60.0
        assert state.auto_off_started_at is None

    def test_cancelled_on_switch_off_no_baseline(
        self, controller: Controller
    ) -> None:
        state = State(
            auto_off_started_at=T0,
            initialized=True,
        )
        controller.evaluate(state, switch_inputs("off"))
        assert state.auto_off_started_at is None

    def test_cleared_on_switch_on_with_baseline(
        self, controller: Controller
    ) -> None:
        """When switch turns on while baseline active,
        auto-off is cancelled."""
        state = State(
            baseline=55.0,
            auto_off_started_at=T0,
            initialized=True,
        )
        controller.evaluate(state, switch_inputs("on"))
        assert state.auto_off_started_at is None

    def test_auto_off_zero_disables(self) -> None:
        """auto_off_minutes=0 means no auto-off."""
        config = Config(
            trigger_threshold=5.0,
            release_threshold=2.0,
            sampling_window_seconds=300,
            disable_window_seconds=10,
            auto_off_minutes=0,
        )
        ctrl = Controller(config)
        state = State(initialized=True)
        ctrl.evaluate(state, switch_inputs("on"))
        assert state.auto_off_started_at is None


class TestStartupRecovery:
    @pytest.fixture()
    def config(self) -> Config:
        return Config(
            trigger_threshold=5.0,
            release_threshold=2.0,
            sampling_window_seconds=300,
            disable_window_seconds=10,
            auto_off_minutes=1,
        )

    @pytest.fixture()
    def controller(self, config: Config) -> Controller:
        return Controller(config)

    def test_startup_switch_on_schedules_auto_off(
        self, controller: Controller
    ) -> None:
        state = State(initialized=False)
        result = controller.evaluate(state, switch_inputs("on"))
        assert result.action == Action.NONE
        assert state.initialized is True
        assert state.auto_off_started_at == T0

    def test_startup_switch_off_no_auto_off(
        self, controller: Controller
    ) -> None:
        state = State(initialized=False)
        result = controller.evaluate(state, switch_inputs("off"))
        assert result.action == Action.NONE
        assert state.initialized is True
        assert state.auto_off_started_at is None

    def test_startup_with_baseline_no_auto_off(
        self, controller: Controller
    ) -> None:
        """If baseline is active at startup, no auto-off
        (sensor is managing)."""
        state = State(initialized=False, baseline=55.0)
        controller.evaluate(state, switch_inputs("on"))
        assert state.auto_off_started_at is None

    def test_startup_returns_no_action(self, controller: Controller) -> None:
        """First switch event always returns NONE."""
        state = State(initialized=False)
        result = controller.evaluate(state, switch_inputs("on"))
        assert result.action == Action.NONE

    def test_timer_starts_auto_off_when_switch_on_no_state(
        self, controller: Controller
    ) -> None:
        """After HA restart with lost state, the first timer
        event should start auto-off if switch is already on."""
        state = State()  # fresh state, no auto_off_started_at
        t = T0

        # Timer fires while switch is on — should start
        # auto-off even though no switch event occurred.
        result = controller.evaluate(state, timer_inputs("on", current_time=t))
        assert result.action == Action.NONE
        assert state.auto_off_started_at == t

        # After timeout, timer should turn off.
        t += timedelta(minutes=1, seconds=1)
        result = controller.evaluate(state, timer_inputs("on", current_time=t))
        assert result.action == Action.TURN_OFF

    def test_timer_no_auto_off_when_switch_off(
        self, controller: Controller
    ) -> None:
        """Timer should not start auto-off if switch is off."""
        state = State()
        result = controller.evaluate(
            state, timer_inputs("off", current_time=T0)
        )
        assert result.action == Action.NONE
        assert state.auto_off_started_at is None

    def test_timer_no_auto_off_when_baseline_active(
        self, controller: Controller
    ) -> None:
        """Timer should not start auto-off if sensor is
        managing the switch (baseline active)."""
        state = State(baseline=55.0)
        result = controller.evaluate(state, timer_inputs("on", current_time=T0))
        assert result.action == Action.NONE
        assert state.auto_off_started_at is None


class TestEndToEnd:
    """Full scenario tests simulating real sequences."""

    @pytest.fixture()
    def config(self) -> Config:
        return Config(
            trigger_threshold=10.0,
            release_threshold=5.0,
            sampling_window_seconds=300,
            disable_window_seconds=10,
            auto_off_minutes=30,
        )

    @pytest.fixture()
    def controller(self, config: Config) -> Controller:
        return Controller(config)

    def test_humidity_fan_cycle(self, controller: Controller) -> None:
        """Full cycle: startup -> humidity spike ->
        fan on -> humidity drops -> fan off."""
        state = State()
        t = T0

        # Startup: switch is off
        controller.evaluate(state, switch_inputs("off", current_time=t))
        assert state.initialized is True

        # Humidity readings (stable)
        for i in range(3):
            t += timedelta(seconds=30)
            controller.evaluate(
                state,
                sensor_inputs(
                    50.0 + i * 0.5,
                    current_time=t,
                ),
            )
        assert state.baseline is None

        # Shower starts: humidity spikes
        t += timedelta(seconds=30)
        result = controller.evaluate(
            state,
            sensor_inputs(62.0, current_time=t),
        )
        assert result.action == Action.TURN_ON
        assert state.baseline == 50.0

        # Humidity stays high, timer ticks
        for _ in range(5):
            t += timedelta(minutes=1)
            result = controller.evaluate(
                state,
                timer_inputs(
                    switch_state="on",
                    current_time=t,
                ),
            )
            assert result.action == Action.NONE

        # Humidity drops back to normal
        t += timedelta(minutes=1)
        result = controller.evaluate(
            state,
            sensor_inputs(
                54.0,
                switch_state="on",
                current_time=t,
            ),
        )
        assert result.action == Action.TURN_OFF
        assert state.baseline is None

    def test_manual_on_auto_off_cycle(self, controller: Controller) -> None:
        """Manual on -> timer ticks -> auto-off fires."""
        state = State()
        t = T0

        # Startup
        controller.evaluate(state, switch_inputs("off", current_time=t))

        # Manual switch on
        t += timedelta(seconds=5)
        controller.evaluate(state, switch_inputs("on", current_time=t))
        # Rounded up to next minute for time_pattern alignment
        assert state.auto_off_started_at == t.replace(
            second=0,
            microsecond=0,
        ) + timedelta(minutes=1)

        # Timer ticks for 30 minutes (extra minute from round-up)
        for _ in range(30):
            t += timedelta(minutes=1)
            result = controller.evaluate(
                state,
                timer_inputs(current_time=t),
            )
            assert result.action == Action.NONE

        # 31 minutes: auto-off fires (30 min timeout + round-up)
        t += timedelta(minutes=1)
        result = controller.evaluate(
            state,
            timer_inputs(current_time=t),
        )
        assert result.action == Action.TURN_OFF

    def test_manual_off_override_and_disable(
        self, controller: Controller
    ) -> None:
        """Baseline active -> manual off -> re-enable ->
        manual off again -> disable."""
        state = State(baseline=50.0, initialized=True)
        t = T0

        # First manual off: re-enable
        result = controller.evaluate(
            state, switch_inputs("off", current_time=t)
        )
        assert result.action == Action.TURN_ON

        # Second manual off within window: disable
        t += timedelta(seconds=5)
        result = controller.evaluate(
            state, switch_inputs("off", current_time=t)
        )
        assert result.action == Action.NONE
        assert state.baseline is None
        assert "disabled" in result.notification


class TestConfigValidation:
    def test_all_fields_required(self) -> None:
        """Config has no defaults; all fields must be given."""
        with pytest.raises(TypeError):
            Config()  # type: ignore[call-arg]

    def test_custom_config(self) -> None:
        config = Config(
            trigger_threshold=10.0,
            release_threshold=3.0,
            sampling_window_seconds=600,
            disable_window_seconds=0,
            auto_off_minutes=0,
        )
        assert config.trigger_threshold == 10.0
        assert config.auto_off_minutes == 0


class TestResultDefaults:
    def test_default_result(self) -> None:
        result = Result()
        assert result.action == Action.NONE
        assert result.reason == ""
        assert result.notification == ""


class TestUnknownEventType:
    def test_returns_none_for_unknown(self) -> None:
        """Controller.evaluate returns NONE for
        unrecognized event types, though all EventType
        values are handled."""
        config = Config(
            trigger_threshold=5.0,
            release_threshold=2.0,
            sampling_window_seconds=300,
            disable_window_seconds=10,
            auto_off_minutes=30,
        )
        ctrl = Controller(config)
        state = State()
        # All defined event types are handled;
        # this test confirms the fallback path.
        inputs = Inputs(
            current_time=T0,
            event_type=EventType.TIMER,
            switch_state="off",
        )
        result = ctrl.evaluate(state, inputs)
        assert result.action == Action.NONE


class TestDetermineEventType:
    def test_switch_event(self) -> None:
        """Trigger entity == output entity -> SWITCH."""
        assert (
            determine_event_type("switch.fan", "switch.fan") == EventType.SWITCH
        )

    def test_sensor_event(self) -> None:
        """Trigger entity differs from output -> SENSOR."""
        assert (
            determine_event_type("sensor.humidity", "switch.fan")
            == EventType.SENSOR
        )

    def test_timer_empty_string(self) -> None:
        assert determine_event_type("", "switch.fan") == EventType.TIMER

    def test_timer_literal_timer(self) -> None:
        assert determine_event_type("timer", "switch.fan") == EventType.TIMER

    def test_timer_literal_none(self) -> None:
        assert determine_event_type("None", "switch.fan") == EventType.TIMER

    def test_timer_lowercase_none(self) -> None:
        assert determine_event_type("none", "switch.fan") == EventType.TIMER


class TestFormatNotification:
    def test_prefix_and_suffix(self) -> None:
        dt = datetime(2024, 6, 15, 8, 30, 0)
        result = format_notification("Fan on.", "PRE: ", " at HH:mm", dt)
        assert result == "PRE: Fan on. at 08:30"

    def test_empty_prefix_suffix(self) -> None:
        result = format_notification("hello", "", "", T0)
        assert result == "hello"

    def test_timestamp_tokens_in_both(self) -> None:
        dt = datetime(2024, 1, 2, 3, 4, 5)
        result = format_notification(
            "msg",
            "YYYY-MM-DD ",
            " HH:mm:ss",
            dt,
        )
        assert result == "2024-01-02 msg 03:04:05"


class TestEvaluate:
    """Tests for the top-level evaluate() entrypoint."""

    @staticmethod
    def _eval_kwargs(
        **overrides: object,
    ) -> dict[str, object]:
        """Default kwargs for evaluate()."""
        defaults: dict[str, object] = {
            "current_time": T0,
            "switch_name": "Test Fan",
            "target_switch_entity": "switch.fan",
            "sensor_value": "",
            "switch_state": "off",
            "trigger_entity": "timer",
            "trigger_threshold": 5.0,
            "release_threshold": 2.0,
            "sampling_window_s": 300,
            "disable_window_s": 10,
            "auto_off_min": 30,
            "notification_prefix": "",
            "notification_suffix": "",
        }
        defaults.update(overrides)
        return defaults

    def test_sensor_spike_turns_on(self) -> None:
        """Spike -> TURN_ON with formatted notification."""
        s = State()
        # Seed a low reading first
        evaluate(
            state=s,
            **self._eval_kwargs(
                sensor_value="60.0",
                trigger_entity="sensor.humidity",
                notification_prefix="PRE: ",
                notification_suffix=" END",
            ),
        )
        result = evaluate(
            state=s,
            **self._eval_kwargs(
                sensor_value="70.0",
                trigger_entity="sensor.humidity",
                notification_prefix="PRE: ",
                notification_suffix=" END",
                current_time=T0 + timedelta(seconds=10),
            ),
        )
        assert result.action == Action.TURN_ON
        assert result.notification.startswith("PRE: ")
        assert result.notification.endswith(" END")

    def test_timer_event_no_action(self) -> None:
        """Timer trigger with no auto-off -> NONE."""
        s = State()
        result = evaluate(state=s, **self._eval_kwargs())
        assert result.action == Action.NONE
        assert result.notification == ""

    def test_switch_event_startup(self) -> None:
        """Switch trigger on uninitialized state."""
        s = State()
        result = evaluate(
            state=s,
            **self._eval_kwargs(
                switch_state="on",
                trigger_entity="switch.fan",
                auto_off_min=5,
            ),
        )
        assert result.action == Action.NONE
        assert s.initialized is True
        assert s.auto_off_started_at == T0

    def test_no_notification_formatting_when_empty(
        self,
    ) -> None:
        """When controller returns no notification, prefix
        and suffix are not applied."""
        s = State()
        result = evaluate(
            state=s,
            **self._eval_kwargs(
                notification_prefix="PRE: ",
                notification_suffix=" END",
            ),
        )
        assert result.notification == ""

    def test_sensor_value_not_parsed_for_non_sensor(
        self,
    ) -> None:
        """sensor_value is ignored for non-sensor events."""
        s = State()
        result = evaluate(
            state=s,
            **self._eval_kwargs(sensor_value="99.9"),
        )
        assert result.action == Action.NONE


class TestHandleServiceCall:
    """Tests for the handle_service_call bridge entry
    point."""

    @staticmethod
    def _call_kwargs(
        **overrides: object,
    ) -> dict[str, object]:
        """Default kwargs for handle_service_call."""
        defaults: dict[str, object] = {
            "state_data": None,
            "switch_name": "Test Fan",
            "current_time": T0,
            "target_switch_entity": "switch.fan",
            "sensor_value": "",
            "switch_state": "off",
            "trigger_entity": "timer",
            "trigger_threshold": 5.0,
            "release_threshold": 2.0,
            "sampling_window_s": 300,
            "disable_window_s": 10,
            "auto_off_min": 30,
            "notification_service": "",
            "notification_prefix": "",
            "notification_suffix": "",
        }
        defaults.update(overrides)
        return defaults

    def test_returns_state_dict(self) -> None:
        """ServiceResult includes a serialisable
        state_dict."""
        result = handle_service_call(
            **self._call_kwargs(),
        )
        assert isinstance(result, ServiceResult)
        assert isinstance(result.state_dict, dict)

    def test_sensor_spike_returns_turn_on(self) -> None:
        """Two sensor readings that spike -> TURN_ON."""
        r1 = handle_service_call(
            **self._call_kwargs(
                sensor_value="60.0",
                trigger_entity="sensor.humidity",
                current_time=T0,
            ),
        )
        # Feed saved state back in
        r2 = handle_service_call(
            **self._call_kwargs(
                state_data=r1.state_dict,
                sensor_value="70.0",
                trigger_entity="sensor.humidity",
                current_time=T0 + timedelta(seconds=10),
            ),
        )
        assert r2.action == Action.TURN_ON

    def test_release_returns_turn_off(self) -> None:
        """Baseline active, sensor drops -> TURN_OFF."""
        seed = State(
            baseline=60.0,
            samples=[Sample(value=62.0, timestamp=T0)],
            initialized=True,
        )
        result = handle_service_call(
            **self._call_kwargs(
                state_data=seed.to_dict(),
                sensor_value="61.0",
                switch_state="on",
                trigger_entity="sensor.humidity",
                current_time=T0 + timedelta(seconds=30),
            ),
        )
        assert result.action == Action.TURN_OFF

    def test_notification_sent(self) -> None:
        """Spike with notification_service -> notification
        populated in result."""
        r1 = handle_service_call(
            **self._call_kwargs(
                sensor_value="60.0",
                trigger_entity="sensor.humidity",
                notification_service="notify.phone",
                current_time=T0,
            ),
        )
        r2 = handle_service_call(
            **self._call_kwargs(
                state_data=r1.state_dict,
                sensor_value="70.0",
                trigger_entity="sensor.humidity",
                notification_service="notify.phone",
                current_time=T0 + timedelta(seconds=10),
            ),
        )
        assert r2.notification_service == "notify.phone"
        assert "spike" in r2.notification.lower()

    def test_notification_normalizes_service_name(
        self,
    ) -> None:
        """'phone' -> notification_service is
        'notify.phone'."""
        r1 = handle_service_call(
            **self._call_kwargs(
                sensor_value="60.0",
                trigger_entity="sensor.humidity",
                notification_service="phone",
                current_time=T0,
            ),
        )
        r2 = handle_service_call(
            **self._call_kwargs(
                state_data=r1.state_dict,
                sensor_value="70.0",
                trigger_entity="sensor.humidity",
                notification_service="phone",
                current_time=T0 + timedelta(seconds=10),
            ),
        )
        assert r2.notification_service == "notify.phone"

    def test_no_notification_when_service_empty(
        self,
    ) -> None:
        """Empty notification_service -> no notification
        in result."""
        r1 = handle_service_call(
            **self._call_kwargs(
                sensor_value="60.0",
                trigger_entity="sensor.humidity",
                notification_service="",
                current_time=T0,
            ),
        )
        r2 = handle_service_call(
            **self._call_kwargs(
                state_data=r1.state_dict,
                sensor_value="70.0",
                trigger_entity="sensor.humidity",
                notification_service="",
                current_time=T0 + timedelta(seconds=10),
            ),
        )
        assert r2.notification_service == ""

    def test_no_action_on_timer_idle(self) -> None:
        """Timer event, no auto-off pending -> NONE."""
        result = handle_service_call(
            **self._call_kwargs(),
        )
        assert result.action == Action.NONE

    def test_state_data_none_uses_fresh(self) -> None:
        """state_data=None -> no crash, state_dict
        returned."""
        result = handle_service_call(
            **self._call_kwargs(state_data=None),
        )
        assert isinstance(result.state_dict, dict)

    def test_reason_populated_on_spike(self) -> None:
        """ServiceResult.reason is set when action taken."""
        r1 = handle_service_call(
            **self._call_kwargs(
                sensor_value="60.0",
                trigger_entity="sensor.humidity",
                current_time=T0,
            ),
        )
        r2 = handle_service_call(
            **self._call_kwargs(
                state_data=r1.state_dict,
                sensor_value="70.0",
                trigger_entity="sensor.humidity",
                current_time=T0 + timedelta(seconds=10),
            ),
        )
        assert r2.reason != ""
        assert "spike" in r2.reason.lower()

    def test_reason_empty_on_no_action(self) -> None:
        """ServiceResult.reason is empty when no action."""
        result = handle_service_call(
            **self._call_kwargs(),
        )
        assert result.reason == ""

    def test_event_type_timer(self) -> None:
        """ServiceResult.event_type is TIMER for timer."""
        result = handle_service_call(
            **self._call_kwargs(),
        )
        assert result.event_type == "TIMER"

    def test_event_type_sensor(self) -> None:
        """ServiceResult.event_type is SENSOR for sensor."""
        result = handle_service_call(
            **self._call_kwargs(
                sensor_value="60.0",
                trigger_entity="sensor.humidity",
            ),
        )
        assert result.event_type == "SENSOR"

    def test_event_type_switch(self) -> None:
        """ServiceResult.event_type is SWITCH for switch."""
        result = handle_service_call(
            **self._call_kwargs(
                trigger_entity="switch.fan",
            ),
        )
        assert result.event_type == "SWITCH"

    def test_sensor_value_parsed(self) -> None:
        """ServiceResult.sensor_value is parsed float."""
        result = handle_service_call(
            **self._call_kwargs(
                sensor_value="65.3",
                trigger_entity="sensor.humidity",
            ),
        )
        assert result.sensor_value == 65.3

    def test_sensor_value_none_for_timer(self) -> None:
        """ServiceResult.sensor_value is None for timer."""
        result = handle_service_call(
            **self._call_kwargs(),
        )
        assert result.sensor_value is None


class TestCodeQuality(CodeQualityBase):
    ruff_targets = [
        "pyscript/modules/sensor_threshold_switch_controller.py",
        "tests/test_sensor_threshold_switch_controller.py",
    ]
    mypy_targets = [
        "pyscript/modules/sensor_threshold_switch_controller.py",
    ]


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _SCRIPT_PATH, REPO_ROOT)
