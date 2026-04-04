# This is AI generated code
"""Pure business logic for sensor-threshold-based switch control.

No PyScript or Home Assistant dependencies.

Controls a switch based on sensor value spikes (e.g., humidity),
with manual override protection, auto-off, and notifications.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum, auto
from typing import Any

from notification_helpers import format_notification


class EventType(Enum):
    """Type of event that triggered evaluation."""

    SENSOR = auto()
    SWITCH = auto()
    TIMER = auto()


class Action(Enum):
    """Actions the automation can take."""

    NONE = auto()
    TURN_ON = auto()
    TURN_OFF = auto()


@dataclass
class Sample:
    """A single sensor reading with timestamp."""

    value: float
    timestamp: datetime


@dataclass
class Config:
    """Configuration parameters (set per-instance via blueprint)."""

    trigger_threshold: float
    release_threshold: float
    sampling_window_seconds: int
    disable_window_seconds: int
    auto_off_minutes: int


@dataclass
class State:
    """Persistent state between invocations."""

    samples: list[Sample] = field(default_factory=list)
    baseline: float | None = None
    overrides: list[datetime] = field(default_factory=list)
    auto_off_started_at: datetime | None = None
    initialized: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSON persistence."""
        return {
            "samples": [
                {
                    "value": s.value,
                    "timestamp": s.timestamp.isoformat(),
                }
                for s in self.samples
            ],
            "baseline": self.baseline,
            "overrides": [ts.isoformat() for ts in self.overrides],
            "auto_off_started_at": (
                self.auto_off_started_at.isoformat()
                if self.auto_off_started_at
                else None
            ),
            "initialized": self.initialized,
        }

    # Deserialization: use module-level state_from_dict().
    # PyScript's AST evaluator cannot call @classmethod.


def state_from_dict(data: dict[str, Any]) -> "State":
    """Deserialize State from JSON persistence.

    Module-level function because PyScript's AST evaluator
    cannot call @classmethod (or any built-in decorator).
    """
    samples = [
        Sample(
            value=s["value"],
            timestamp=datetime.fromisoformat(s["timestamp"]),
        )
        for s in data.get("samples", [])
    ]
    overrides = [datetime.fromisoformat(ts) for ts in data.get("overrides", [])]
    auto_off_raw = data.get("auto_off_started_at")
    return State(
        samples=samples,
        baseline=data.get("baseline"),
        overrides=overrides,
        auto_off_started_at=(
            datetime.fromisoformat(auto_off_raw) if auto_off_raw else None
        ),
        initialized=data.get("initialized", False),
    )


@dataclass
class Inputs:
    """Inputs for a single evaluation."""

    current_time: datetime
    event_type: EventType
    sensor_value: float | None = None
    switch_state: str = "off"
    switch_name: str = ""


@dataclass
class Result:
    """Result of a single evaluation.

    notification is non-empty when a notification should be sent.
    """

    action: Action = Action.NONE
    reason: str = ""
    notification: str = ""


def parse_float(value: str | None) -> float | None:
    """Parse a string to float, handling HA special values."""
    if value is None:
        return None
    if value in ("", "unknown", "unavailable"):
        return None
    try:
        result = float(value)
        if math.isnan(result) or math.isinf(result):
            return None
        return result
    except (TypeError, ValueError):
        return None


def _round_up_to_minute(dt: datetime) -> datetime:
    """Round a datetime UP to the next minute boundary.

    auto_off_started_at is checked against time_pattern
    triggers that fire at minute boundaries.  Rounding up
    guarantees the actual auto-off delay is never shorter
    than the configured timeout (it may be up to ~1 minute
    longer).
    """
    if dt.second == 0 and dt.microsecond == 0:
        return dt
    return dt.replace(second=0, microsecond=0) + timedelta(
        minutes=1,
    )


# ── Notification messages ──
# Edit these templates to change user-facing text.
# {name} = switch friendly name.  Other placeholders
# are scenario-specific (documented inline).

# Switch turned on/off (shared across scenarios).
_MSG_SWITCH_ON = "Turned on {name}. {reason}"
_MSG_SWITCH_OFF = "Turned off {name}. {reason}"

# Sensor spike detected, turning switch on.
# {max_val}, {min_val}, {threshold}: sensor values.
_MSG_SPIKE_REASON = (
    "Sensor spike: {max_val} (max) > {min_val} (min) + {threshold} (threshold)"
)

# Sensor returned to normal, turning switch off.
# {max_val}, {baseline}, {release}: sensor values.
_MSG_RELEASE_REASON = (
    "Sensor release: {max_val} (max)"
    " <= {baseline} (baseline)"
    " + {release} (release)"
)

# Manual switch-off while sensor is overriding.
_MSG_OVERRIDE_ENABLE_REASON = "Manual off while sensor override active"

# Double switch-off disables sensor override.
_MSG_OVERRIDE_DISABLED = "Sensor override disabled for {name}"

# Auto-off timer expired.  {mins}: configured minutes.
_MSG_AUTO_OFF_REASON = "Auto-off after {mins} minute(s)"


# ── Module-level logic functions (pyscript-compatible) ──


def _ctrl_evaluate(
    config: Config,
    state: State,
    inputs: Inputs,
) -> Result:
    """Dispatch to the correct handler."""
    if inputs.event_type == EventType.SENSOR:
        return _ctrl_handle_sensor(config, state, inputs)
    elif inputs.event_type == EventType.SWITCH:
        return _ctrl_handle_switch(config, state, inputs)
    elif inputs.event_type == EventType.TIMER:
        return _ctrl_handle_timer(config, state, inputs)
    return Result()


def _ctrl_handle_sensor(
    config: Config,
    state: State,
    inputs: Inputs,
) -> Result:
    """Handle sensor value change event."""
    if inputs.sensor_value is None:
        return Result()

    value = inputs.sensor_value
    now = inputs.current_time
    window = timedelta(
        seconds=config.sampling_window_seconds,
    )

    # Prune old samples and add new one
    state.samples = [s for s in state.samples if now - s.timestamp <= window]
    state.samples.append(Sample(value=value, timestamp=now))

    # Compute min/max over window
    values = [s.value for s in state.samples]
    min_val = min(values)
    max_val = max(values)

    if state.baseline is None:
        return _ctrl_check_spike(
            config,
            state,
            inputs,
            min_val,
            max_val,
        )

    return _ctrl_check_release(
        config,
        state,
        inputs,
        max_val,
    )


def _ctrl_check_spike(
    config: Config,
    state: State,
    inputs: Inputs,
    min_val: float,
    max_val: float,
) -> Result:
    """Check if sensor values spiked above threshold."""
    threshold = config.trigger_threshold
    if max_val <= min_val + threshold:
        return Result()

    # Spike detected: set baseline
    state.baseline = min_val
    state.overrides = []
    state.auto_off_started_at = None

    if inputs.switch_state == "on":
        # Already on (e.g., manual), sensor takes over
        return Result()

    name = inputs.switch_name
    reason = _MSG_SPIKE_REASON.format(
        max_val=max_val,
        min_val=min_val,
        threshold=threshold,
    )
    return Result(
        action=Action.TURN_ON,
        reason=reason,
        notification=_MSG_SWITCH_ON.format(
            name=name,
            reason=reason,
        ),
    )


def _ctrl_check_release(
    config: Config,
    state: State,
    inputs: Inputs,
    max_val: float,
) -> Result:
    """Check if sensor values dropped below release."""
    assert state.baseline is not None
    release = config.release_threshold

    if max_val > state.baseline + release:
        return Result()

    # Release detected: clear baseline
    old_baseline = state.baseline
    state.baseline = None
    state.overrides = []
    state.auto_off_started_at = None

    if inputs.switch_state == "off":
        # Already off
        return Result()

    name = inputs.switch_name
    reason = _MSG_RELEASE_REASON.format(
        max_val=max_val,
        baseline=old_baseline,
        release=release,
    )
    return Result(
        action=Action.TURN_OFF,
        reason=reason,
        notification=_MSG_SWITCH_OFF.format(
            name=name,
            reason=reason,
        ),
    )


def _ctrl_handle_switch(
    config: Config,
    state: State,
    inputs: Inputs,
) -> Result:
    """Handle switch state change event."""
    switch_state = inputs.switch_state

    # Startup recovery
    if not state.initialized:
        state.initialized = True
        if (
            switch_state == "on"
            and state.baseline is None
            and config.auto_off_minutes > 0
        ):
            state.auto_off_started_at = _round_up_to_minute(
                inputs.current_time,
            )
        return Result()

    if switch_state == "on":
        if state.baseline is None:
            # Manual on: schedule auto-off
            if config.auto_off_minutes > 0:
                state.auto_off_started_at = _round_up_to_minute(
                    inputs.current_time,
                )
        else:
            # Sensor managing: cancel auto-off
            state.auto_off_started_at = None
        return Result()

    # switch_state == "off"
    if state.baseline is not None:
        return _ctrl_handle_manual_override(
            config,
            state,
            inputs,
        )

    # Off without baseline: cancel auto-off
    state.auto_off_started_at = None
    return Result()


def _ctrl_handle_manual_override(
    config: Config,
    state: State,
    inputs: Inputs,
) -> Result:
    """Handle manual switch-off while baseline active."""
    now = inputs.current_time
    name = inputs.switch_name
    window_s = config.disable_window_seconds

    # Filter overrides by disable window
    if window_s > 0:
        window = timedelta(seconds=window_s)
        state.overrides = [ts for ts in state.overrides if now - ts <= window]
    else:
        state.overrides = []

    state.overrides.append(now)

    should_disable = window_s > 0 and len(state.overrides) >= 2

    if should_disable:
        # Double-off: disable sensor override
        state.baseline = None
        state.overrides = []
        state.auto_off_started_at = None
        return Result(
            notification=_MSG_OVERRIDE_DISABLED.format(
                name=name,
            ),
        )

    # Re-enable switch
    reason = _MSG_OVERRIDE_ENABLE_REASON
    return Result(
        action=Action.TURN_ON,
        reason=reason,
        notification=_MSG_SWITCH_ON.format(
            name=name,
            reason=reason,
        ),
    )


def _ctrl_handle_timer(
    config: Config,
    state: State,
    inputs: Inputs,
) -> Result:
    """Handle periodic timer event for auto-off."""
    # Start auto-off if switch is on with no baseline and
    # no timer running (e.g., after HA restart with lost
    # state, or if the switch event was missed).
    if (
        state.auto_off_started_at is None
        and state.baseline is None
        and inputs.switch_state == "on"
        and config.auto_off_minutes > 0
    ):
        state.auto_off_started_at = _round_up_to_minute(
            inputs.current_time,
        )

    if (
        state.auto_off_started_at is not None
        and state.baseline is None
        and inputs.switch_state == "on"
        and config.auto_off_minutes > 0
    ):
        elapsed = (
            inputs.current_time - state.auto_off_started_at
        ).total_seconds()
        timeout = config.auto_off_minutes * 60
        if elapsed >= timeout:
            state.auto_off_started_at = None
            name = inputs.switch_name
            mins = config.auto_off_minutes
            reason = _MSG_AUTO_OFF_REASON.format(
                mins=f"{mins:g}",
            )
            return Result(
                action=Action.TURN_OFF,
                reason=reason,
                notification=_MSG_SWITCH_OFF.format(
                    name=name,
                    reason=reason,
                ),
            )

    return Result()


# ── Controller class (delegates to module-level fns) ──


class Controller:
    """Wrapper class for test compatibility.

    All logic lives in the ``_ctrl_*`` module-level
    functions above so that pyscript's AST evaluator
    (which does not reliably bind ``self``) works
    correctly.
    """

    def __init__(self, config: Config) -> None:
        self.config = config

    def evaluate(
        self,
        state: State,
        inputs: Inputs,
    ) -> Result:
        """Evaluate current state and return action."""
        return _ctrl_evaluate(self.config, state, inputs)


def determine_event_type(
    trigger_entity: str,
    target_switch_entity: str,
) -> EventType:
    """Determine event type from trigger entity."""
    if trigger_entity == target_switch_entity:
        return EventType.SWITCH
    if trigger_entity in ("", "timer", "None", "none"):
        return EventType.TIMER
    return EventType.SENSOR


def evaluate(
    *,
    current_time: datetime,
    switch_name: str,
    state: State,
    target_switch_entity: str,
    sensor_value: str,
    switch_state: str,
    trigger_entity: str,
    trigger_threshold: float,
    release_threshold: float,
    sampling_window_s: int,
    disable_window_s: int,
    auto_off_min: int,
    notification_prefix: str,
    notification_suffix: str,
) -> Result:
    """Top-level evaluation entrypoint.

    Builds Config, determines event type, parses sensor
    value, evaluates via Controller, and formats the
    notification.  Returns a fully-formed Result.
    """
    config = Config(
        trigger_threshold=trigger_threshold,
        release_threshold=release_threshold,
        sampling_window_seconds=sampling_window_s,
        disable_window_seconds=disable_window_s,
        auto_off_minutes=auto_off_min,
    )

    event_type = determine_event_type(
        trigger_entity,
        target_switch_entity,
    )

    parsed_value: float | None = None
    if event_type == EventType.SENSOR:
        parsed_value = parse_float(sensor_value)

    inputs = Inputs(
        current_time=current_time,
        event_type=event_type,
        sensor_value=parsed_value,
        switch_state=switch_state,
        switch_name=switch_name,
    )

    result = _ctrl_evaluate(config, state, inputs)

    if result.notification:
        result.notification = format_notification(
            result.notification,
            notification_prefix,
            notification_suffix,
            current_time,
        )

    return result


@dataclass
class ServiceResult:
    """Outcome of handle_service_call for the bridge to act on."""

    state_dict: dict[str, Any]
    action: Action = Action.NONE
    reason: str = ""
    event_type: str = ""
    sensor_value: float | None = None
    notification: str = ""
    notification_service: str = ""


def handle_service_call(
    *,
    state_data: dict[str, Any] | None,
    switch_name: str,
    current_time: datetime,
    target_switch_entity: str,
    notification_service: str,
    **kwargs: Any,
) -> ServiceResult:
    """Pure bridge entry point called by ha_pyscript_automations.py.

    Accepts pre-loaded data (no HA dependencies) and
    returns a ``ServiceResult`` describing what the caller
    should do.  The caller (ha_pyscript_automations.py) handles all HA
    interactions: state persistence, service calls, and
    notifications.

    Remaining kwargs are passed through to evaluate().
    """
    # Parse state
    try:
        s = state_from_dict(state_data) if state_data else State()
    except Exception:
        s = State()

    # Determine event type and parse sensor value
    event_type = determine_event_type(
        kwargs.get("trigger_entity", "timer"),
        target_switch_entity,
    )
    parsed_sensor: float | None = None
    if event_type == EventType.SENSOR:
        parsed_sensor = parse_float(
            kwargs.get("sensor_value", ""),
        )

    # Evaluate (pure logic)
    result = evaluate(
        current_time=current_time,
        switch_name=switch_name,
        state=s,
        target_switch_entity=target_switch_entity,
        **kwargs,
    )

    # Normalise notification service name
    svc = ""
    if result.notification and notification_service:
        svc = notification_service
        if not svc.startswith("notify."):
            svc = f"notify.{svc}"

    return ServiceResult(
        state_dict=s.to_dict(),
        action=result.action,
        reason=result.reason,
        event_type=event_type.name,
        sensor_value=parsed_sensor,
        notification=result.notification or "",
        notification_service=svc,
    )
