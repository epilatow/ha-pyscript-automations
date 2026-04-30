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
"""Integration-level tests for the native EDW handler.

Exercises the parts the in-process unit tests
(``tests/test_entity_defaults_watchdog_handler.py``)
deliberately don't cover: the live ``vol.Schema`` argparse,
the helper-driven multi-line regex validation, the full
``_async_service_layer`` build-and-apply loop against
``hass.states`` / ``hass.config_entries.async_entries`` /
the entity + device registries (truth-set assembly +
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
from conftest import CodeQualityBase  # noqa: E402

# pytest-HACC's plugins refuse to load if any
# homeassistant.components.* module is already in
# sys.modules. Defer imports until inside the tests.
DOMAIN = "blueprint_toolkit"
SERVICE = "entity_defaults_watchdog"


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
    the argparse-error code path can dispatch to it. The
    pytest-HACC harness doesn't auto-load it.
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
    instance_id: str = "automation.edw_test",
    drift_checks: list[str] | None = None,
    include_integrations: list[str] | None = None,
    exclude_integrations: list[str] | None = None,
    device_exclude_regex: str = "",
    exclude_entities: list[str] | None = None,
    entity_id_exclude_regex: str = "",
    entity_name_exclude_regex: str = "",
    check_interval_minutes: int = 60,
    max_device_notifications: int = 0,
) -> dict[str, Any]:
    """Build a fully-populated EDW service-call payload."""
    return {
        "instance_id": instance_id,
        "trigger_id": "manual",
        "drift_checks_raw": drift_checks or [],
        "include_integrations_raw": include_integrations or [],
        "exclude_integrations_raw": exclude_integrations or [],
        "device_exclude_regex_raw": device_exclude_regex,
        "exclude_entities_raw": exclude_entities or [],
        "entity_id_exclude_regex_raw": entity_id_exclude_regex,
        "entity_name_exclude_regex_raw": entity_name_exclude_regex,
        "check_interval_minutes_raw": check_interval_minutes,
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
            {"instance_id": "automation.edw_bad_call"},
            blocking=True,
        )

        from homeassistant.components.persistent_notification import (
            _async_get_or_create_notifications,
        )

        notifs: dict[str, Any] = _async_get_or_create_notifications(hass)
        notif_id = (
            "blueprint_toolkit_entity_defaults_watchdog"
            "__automation.edw_bad_call__config_error"
        )
        assert notif_id in notifs, "config-error notification was not emitted"
        assert "schema:" in notifs[notif_id]["message"]

    async def test_unknown_drift_check_creates_notification(
        self,
        hass,  # noqa: ANN001
    ) -> None:
        """Cross-validation rejects values not in CHECK_ALL."""
        await _setup_integration(hass)

        payload = _valid_payload(
            instance_id="automation.edw_bad_check",
            drift_checks=["device-entity-id", "bogus-check"],
        )
        await hass.services.async_call(DOMAIN, SERVICE, payload, blocking=True)

        from homeassistant.components.persistent_notification import (
            _async_get_or_create_notifications,
        )

        notifs: dict[str, Any] = _async_get_or_create_notifications(hass)
        notif_id = (
            "blueprint_toolkit_entity_defaults_watchdog"
            "__automation.edw_bad_check__config_error"
        )
        assert notif_id in notifs
        msg: str = notifs[notif_id]["message"]
        assert "drift_checks" in msg
        assert "bogus-check" in msg

    async def test_invalid_regex_creates_notification(
        self,
        hass,  # noqa: ANN001
    ) -> None:
        """A bad regex line in any of the three regex fields
        surfaces as a per-line config error.
        """
        await _setup_integration(hass)

        payload = _valid_payload(
            instance_id="automation.edw_bad_regex",
            entity_id_exclude_regex="[unclosed",
        )
        await hass.services.async_call(DOMAIN, SERVICE, payload, blocking=True)

        from homeassistant.components.persistent_notification import (
            _async_get_or_create_notifications,
        )

        notifs: dict[str, Any] = _async_get_or_create_notifications(hass)
        notif_id = (
            "blueprint_toolkit_entity_defaults_watchdog"
            "__automation.edw_bad_regex__config_error"
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
            instance_id="automation.edw_match_all",
            device_exclude_regex=".*",
        )
        await hass.services.async_call(DOMAIN, SERVICE, payload, blocking=True)

        from homeassistant.components.persistent_notification import (
            _async_get_or_create_notifications,
        )

        notifs: dict[str, Any] = _async_get_or_create_notifications(hass)
        notif_id = (
            "blueprint_toolkit_entity_defaults_watchdog"
            "__automation.edw_match_all__config_error"
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
            "automation.edw_link",
            "on",
            {"friendly_name": "EDW: Linked", "id": "1234"},
        )
        await hass.services.async_call(
            DOMAIN,
            SERVICE,
            {"instance_id": "automation.edw_link"},
            blocking=True,
        )

        from homeassistant.components.persistent_notification import (
            _async_get_or_create_notifications,
        )

        notifs: dict[str, Any] = _async_get_or_create_notifications(hass)
        notif_id = (
            "blueprint_toolkit_entity_defaults_watchdog"
            "__automation.edw_link__config_error"
        )
        assert notif_id in notifs
        body: str = notifs[notif_id]["message"]
        assert body.startswith(
            "Automation: [EDW: Linked](/config/automation/edit/1234)\n",
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
            {"instance_id": "automation.edw_dismiss"},
            blocking=True,
        )
        # Then a good call with the same instance_id.
        await hass.services.async_call(
            DOMAIN,
            SERVICE,
            _valid_payload(instance_id="automation.edw_dismiss"),
            blocking=True,
        )

        from homeassistant.components.persistent_notification import (
            _async_get_or_create_notifications,
        )

        notifs: dict[str, Any] = _async_get_or_create_notifications(hass)
        notif_id = (
            "blueprint_toolkit_entity_defaults_watchdog"
            "__automation.edw_dismiss__config_error"
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
        ``blueprint_toolkit.entity_defaults_watchdog_<slug>_state``
        with the common attrs (``instance_id``, ``last_run``,
        ``runtime``) plus the per-port stat extras.
        """
        await _setup_integration(hass)
        await hass.services.async_call(
            DOMAIN,
            SERVICE,
            _valid_payload(instance_id="automation.edw_scan"),
            blocking=True,
        )
        await hass.async_block_till_done()

        state = hass.states.get(
            "blueprint_toolkit.entity_defaults_watchdog_edw_scan_state",
        )
        assert state is not None, "diagnostic state entity not created"
        assert state.state == "ok"
        attrs = state.attributes
        # Common attrs.
        assert attrs["instance_id"] == "automation.edw_scan"
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
            "entity_name_issues",
            "entity_id_issues",
            "deviceless_entities",
            "deviceless_excluded",
            "deviceless_drift",
            "deviceless_stale",
        ):
            assert key in attrs, f"missing diagnostic attr: {key}"
        # Trigger label propagates from the payload.
        assert attrs["last_trigger"] == "manual"

    async def test_deviceless_notification_carries_automation_link(
        self,
        hass,  # noqa: ANN001
    ) -> None:
        """EDW's deviceless aggregate notification must carry
        the ``Automation: [name](link)`` prefix. Regression
        guard for the same plan-flagged P1 RW caught: a
        ``PersistentNotification`` constructed without
        ``instance_id`` silently loses the click-through
        link, and the dispatcher's gate makes that
        invisible at code-review time.

        Trigger a deviceless finding by pre-seeding a state-
        only entity in a ``DEVICELESS_DOMAINS`` whose
        ``friendly_name`` doesn't match its slugified
        ``object_id`` -- ``EDW``'s state-only safety net
        path picks it up without needing a registry entry.
        """
        await _setup_integration(hass)
        # Register the automation entity so the dispatcher
        # can find a friendly name + YAML id to build the
        # link.
        hass.states.async_set(
            "automation.edw_finding",
            "on",
            {"friendly_name": "EDW: Finding", "id": "9999"},
        )
        # Plant a state-only entity whose effective name's
        # slugified form doesn't match its object_id. The
        # logic module's deviceless evaluator flags this as
        # drift and emits the deviceless aggregate
        # notification.
        hass.states.async_set(
            "input_text.legacy_object_id",
            "value",
            {"friendly_name": "Brand New Name"},
        )

        await hass.services.async_call(
            DOMAIN,
            SERVICE,
            _valid_payload(instance_id="automation.edw_finding"),
            blocking=True,
        )
        await hass.async_block_till_done()

        from homeassistant.components.persistent_notification import (
            _async_get_or_create_notifications,
        )

        notifs: dict[str, Any] = _async_get_or_create_notifications(hass)
        notif_id = (
            "blueprint_toolkit_entity_defaults_watchdog"
            "__automation.edw_finding__deviceless"
        )
        assert notif_id in notifs, (
            f"expected deviceless notification; got {sorted(notifs.keys())}"
        )
        body: str = notifs[notif_id]["message"]
        # Critical assertion: the dispatcher prepended the
        # automation-link header. Without ``instance_id`` on
        # the underlying ``PersistentNotification`` spec, the
        # body would start with the per-category content
        # directly.
        assert body.startswith(
            "Automation: [EDW: Finding](/config/automation/edit/9999)\n",
        ), f"missing automation-link prefix; body was: {body[:200]!r}"


class TestCodeQuality(CodeQualityBase):
    ruff_targets = [
        "tests/test_entity_defaults_watchdog_integration.py",
    ]
    mypy_targets: list[str] = []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", *sys.argv[1:]]))
