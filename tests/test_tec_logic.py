#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest", "pytest-cov", "ruff", "mypy"]
# ///
# This is AI generated code
"""Tests for the TEC logic module.

Ported from ``tests/test_trigger_entity_controller.py``
(which targets the pyscript copy) so the two stay in
behaviour parity. The pyscript copy retires in a
follow-up commit; until then, both files run.

Also includes a parametric parity test that imports
the pyscript module (skipped if it isn't on the
import path) and asserts both modules produce
identical Result for the same Config + Inputs.
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

sys.path.insert(0, str(REPO_ROOT))

import pytest  # noqa: E402
from conftest import CodeQualityBase  # noqa: E402

from custom_components.blueprint_toolkit.tec.logic import (  # noqa: E402
    ActionType,
    Config,
    EventType,
    Inputs,
    NotificationEvent,
    Period,
    determine_event_type,
    evaluate,
    is_trigger_suppressed,
    parse_notification_events,
    parse_period,
)

# Pyscript copy is the parity-comparison target;
# importable in the dev tree but allowed to be missing
# in minimal CI envs.
sys.path.insert(0, str(REPO_ROOT / "pyscript" / "modules"))
try:
    import trigger_entity_controller as pyscript_tec  # noqa: E402
except ImportError:  # pragma: no cover
    pyscript_tec = None  # type: ignore[assignment]

T0 = datetime(2024, 1, 15, 12, 0, 0)

LIGHT = "light.hallway"
LIGHT2 = "light.hallway_2"
MOTION = "binary_sensor.motion"
MOTION2 = "binary_sensor.motion_2"
OCCUPIED = "input_boolean.bedroom_occupied"


def _config(**overrides: object) -> Config:
    defaults: dict[str, object] = {
        "controlled_entities": [LIGHT],
        "auto_off_minutes": 0,
        "auto_off_disabling_entities": [],
        "trigger_entities": [MOTION],
        "trigger_period": Period.ALWAYS,
        "trigger_forces_on": False,
        "trigger_disabling_entities": [],
        "trigger_disabling_period": Period.ALWAYS,
        "notification_prefix": "",
        "notification_suffix": "",
        "notification_events": [],
    }
    defaults.update(overrides)
    return Config(**defaults)  # type: ignore[arg-type]


def _inputs(
    event_type: EventType = EventType.TIMER,
    changed_entity: str = "timer",
    triggers_on: bool = False,
    controlled_on: bool = False,
    is_day_time: bool = False,
    triggers_disabled: bool = False,
    auto_off_disabled: bool = False,
    auto_off_at: datetime | None = None,
    current_time: datetime = T0,
    friendly_names: dict[str, str] | None = None,
) -> Inputs:
    return Inputs(
        current_time=current_time,
        event_type=event_type,
        changed_entity=changed_entity,
        triggers_on=triggers_on,
        controlled_on=controlled_on,
        is_day_time=is_day_time,
        triggers_disabled=triggers_disabled,
        auto_off_disabled=auto_off_disabled,
        auto_off_at=auto_off_at,
        friendly_names=friendly_names or {},
    )


# -- parse_period --


class TestParsePeriod:
    def test_always(self) -> None:
        assert parse_period("always") == Period.ALWAYS

    def test_night_time(self) -> None:
        assert parse_period("night-time") == Period.NIGHT_TIME

    def test_day_time(self) -> None:
        assert parse_period("day-time") == Period.DAY_TIME

    def test_case_insensitive(self) -> None:
        assert parse_period("NIGHT-TIME") == Period.NIGHT_TIME
        assert parse_period("Day-Time") == Period.DAY_TIME

    def test_whitespace(self) -> None:
        assert parse_period("  always  ") == Period.ALWAYS

    def test_unknown_defaults_to_always(self) -> None:
        assert parse_period("bogus") == Period.ALWAYS
        assert parse_period("") == Period.ALWAYS


# -- parse_notification_events --


class TestParseNotificationEvents:
    def test_empty_list(self) -> None:
        assert parse_notification_events([]) == []

    def test_single_event(self) -> None:
        result = parse_notification_events(["triggered-on"])
        assert result == [NotificationEvent.TRIGGERED_ON]

    def test_all_events(self) -> None:
        result = parse_notification_events(
            ["triggered-on", "forced-on", "auto-off"],
        )
        assert result == [
            NotificationEvent.TRIGGERED_ON,
            NotificationEvent.FORCED_ON,
            NotificationEvent.AUTO_OFF,
        ]

    def test_unknown_ignored(self) -> None:
        result = parse_notification_events(
            ["triggered-on", "bogus"],
        )
        assert result == [NotificationEvent.TRIGGERED_ON]


# -- determine_event_type --


class TestDetermineEventType:
    def test_trigger_on(self) -> None:
        result = determine_event_type(
            MOTION,
            "on",
            [MOTION],
            [LIGHT],
            [],
        )
        assert result == EventType.TRIGGER_ON

    def test_trigger_off(self) -> None:
        result = determine_event_type(
            MOTION,
            "off",
            [MOTION],
            [LIGHT],
            [],
        )
        assert result == EventType.TRIGGER_OFF

    def test_controlled_on(self) -> None:
        result = determine_event_type(
            LIGHT,
            "on",
            [MOTION],
            [LIGHT],
            [],
        )
        assert result == EventType.CONTROLLED_ON

    def test_controlled_off(self) -> None:
        result = determine_event_type(
            LIGHT,
            "off",
            [MOTION],
            [LIGHT],
            [],
        )
        assert result == EventType.CONTROLLED_OFF

    def test_timer_string(self) -> None:
        result = determine_event_type(
            "timer",
            "",
            [MOTION],
            [LIGHT],
            [],
        )
        assert result == EventType.TIMER

    def test_empty_string_is_timer(self) -> None:
        result = determine_event_type(
            "",
            "",
            [MOTION],
            [LIGHT],
            [],
        )
        assert result == EventType.TIMER

    def test_unknown_entity(self) -> None:
        result = determine_event_type(
            "sensor.temperature",
            "on",
            [MOTION],
            [LIGHT],
            [],
        )
        assert result is None

    def test_unavailable_state(self) -> None:
        result = determine_event_type(
            MOTION,
            "unavailable",
            [MOTION],
            [LIGHT],
            [],
        )
        assert result is None

    def test_unknown_state(self) -> None:
        result = determine_event_type(
            LIGHT,
            "unknown",
            [MOTION],
            [LIGHT],
            [],
        )
        assert result is None

    def test_disabling_entity_changed(self) -> None:
        result = determine_event_type(
            OCCUPIED,
            "on",
            [MOTION],
            [LIGHT],
            [OCCUPIED],
        )
        assert result == EventType.DISABLING_CHANGED

    def test_disabling_entity_off(self) -> None:
        result = determine_event_type(
            OCCUPIED,
            "off",
            [MOTION],
            [LIGHT],
            [OCCUPIED],
        )
        assert result == EventType.DISABLING_CHANGED


# -- is_trigger_suppressed --


class TestIsTriggerSuppressed:
    def test_always_never_suppressed_by_time(self) -> None:
        cfg = _config(trigger_period=Period.ALWAYS)
        assert not is_trigger_suppressed(cfg, True, False)
        assert not is_trigger_suppressed(cfg, False, False)

    def test_night_time_suppressed_during_day(self) -> None:
        cfg = _config(trigger_period=Period.NIGHT_TIME)
        assert is_trigger_suppressed(cfg, True, False)

    def test_night_time_not_suppressed_at_night(
        self,
    ) -> None:
        cfg = _config(trigger_period=Period.NIGHT_TIME)
        assert not is_trigger_suppressed(cfg, False, False)

    def test_day_time_suppressed_at_night(self) -> None:
        cfg = _config(trigger_period=Period.DAY_TIME)
        assert is_trigger_suppressed(cfg, False, False)

    def test_day_time_not_suppressed_during_day(
        self,
    ) -> None:
        cfg = _config(trigger_period=Period.DAY_TIME)
        assert not is_trigger_suppressed(cfg, True, False)

    def test_disabling_entity_on_always(self) -> None:
        cfg = _config(
            trigger_disabling_entities=[OCCUPIED],
            trigger_disabling_period=Period.ALWAYS,
        )
        assert is_trigger_suppressed(cfg, True, True)
        assert is_trigger_suppressed(cfg, False, True)

    def test_disabling_entity_off(self) -> None:
        cfg = _config(
            trigger_disabling_entities=[OCCUPIED],
            trigger_disabling_period=Period.ALWAYS,
        )
        assert not is_trigger_suppressed(cfg, True, False)

    def test_disabling_night_only_during_day(self) -> None:
        cfg = _config(
            trigger_disabling_entities=[OCCUPIED],
            trigger_disabling_period=Period.NIGHT_TIME,
        )
        # Disabling period is NIGHT_TIME but it's daytime
        # -> disabling is suppressed -> trigger NOT suppressed
        assert not is_trigger_suppressed(cfg, True, True)

    def test_disabling_night_only_at_night(self) -> None:
        cfg = _config(
            trigger_disabling_entities=[OCCUPIED],
            trigger_disabling_period=Period.NIGHT_TIME,
        )
        assert is_trigger_suppressed(cfg, False, True)


# -- _format_notification (via evaluate) --


class TestFormatNotification:
    def test_event_in_list_generates_notification(
        self,
    ) -> None:
        cfg = _config(
            notification_events=[
                NotificationEvent.TRIGGERED_ON,
            ],
            notification_prefix="PRE: ",
            notification_suffix="",
        )
        inp = _inputs(
            event_type=EventType.TRIGGER_ON,
            changed_entity=MOTION,
        )
        result = evaluate(cfg, inp)
        assert result.notification.startswith("PRE: ")
        assert "Triggered on" in result.notification

    def test_empty_list_enables_all_events(
        self,
    ) -> None:
        cfg = _config(
            notification_events=[],
            notification_prefix="PRE: ",
            notification_suffix="",
        )
        inp = _inputs(
            event_type=EventType.TRIGGER_ON,
            changed_entity=MOTION,
        )
        result = evaluate(cfg, inp)
        assert "Triggered on" in result.notification

    def test_event_not_in_list_no_notification(
        self,
    ) -> None:
        cfg = _config(
            notification_events=[
                NotificationEvent.AUTO_OFF,
            ],
        )
        inp = _inputs(
            event_type=EventType.TRIGGER_ON,
            changed_entity=MOTION,
        )
        result = evaluate(cfg, inp)
        assert result.notification == ""

    def test_uses_friendly_names(self) -> None:
        cfg = _config(
            notification_events=[],
            notification_prefix="",
            notification_suffix="",
        )
        inp = _inputs(
            event_type=EventType.TRIGGER_ON,
            changed_entity=MOTION,
            friendly_names={
                LIGHT: "Hallway Light",
            },
        )
        result = evaluate(cfg, inp)
        assert "Hallway Light" in result.notification
        assert LIGHT not in result.notification

    def test_falls_back_to_entity_id(self) -> None:
        cfg = _config(
            notification_events=[],
            notification_prefix="",
            notification_suffix="",
        )
        inp = _inputs(
            event_type=EventType.TRIGGER_ON,
            changed_entity=MOTION,
            friendly_names={},
        )
        result = evaluate(cfg, inp)
        assert LIGHT in result.notification

    def test_suffix_with_timestamp_tokens(self) -> None:
        dt = datetime(2024, 6, 15, 8, 30, 0)
        cfg = _config(
            notification_events=[
                NotificationEvent.TRIGGERED_ON,
            ],
            notification_prefix="",
            notification_suffix=" at HH:mm",
        )
        inp = _inputs(
            event_type=EventType.TRIGGER_ON,
            changed_entity=MOTION,
            current_time=dt,
        )
        result = evaluate(cfg, inp)
        assert result.notification.endswith(" at 08:30")


# -- TRIGGER_ON --


class TestTriggerOn:
    def test_turns_on_all_controlled(self) -> None:
        cfg = _config(
            controlled_entities=[LIGHT, LIGHT2],
        )
        inp = _inputs(
            event_type=EventType.TRIGGER_ON,
            changed_entity=MOTION,
        )
        result = evaluate(cfg, inp)
        assert result.action == ActionType.TURN_ON
        assert result.target_entities == [LIGHT, LIGHT2]

    def test_clears_auto_off_at(self) -> None:
        cfg = _config(auto_off_minutes=2)
        inp = _inputs(
            event_type=EventType.TRIGGER_ON,
            changed_entity=MOTION,
            auto_off_at=T0 + timedelta(minutes=5),
        )
        result = evaluate(cfg, inp)
        assert result.action == ActionType.TURN_ON
        assert result.auto_off_at is None

    def test_already_on_skips_action(self) -> None:
        cfg = _config()
        inp = _inputs(
            event_type=EventType.TRIGGER_ON,
            changed_entity=MOTION,
            controlled_on=True,
        )
        result = evaluate(cfg, inp)
        assert result.action == ActionType.NONE
        assert result.notification == ""

    def test_already_on_clears_auto_off(self) -> None:
        cfg = _config(auto_off_minutes=2)
        inp = _inputs(
            event_type=EventType.TRIGGER_ON,
            changed_entity=MOTION,
            controlled_on=True,
            auto_off_at=T0 + timedelta(minutes=5),
        )
        result = evaluate(cfg, inp)
        assert result.auto_off_at is None

    def test_suppressed_no_action(self) -> None:
        cfg = _config(
            trigger_period=Period.NIGHT_TIME,
        )
        inp = _inputs(
            event_type=EventType.TRIGGER_ON,
            changed_entity=MOTION,
            is_day_time=True,
        )
        result = evaluate(cfg, inp)
        assert result.action == ActionType.NONE

    def test_suppressed_preserves_auto_off_at(
        self,
    ) -> None:
        deadline = T0 + timedelta(minutes=5)
        cfg = _config(
            trigger_period=Period.NIGHT_TIME,
        )
        inp = _inputs(
            event_type=EventType.TRIGGER_ON,
            changed_entity=MOTION,
            is_day_time=True,
            auto_off_at=deadline,
        )
        result = evaluate(cfg, inp)
        assert result.auto_off_at == deadline

    def test_disabling_entity_suppresses(self) -> None:
        cfg = _config(
            trigger_disabling_entities=[OCCUPIED],
            trigger_disabling_period=Period.ALWAYS,
        )
        inp = _inputs(
            event_type=EventType.TRIGGER_ON,
            changed_entity=MOTION,
            triggers_disabled=True,
        )
        result = evaluate(cfg, inp)
        assert result.action == ActionType.NONE


# -- TRIGGER_OFF --


class TestTriggerOff:
    def test_other_triggers_active_no_action(self) -> None:
        cfg = _config(
            trigger_entities=[MOTION, MOTION2],
        )
        inp = _inputs(
            event_type=EventType.TRIGGER_OFF,
            changed_entity=MOTION,
            triggers_on=True,
            controlled_on=True,
        )
        result = evaluate(cfg, inp)
        assert result.action == ActionType.NONE

    def test_all_off_starts_auto_off(self) -> None:
        cfg = _config(auto_off_minutes=2)
        inp = _inputs(
            event_type=EventType.TRIGGER_OFF,
            changed_entity=MOTION,
            triggers_on=False,
            controlled_on=True,
        )
        result = evaluate(cfg, inp)
        assert result.auto_off_at == T0 + timedelta(
            minutes=2,
        )

    def test_all_off_no_auto_off_configured(self) -> None:
        cfg = _config(auto_off_minutes=0)
        inp = _inputs(
            event_type=EventType.TRIGGER_OFF,
            changed_entity=MOTION,
            triggers_on=False,
            controlled_on=True,
        )
        result = evaluate(cfg, inp)
        assert result.auto_off_at is None

    def test_all_off_controlled_already_off(self) -> None:
        cfg = _config(auto_off_minutes=2)
        inp = _inputs(
            event_type=EventType.TRIGGER_OFF,
            changed_entity=MOTION,
            triggers_on=False,
            controlled_on=False,
        )
        result = evaluate(cfg, inp)
        assert result.auto_off_at is None


# -- CONTROLLED_ON --


class TestControlledOn:
    def test_auto_off_no_trigger(self) -> None:
        cfg = _config(auto_off_minutes=1)
        inp = _inputs(
            event_type=EventType.CONTROLLED_ON,
            changed_entity=LIGHT,
            triggers_on=False,
        )
        result = evaluate(cfg, inp)
        assert result.action == ActionType.NONE
        assert result.auto_off_at == T0 + timedelta(
            minutes=1,
        )

    def test_auto_off_trigger_active_defers(self) -> None:
        cfg = _config(auto_off_minutes=1)
        inp = _inputs(
            event_type=EventType.CONTROLLED_ON,
            changed_entity=LIGHT,
            triggers_on=True,
        )
        result = evaluate(cfg, inp)
        assert result.action == ActionType.NONE
        assert result.auto_off_at is None

    def test_no_auto_off_configured(self) -> None:
        cfg = _config(auto_off_minutes=0)
        inp = _inputs(
            event_type=EventType.CONTROLLED_ON,
            changed_entity=LIGHT,
        )
        result = evaluate(cfg, inp)
        assert result.action == ActionType.NONE
        assert result.auto_off_at is None


# -- CONTROLLED_OFF --


class TestControlledOff:
    def test_force_on_trigger_active(self) -> None:
        cfg = _config(trigger_forces_on=True)
        inp = _inputs(
            event_type=EventType.CONTROLLED_OFF,
            changed_entity=LIGHT,
            triggers_on=True,
        )
        result = evaluate(cfg, inp)
        assert result.action == ActionType.TURN_ON
        assert result.target_entities == [LIGHT]

    def test_force_on_suppressed_no_action(self) -> None:
        cfg = _config(
            trigger_forces_on=True,
            trigger_period=Period.NIGHT_TIME,
        )
        inp = _inputs(
            event_type=EventType.CONTROLLED_OFF,
            changed_entity=LIGHT,
            triggers_on=True,
            is_day_time=True,
        )
        result = evaluate(cfg, inp)
        assert result.action == ActionType.NONE

    def test_force_on_suppressed_by_disabling(
        self,
    ) -> None:
        cfg = _config(
            trigger_forces_on=True,
            trigger_disabling_entities=[OCCUPIED],
        )
        inp = _inputs(
            event_type=EventType.CONTROLLED_OFF,
            changed_entity=LIGHT,
            triggers_on=True,
            triggers_disabled=True,
        )
        result = evaluate(cfg, inp)
        assert result.action == ActionType.NONE

    def test_force_on_no_trigger(self) -> None:
        cfg = _config(trigger_forces_on=True)
        inp = _inputs(
            event_type=EventType.CONTROLLED_OFF,
            changed_entity=LIGHT,
            triggers_on=False,
        )
        result = evaluate(cfg, inp)
        assert result.action == ActionType.NONE

    def test_force_on_disabled(self) -> None:
        cfg = _config(trigger_forces_on=False)
        inp = _inputs(
            event_type=EventType.CONTROLLED_OFF,
            changed_entity=LIGHT,
            triggers_on=True,
        )
        result = evaluate(cfg, inp)
        assert result.action == ActionType.NONE

    def test_others_still_on_preserves_timer(
        self,
    ) -> None:
        deadline = T0 + timedelta(minutes=5)
        cfg = _config()
        inp = _inputs(
            event_type=EventType.CONTROLLED_OFF,
            changed_entity=LIGHT,
            controlled_on=True,
            auto_off_at=deadline,
        )
        result = evaluate(cfg, inp)
        assert result.auto_off_at == deadline

    def test_all_off_clears_timer(self) -> None:
        cfg = _config()
        inp = _inputs(
            event_type=EventType.CONTROLLED_OFF,
            changed_entity=LIGHT,
            controlled_on=False,
            auto_off_at=T0 + timedelta(minutes=5),
        )
        result = evaluate(cfg, inp)
        assert result.auto_off_at is None

    def test_force_on_preserves_timer(self) -> None:
        deadline = T0 + timedelta(minutes=5)
        cfg = _config(trigger_forces_on=True)
        inp = _inputs(
            event_type=EventType.CONTROLLED_OFF,
            changed_entity=LIGHT,
            triggers_on=True,
            auto_off_at=deadline,
        )
        result = evaluate(cfg, inp)
        assert result.auto_off_at == deadline

    def test_force_on_notification(self) -> None:
        cfg = _config(
            trigger_forces_on=True,
            notification_events=[
                NotificationEvent.FORCED_ON,
            ],
            notification_prefix="TEC: ",
            notification_suffix="",
        )
        inp = _inputs(
            event_type=EventType.CONTROLLED_OFF,
            changed_entity=LIGHT,
            triggers_on=True,
            friendly_names={LIGHT: "Hallway Light"},
        )
        result = evaluate(cfg, inp)
        assert "Forced on" in result.notification
        assert "Hallway Light" in result.notification


# -- TIMER --


class TestTimer:
    def test_expired_turns_off_all(self) -> None:
        cfg = _config(
            controlled_entities=[LIGHT, LIGHT2],
        )
        inp = _inputs(
            auto_off_at=T0 - timedelta(seconds=1),
        )
        result = evaluate(cfg, inp)
        assert result.action == ActionType.TURN_OFF
        assert result.target_entities == [LIGHT, LIGHT2]
        assert result.auto_off_at is None

    def test_not_expired_no_action(self) -> None:
        deadline = T0 + timedelta(minutes=5)
        cfg = _config()
        inp = _inputs(auto_off_at=deadline)
        result = evaluate(cfg, inp)
        assert result.action == ActionType.NONE
        assert result.auto_off_at == deadline

    def test_no_timer_no_action(self) -> None:
        cfg = _config()
        inp = _inputs(auto_off_at=None)
        result = evaluate(cfg, inp)
        assert result.action == ActionType.NONE

    def test_exact_boundary_triggers(self) -> None:
        cfg = _config()
        inp = _inputs(auto_off_at=T0, current_time=T0)
        result = evaluate(cfg, inp)
        assert result.action == ActionType.TURN_OFF

    def test_auto_off_notification(self) -> None:
        cfg = _config(
            notification_events=[
                NotificationEvent.AUTO_OFF,
            ],
            notification_prefix="",
            notification_suffix="",
        )
        inp = _inputs(
            auto_off_at=T0 - timedelta(seconds=1),
            friendly_names={LIGHT: "Hallway Light"},
        )
        result = evaluate(cfg, inp)
        assert "Auto-off" in result.notification
        assert "Hallway Light" in result.notification

    def test_catch_up_starts_timer(self) -> None:
        """Timer tick with controlled on + no timer
        starts the auto-off timer (e.g., after reboot).
        """
        cfg = _config(auto_off_minutes=2)
        inp = _inputs(
            auto_off_at=None,
            controlled_on=True,
            triggers_on=False,
        )
        result = evaluate(cfg, inp)
        assert result.action == ActionType.NONE
        assert result.auto_off_at == T0 + timedelta(
            minutes=2,
        )

    def test_catch_up_skipped_when_trigger_active(
        self,
    ) -> None:
        cfg = _config(auto_off_minutes=2)
        inp = _inputs(
            auto_off_at=None,
            controlled_on=True,
            triggers_on=True,
        )
        result = evaluate(cfg, inp)
        assert result.auto_off_at is None

    def test_catch_up_skipped_when_auto_off_disabled(
        self,
    ) -> None:
        cfg = _config(auto_off_minutes=2)
        inp = _inputs(
            auto_off_at=None,
            controlled_on=True,
            auto_off_disabled=True,
        )
        result = evaluate(cfg, inp)
        assert result.auto_off_at is None

    def test_catch_up_skipped_when_no_auto_off(
        self,
    ) -> None:
        cfg = _config(auto_off_minutes=0)
        inp = _inputs(
            auto_off_at=None,
            controlled_on=True,
        )
        result = evaluate(cfg, inp)
        assert result.auto_off_at is None

    def test_catch_up_skipped_when_controlled_off(
        self,
    ) -> None:
        cfg = _config(auto_off_minutes=2)
        inp = _inputs(
            auto_off_at=None,
            controlled_on=False,
        )
        result = evaluate(cfg, inp)
        assert result.auto_off_at is None


# -- AUTO-OFF DISABLING --


class TestAutoOffDisabling:
    def test_trigger_off_no_timer_when_disabled(
        self,
    ) -> None:
        cfg = _config(auto_off_minutes=2)
        inp = _inputs(
            event_type=EventType.TRIGGER_OFF,
            changed_entity=MOTION,
            triggers_on=False,
            controlled_on=True,
            auto_off_disabled=True,
        )
        result = evaluate(cfg, inp)
        assert result.auto_off_at is None

    def test_controlled_on_no_timer_when_disabled(
        self,
    ) -> None:
        cfg = _config(auto_off_minutes=2)
        inp = _inputs(
            event_type=EventType.CONTROLLED_ON,
            changed_entity=LIGHT,
            triggers_on=False,
            auto_off_disabled=True,
        )
        result = evaluate(cfg, inp)
        assert result.action == ActionType.NONE
        assert result.auto_off_at is None

    def test_timer_suppressed_when_disabled(
        self,
    ) -> None:
        deadline = T0 - timedelta(seconds=1)
        cfg = _config()
        inp = _inputs(
            auto_off_at=deadline,
            auto_off_disabled=True,
        )
        result = evaluate(cfg, inp)
        assert result.action == ActionType.NONE
        assert result.auto_off_at == deadline

    def test_timer_fires_when_not_disabled(
        self,
    ) -> None:
        cfg = _config()
        inp = _inputs(
            auto_off_at=T0 - timedelta(seconds=1),
            auto_off_disabled=False,
        )
        result = evaluate(cfg, inp)
        assert result.action == ActionType.TURN_OFF

    def test_disabling_on_clears_timer(self) -> None:
        deadline = T0 + timedelta(minutes=5)
        cfg = _config(
            auto_off_disabling_entities=[OCCUPIED],
        )
        inp = _inputs(
            event_type=EventType.DISABLING_CHANGED,
            changed_entity=OCCUPIED,
            auto_off_disabled=True,
            auto_off_at=deadline,
        )
        result = evaluate(cfg, inp)
        assert result.auto_off_at is None

    def test_disabling_off_starts_timer(self) -> None:
        cfg = _config(
            auto_off_disabling_entities=[OCCUPIED],
            auto_off_minutes=2,
        )
        inp = _inputs(
            event_type=EventType.DISABLING_CHANGED,
            changed_entity=OCCUPIED,
            auto_off_disabled=False,
            controlled_on=True,
            auto_off_at=None,
        )
        result = evaluate(cfg, inp)
        assert result.auto_off_at == T0 + timedelta(
            minutes=2,
        )

    def test_disabling_off_no_timer_when_trigger_active(
        self,
    ) -> None:
        cfg = _config(
            auto_off_disabling_entities=[OCCUPIED],
            auto_off_minutes=2,
        )
        inp = _inputs(
            event_type=EventType.DISABLING_CHANGED,
            changed_entity=OCCUPIED,
            auto_off_disabled=False,
            controlled_on=True,
            triggers_on=True,
            auto_off_at=None,
        )
        result = evaluate(cfg, inp)
        assert result.auto_off_at is None

    def test_overlapping_disabling_entity(self) -> None:
        """Same entity in both disabling lists. A single
        event is classified as DISABLING_CHANGED and
        both booleans are set.
        """
        cfg = _config(
            trigger_disabling_entities=[OCCUPIED],
            auto_off_disabling_entities=[OCCUPIED],
            auto_off_minutes=2,
        )
        # Entity turns on: timer should be cleared
        inp = _inputs(
            event_type=EventType.DISABLING_CHANGED,
            changed_entity=OCCUPIED,
            triggers_disabled=True,
            auto_off_disabled=True,
            auto_off_at=T0 + timedelta(minutes=5),
        )
        result = evaluate(cfg, inp)
        assert result.auto_off_at is None

    def test_non_overlapping_disabling_entities(
        self,
    ) -> None:
        """Different entities for trigger vs auto-off
        disabling. Auto-off disabling turns on but
        trigger disabling is off.
        """
        cfg = _config(
            trigger_disabling_entities=[OCCUPIED],
            auto_off_disabling_entities=[
                "input_boolean.garage_occupied",
            ],
            auto_off_minutes=2,
        )
        # Only auto-off disabling active
        inp = _inputs(
            event_type=EventType.DISABLING_CHANGED,
            changed_entity="input_boolean.garage_occupied",
            triggers_disabled=False,
            auto_off_disabled=True,
            auto_off_at=T0 + timedelta(minutes=5),
        )
        result = evaluate(cfg, inp)
        assert result.auto_off_at is None

    def test_trigger_only_disabling_is_noop(
        self,
    ) -> None:
        """Entity only in trigger_disabling_entities.
        DISABLING_CHANGED fires but auto-off state is
        preserved (trigger suppression is evaluated
        lazily on next trigger event).
        """
        deadline = T0 + timedelta(minutes=5)
        cfg = _config(
            trigger_disabling_entities=[OCCUPIED],
            auto_off_minutes=2,
        )
        inp = _inputs(
            event_type=EventType.DISABLING_CHANGED,
            changed_entity=OCCUPIED,
            triggers_disabled=True,
            auto_off_disabled=False,
            auto_off_at=deadline,
        )
        result = evaluate(cfg, inp)
        assert result.auto_off_at == deadline


# -- End-to-end scenarios --


class TestEndToEndScenarios:
    def test_motion_on_off_auto_off_cycle(self) -> None:
        """Full cycle: motion on -> lights on ->
        motion off -> auto-off countdown -> lights off.
        """
        cfg = _config(auto_off_minutes=2)

        # Motion detected
        r1 = evaluate(
            cfg,
            _inputs(
                event_type=EventType.TRIGGER_ON,
                changed_entity=MOTION,
            ),
        )
        assert r1.action == ActionType.TURN_ON
        assert r1.auto_off_at is None

        # Light turns on (CONTROLLED_ON), trigger active
        r2 = evaluate(
            cfg,
            _inputs(
                event_type=EventType.CONTROLLED_ON,
                changed_entity=LIGHT,
                triggers_on=True,
                controlled_on=True,
            ),
        )
        assert r2.action == ActionType.NONE
        assert r2.auto_off_at is None  # deferred

        # Motion clears
        r3 = evaluate(
            cfg,
            _inputs(
                event_type=EventType.TRIGGER_OFF,
                changed_entity=MOTION,
                triggers_on=False,
                controlled_on=True,
            ),
        )
        assert r3.action == ActionType.NONE
        assert r3.auto_off_at == T0 + timedelta(
            minutes=2,
        )

        # Timer tick before expiry
        r4 = evaluate(
            cfg,
            _inputs(
                auto_off_at=r3.auto_off_at,
                current_time=T0 + timedelta(seconds=60),
            ),
        )
        assert r4.action == ActionType.NONE

        # Timer tick after expiry
        r5 = evaluate(
            cfg,
            _inputs(
                auto_off_at=r3.auto_off_at,
                current_time=T0 + timedelta(seconds=121),
            ),
        )
        assert r5.action == ActionType.TURN_OFF
        assert r5.auto_off_at is None

    def test_suppressed_motion_no_action(self) -> None:
        """Night-only trigger during daytime."""
        cfg = _config(
            trigger_period=Period.NIGHT_TIME,
        )
        result = evaluate(
            cfg,
            _inputs(
                event_type=EventType.TRIGGER_ON,
                changed_entity=MOTION,
                is_day_time=True,
            ),
        )
        assert result.action == ActionType.NONE

    def test_manual_on_auto_off_no_triggers(self) -> None:
        """Manual turn-on with auto-off, no triggers."""
        cfg = _config(
            trigger_entities=[],
            auto_off_minutes=5,
        )

        # Light manually turned on
        r1 = evaluate(
            cfg,
            _inputs(
                event_type=EventType.CONTROLLED_ON,
                changed_entity=LIGHT,
                triggers_on=False,
            ),
        )
        assert r1.action == ActionType.NONE
        assert r1.auto_off_at == T0 + timedelta(
            minutes=5,
        )

        # Timer expires
        r2 = evaluate(
            cfg,
            _inputs(
                auto_off_at=r1.auto_off_at,
                current_time=T0 + timedelta(minutes=5, seconds=1),
            ),
        )
        assert r2.action == ActionType.TURN_OFF

    def test_force_on_while_trigger_active(self) -> None:
        """Force-on: light turned off while motion active."""
        cfg = _config(trigger_forces_on=True)

        result = evaluate(
            cfg,
            _inputs(
                event_type=EventType.CONTROLLED_OFF,
                changed_entity=LIGHT,
                triggers_on=True,
            ),
        )
        assert result.action == ActionType.TURN_ON
        assert result.target_entities == [LIGHT]

    def test_hallway_bedroom_scenario(self) -> None:
        """Hallway light: always on with motion, except
        at night when bedroom is occupied.
        """
        cfg = _config(
            trigger_disabling_entities=[OCCUPIED],
            trigger_disabling_period=Period.NIGHT_TIME,
            auto_off_minutes=2,
        )

        # Daytime, bedroom occupied -> still turns on
        r1 = evaluate(
            cfg,
            _inputs(
                event_type=EventType.TRIGGER_ON,
                changed_entity=MOTION,
                is_day_time=True,
                triggers_disabled=True,
            ),
        )
        assert r1.action == ActionType.TURN_ON

        # Nighttime, bedroom NOT occupied -> turns on
        r2 = evaluate(
            cfg,
            _inputs(
                event_type=EventType.TRIGGER_ON,
                changed_entity=MOTION,
                is_day_time=False,
                triggers_disabled=False,
            ),
        )
        assert r2.action == ActionType.TURN_ON

        # Nighttime, bedroom occupied -> suppressed
        r3 = evaluate(
            cfg,
            _inputs(
                event_type=EventType.TRIGGER_ON,
                changed_entity=MOTION,
                is_day_time=False,
                triggers_disabled=True,
            ),
        )
        assert r3.action == ActionType.NONE

    def test_garage_scenario(self) -> None:
        """Garage: trigger + auto-off disabled when
        occupied. Full manual control.
        """
        cfg = _config(
            trigger_disabling_entities=[OCCUPIED],
            trigger_disabling_period=Period.ALWAYS,
            auto_off_disabling_entities=[OCCUPIED],
            auto_off_minutes=2,
        )

        # Occupied: trigger suppressed
        r1 = evaluate(
            cfg,
            _inputs(
                event_type=EventType.TRIGGER_ON,
                changed_entity=MOTION,
                triggers_disabled=True,
                auto_off_disabled=True,
            ),
        )
        assert r1.action == ActionType.NONE

        # Occupied: manual on, no auto-off timer
        r2 = evaluate(
            cfg,
            _inputs(
                event_type=EventType.CONTROLLED_ON,
                changed_entity=LIGHT,
                auto_off_disabled=True,
            ),
        )
        assert r2.action == ActionType.NONE
        assert r2.auto_off_at is None

        # Not occupied: trigger works
        r3 = evaluate(
            cfg,
            _inputs(
                event_type=EventType.TRIGGER_ON,
                changed_entity=MOTION,
                triggers_disabled=False,
                auto_off_disabled=False,
            ),
        )
        assert r3.action == ActionType.TURN_ON

    def test_reboot_catch_up(self) -> None:
        """After HA reboot, controlled entity is on but
        no timer exists. Timer tick starts the timer.
        """
        cfg = _config(auto_off_minutes=2)

        # Simulates post-reboot: light on, no timer
        r1 = evaluate(
            cfg,
            _inputs(
                auto_off_at=None,
                controlled_on=True,
            ),
        )
        assert r1.action == ActionType.NONE
        assert r1.auto_off_at == T0 + timedelta(
            minutes=2,
        )

        # Next tick: timer expires
        r2 = evaluate(
            cfg,
            _inputs(
                auto_off_at=r1.auto_off_at,
                controlled_on=True,
                current_time=T0 + timedelta(minutes=3),
            ),
        )
        assert r2.action == ActionType.TURN_OFF

    def test_disabling_clears_then_catch_up(
        self,
    ) -> None:
        """Auto-off disabling active with light on.
        Disabling clears. Next timer tick starts timer.
        """
        cfg = _config(
            auto_off_disabling_entities=[OCCUPIED],
            auto_off_minutes=2,
        )

        # Disabling active: no timer
        r1 = evaluate(
            cfg,
            _inputs(
                auto_off_at=None,
                controlled_on=True,
                auto_off_disabled=True,
            ),
        )
        assert r1.auto_off_at is None

        # Disabling clears: catch-up starts timer
        r2 = evaluate(
            cfg,
            _inputs(
                auto_off_at=None,
                controlled_on=True,
                auto_off_disabled=False,
                current_time=T0 + timedelta(minutes=1),
            ),
        )
        assert r2.action == ActionType.NONE
        assert r2.auto_off_at is not None


# --------------------------------------------------------
# Parity with the pyscript copy
# --------------------------------------------------------


class TestImportSurfaceParity:
    """Both copies expose the same public symbols."""

    EXPECTED = (
        "ActionType",
        "Config",
        "EventType",
        "Inputs",
        "NotificationEvent",
        "Period",
        "Result",
        "determine_event_type",
        "evaluate",
        "is_trigger_suppressed",
        "parse_notification_events",
        "parse_period",
    )

    def test_tec_exports_expected(self) -> None:
        from custom_components.blueprint_toolkit.tec import logic as tec_mod

        for name in self.EXPECTED:
            assert hasattr(tec_mod, name), (
                f"tec logic module missing symbol: {name}"
            )

    @pytest.mark.skipif(
        pyscript_tec is None,
        reason="pyscript module not importable in this env",
    )
    def test_pyscript_exports_expected(self) -> None:
        for name in self.EXPECTED:
            assert hasattr(pyscript_tec, name), (
                f"pyscript logic module missing symbol: {name}"
            )


class TestParityWithPyscript:
    """TEC + pyscript modules return identical Result for the same inputs."""

    @pytest.mark.skipif(
        pyscript_tec is None,
        reason="pyscript module not importable in this env",
    )
    @pytest.mark.parametrize(
        "event_kind",
        [
            "trigger_on",
            "trigger_off",
            "controlled_on",
            "controlled_off",
            "timer_expired",
            "timer_catch_up",
        ],
    )
    def test_evaluate_parity(self, event_kind: str) -> None:
        from custom_components.blueprint_toolkit.tec import logic as tec_mod

        scenarios: dict[str, dict[str, object]] = {
            "trigger_on": {
                "event_type": "TRIGGER_ON",
                "kwargs": {"controlled_on": False},
            },
            "trigger_off": {
                "event_type": "TRIGGER_OFF",
                "kwargs": {"controlled_on": True, "triggers_on": False},
            },
            "controlled_on": {
                "event_type": "CONTROLLED_ON",
                "kwargs": {"controlled_on": True, "triggers_on": False},
            },
            "controlled_off": {
                "event_type": "CONTROLLED_OFF",
                "kwargs": {"controlled_on": False},
            },
            "timer_expired": {
                "event_type": "TIMER",
                "kwargs": {
                    "controlled_on": True,
                    "triggers_on": False,
                    "auto_off_at": T0 - timedelta(minutes=1),
                },
            },
            "timer_catch_up": {
                "event_type": "TIMER",
                "kwargs": {
                    "controlled_on": True,
                    "triggers_on": False,
                    "auto_off_at": None,
                },
            },
        }
        scenario = scenarios[event_kind]

        def _build(mod: object) -> object:
            cfg = mod.Config(  # type: ignore[attr-defined]
                controlled_entities=[LIGHT],
                auto_off_minutes=5,
                auto_off_disabling_entities=[],
                trigger_entities=[MOTION],
                trigger_period=mod.Period.ALWAYS,  # type: ignore[attr-defined]
                trigger_forces_on=False,
                trigger_disabling_entities=[],
                trigger_disabling_period=mod.Period.ALWAYS,  # type: ignore[attr-defined]
                notification_prefix="",
                notification_suffix="",
                notification_events=[],
            )
            ipt = mod.Inputs(  # type: ignore[attr-defined]
                current_time=T0,
                event_type=getattr(
                    mod.EventType,  # type: ignore[attr-defined]
                    str(scenario["event_type"]),
                ),
                changed_entity=MOTION,
                triggers_on=False,
                controlled_on=False,
                is_day_time=True,
                triggers_disabled=False,
                auto_off_disabled=False,
                auto_off_at=None,
            )
            for k, v in scenario["kwargs"].items():  # type: ignore[union-attr]
                setattr(ipt, k, v)
            return mod.evaluate(cfg, ipt)  # type: ignore[attr-defined]

        tec_result = _build(tec_mod)
        pyscript_result = _build(pyscript_tec)

        assert tec_result.action.name == pyscript_result.action.name
        assert tec_result.target_entities == pyscript_result.target_entities
        assert tec_result.auto_off_at == pyscript_result.auto_off_at


class TestCodeQuality(CodeQualityBase):
    ruff_targets = [
        "tests/test_tec_logic.py",
        "custom_components/blueprint_toolkit/tec/logic.py",
        "custom_components/blueprint_toolkit/tec/__init__.py",
        "custom_components/blueprint_toolkit/tec/handler.py",
        "custom_components/blueprint_toolkit/helpers.py",
    ]
    mypy_targets: list[str] = [
        "custom_components/blueprint_toolkit/tec/logic.py",
        "custom_components/blueprint_toolkit/tec/handler.py",
        "custom_components/blueprint_toolkit/helpers.py",
    ]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", *sys.argv[1:]]))
