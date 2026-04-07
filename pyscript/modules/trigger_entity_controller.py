# This is AI generated code
"""Business logic for trigger entity controller.

No PyScript runtime dependencies.

Controls entities with optional trigger-based activation
and auto-off timer.  Supports time-of-day restrictions,
disabling entities, and force-on behavior.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum, auto

from notification_helpers import format_notification


class EventType(Enum):
    """Type of event that triggered evaluation."""

    TRIGGER_ON = auto()
    TRIGGER_OFF = auto()
    CONTROLLED_ON = auto()
    CONTROLLED_OFF = auto()
    DISABLING_CHANGED = auto()
    TIMER = auto()


class ActionType(Enum):
    """Actions the automation can take."""

    NONE = auto()
    TURN_ON = auto()
    TURN_OFF = auto()


class Period(Enum):
    """Time period for trigger/disabling evaluation."""

    ALWAYS = "always"
    NIGHT_TIME = "night-time"
    DAY_TIME = "day-time"


class NotificationEvent(Enum):
    """Events that can generate notifications.

    These notifications are only sent when the automation
    changes the state of an external entity.  Internal
    state changes (e.g., timer updates) do not generate
    these notifications.
    """

    TRIGGERED_ON = "triggered-on"
    FORCED_ON = "forced-on"
    AUTO_OFF = "auto-off"


@dataclass
class Config:
    """Configuration parameters (set per-instance)."""

    controlled_entities: list[str]
    auto_off_minutes: int
    auto_off_disabling_entities: list[str]
    trigger_entities: list[str]
    trigger_period: Period
    trigger_forces_on: bool
    trigger_disabling_entities: list[str]
    trigger_disabling_period: Period
    notification_prefix: str
    notification_suffix: str
    notification_events: list[NotificationEvent]


@dataclass
class Inputs:
    """Inputs for a single evaluation."""

    current_time: datetime
    event_type: EventType
    changed_entity: str
    triggers_on: bool
    controlled_on: bool
    is_day_time: bool
    triggers_disabled: bool
    auto_off_disabled: bool
    auto_off_at: datetime | None
    friendly_names: dict[str, str] = field(
        default_factory=dict,
    )


@dataclass
class Result:
    """Result of a single evaluation."""

    action: ActionType = ActionType.NONE
    target_entities: list[str] = field(
        default_factory=list,
    )
    auto_off_at: datetime | None = None
    reason: str = ""
    notification: str = ""


def parse_period(value: str) -> Period:
    """Parse a period string from blueprint input."""
    normalized = value.strip().lower()
    for p in Period:
        if p.value == normalized:
            return p
    return Period.ALWAYS


def parse_notification_events(
    values: list[str],
) -> list[NotificationEvent]:
    """Parse notification event strings."""
    result: list[NotificationEvent] = []
    for v in values:
        normalized = str(v).strip().lower()
        for evt in NotificationEvent:
            if evt.value == normalized:
                result.append(evt)
                break
    return result


def determine_event_type(
    entity_id: str,
    to_state: str,
    trigger_entities: list[str],
    controlled_entities: list[str],
    disabling_entities: list[str],
) -> EventType | None:
    """Determine event type from trigger context.

    Returns None if the trigger cannot be classified
    (e.g., an entity transitioning to 'unavailable').
    disabling_entities: combined list of all disabling
      entity IDs (trigger + auto-off).
    """
    if entity_id in ("", "timer", "None", "none"):
        return EventType.TIMER
    if to_state not in ("on", "off"):
        return None
    if entity_id in trigger_entities:
        if to_state == "on":
            return EventType.TRIGGER_ON
        return EventType.TRIGGER_OFF
    if entity_id in controlled_entities:
        if to_state == "on":
            return EventType.CONTROLLED_ON
        return EventType.CONTROLLED_OFF
    if entity_id in disabling_entities:
        return EventType.DISABLING_CHANGED
    return None


def _period_suppressed(
    period: Period,
    is_day_time: bool,
) -> bool:
    """Check if a period gate suppresses activation."""
    if period == Period.NIGHT_TIME and is_day_time:
        return True
    if period == Period.DAY_TIME and not is_day_time:
        return True
    return False


def is_trigger_suppressed(
    config: Config,
    is_day_time: bool,
    triggers_disabled: bool,
) -> bool:
    """Determine if triggering is currently suppressed.

    Suppressed by period:
      - NIGHT_TIME: suppressed when daytime
      - DAY_TIME: suppressed when nighttime
      - ALWAYS: never suppressed by time

    Suppressed by disabling entities:
      - Any disabling entity is "on" AND current time
        matches the disabling_period
    """
    if _period_suppressed(
        config.trigger_period,
        is_day_time,
    ):
        return True
    if triggers_disabled and not _period_suppressed(
        config.trigger_disabling_period,
        is_day_time,
    ):
        return True
    return False


def _format_notification(
    config: Config,
    event: NotificationEvent,
    message: str,
    current_time: datetime,
) -> str:
    """Format notification if event is enabled.

    Empty notification_events means all events are
    enabled.  Returns empty string if event is filtered
    out.
    """
    if config.notification_events and event not in config.notification_events:
        return ""
    return format_notification(
        message,
        config.notification_prefix,
        config.notification_suffix,
        current_time,
    )


def _friendly(
    entity_id: str,
    names: dict[str, str],
) -> str:
    """Look up friendly name, fallback to entity_id."""
    return names.get(entity_id, entity_id)


def _friendly_list(
    entity_ids: list[str],
    names: dict[str, str],
) -> str:
    """Comma-separated friendly names."""
    return ", ".join([_friendly(eid, names) for eid in entity_ids])


def _compute_auto_off_at(
    config: Config,
    current_time: datetime,
) -> datetime:
    """Compute auto_off_at from current time.

    Callers must verify config.auto_off_minutes > 0.
    """
    assert config.auto_off_minutes > 0
    return current_time + timedelta(
        minutes=config.auto_off_minutes,
    )


def _handle_trigger_on(
    config: Config,
    inputs: Inputs,
    suppressed: bool,
) -> Result:
    """Handle a trigger entity turning on."""
    if suppressed:
        return Result(
            auto_off_at=inputs.auto_off_at,
            reason="trigger suppressed",
        )
    if inputs.controlled_on:
        return Result(
            auto_off_at=None,
            reason="trigger activated, already on",
        )
    names = _friendly_list(
        config.controlled_entities,
        inputs.friendly_names,
    )
    return Result(
        action=ActionType.TURN_ON,
        target_entities=list(config.controlled_entities),
        auto_off_at=None,
        reason="trigger activated",
        notification=_format_notification(
            config,
            NotificationEvent.TRIGGERED_ON,
            f"Triggered on {names}",
            inputs.current_time,
        ),
    )


def _handle_trigger_off(
    config: Config,
    inputs: Inputs,
) -> Result:
    """Handle a trigger entity turning off."""
    if inputs.triggers_on:
        return Result(
            auto_off_at=inputs.auto_off_at,
            reason="other triggers still active",
        )
    if (
        config.auto_off_minutes > 0
        and inputs.controlled_on
        and not inputs.auto_off_disabled
    ):
        return Result(
            auto_off_at=_compute_auto_off_at(
                config,
                inputs.current_time,
            ),
            reason="all triggers off, starting auto-off",
        )
    return Result(
        auto_off_at=None,
        reason="all triggers off, no auto-off needed",
    )


def _handle_controlled_on(
    config: Config,
    inputs: Inputs,
) -> Result:
    """Handle a controlled entity turning on."""
    if (
        config.auto_off_minutes > 0
        and not inputs.triggers_on
        and not inputs.auto_off_disabled
    ):
        return Result(
            auto_off_at=_compute_auto_off_at(
                config,
                inputs.current_time,
            ),
            reason="controlled on, no trigger, starting auto-off",
        )
    if config.auto_off_minutes > 0 and inputs.triggers_on:
        return Result(
            auto_off_at=None,
            reason="controlled on, trigger active, deferring auto-off",
        )
    if config.auto_off_minutes > 0 and inputs.auto_off_disabled:
        return Result(
            auto_off_at=None,
            reason="controlled on, auto-off disabled",
        )
    return Result(
        auto_off_at=None,
        reason="controlled on, no auto-off configured",
    )


def _handle_controlled_off(
    config: Config,
    inputs: Inputs,
    suppressed: bool,
) -> Result:
    """Handle a controlled entity turning off."""
    if config.trigger_forces_on and inputs.triggers_on and not suppressed:
        return Result(
            action=ActionType.TURN_ON,
            target_entities=[inputs.changed_entity],
            auto_off_at=inputs.auto_off_at,
            reason="force on: trigger still active",
            notification=_format_notification(
                config,
                NotificationEvent.FORCED_ON,
                "Forced on "
                + _friendly(
                    inputs.changed_entity,
                    inputs.friendly_names,
                ),
                inputs.current_time,
            ),
        )
    if inputs.controlled_on:
        return Result(
            auto_off_at=inputs.auto_off_at,
            reason="other controlled entities still on",
        )
    return Result(
        auto_off_at=None,
        reason="all controlled entities off",
    )


def _handle_disabling_changed(
    config: Config,
    inputs: Inputs,
) -> Result:
    """Handle a disabling entity state change.

    Updates auto-off timer state.  Trigger disabling
    is evaluated lazily on the next trigger event, so
    no trigger-related action is needed here.
    """
    if inputs.auto_off_disabled:
        # Auto-off disabling active: clear any timer
        return Result(
            auto_off_at=None,
            reason="auto-off disabling active, clearing timer",
        )
    # Auto-off disabling cleared: start timer if needed
    if (
        inputs.auto_off_at is None
        and config.auto_off_minutes > 0
        and inputs.controlled_on
        and not inputs.triggers_on
    ):
        return Result(
            auto_off_at=_compute_auto_off_at(
                config,
                inputs.current_time,
            ),
            reason="auto-off disabling cleared, starting timer",
        )
    return Result(
        auto_off_at=inputs.auto_off_at,
        reason="disabling changed, no action needed",
    )


def _handle_timer(
    config: Config,
    inputs: Inputs,
) -> Result:
    """Handle a periodic timer tick."""
    if (
        inputs.auto_off_at is not None
        and inputs.current_time >= inputs.auto_off_at
        and not inputs.auto_off_disabled
    ):
        names = _friendly_list(
            config.controlled_entities,
            inputs.friendly_names,
        )
        return Result(
            action=ActionType.TURN_OFF,
            target_entities=list(config.controlled_entities),
            auto_off_at=None,
            reason="auto-off timer expired",
            notification=_format_notification(
                config,
                NotificationEvent.AUTO_OFF,
                f"Auto-off {names}",
                inputs.current_time,
            ),
        )
    # Catch-up: start timer if controlled entities are on
    # but no timer is set (e.g., after HA reboot, or after
    # auto-off disabling clears).
    if (
        inputs.auto_off_at is None
        and config.auto_off_minutes > 0
        and inputs.controlled_on
        and not inputs.triggers_on
        and not inputs.auto_off_disabled
    ):
        return Result(
            auto_off_at=_compute_auto_off_at(
                config,
                inputs.current_time,
            ),
            reason="catch-up: starting auto-off timer",
        )
    return Result(
        auto_off_at=inputs.auto_off_at,
        reason="timer tick, no action needed",
    )


def evaluate(config: Config, inputs: Inputs) -> Result:
    """Evaluate a single event and return the action.

    Main entry point for the logic module.

    The service wrapper calls this on each event:
    1. Validates entities exist (wrapper, before this)
    2. Determines event type (wrapper, before this)
    3. Evaluates suppression (period + disabling)
    4. Dispatches to the appropriate handler:
       - TRIGGER_ON: turn on controlled entities
         (unless suppressed)
       - TRIGGER_OFF: start auto-off countdown
         (if all triggers clear)
       - CONTROLLED_ON: start auto-off if no triggers
       - CONTROLLED_OFF: force-on if trigger active
       - DISABLING_CHANGED: clear or start auto-off
         timer based on disabling state
       - TIMER: fire auto-off if expired, or catch-up
         start timer if needed (e.g., after reboot)
    5. Wrapper sends notification if result has one
    6. Wrapper persists auto_off_at state

    Purely reactive: no sleeping, no waiting.
    """
    suppressed = is_trigger_suppressed(
        config,
        inputs.is_day_time,
        inputs.triggers_disabled,
    )

    if inputs.event_type == EventType.TRIGGER_ON:
        return _handle_trigger_on(
            config,
            inputs,
            suppressed,
        )
    if inputs.event_type == EventType.TRIGGER_OFF:
        return _handle_trigger_off(config, inputs)
    if inputs.event_type == EventType.CONTROLLED_ON:
        return _handle_controlled_on(config, inputs)
    if inputs.event_type == EventType.CONTROLLED_OFF:
        return _handle_controlled_off(
            config,
            inputs,
            suppressed,
        )
    if inputs.event_type == EventType.DISABLING_CHANGED:
        return _handle_disabling_changed(config, inputs)
    if inputs.event_type == EventType.TIMER:
        return _handle_timer(config, inputs)

    return Result(reason="unknown event type")
