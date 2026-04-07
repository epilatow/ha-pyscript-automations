#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest", "pytest-cov", "ruff", "mypy"]
# ///
# This is AI generated code
"""Tests for device_watchdog module."""

import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

_SCRIPT_PATH = REPO_ROOT / "pyscript" / "modules" / "device_watchdog.py"

sys.path.insert(0, str(_SCRIPT_PATH.parent))

import pytest  # noqa: E402
from conftest import CodeQualityBase  # noqa: E402
from device_watchdog import (  # noqa: E402
    Config,
    DeviceInfo,
    EntityInfo,
    _build_notification_message,
    _check_staleness,
    _evaluate_device,
    _filter_entities,
    _matches_pattern,
    evaluate_devices,
    should_run,
)

T0 = datetime(2024, 1, 15, 12, 0, 0)


# ── Helpers ─────────────────────────────────────────


def _config(**overrides: object) -> Config:
    defaults: dict[str, object] = {
        "device_exclude_regex": "",
        "entity_exclude_regex": "",
        "monitored_entity_domains": [],
        "dead_threshold_seconds": 86400,
    }
    defaults.update(overrides)
    return Config(**defaults)  # type: ignore[arg-type]


def _entity(
    entity_id: str = "sensor.test",
    state: str = "42.0",
    last_changed: datetime | None = None,
) -> EntityInfo:
    return EntityInfo(
        entity_id=entity_id,
        state=state,
        last_changed=last_changed or T0,
    )


def _device(
    device_id: str = "dev1",
    device_name: str = "Test Device",
    entities: list[EntityInfo] | None = None,
) -> DeviceInfo:
    return DeviceInfo(
        device_id=device_id,
        device_name=device_name,
        device_url="/config/devices/device/" + device_id,
        entities=entities or [],
    )


# ── Tests ───────────────────────────────────────────


class TestShouldRun:
    def test_runs_on_interval_boundary(self) -> None:
        # 12:00 is minute 0 from epoch; 60 divides evenly
        t = datetime(2024, 1, 15, 12, 0, 0)
        assert should_run(60, t) is True

    def test_skips_off_interval(self) -> None:
        t = datetime(2024, 1, 15, 12, 1, 0)
        assert should_run(60, t) is False

    def test_interval_one_always_runs(self) -> None:
        t = datetime(2024, 1, 15, 12, 37, 0)
        assert should_run(1, t) is True

    def test_interval_zero_always_runs(self) -> None:
        t = datetime(2024, 1, 15, 12, 37, 0)
        assert should_run(0, t) is True

    def test_negative_interval_always_runs(self) -> None:
        t = datetime(2024, 1, 15, 12, 37, 0)
        assert should_run(-5, t) is True


class TestMatchesPattern:
    def test_empty_pattern_no_match(self) -> None:
        assert _matches_pattern("anything", "") is False

    def test_simple_match(self) -> None:
        assert _matches_pattern("sensor.temp", "temp")

    def test_case_insensitive(self) -> None:
        assert _matches_pattern("Sensor.Temp", "sensor")

    def test_regex_pattern(self) -> None:
        assert _matches_pattern(
            "sensor.outdoor_temp",
            r"outdoor.*temp",
        )

    def test_no_match(self) -> None:
        assert not _matches_pattern("sensor.temp", "humid")

    def test_invalid_regex_no_crash(self) -> None:
        assert not _matches_pattern("test", "[invalid")


class TestFilterEntities:
    def test_no_filters_keeps_all(self) -> None:
        cfg = _config()
        entities = [_entity("sensor.a"), _entity("sensor.b")]
        kept, filtered = _filter_entities(cfg, entities)
        assert len(kept) == 2
        assert len(filtered) == 0

    def test_domain_filter(self) -> None:
        cfg = _config(
            monitored_entity_domains=["sensor"],
        )
        entities = [
            _entity("sensor.temp"),
            _entity("binary_sensor.door"),
            _entity("switch.light"),
        ]
        kept, filtered = _filter_entities(cfg, entities)
        assert len(kept) == 1
        assert kept[0].entity_id == "sensor.temp"
        assert len(filtered) == 2

    def test_entity_exclude_regex(self) -> None:
        cfg = _config(entity_exclude_regex="battery")
        entities = [
            _entity("sensor.temp"),
            _entity("sensor.battery_level"),
        ]
        kept, filtered = _filter_entities(cfg, entities)
        assert len(kept) == 1
        assert kept[0].entity_id == "sensor.temp"

    def test_domain_and_exclude_combined(self) -> None:
        cfg = _config(
            monitored_entity_domains=["sensor"],
            entity_exclude_regex="battery",
        )
        entities = [
            _entity("sensor.temp"),
            _entity("sensor.battery"),
            _entity("binary_sensor.door"),
        ]
        kept, filtered = _filter_entities(cfg, entities)
        assert len(kept) == 1
        assert kept[0].entity_id == "sensor.temp"
        assert len(filtered) == 2

    def test_empty_entity_list(self) -> None:
        cfg = _config()
        kept, filtered = _filter_entities(cfg, [])
        assert kept == []
        assert filtered == []

    def test_entity_without_dot(self) -> None:
        cfg = _config(monitored_entity_domains=["sensor"])
        entities = [_entity("nodot")]
        kept, filtered = _filter_entities(cfg, entities)
        assert len(kept) == 0
        assert len(filtered) == 1


class TestCheckStaleness:
    def test_no_entities_not_stale(self) -> None:
        is_stale, eid, ts = _check_staleness([], 3600, T0)
        assert is_stale is False
        assert eid is None
        assert ts is None

    def test_recent_entity_not_stale(self) -> None:
        entities = [
            _entity(last_changed=T0 - timedelta(minutes=5)),
        ]
        is_stale, _, _ = _check_staleness(
            entities,
            3600,
            T0,
        )
        assert is_stale is False

    def test_old_entity_is_stale(self) -> None:
        entities = [
            _entity(last_changed=T0 - timedelta(hours=25)),
        ]
        is_stale, _, _ = _check_staleness(
            entities,
            86400,
            T0,
        )
        assert is_stale is True

    def test_returns_newest_entity(self) -> None:
        entities = [
            _entity(
                "sensor.old",
                last_changed=T0 - timedelta(hours=2),
            ),
            _entity(
                "sensor.new",
                last_changed=T0 - timedelta(minutes=5),
            ),
        ]
        _, newest_eid, newest_ts = _check_staleness(
            entities,
            86400,
            T0,
        )
        assert newest_eid == "sensor.new"
        assert newest_ts == T0 - timedelta(minutes=5)

    def test_exact_threshold_not_stale(self) -> None:
        entities = [
            _entity(last_changed=T0 - timedelta(seconds=3600)),
        ]
        is_stale, _, _ = _check_staleness(
            entities,
            3600,
            T0,
        )
        assert is_stale is False

    def test_one_second_over_is_stale(self) -> None:
        entities = [
            _entity(last_changed=T0 - timedelta(seconds=3601)),
        ]
        is_stale, _, _ = _check_staleness(
            entities,
            3600,
            T0,
        )
        assert is_stale is True

    def test_mixed_unavailable_and_fresh(self) -> None:
        entities = [
            _entity(
                "sensor.bad",
                state="unavailable",
                last_changed=T0 - timedelta(hours=25),
            ),
            _entity(
                "sensor.good",
                state="42.0",
                last_changed=T0 - timedelta(minutes=5),
            ),
        ]
        is_stale, newest_eid, _ = _check_staleness(
            entities,
            86400,
            T0,
        )
        assert is_stale is False
        assert newest_eid == "sensor.good"


class TestEvaluateDevice:
    def test_healthy_device(self) -> None:
        cfg = _config()
        device = _device(
            entities=[
                _entity(
                    state="42.0",
                    last_changed=T0 - timedelta(minutes=5),
                ),
            ],
        )
        result = _evaluate_device(cfg, device, T0)
        assert result.has_issue is False
        assert result.notification_message == ""

    def test_unavailable_entity(self) -> None:
        cfg = _config()
        device = _device(
            entities=[
                _entity(
                    "sensor.temp",
                    state="unavailable",
                    last_changed=T0 - timedelta(minutes=5),
                ),
            ],
        )
        result = _evaluate_device(cfg, device, T0)
        assert result.has_issue is True
        assert "sensor.temp" in result.unavailable_entities
        assert "Unavailable" in result.notification_message

    def test_unknown_entity(self) -> None:
        cfg = _config()
        device = _device(
            entities=[
                _entity(
                    state="unknown",
                    last_changed=T0 - timedelta(minutes=5),
                ),
            ],
        )
        result = _evaluate_device(cfg, device, T0)
        assert result.has_issue is True

    def test_stale_device(self) -> None:
        cfg = _config(dead_threshold_seconds=3600)
        device = _device(
            entities=[
                _entity(
                    state="42.0",
                    last_changed=T0 - timedelta(hours=2),
                ),
            ],
        )
        result = _evaluate_device(cfg, device, T0)
        assert result.has_issue is True
        assert result.is_stale is True

    def test_excluded_device(self) -> None:
        cfg = _config(device_exclude_regex="Test")
        device = _device(
            device_name="Test Device",
            entities=[
                _entity(state="unavailable"),
            ],
        )
        result = _evaluate_device(cfg, device, T0)
        assert result.has_issue is False
        assert result.entities_evaluated == 0
        assert result.entities_filtered == 1

    def test_notification_id_format(self) -> None:
        cfg = _config()
        device = _device(device_id="abc123")
        result = _evaluate_device(cfg, device, T0)
        assert result.notification_id == ("device_watchdog_abc123")

    def test_notification_title(self) -> None:
        cfg = _config()
        device = _device(
            device_name="Kitchen Sensor",
            entities=[_entity(state="unavailable")],
        )
        result = _evaluate_device(cfg, device, T0)
        assert result.notification_title == ("Device watchdog: Kitchen Sensor")

    def test_no_entities_not_stale(self) -> None:
        cfg = _config()
        device = _device(entities=[])
        result = _evaluate_device(cfg, device, T0)
        assert result.has_issue is False
        assert result.is_stale is False

    def test_entity_counts(self) -> None:
        cfg = _config(
            monitored_entity_domains=["sensor"],
        )
        device = _device(
            entities=[
                _entity("sensor.a"),
                _entity("binary_sensor.b"),
                _entity("sensor.c"),
            ],
        )
        result = _evaluate_device(cfg, device, T0)
        assert result.entities_evaluated == 2
        assert result.entities_filtered == 1


class TestBuildNotificationMessage:
    def test_unavailable_entities_listed(self) -> None:
        device = _device(device_name="My Device")
        unavailable = [
            _entity("sensor.temp", state="unavailable"),
            _entity("sensor.humid", state="unavailable"),
        ]
        msg = _build_notification_message(
            device,
            unavailable,
            False,
            None,
            None,
            _config(),
        )
        assert "sensor.temp" in msg
        assert "sensor.humid" in msg
        assert "Unavailable entity" in msg

    def test_stale_with_newest(self) -> None:
        device = _device()
        ts = T0 - timedelta(hours=25)
        msg = _build_notification_message(
            device,
            [],
            True,
            "sensor.old",
            ts,
            _config(),
        )
        assert "No entity state change" in msg
        assert "sensor.old" in msg
        assert ts.isoformat() in msg

    def test_stale_no_prior_updates(self) -> None:
        device = _device()
        msg = _build_notification_message(
            device,
            [],
            True,
            None,
            None,
            _config(),
        )
        assert "No prior updates detected" in msg

    def test_device_url_in_message(self) -> None:
        device = _device(device_id="abc")
        msg = _build_notification_message(
            device,
            [_entity(state="unavailable")],
            False,
            None,
            None,
            _config(),
        )
        assert "/config/devices/device/abc" in msg

    def test_asserts_on_no_issues(self) -> None:
        """Calling with no unavailable and not stale is a bug."""
        device = _device()
        with pytest.raises(AssertionError):
            _build_notification_message(
                device,
                [],
                False,
                None,
                None,
                _config(),
            )


class TestEvaluateDevices:
    def test_multiple_devices(self) -> None:
        cfg = _config()
        devices = [
            _device(
                "healthy",
                "Healthy",
                [_entity(last_changed=T0)],
            ),
            _device(
                "sick",
                "Sick",
                [_entity(state="unavailable")],
            ),
        ]
        results = evaluate_devices(cfg, devices, T0)
        assert len(results) == 2
        healthy = [r for r in results if not r.has_issue]
        sick = [r for r in results if r.has_issue]
        assert len(healthy) == 1
        assert len(sick) == 1
        assert healthy[0].device_id == "healthy"
        assert sick[0].device_id == "sick"

    def test_empty_device_list(self) -> None:
        cfg = _config()
        results = evaluate_devices(cfg, [], T0)
        assert results == []

    def test_all_healthy(self) -> None:
        cfg = _config()
        devices = [
            _device(
                "d1",
                "D1",
                [_entity(last_changed=T0)],
            ),
            _device(
                "d2",
                "D2",
                [_entity(last_changed=T0)],
            ),
        ]
        results = evaluate_devices(cfg, devices, T0)
        assert all(not r.has_issue for r in results)

    def test_all_unhealthy(self) -> None:
        cfg = _config(dead_threshold_seconds=60)
        old = T0 - timedelta(hours=1)
        devices = [
            _device(
                "d1",
                "D1",
                [_entity(last_changed=old)],
            ),
            _device(
                "d2",
                "D2",
                [_entity(last_changed=old)],
            ),
        ]
        results = evaluate_devices(cfg, devices, T0)
        assert all(r.has_issue for r in results)


class TestCodeQuality(CodeQualityBase):
    ruff_targets = [
        "pyscript/modules/device_watchdog.py",
        "tests/test_device_watchdog.py",
    ]
    mypy_targets = [
        "pyscript/modules/device_watchdog.py",
    ]


# ── Entry point ─────────────────────────────────────

if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _SCRIPT_PATH, REPO_ROOT)
