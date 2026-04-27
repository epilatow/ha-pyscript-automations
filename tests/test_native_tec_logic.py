#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest", "pytest-cov", "ruff", "mypy"]
# ///
# This is AI generated code
"""Smoke + parity tests for the lifted TEC logic module.

The native port at
``custom_components/blueprint_toolkit/tec/logic.py`` is a
near-verbatim lift of the pyscript module at
``pyscript/modules/trigger_entity_controller.py`` -- only
``import helpers`` was changed to ``from . import helpers``
to fit a Python package. The pyscript copy is exhaustively
covered by ``tests/test_trigger_entity_controller.py``;
this file adds:

1. An import-surface smoke test that catches accidental
   API drift between the two copies.
2. A handful of evaluate-the-decision-tree scenarios
   confirming the lifted module produces the same
   result as the pyscript module would for the same
   ``Config`` + ``Inputs``.

If the two copies ever diverge in API or behaviour, this
file fails fast.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

import pytest  # noqa: E402
from conftest import CodeQualityBase  # noqa: E402

# Lifted module under test.
from custom_components.blueprint_toolkit.tec import (  # noqa: E402
    logic as native,
)

# Pyscript-side module (parity comparison target). Skipped
# if the path isn't importable (e.g., minimal CI envs).
sys.path.insert(0, str(REPO_ROOT / "pyscript" / "modules"))
try:
    import trigger_entity_controller as pyscript_tec  # noqa: E402
except ImportError:  # pragma: no cover
    pyscript_tec = None  # type: ignore[assignment]


T0 = datetime(2024, 1, 15, 12, 0, 0)
LIGHT = "light.hallway"
MOTION = "binary_sensor.motion"


def _config(**overrides: object) -> native.Config:
    defaults: dict[str, object] = {
        "controlled_entities": [LIGHT],
        "auto_off_minutes": 0,
        "auto_off_disabling_entities": [],
        "trigger_entities": [MOTION],
        "trigger_period": native.Period.ALWAYS,
        "trigger_forces_on": False,
        "trigger_disabling_entities": [],
        "trigger_disabling_period": native.Period.ALWAYS,
        "notification_prefix": "TEC: ",
        "notification_suffix": "",
        "notification_events": [],
    }
    defaults.update(overrides)
    return native.Config(**defaults)  # type: ignore[arg-type]


def _inputs(
    *,
    event_type: native.EventType,
    changed_entity: str = MOTION,
    triggers_on: bool = False,
    controlled_on: bool = False,
    is_day_time: bool = True,
    triggers_disabled: bool = False,
    auto_off_disabled: bool = False,
    auto_off_at: datetime | None = None,
    current_time: datetime = T0,
) -> native.Inputs:
    return native.Inputs(
        current_time=current_time,
        event_type=event_type,
        changed_entity=changed_entity,
        triggers_on=triggers_on,
        controlled_on=controlled_on,
        is_day_time=is_day_time,
        triggers_disabled=triggers_disabled,
        auto_off_disabled=auto_off_disabled,
        auto_off_at=auto_off_at,
    )


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

    def test_native_exports_expected(self) -> None:
        for name in self.EXPECTED:
            assert hasattr(native, name), (
                f"native logic module missing symbol: {name}"
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


class TestNativeDecisionTreeSmoke:
    """A few representative scenarios exercising the lifted module."""

    def test_trigger_on_turns_on(self) -> None:
        config = _config(auto_off_minutes=5)
        inputs = _inputs(
            event_type=native.EventType.TRIGGER_ON,
            controlled_on=False,
        )
        result = native.evaluate(config, inputs)
        assert result.action == native.ActionType.TURN_ON
        assert result.target_entities == [LIGHT]
        assert result.auto_off_at is None

    def test_trigger_off_arms_auto_off(self) -> None:
        config = _config(auto_off_minutes=5)
        inputs = _inputs(
            event_type=native.EventType.TRIGGER_OFF,
            controlled_on=True,
            triggers_on=False,
        )
        result = native.evaluate(config, inputs)
        assert result.action == native.ActionType.NONE
        assert result.auto_off_at == T0 + timedelta(minutes=5)

    def test_timer_expires_turns_off(self) -> None:
        config = _config(auto_off_minutes=5)
        inputs = _inputs(
            event_type=native.EventType.TIMER,
            controlled_on=True,
            triggers_on=False,
            auto_off_at=T0 - timedelta(minutes=1),  # already past
        )
        result = native.evaluate(config, inputs)
        assert result.action == native.ActionType.TURN_OFF
        assert result.auto_off_at is None

    def test_timer_catch_up_arms_when_controlled_on(self) -> None:
        config = _config(auto_off_minutes=5)
        inputs = _inputs(
            event_type=native.EventType.TIMER,
            controlled_on=True,
            triggers_on=False,
            auto_off_at=None,  # catch-up scenario
        )
        result = native.evaluate(config, inputs)
        assert result.action == native.ActionType.NONE
        assert result.auto_off_at == T0 + timedelta(minutes=5)


class TestParityWithPyscript:
    """Native + pyscript modules return identical Result for the same inputs."""

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
        # Build matching Config / Inputs in both modules.
        # The dataclasses are structurally identical so we
        # can use the native fixtures and re-instantiate
        # against the pyscript module via attribute lookup.
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

        native_result = _build(native)
        pyscript_result = _build(pyscript_tec)

        # Compare the load-bearing fields. The dataclasses
        # are not comparable across module identities, so
        # we walk the attributes manually.
        assert native_result.action.name == pyscript_result.action.name
        assert native_result.target_entities == pyscript_result.target_entities
        assert native_result.auto_off_at == pyscript_result.auto_off_at


class TestCodeQuality(CodeQualityBase):
    ruff_targets = [
        "tests/test_native_tec_logic.py",
        "custom_components/blueprint_toolkit/tec/logic.py",
        "custom_components/blueprint_toolkit/tec/__init__.py",
        "custom_components/blueprint_toolkit/helpers.py",
    ]
    mypy_targets: list[str] = [
        "custom_components/blueprint_toolkit/tec/logic.py",
        "custom_components/blueprint_toolkit/helpers.py",
    ]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", *sys.argv[1:]]))
