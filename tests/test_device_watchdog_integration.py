#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = [
#     "pytest",
#     "pytest-cov",
#     "ruff",
#     "mypy",
#     "pytest-homeassistant-custom-component==0.13.324",
# ]
# ///
# This is AI generated code
"""Integration-level tests for the DW handler.

Exercises the parts the in-process unit tests
(``tests/test_device_watchdog_handler.py``) deliberately
don't cover: the live ``vol.Schema`` argparse, the
helper-driven multi-line regex validation, the full
``_async_service_layer`` build-and-apply loop against the
entity + device registries (truth-set assembly +
executor offload of ``run_evaluation`` + sweep dispatch +
``update_instance_state``), and the
automation-link-prefix-on-notification-body invariant the
plan flags as a P1 regression. Same pytest-HACC harness
as ``test_integration.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Make custom_components/ importable as a top-level package;
# the uv-script env doesn't add the repo root to sys.path
# the way ``python -m pytest`` would.
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest  # noqa: E402
from conftest import (  # noqa: E402
    CodeQualityBase,
    RecoveryEventsIntegrationBase,
)

DOMAIN = "blueprint_toolkit"
SERVICE = "device_watchdog"


@pytest.fixture(autouse=True)
def install_our_integration(hass, enable_custom_integrations):  # noqa: ANN001
    """Symlink our integration into pytest-HACC's config_dir."""
    import shutil

    src = (
        Path(__file__).parent.parent / "custom_components" / "blueprint_toolkit"
    )
    cc = Path(hass.config.config_dir) / "custom_components"
    cc.mkdir(exist_ok=True)
    dst = cc / "blueprint_toolkit"
    if dst.is_symlink() or dst.exists():
        if dst.is_dir() and not dst.is_symlink():
            shutil.rmtree(dst)
        else:
            dst.unlink()
    dst.symlink_to(src)
    from homeassistant.loader import DATA_CUSTOM_COMPONENTS

    hass.data.pop(DATA_CUSTOM_COMPONENTS, None)
    yield
    if dst.is_symlink():
        dst.unlink()


def _mock_config_entry(**kwargs):  # noqa: ANN001, ANN201
    """Lazy-import wrapper for MockConfigEntry."""
    from pytest_homeassistant_custom_component.common import (
        MockConfigEntry,
    )

    return MockConfigEntry(**kwargs)


async def _setup_integration(hass: Any) -> Any:
    """Create + load a config entry; return it.

    Also explicitly sets up ``persistent_notification`` so
    the argparse-error code path can dispatch to it.
    """
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "persistent_notification", {})
    entry = _mock_config_entry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _valid_payload(
    *,
    instance_id: str = "automation.dw_test",
    include_integrations: list[str] | None = None,
    exclude_integrations: list[str] | None = None,
    device_exclude_regex: str = "",
    entity_id_exclude_regex: str = "",
    monitored_entity_domains: list[str] | None = None,
    check_interval_minutes: int = 60,
    dead_device_threshold_minutes: int = 1440,
    enabled_checks: list[str] | None = None,
    max_device_notifications: int = 0,
) -> dict[str, Any]:
    """Build a fully-populated DW service-call payload."""
    return {
        "instance_id": instance_id,
        "trigger_id": "manual",
        "include_integrations_raw": include_integrations or [],
        "exclude_integrations_raw": exclude_integrations or [],
        "device_exclude_regex_raw": device_exclude_regex,
        "entity_id_exclude_regex_raw": entity_id_exclude_regex,
        "monitored_entity_domains_raw": monitored_entity_domains or [],
        "check_interval_minutes_raw": check_interval_minutes,
        "dead_device_threshold_minutes_raw": dead_device_threshold_minutes,
        "enabled_checks_raw": enabled_checks or [],
        "max_device_notifications_raw": max_device_notifications,
        "debug_logging_raw": False,
    }


# --------------------------------------------------------
# Argparse / config-error notification path
# --------------------------------------------------------


class TestArgparseEmitsConfigErrorNotification:
    async def test_missing_required_keys_create_notification(
        self,
        hass,  # noqa: ANN001
    ) -> None:
        """A bad call must show up as a persistent notification."""
        await _setup_integration(hass)

        await hass.services.async_call(
            DOMAIN,
            SERVICE,
            {"instance_id": "automation.dw_bad_call"},
            blocking=True,
        )

        from homeassistant.components.persistent_notification import (
            _async_get_or_create_notifications,
        )

        notifs: dict[str, Any] = _async_get_or_create_notifications(hass)
        notif_id = (
            "blueprint_toolkit_device_watchdog"
            "__automation.dw_bad_call__config_error"
        )
        assert notif_id in notifs, "config-error notification was not emitted"
        assert "schema:" in notifs[notif_id]["message"]

    async def test_unknown_check_creates_notification(
        self,
        hass,  # noqa: ANN001
    ) -> None:
        """Cross-validation rejects values not in CHECK_ALL."""
        await _setup_integration(hass)

        payload = _valid_payload(
            instance_id="automation.dw_bad_check",
            enabled_checks=["unavailable-entities", "bogus-check"],
        )
        await hass.services.async_call(DOMAIN, SERVICE, payload, blocking=True)

        from homeassistant.components.persistent_notification import (
            _async_get_or_create_notifications,
        )

        notifs: dict[str, Any] = _async_get_or_create_notifications(hass)
        notif_id = (
            "blueprint_toolkit_device_watchdog"
            "__automation.dw_bad_check__config_error"
        )
        assert notif_id in notifs
        msg: str = notifs[notif_id]["message"]
        assert "enabled_checks" in msg
        assert "bogus-check" in msg

    async def test_invalid_regex_creates_notification(
        self,
        hass,  # noqa: ANN001
    ) -> None:
        """A bad regex line in either regex field surfaces as
        a per-line config error.
        """
        await _setup_integration(hass)

        payload = _valid_payload(
            instance_id="automation.dw_bad_regex",
            entity_id_exclude_regex="[unclosed",
        )
        await hass.services.async_call(DOMAIN, SERVICE, payload, blocking=True)

        from homeassistant.components.persistent_notification import (
            _async_get_or_create_notifications,
        )

        notifs: dict[str, Any] = _async_get_or_create_notifications(hass)
        notif_id = (
            "blueprint_toolkit_device_watchdog"
            "__automation.dw_bad_regex__config_error"
        )
        assert notif_id in notifs
        msg: str = notifs[notif_id]["message"]
        assert "[unclosed" in msg
        assert "entity_id_exclude_regex" in msg

    async def test_match_all_regex_creates_notification(
        self,
        hass,  # noqa: ANN001
    ) -> None:
        """``.*`` matches every entity; the helper rejects
        it with a ``"matches empty string"`` error.
        """
        await _setup_integration(hass)

        payload = _valid_payload(
            instance_id="automation.dw_match_all",
            device_exclude_regex=".*",
        )
        await hass.services.async_call(DOMAIN, SERVICE, payload, blocking=True)

        from homeassistant.components.persistent_notification import (
            _async_get_or_create_notifications,
        )

        notifs: dict[str, Any] = _async_get_or_create_notifications(hass)
        notif_id = (
            "blueprint_toolkit_device_watchdog"
            "__automation.dw_match_all__config_error"
        )
        assert notif_id in notifs
        assert "matches empty string" in notifs[notif_id]["message"]

    async def test_notification_includes_automation_link_when_known(
        self,
        hass,  # noqa: ANN001
    ) -> None:
        """When the automation entity is registered, the
        config-error body starts with the
        ``Automation: [name](link)`` header.
        """
        await _setup_integration(hass)
        hass.states.async_set(
            "automation.dw_link",
            "on",
            {"friendly_name": "DW: Linked", "id": "1234"},
        )
        await hass.services.async_call(
            DOMAIN,
            SERVICE,
            {"instance_id": "automation.dw_link"},
            blocking=True,
        )

        from homeassistant.components.persistent_notification import (
            _async_get_or_create_notifications,
        )

        notifs: dict[str, Any] = _async_get_or_create_notifications(hass)
        notif_id = (
            "blueprint_toolkit_device_watchdog"
            "__automation.dw_link__config_error"
        )
        assert notif_id in notifs
        body: str = notifs[notif_id]["message"]
        assert body.startswith(
            "Automation: [DW: Linked](/config/automation/edit/1234)\n",
        )

    async def test_successful_call_dismisses_prior_notification(
        self,
        hass,  # noqa: ANN001
    ) -> None:
        await _setup_integration(hass)
        # Bad call first.
        await hass.services.async_call(
            DOMAIN,
            SERVICE,
            {"instance_id": "automation.dw_dismiss"},
            blocking=True,
        )
        # Then a good call with the same instance_id.
        await hass.services.async_call(
            DOMAIN,
            SERVICE,
            _valid_payload(instance_id="automation.dw_dismiss"),
            blocking=True,
        )

        from homeassistant.components.persistent_notification import (
            _async_get_or_create_notifications,
        )

        notifs: dict[str, Any] = _async_get_or_create_notifications(hass)
        notif_id = (
            "blueprint_toolkit_device_watchdog"
            "__automation.dw_dismiss__config_error"
        )
        assert notif_id not in notifs


# --------------------------------------------------------
# Service layer: scan + diagnostic state
# --------------------------------------------------------


class TestServiceLayerScan:
    async def test_successful_scan_creates_diagnostic_state(
        self,
        hass,  # noqa: ANN001
    ) -> None:
        """A successful scan populates the diagnostic state
        entity at
        ``blueprint_toolkit.dw_<slug>_state``
        with the common attrs (``instance_id``, ``last_run``,
        ``runtime``) plus the per-port stat extras.
        """
        await _setup_integration(hass)
        await hass.services.async_call(
            DOMAIN,
            SERVICE,
            _valid_payload(instance_id="automation.dw_scan"),
            blocking=True,
        )
        await hass.async_block_till_done()

        state = hass.states.get(
            "blueprint_toolkit.dw_dw_scan_state",
        )
        assert state is not None, "diagnostic state entity not created"
        assert state.state == "ok"
        attrs = state.attributes
        # Common attrs.
        assert attrs["instance_id"] == "automation.dw_scan"
        assert "last_run" in attrs
        assert "runtime" in attrs
        # Per-port stat extras (subset; full list in the
        # handler).
        for key in (
            "integrations",
            "integrations_excluded",
            "devices",
            "devices_excluded",
            "entities",
            "entities_excluded",
            "device_issues",
            "entity_issues",
            "device_stale_issues",
        ):
            assert key in attrs, f"missing diagnostic attr: {key}"
        # Trigger label propagates from the payload.
        assert attrs["last_trigger"] == "manual"


class TestPerDeviceLinkPrefix:
    async def test_per_device_notification_carries_automation_link(
        self,
        hass,  # noqa: ANN001
    ) -> None:
        """DW's per-device notification must carry the
        ``Automation: [name](link)`` prefix the dispatcher
        prepends. Same regression guard EDW + RW have for
        their finding notifications.

        Pattern: ``template.integration_entities()`` looks
        up entries by config-entry title, so a mock
        ``MockConfigEntry(title="fake_integration")`` plus
        a registry entry tied to it is enough to drive a
        full scan through the per-device builder.
        """
        from homeassistant.helpers import device_registry as dr
        from homeassistant.helpers import entity_registry as er

        await _setup_integration(hass)
        # Register the automation entity for the link.
        hass.states.async_set(
            "automation.dw_finding",
            "on",
            {"friendly_name": "DW: Finding", "id": "9999"},
        )

        fake_entry = _mock_config_entry(
            domain="fake_integration",
            title="fake_integration",
        )
        fake_entry.add_to_hass(hass)

        dev_reg = dr.async_get(hass)
        ent_reg = er.async_get(hass)

        device = dev_reg.async_get_or_create(
            config_entry_id=fake_entry.entry_id,
            identifiers={("fake_integration", "device-1")},
            name="Test Device",
        )
        entry = ent_reg.async_get_or_create(
            domain="binary_sensor",
            platform="fake_integration",
            unique_id="unavail-1",
            device_id=device.id,
            config_entry=fake_entry,
            original_name="unavail",
        )
        # Mark the entity unavailable so DW flags it. Use
        # the actual entity_id HA assigned (the registry
        # appends the platform name to disambiguate from
        # other ``binary_sensor.unavail`` entries).
        hass.states.async_set(
            entry.entity_id,
            "unavailable",
            {"friendly_name": "Unavail"},
        )

        await hass.services.async_call(
            DOMAIN,
            SERVICE,
            _valid_payload(
                instance_id="automation.dw_finding",
                include_integrations=["fake_integration"],
                enabled_checks=["unavailable-entities"],
            ),
            blocking=True,
        )
        await hass.async_block_till_done()

        from homeassistant.components.persistent_notification import (
            _async_get_or_create_notifications,
        )

        notifs: dict[str, Any] = _async_get_or_create_notifications(hass)
        # Find the per-device notification (suffix
        # ``device_<device_id>``).
        # Diagnostic-state introspection helps if the
        # finding doesn't materialize -- the per-port stat
        # extras tell us what DW saw.
        state = hass.states.get(
            "blueprint_toolkit.dw_dw_finding_state",
        )
        attrs = state.attributes if state else {}
        per_device = [
            (nid, body)
            for nid, body in notifs.items()
            if nid.startswith(
                "blueprint_toolkit_device_watchdog__"
                "automation.dw_finding__device_"
            )
        ]
        assert per_device, (
            f"expected per-device notification; "
            f"diagnostic state attrs: {attrs}; "
            f"got notifs: {sorted(notifs.keys())}"
        )
        nid, payload = per_device[0]
        body: str = payload["message"]
        assert body.startswith(
            "Automation: [DW: Finding](/config/automation/edit/9999)\n",
        ), f"missing automation-link prefix; body was: {body[:200]!r}"


class TestRecoveryEvents(RecoveryEventsIntegrationBase):
    service_tag = "DW"
    setup_integration = staticmethod(_setup_integration)


class TestCodeQuality(CodeQualityBase):
    ruff_targets = [
        "tests/test_device_watchdog_integration.py",
    ]
    mypy_targets: list[str] = []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", *sys.argv[1:]]))
