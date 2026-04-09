#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest", "pytest-cov", "ruff", "mypy"]
# ///
# This is AI generated code
"""Tests for entity_defaults_watchdog logic module."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

_SCRIPT_PATH = (
    REPO_ROOT / "pyscript" / "modules" / "entity_defaults_watchdog.py"
)

sys.path.insert(0, str(_SCRIPT_PATH.parent))

from conftest import CodeQualityBase  # noqa: E402
from entity_defaults_watchdog import (  # noqa: E402
    Config,
    DeviceInfo,
    DriftDetail,
    EntityDriftInfo,
    _build_notification_message,
    _check_entity_drift,
    _check_id_enabled,
    _check_name_enabled,
    _compute_recommended_override,
    _evaluate_device,
    _is_excluded,
    evaluate_devices,
)
from helpers import DeviceEntry  # noqa: E402

# ── Helpers ─────────────────────────────────────────


def _config(**overrides: object) -> Config:
    defaults: dict[str, object] = {
        "drift_checks": [],
        "device_exclude_regex": "",
        "exclude_entity_ids": [],
        "entity_id_exclude_regex": "",
        "entity_name_exclude_regex": "",
    }
    defaults.update(overrides)
    return Config(**defaults)  # type: ignore[arg-type]


def _entity_drift(
    entity_id: str = "sensor.test",
    has_entity_name: bool = True,
    has_name_override: bool = False,
    expected_entity_id: str | None = "sensor.test",
    current_name: str = "Test",
    expected_name: str | None = None,
) -> EntityDriftInfo:
    return EntityDriftInfo(
        entity_id=entity_id,
        has_entity_name=has_entity_name,
        has_name_override=has_name_override,
        expected_entity_id=expected_entity_id,
        current_name=current_name,
        expected_name=expected_name,
    )


def _device(
    device_id: str = "dev1",
    device_name: str = "Test Device",
    default_name: str = "Test Device",
    integrations: list[str] | None = None,
    entities: list[EntityDriftInfo] | None = None,
) -> DeviceInfo:
    ie: dict[str, set[str]] = {}
    for i in integrations or []:
        ie[i] = set()
    return DeviceInfo(
        de=DeviceEntry(
            id=device_id,
            url="/config/devices/device/" + device_id,
            name=device_name,
            default_name=default_name,
            integration_entities=ie,
        ),
        entities=entities or [],
    )


# ── Tests ───────────────────────────────────────────


class TestCheckEnabled:
    def test_empty_checks_enables_all(self) -> None:
        cfg = _config(drift_checks=[])
        assert _check_id_enabled(cfg) is True
        assert _check_name_enabled(cfg) is True

    def test_id_only(self) -> None:
        cfg = _config(drift_checks=["entity-id"])
        assert _check_id_enabled(cfg) is True
        assert _check_name_enabled(cfg) is False

    def test_name_only(self) -> None:
        cfg = _config(drift_checks=["entity-name"])
        assert _check_id_enabled(cfg) is False
        assert _check_name_enabled(cfg) is True

    def test_both_explicit(self) -> None:
        cfg = _config(
            drift_checks=["entity-id", "entity-name"],
        )
        assert _check_id_enabled(cfg) is True
        assert _check_name_enabled(cfg) is True


class TestIsExcluded:
    def test_not_excluded(self) -> None:
        cfg = _config()
        assert _is_excluded(cfg, "sensor.temp", "Temp") is False

    def test_excluded_by_entity_id_list(self) -> None:
        cfg = _config(exclude_entity_ids=["sensor.temp"])
        assert _is_excluded(cfg, "sensor.temp", "Temp") is True

    def test_excluded_by_entity_id_regex(self) -> None:
        cfg = _config(entity_id_exclude_regex="battery")
        assert _is_excluded(cfg, "sensor.battery_level", "Battery") is True
        assert _is_excluded(cfg, "sensor.temp", "Temp") is False

    def test_excluded_by_entity_name_regex(self) -> None:
        cfg = _config(entity_name_exclude_regex="Battery")
        assert _is_excluded(cfg, "sensor.bat", "Battery Level") is True
        assert _is_excluded(cfg, "sensor.bat", "Temperature") is False

    def test_multiple_exclusions(self) -> None:
        cfg = _config(
            exclude_entity_ids=["sensor.a"],
            entity_id_exclude_regex="b$",
            entity_name_exclude_regex="^Ignore",
        )
        assert _is_excluded(cfg, "sensor.a", "A") is True
        assert _is_excluded(cfg, "sensor.b", "B") is True
        assert _is_excluded(cfg, "sensor.c", "Ignore Me") is True
        assert _is_excluded(cfg, "sensor.c", "Keep") is False


class TestCheckEntityDrift:
    def test_no_drift(self) -> None:
        cfg = _config()
        entity = _entity_drift()
        assert _check_entity_drift(cfg, entity, _device()) is None

    def test_id_drift(self) -> None:
        cfg = _config()
        entity = _entity_drift(
            entity_id="sensor.old_name",
            expected_entity_id="sensor.new_name",
        )
        result = _check_entity_drift(cfg, entity, _device())
        assert result is not None
        assert result.id_drifted is True
        assert result.name_drifted is False

    def test_name_drift(self) -> None:
        cfg = _config()
        entity = _entity_drift(
            has_name_override=True,
            current_name="Old Name",
            expected_name="New Name",
        )
        result = _check_entity_drift(cfg, entity, _device())
        assert result is not None
        assert result.name_drifted is True
        assert result.id_drifted is False

    def test_both_drift(self) -> None:
        cfg = _config()
        entity = _entity_drift(
            entity_id="sensor.old",
            expected_entity_id="sensor.new",
            has_name_override=True,
            current_name="Old",
            expected_name="New",
        )
        result = _check_entity_drift(cfg, entity, _device())
        assert result is not None
        assert result.id_drifted is True
        assert result.name_drifted is True

    def test_name_drift_only_when_override(self) -> None:
        cfg = _config()
        entity = _entity_drift(
            has_name_override=False,
            current_name="Old",
            expected_name="New",
        )
        result = _check_entity_drift(cfg, entity, _device())
        assert result is None

    def test_id_check_disabled(self) -> None:
        cfg = _config(drift_checks=["entity-name"])
        entity = _entity_drift(
            entity_id="sensor.old",
            expected_entity_id="sensor.new",
        )
        assert _check_entity_drift(cfg, entity, _device()) is None

    def test_name_check_disabled(self) -> None:
        cfg = _config(drift_checks=["entity-id"])
        entity = _entity_drift(
            has_name_override=True,
            current_name="Old",
            expected_name="New",
        )
        assert _check_entity_drift(cfg, entity, _device()) is None

    def test_excluded_entity_skipped(self) -> None:
        cfg = _config(exclude_entity_ids=["sensor.skip"])
        entity = _entity_drift(
            entity_id="sensor.skip",
            expected_entity_id="sensor.new",
        )
        assert _check_entity_drift(cfg, entity, _device()) is None

    def test_none_expected_id_no_drift(self) -> None:
        cfg = _config()
        entity = _entity_drift(
            entity_id="sensor.test",
            expected_entity_id=None,
        )
        assert _check_entity_drift(cfg, entity, _device()) is None

    def test_none_expected_name_no_drift(self) -> None:
        cfg = _config()
        entity = _entity_drift(
            has_name_override=True,
            current_name="Old",
            expected_name=None,
        )
        assert _check_entity_drift(cfg, entity, _device()) is None

    def test_redundant_prefix_preserved(self) -> None:
        cfg = _config()
        dev = _device(device_name="Kitchen Sensor")
        entity = _entity_drift(
            has_name_override=True,
            current_name="Kitchen Sensor Temperature",
            expected_name="Temperature",
        )
        result = _check_entity_drift(cfg, entity, dev)
        assert result is not None
        assert result.has_redundant_prefix is True

    def test_hen_false_correct_override_no_drift(
        self,
    ) -> None:
        cfg = _config()
        # Device renamed from "Pedestal Fan" to
        # "Main Bedroom Pedestal Fan". Entity has
        # correct suffix override.
        dev = _device(
            device_name="Main Bedroom Pedestal Fan",
            default_name="Pedestal Fan",
        )
        entity = _entity_drift(
            has_entity_name=False,
            has_name_override=True,
            current_name="Temperature",
            expected_name="Pedestal Fan Temperature",
        )
        assert _check_entity_drift(cfg, entity, dev) is None

    def test_hen_false_wrong_override_drifted(
        self,
    ) -> None:
        cfg = _config()
        dev = _device(
            device_name="Main Bedroom Pedestal Fan",
            default_name="Pedestal Fan",
        )
        entity = _entity_drift(
            has_entity_name=False,
            has_name_override=True,
            current_name="Pedestal Fan Temperature",
            expected_name="Pedestal Fan Temperature",
        )
        result = _check_entity_drift(cfg, entity, dev)
        assert result is not None
        assert result.name_drifted is True
        assert result.recommended_override == "Temperature"

    def test_hen_false_no_override_drifted(
        self,
    ) -> None:
        cfg = _config()
        dev = _device(
            device_name="Main Bedroom Pedestal Fan",
            default_name="Pedestal Fan",
        )
        entity = _entity_drift(
            has_entity_name=False,
            has_name_override=False,
            current_name="Pedestal Fan Temperature",
            expected_name="Pedestal Fan Temperature",
        )
        result = _check_entity_drift(cfg, entity, dev)
        assert result is not None
        assert result.name_drifted is True

    def test_hen_false_device_entity_correct(
        self,
    ) -> None:
        cfg = _config()
        dev = _device(
            device_name="Main Bedroom Pedestal Fan",
            default_name="Pedestal Fan",
        )
        entity = _entity_drift(
            has_entity_name=False,
            has_name_override=True,
            current_name="Main Bedroom Pedestal Fan",
            expected_name="Pedestal Fan",
        )
        assert _check_entity_drift(cfg, entity, dev) is None


class TestComputeRecommendedOverride:
    def test_multi_integration_skips(self) -> None:
        result = _compute_recommended_override(
            entity_name="Pedestal Fan Temperature",
            device_default_name="Pedestal Fan",
            device_display_name="Main Bedroom Fan",
            has_entity_name=False,
            multi_integration=True,
        )
        assert result is None

    def test_single_integration_recommends(self) -> None:
        result = _compute_recommended_override(
            entity_name="Pedestal Fan Temperature",
            device_default_name="Pedestal Fan",
            device_display_name="Main Bedroom Fan",
            has_entity_name=False,
            multi_integration=False,
        )
        assert result == "Temperature"


class TestBuildNotificationMessage:
    def test_name_overrides_section(self) -> None:
        device = _device(device_name="Kitchen Sensor")
        drifted = [
            DriftDetail(
                entity_id="sensor.kitchen_temp",
                id_drifted=False,
                name_drifted=True,
                current_name="Old Temp",
                expected_name="Temperature",
            ),
        ]
        msg = _build_notification_message(device, drifted)
        assert "**Name overrides to clear:**" in msg
        assert '"Old Temp"' in msg
        assert '"Temperature"' not in msg
        assert "exclusion list" in msg

    def test_redundant_prefix_section(self) -> None:
        device = _device(device_name="Kitchen Sensor")
        drifted = [
            DriftDetail(
                entity_id="sensor.kitchen_co2",
                id_drifted=False,
                name_drifted=True,
                current_name="Kitchen Sensor CO2",
                expected_name="CO2",
                has_redundant_prefix=True,
            ),
        ]
        msg = _build_notification_message(device, drifted)
        assert "**Name overrides with redundant" in msg
        assert "Kitchen Sensor" in msg
        assert "already adds" in msg

    def test_id_only_section(self) -> None:
        device = _device()
        drifted = [
            DriftDetail(
                entity_id="sensor.old_battery",
                id_drifted=True,
                name_drifted=False,
                current_name="Battery",
                expected_name=None,
            ),
        ]
        msg = _build_notification_message(device, drifted)
        assert "**Non-default entity IDs:**" in msg
        assert "`sensor.old_battery`" in msg
        assert "Recreate entity IDs" in msg

    def test_name_and_id_entity_in_name_section_only(
        self,
    ) -> None:
        device = _device()
        drifted = [
            DriftDetail(
                entity_id="sensor.old",
                id_drifted=True,
                name_drifted=True,
                current_name="Old",
                expected_name="New",
            ),
        ]
        msg = _build_notification_message(device, drifted)
        assert "**Name overrides to clear:**" in msg
        assert "**Non-default entity IDs:**" not in msg

    def test_mixed_sections(self) -> None:
        device = _device(device_name="Dev")
        drifted = [
            DriftDetail(
                entity_id="sensor.name_issue",
                id_drifted=False,
                name_drifted=True,
                current_name="Old",
                expected_name="New",
            ),
            DriftDetail(
                entity_id="sensor.id_issue",
                id_drifted=True,
                name_drifted=False,
                current_name="Fine",
                expected_name=None,
            ),
        ]
        msg = _build_notification_message(device, drifted)
        assert "**Name overrides to clear:**" in msg
        assert "**Non-default entity IDs:**" in msg
        assert "Fix names before recreating IDs" in msg

    def test_device_url_in_message(self) -> None:
        device = _device(device_id="abc123")
        drifted = [
            DriftDetail(
                entity_id="sensor.x",
                id_drifted=True,
                name_drifted=False,
                current_name="X",
                expected_name=None,
            ),
        ]
        msg = _build_notification_message(device, drifted)
        assert "/config/devices/device/abc123" in msg

    def test_integrations_in_message(self) -> None:
        device = _device(
            integrations=["enphase_envoy", "zwave_js"],
        )
        drifted = [
            DriftDetail(
                entity_id="sensor.x",
                id_drifted=True,
                name_drifted=False,
                current_name="X",
                expected_name=None,
            ),
        ]
        msg = _build_notification_message(device, drifted)
        assert "Integrations: enphase_envoy, zwave_js" in msg

    def test_no_integrations_omits_line(self) -> None:
        device = _device()
        drifted = [
            DriftDetail(
                entity_id="sensor.x",
                id_drifted=True,
                name_drifted=False,
                current_name="X",
                expected_name=None,
            ),
        ]
        msg = _build_notification_message(device, drifted)
        assert "Integrations:" not in msg

    def test_name_overrides_to_set_section(
        self,
    ) -> None:
        device = _device(device_name="Main Bedroom Fan")
        drifted = [
            DriftDetail(
                entity_id="number.main_bedroom_fan_angle",
                id_drifted=False,
                name_drifted=True,
                current_name="Fan Angle",
                expected_name=None,
                recommended_override="Angle",
            ),
        ]
        msg = _build_notification_message(device, drifted)
        assert "**Name overrides to set:**" in msg
        assert 'set to "Angle"' in msg
        assert "legacy entities" in msg

    def test_id_only_simple_fix(self) -> None:
        device = _device()
        drifted = [
            DriftDetail(
                entity_id="sensor.old",
                id_drifted=True,
                name_drifted=False,
                current_name="X",
                expected_name=None,
            ),
        ]
        msg = _build_notification_message(device, drifted)
        assert "Recreate entity IDs" in msg
        assert "How to fix" not in msg

    def test_name_only_mentions_next_check(self) -> None:
        device = _device()
        drifted = [
            DriftDetail(
                entity_id="sensor.x",
                id_drifted=False,
                name_drifted=True,
                current_name="Old",
                expected_name="New",
            ),
        ]
        msg = _build_notification_message(device, drifted)
        assert "next check" in msg


class TestEvaluateDevice:
    def test_no_drift(self) -> None:
        cfg = _config()
        device = _device(entities=[_entity_drift()])
        result = _evaluate_device(cfg, device)
        assert result.has_drift is False
        assert result.drifted_entities == []

    def test_drift_detected(self) -> None:
        cfg = _config()
        device = _device(
            entities=[
                _entity_drift(
                    entity_id="sensor.old",
                    expected_entity_id="sensor.new",
                ),
            ],
        )
        result = _evaluate_device(cfg, device)
        assert result.has_drift is True
        assert len(result.drifted_entities) == 1

    def test_excluded_device(self) -> None:
        cfg = _config(device_exclude_regex="Test")
        device = _device(
            device_name="Test Device",
            entities=[
                _entity_drift(
                    entity_id="sensor.old",
                    expected_entity_id="sensor.new",
                ),
            ],
        )
        result = _evaluate_device(cfg, device)
        assert result.has_drift is False
        assert result.device_excluded is True
        assert result.entities_checked == 0
        assert result.entities_excluded == 0

    def test_entity_counts(self) -> None:
        cfg = _config(exclude_entity_ids=["sensor.skip"])
        device = _device(
            entities=[
                _entity_drift(
                    entity_id="sensor.drift",
                    expected_entity_id="sensor.new",
                ),
                _entity_drift(entity_id="sensor.clean"),
                _entity_drift(entity_id="sensor.skip"),
            ],
        )
        result = _evaluate_device(cfg, device)
        assert result.entities_checked == 2
        assert result.entities_excluded == 1

    def test_notification_id_format(self) -> None:
        cfg = _config()
        device = _device(device_id="abc123")
        result = _evaluate_device(cfg, device)
        assert result.notification_id == ("entity_defaults_watchdog_abc123")

    def test_notification_title(self) -> None:
        cfg = _config()
        device = _device(
            device_name="Kitchen Sensor",
            entities=[
                _entity_drift(
                    entity_id="sensor.old",
                    expected_entity_id="sensor.new",
                ),
            ],
        )
        result = _evaluate_device(cfg, device)
        assert result.notification_title == (
            "Entity defaults watchdog: Kitchen Sensor"
        )

    def test_no_title_when_clean(self) -> None:
        cfg = _config()
        device = _device(entities=[_entity_drift()])
        result = _evaluate_device(cfg, device)
        assert result.notification_title == ""

    def test_to_notification(self) -> None:
        cfg = _config()
        device = _device(
            entities=[
                _entity_drift(
                    entity_id="sensor.old",
                    expected_entity_id="sensor.new",
                ),
            ],
        )
        result = _evaluate_device(cfg, device)
        notif = result.to_notification()
        assert notif.active is True
        assert notif.notification_id.startswith(
            "entity_defaults_watchdog_",
        )


class TestEvaluateDevices:
    def test_multiple_devices(self) -> None:
        cfg = _config()
        devices = [
            _device(
                "clean",
                "Clean",
                entities=[_entity_drift()],
            ),
            _device(
                "drifted",
                "Drifted",
                entities=[
                    _entity_drift(
                        entity_id="sensor.old",
                        expected_entity_id="sensor.new",
                    ),
                ],
            ),
        ]
        results = evaluate_devices(cfg, devices)
        assert len(results) == 2
        clean = [r for r in results if not r.has_drift]
        drifted = [r for r in results if r.has_drift]
        assert len(clean) == 1
        assert len(drifted) == 1

    def test_empty_device_list(self) -> None:
        cfg = _config()
        results = evaluate_devices(cfg, [])
        assert results == []

    def test_all_clean(self) -> None:
        cfg = _config()
        devices = [
            _device("d1", "D1", entities=[_entity_drift()]),
            _device("d2", "D2", entities=[_entity_drift()]),
        ]
        results = evaluate_devices(cfg, devices)
        assert all(not r.has_drift for r in results)


class TestCodeQuality(CodeQualityBase):
    ruff_targets = [
        "pyscript/modules/entity_defaults_watchdog.py",
        "tests/test_entity_defaults_watchdog.py",
    ]
    mypy_targets = [
        "pyscript/modules/entity_defaults_watchdog.py",
    ]


# ── Entry point ─────────────────────────────────────

if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _SCRIPT_PATH, REPO_ROOT)
