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
    RegistryEntry,
    _build_notification_message,
    _check_staleness,
    _evaluate_device,
    _filter_entities,
    check_disabled_diagnostics,
    evaluate_devices,
    evaluate_diagnostics,
)
from helpers import (  # noqa: E402
    DeviceEntry,
    PersistentNotification,
)

T0 = datetime(2024, 1, 15, 12, 0, 0)


# ── Helpers ─────────────────────────────────────────


def _config(**overrides: object) -> Config:
    defaults: dict[str, object] = {
        "device_exclude_regex": "",
        "entity_id_exclude_regex": "",
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
        de=DeviceEntry(
            id=device_id,
            url="/config/devices/device/" + device_id,
            name=device_name,
            default_name=device_name,
        ),
        entities=entities or [],
    )


# ── Tests ───────────────────────────────────────────


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

    def test_entity_id_exclude_regex(self) -> None:
        cfg = _config(entity_id_exclude_regex="battery")
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
            entity_id_exclude_regex="battery",
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
        assert result.device_excluded is True
        assert result.entities_evaluated == 0
        assert result.entities_filtered == 0

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


def _reg_entry(
    entity_id: str = "sensor.test",
    original_name: str = "Test",
    platform: str = "zwave_js",
    entity_category: str | None = "diagnostic",
    disabled: bool = False,
) -> RegistryEntry:
    return RegistryEntry(
        entity_id=entity_id,
        original_name=original_name,
        platform=platform,
        entity_category=entity_category,
        disabled=disabled,
    )


class TestCheckDisabledDiagnostics:
    def test_disabled_entity_flagged(self) -> None:
        entries = [
            _reg_entry(
                original_name="Last seen",
                disabled=True,
            ),
        ]
        result = check_disabled_diagnostics(
            "zwave_js",
            entries,
        )
        assert result == ["Last seen"]

    def test_enabled_entity_not_flagged(self) -> None:
        entries = [
            _reg_entry(
                original_name="Last seen",
                disabled=False,
            ),
        ]
        result = check_disabled_diagnostics(
            "zwave_js",
            entries,
        )
        assert result == []

    def test_missing_entity_skipped(self) -> None:
        result = check_disabled_diagnostics(
            "zwave_js",
            [],
        )
        assert result == []

    def test_non_diagnostic_ignored(self) -> None:
        entries = [
            _reg_entry(
                original_name="Last seen",
                entity_category=None,
                disabled=True,
            ),
        ]
        result = check_disabled_diagnostics(
            "zwave_js",
            entries,
        )
        assert result == []

    def test_wrong_platform_ignored(self) -> None:
        entries = [
            _reg_entry(
                original_name="Last seen",
                platform="matter",
                disabled=True,
            ),
        ]
        result = check_disabled_diagnostics(
            "zwave_js",
            entries,
        )
        assert result == []

    def test_unknown_integration_returns_empty(
        self,
    ) -> None:
        entries = [
            _reg_entry(
                original_name="Last seen",
                platform="unknown",
                disabled=True,
            ),
        ]
        result = check_disabled_diagnostics(
            "unknown",
            entries,
        )
        assert result == []

    def test_multiple_disabled(self) -> None:
        entries = [
            _reg_entry(
                original_name="Last seen",
                disabled=True,
            ),
            _reg_entry(
                original_name="Node status",
                disabled=True,
            ),
        ]
        result = check_disabled_diagnostics(
            "zwave_js",
            entries,
        )
        assert result == ["Last seen", "Node status"]

    def test_partial_disabled(self) -> None:
        entries = [
            _reg_entry(
                original_name="Last seen",
                disabled=False,
            ),
            _reg_entry(
                original_name="Node status",
                disabled=True,
            ),
        ]
        result = check_disabled_diagnostics(
            "zwave_js",
            entries,
        )
        assert result == ["Node status"]


class TestEvaluateDiagnostics:
    def _diag_device(
        self,
        device_id: str = "dev1",
        device_name: str = "Lock",
        integrations: list[str] | None = None,
        registry_entries: list[RegistryEntry] | None = None,
    ) -> DeviceInfo:
        ints = integrations or ["zwave_js"]
        ie: dict[str, set[str]] = {i: set() for i in ints}
        return DeviceInfo(
            de=DeviceEntry(
                id=device_id,
                url="/config/devices/device/" + device_id,
                name=device_name,
                default_name=device_name,
                integration_entities=ie,
            ),
            registry_entries=registry_entries or [],
        )

    def test_device_with_disabled_generates_active(
        self,
    ) -> None:
        device = self._diag_device(
            device_name="Front Door Lock",
            registry_entries=[
                _reg_entry(
                    original_name="Last seen",
                    disabled=True,
                ),
            ],
        )
        results = evaluate_diagnostics([device])
        assert len(results) == 1
        assert results[0].active is True
        assert "Last seen" in results[0].message
        assert "Front Door Lock" in results[0].title

    def test_device_all_enabled_dismisses(
        self,
    ) -> None:
        device = self._diag_device(
            registry_entries=[
                _reg_entry(
                    original_name="Last seen",
                    disabled=False,
                ),
            ],
        )
        results = evaluate_diagnostics([device])
        assert len(results) == 1
        assert results[0].active is False

    def test_notification_id_uses_device_id(
        self,
    ) -> None:
        device = self._diag_device(
            device_id="abc123",
            registry_entries=[
                _reg_entry(
                    original_name="Last seen",
                    disabled=True,
                ),
            ],
        )
        results = evaluate_diagnostics([device])
        assert results[0].notification_id == ("dw_diag_abc123")

    def test_returns_persistent_notification(
        self,
    ) -> None:
        device = self._diag_device(
            registry_entries=[
                _reg_entry(
                    original_name="Last seen",
                    disabled=True,
                ),
            ],
        )
        results = evaluate_diagnostics([device])
        assert isinstance(
            results[0],
            PersistentNotification,
        )

    def test_skips_device_with_no_known_diagnostics(
        self,
    ) -> None:
        device = self._diag_device(
            integrations=["unknown_integration"],
        )
        results = evaluate_diagnostics([device])
        assert results == []

    def test_mixed_known_and_unknown_integrations(
        self,
    ) -> None:
        device = self._diag_device(
            integrations=[
                "unknown_integration",
                "zwave_js",
            ],
            registry_entries=[
                _reg_entry(
                    original_name="Last seen",
                    disabled=True,
                ),
            ],
        )
        results = evaluate_diagnostics([device])
        assert len(results) == 1
        assert results[0].active is True


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
