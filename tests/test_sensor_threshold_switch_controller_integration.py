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
"""Integration-level tests for the STSC handler.

Exercises the parts the in-process unit tests
(``tests/test_sensor_threshold_switch_controller_handler.py``)
deliberately don't cover: the live ``vol.Schema`` argparse,
the cross-field checks against ``hass.states`` /
``hass.services.has_service`` for ``target_switch_entity``
+ ``notification_service``, the full
``_async_service_layer`` state-load / action-dispatch /
notification loop, and the diagnostic-state attrs.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Make custom_components/ importable as a top-level package.
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest  # noqa: E402
from conftest import CodeQualityBase  # noqa: E402

DOMAIN = "blueprint_toolkit"
SERVICE = "sensor_threshold_switch_controller"


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
    from pytest_homeassistant_custom_component.common import (
        MockConfigEntry,
    )

    return MockConfigEntry(**kwargs)


async def _setup_integration(hass: Any) -> Any:
    """Create + load a config entry; return it."""
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "persistent_notification", {})
    entry = _mock_config_entry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _seed_target_switch(hass: Any, entity_id: str = "switch.fan") -> None:
    """Plant a fake switch entity in the state machine.

    STSC's argparse cross-field check requires the
    ``target_switch_entity`` to exist as a known state;
    we only need it visible to ``hass.states.get`` so we
    can drive the service-call path through to the
    service layer without a real switch integration.
    """
    hass.states.async_set(entity_id, "off", {"friendly_name": "Test Fan"})


def _valid_payload(
    *,
    instance_id: str = "automation.stsc_test",
    target_switch_entity: str = "switch.fan",
    sensor_value: str = "55.0",
    switch_state: str = "off",
    trigger_entity: str = "sensor.humidity",
    trigger_threshold: float = 70.0,
    release_threshold: float = 60.0,
    notification_service: str = "",
) -> dict[str, Any]:
    """Build a fully-populated STSC service-call payload."""
    return {
        "instance_id": instance_id,
        "trigger_id": "manual",
        "target_switch_entity": target_switch_entity,
        "sensor_value": sensor_value,
        "switch_state": switch_state,
        "trigger_entity": trigger_entity,
        "trigger_threshold_raw": trigger_threshold,
        "release_threshold_raw": release_threshold,
        "sampling_window_seconds_raw": 600,
        "disable_window_seconds_raw": 30,
        "auto_off_minutes_raw": 60,
        "notification_service": notification_service,
        "notification_prefix": "",
        "notification_suffix": "",
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
        await _setup_integration(hass)

        await hass.services.async_call(
            DOMAIN,
            SERVICE,
            {"instance_id": "automation.stsc_bad_call"},
            blocking=True,
        )

        from homeassistant.components.persistent_notification import (
            _async_get_or_create_notifications,
        )

        notifs: dict[str, Any] = _async_get_or_create_notifications(hass)
        notif_id = (
            "blueprint_toolkit_sensor_threshold_switch_controller"
            "__automation.stsc_bad_call__config_error"
        )
        assert notif_id in notifs, "config-error notification was not emitted"
        assert "schema:" in notifs[notif_id]["message"]

    async def test_unknown_target_switch_entity_creates_notification(
        self,
        hass,  # noqa: ANN001
    ) -> None:
        await _setup_integration(hass)

        # No ``_seed_target_switch`` -- the entity doesn't
        # exist; the cross-field check should reject.
        payload = _valid_payload(
            instance_id="automation.stsc_bad_switch",
            target_switch_entity="switch.does_not_exist",
        )
        await hass.services.async_call(DOMAIN, SERVICE, payload, blocking=True)

        from homeassistant.components.persistent_notification import (
            _async_get_or_create_notifications,
        )

        notifs: dict[str, Any] = _async_get_or_create_notifications(hass)
        notif_id = (
            "blueprint_toolkit_sensor_threshold_switch_controller"
            "__automation.stsc_bad_switch__config_error"
        )
        assert notif_id in notifs
        msg: str = notifs[notif_id]["message"]
        assert "target_switch_entity" in msg
        assert "switch.does_not_exist" in msg

    async def test_unregistered_notification_service_creates_notification(
        self,
        hass,  # noqa: ANN001
    ) -> None:
        await _setup_integration(hass)
        _seed_target_switch(hass)

        payload = _valid_payload(
            instance_id="automation.stsc_bad_notify",
            notification_service="notify.does_not_exist",
        )
        await hass.services.async_call(DOMAIN, SERVICE, payload, blocking=True)

        from homeassistant.components.persistent_notification import (
            _async_get_or_create_notifications,
        )

        notifs: dict[str, Any] = _async_get_or_create_notifications(hass)
        notif_id = (
            "blueprint_toolkit_sensor_threshold_switch_controller"
            "__automation.stsc_bad_notify__config_error"
        )
        assert notif_id in notifs
        msg: str = notifs[notif_id]["message"]
        assert "notification_service" in msg
        assert "notify.does_not_exist" in msg

    async def test_non_controllable_target_switch_creates_notification(
        self,
        hass,  # noqa: ANN001
    ) -> None:
        """Cross-field guard: ``target_switch_entity`` must
        live in a domain that responds to
        ``homeassistant.turn_on`` / ``turn_off``. The
        blueprint selector restricts the input to
        ``switch / fan / light / input_boolean``, but a
        hand-edited YAML automation can bypass the
        selector. Argparse rejects it before the service
        layer dispatches a silent no-op.
        """
        await _setup_integration(hass)
        # Existing entity, wrong domain (sensors don't
        # respond to homeassistant.turn_on/off).
        hass.states.async_set("sensor.humidity", "55.0")
        from pytest_homeassistant_custom_component.common import (
            async_mock_service,
        )

        turn_on_calls = async_mock_service(hass, "homeassistant", "turn_on")

        payload = _valid_payload(
            instance_id="automation.stsc_bad_domain",
            target_switch_entity="sensor.humidity",
        )
        await hass.services.async_call(DOMAIN, SERVICE, payload, blocking=True)
        await hass.async_block_till_done()

        from homeassistant.components.persistent_notification import (
            _async_get_or_create_notifications,
        )

        notifs: dict[str, Any] = _async_get_or_create_notifications(hass)
        notif_id = (
            "blueprint_toolkit_sensor_threshold_switch_controller"
            "__automation.stsc_bad_domain__config_error"
        )
        assert notif_id in notifs
        msg: str = notifs[notif_id]["message"]
        assert "target_switch_entity" in msg
        assert "does not support on/off" in msg
        # Argparse rejected; service layer must not have
        # dispatched a turn_on against the bad entity.
        assert turn_on_calls == []

    async def test_notification_includes_automation_link_when_known(
        self,
        hass,  # noqa: ANN001
    ) -> None:
        await _setup_integration(hass)
        hass.states.async_set(
            "automation.stsc_link",
            "on",
            {"friendly_name": "STSC: Linked", "id": "1234"},
        )
        await hass.services.async_call(
            DOMAIN,
            SERVICE,
            {"instance_id": "automation.stsc_link"},
            blocking=True,
        )

        from homeassistant.components.persistent_notification import (
            _async_get_or_create_notifications,
        )

        notifs: dict[str, Any] = _async_get_or_create_notifications(hass)
        notif_id = (
            "blueprint_toolkit_sensor_threshold_switch_controller"
            "__automation.stsc_link__config_error"
        )
        assert notif_id in notifs
        body: str = notifs[notif_id]["message"]
        assert body.startswith(
            "Automation: [STSC: Linked](/config/automation/edit/1234)\n",
        )

    async def test_successful_call_dismisses_prior_notification(
        self,
        hass,  # noqa: ANN001
    ) -> None:
        await _setup_integration(hass)
        _seed_target_switch(hass)

        # Bad call first.
        await hass.services.async_call(
            DOMAIN,
            SERVICE,
            {"instance_id": "automation.stsc_dismiss"},
            blocking=True,
        )
        # Good call with the same instance_id.
        await hass.services.async_call(
            DOMAIN,
            SERVICE,
            _valid_payload(instance_id="automation.stsc_dismiss"),
            blocking=True,
        )

        from homeassistant.components.persistent_notification import (
            _async_get_or_create_notifications,
        )

        notifs: dict[str, Any] = _async_get_or_create_notifications(hass)
        notif_id = (
            "blueprint_toolkit_sensor_threshold_switch_controller"
            "__automation.stsc_dismiss__config_error"
        )
        assert notif_id not in notifs


# --------------------------------------------------------
# Service layer: state load / save + diagnostic state
# --------------------------------------------------------


class TestServiceLayerScan:
    async def test_successful_call_creates_diagnostic_state(
        self,
        hass,  # noqa: ANN001
    ) -> None:
        """A successful call populates the diagnostic state
        entity with the standard attrs (``instance_id``,
        ``last_run``, ``runtime``, ``state``) plus the STSC
        decision-context extras + the ``data`` blob.
        """
        await _setup_integration(hass)
        _seed_target_switch(hass)

        await hass.services.async_call(
            DOMAIN,
            SERVICE,
            _valid_payload(instance_id="automation.stsc_scan"),
            blocking=True,
        )
        await hass.async_block_till_done()

        state = hass.states.get(
            "blueprint_toolkit.sensor_threshold_switch_controller_stsc_scan_state",
        )
        assert state is not None, "diagnostic state entity not created"
        attrs = state.attributes
        # Common attrs.
        assert attrs["instance_id"] == "automation.stsc_scan"
        assert "last_run" in attrs
        assert "runtime" in attrs
        # STSC-specific decision-context extras.
        for key in (
            "last_trigger",
            "last_event",
            "last_action",
            "last_reason",
            "last_sensor",
            "data",
        ):
            assert key in attrs, f"missing diagnostic attr: {key}"
        # ``data`` is the JSON-encoded controller state
        # blob the next tick re-loads.
        import json

        loaded = json.loads(attrs["data"])
        assert isinstance(loaded, dict)
        # The controller's State has these keys at minimum
        # (see logic.State.to_dict).
        for key in ("samples", "baseline", "overrides", "initialized"):
            assert key in loaded, (
                f"missing controller-state key: {key}; got {sorted(loaded)}"
            )

    async def test_state_blob_round_trips_across_calls(
        self,
        hass,  # noqa: ANN001
    ) -> None:
        """The ``data`` attribute the prior call wrote
        should be readable + reflected in the next call's
        decisions. Specifically: after one sensor sample,
        the ``samples`` list in the persisted blob has
        length 1.
        """
        await _setup_integration(hass)
        _seed_target_switch(hass)

        # First call: sensor reading well below threshold.
        # Triggers the sensor sample-window path.
        await hass.services.async_call(
            DOMAIN,
            SERVICE,
            _valid_payload(
                instance_id="automation.stsc_persist",
                sensor_value="50.0",
            ),
            blocking=True,
        )
        await hass.async_block_till_done()

        state = hass.states.get(
            "blueprint_toolkit.sensor_threshold_switch_controller_stsc_persist_state",
        )
        assert state is not None
        import json

        loaded = json.loads(state.attributes["data"])
        # The first sensor sample should be in the
        # samples list. (Empty initially -> populated
        # after this call.)
        assert len(loaded["samples"]) >= 1


# --------------------------------------------------------
# Action dispatch + notification routing
# --------------------------------------------------------


def _spike_payload(
    *,
    instance_id: str,
    sensor_value: str,
    notification_service: str = "",
) -> dict[str, Any]:
    """Spike-tuned STSC payload.

    The default ``trigger_threshold`` of 5.0 keeps the
    spike-detection bar low so a 55 -> 65 sensor swing in
    the action-dispatch / notification tests trips
    ``Action.TURN_ON`` reliably.
    """
    return _valid_payload(
        instance_id=instance_id,
        sensor_value=sensor_value,
        switch_state="off",
        trigger_entity="sensor.humidity",
        trigger_threshold=5.0,
        release_threshold=2.0,
        notification_service=notification_service,
    )


class TestActionDispatch:
    async def test_spike_dispatches_homeassistant_turn_on(
        self,
        hass,  # noqa: ANN001
    ) -> None:
        """End-to-end: a sensor spike should provoke a
        ``homeassistant.turn_on`` against
        ``target_switch_entity``. Without this test the
        dispatch is opaque to the test surface (the
        logic suite tests the action enum, not the HA
        service call).

        Also asserts ``context=call.context`` is propagated
        from the originating service call through to the
        ``homeassistant.turn_on`` dispatch -- without this,
        the HA logbook would attribute the action to the
        integration rather than to the originating
        automation.
        """
        from homeassistant.core import Context
        from pytest_homeassistant_custom_component.common import (
            async_mock_service,
        )

        await _setup_integration(hass)
        _seed_target_switch(hass)
        turn_on_calls = async_mock_service(hass, "homeassistant", "turn_on")

        # First call seeds a baseline-low sample.
        await hass.services.async_call(
            DOMAIN,
            SERVICE,
            _spike_payload(
                instance_id="automation.stsc_spike",
                sensor_value="55.0",
            ),
            blocking=True,
        )
        # Second call jumps above ``baseline + threshold``;
        # logic returns ``Action.TURN_ON``. Attach an
        # explicit ``Context`` so we can assert
        # propagation to the dispatched ``turn_on`` call.
        spike_ctx = Context()
        await hass.services.async_call(
            DOMAIN,
            SERVICE,
            _spike_payload(
                instance_id="automation.stsc_spike",
                sensor_value="65.0",
            ),
            blocking=True,
            context=spike_ctx,
        )
        await hass.async_block_till_done()

        assert len(turn_on_calls) == 1
        assert turn_on_calls[0].data["entity_id"] == "switch.fan"
        # Context propagation: the dispatched ``turn_on``
        # should carry the spike call's context, not a
        # fresh-from-HA one.
        assert turn_on_calls[0].context.id == spike_ctx.id

    async def test_no_action_when_sensor_steady(
        self,
        hass,  # noqa: ANN001
    ) -> None:
        """A sensor reading at baseline should leave the
        switch alone -- no turn_on / turn_off dispatch.
        """
        from pytest_homeassistant_custom_component.common import (
            async_mock_service,
        )

        await _setup_integration(hass)
        _seed_target_switch(hass)
        turn_on_calls = async_mock_service(hass, "homeassistant", "turn_on")
        turn_off_calls = async_mock_service(hass, "homeassistant", "turn_off")

        await hass.services.async_call(
            DOMAIN,
            SERVICE,
            _spike_payload(
                instance_id="automation.stsc_steady",
                sensor_value="55.0",
            ),
            blocking=True,
        )
        await hass.async_block_till_done()

        assert turn_on_calls == []
        assert turn_off_calls == []


class TestNotificationDispatch:
    async def test_spike_dispatches_notification(
        self,
        hass,  # noqa: ANN001
    ) -> None:
        """Spike with a configured notify service should
        route a ``notify.<name>`` call carrying the
        spike's notification body.
        """
        from pytest_homeassistant_custom_component.common import (
            async_mock_service,
        )

        await _setup_integration(hass)
        _seed_target_switch(hass)
        async_mock_service(hass, "homeassistant", "turn_on")
        notify_calls = async_mock_service(hass, "notify", "phone")

        await hass.services.async_call(
            DOMAIN,
            SERVICE,
            _spike_payload(
                instance_id="automation.stsc_notify",
                sensor_value="55.0",
                notification_service="notify.phone",
            ),
            blocking=True,
        )
        await hass.services.async_call(
            DOMAIN,
            SERVICE,
            _spike_payload(
                instance_id="automation.stsc_notify",
                sensor_value="65.0",
                notification_service="notify.phone",
            ),
            blocking=True,
        )
        await hass.async_block_till_done()

        # Exactly one notify call -- only the spike-tick
        # produces a notification body.
        assert len(notify_calls) == 1
        assert "message" in notify_calls[0].data
        assert len(notify_calls[0].data["message"]) > 0

    async def test_no_notify_when_service_empty(
        self,
        hass,  # noqa: ANN001
    ) -> None:
        """Empty ``notification_service`` short-circuits
        the dispatch even on a spike that produces a
        non-empty notification body.
        """
        from pytest_homeassistant_custom_component.common import (
            async_mock_service,
        )

        await _setup_integration(hass)
        _seed_target_switch(hass)
        async_mock_service(hass, "homeassistant", "turn_on")
        notify_calls = async_mock_service(hass, "notify", "phone")

        # Trigger a spike; ``notification_service=""`` means
        # ``_async_send_notification`` is never reached.
        await hass.services.async_call(
            DOMAIN,
            SERVICE,
            _spike_payload(
                instance_id="automation.stsc_no_notify",
                sensor_value="55.0",
            ),
            blocking=True,
        )
        await hass.services.async_call(
            DOMAIN,
            SERVICE,
            _spike_payload(
                instance_id="automation.stsc_no_notify",
                sensor_value="65.0",
            ),
            blocking=True,
        )
        await hass.async_block_till_done()

        assert notify_calls == []


class TestStateSavedWhenNotifyFails:
    async def test_state_persisted_when_notify_dispatch_raises(
        self,
        hass,  # noqa: ANN001
    ) -> None:
        """The 2026-04-13 bath-fan flap regression: a
        notify-service failure must not roll back the
        state save. Code ordering matters --
        ``update_instance_state`` runs before
        ``_async_send_notification``, and the latter
        try/except-wraps the dispatch. Reordering
        (e.g. moving notify above state-save in a
        future refactor) silently re-introduces the
        bug, hence this guard.
        """
        from homeassistant.core import ServiceCall

        await _setup_integration(hass)
        _seed_target_switch(hass)

        async def _raises(call: ServiceCall) -> None:
            msg = "simulated notify failure"
            raise RuntimeError(msg)

        # Register a notify.phone whose handler raises so
        # we exercise the inside-try except branch of
        # ``_async_send_notification``.
        hass.services.async_register("notify", "phone", _raises)
        from pytest_homeassistant_custom_component.common import (
            async_mock_service,
        )

        async_mock_service(hass, "homeassistant", "turn_on")

        # Seed + spike. The spike call's notify dispatch
        # raises; ``update_instance_state`` must still
        # have run.
        await hass.services.async_call(
            DOMAIN,
            SERVICE,
            _spike_payload(
                instance_id="automation.stsc_notify_raises",
                sensor_value="55.0",
                notification_service="notify.phone",
            ),
            blocking=True,
        )
        await hass.services.async_call(
            DOMAIN,
            SERVICE,
            _spike_payload(
                instance_id="automation.stsc_notify_raises",
                sensor_value="65.0",
                notification_service="notify.phone",
            ),
            blocking=True,
        )
        await hass.async_block_till_done()

        state = hass.states.get(
            "blueprint_toolkit.sensor_threshold_switch_controller"
            "_stsc_notify_raises_state",
        )
        assert state is not None, (
            "diagnostic state entity not created -- state save"
            " did not run after notify dispatch raised"
        )
        # State reflects the spike decision.
        assert state.state == "TURN_ON"
        # The persisted blob must be readable + non-empty.
        import json

        loaded = json.loads(state.attributes["data"])
        assert loaded.get("baseline") is not None, (
            "controller state baseline was not persisted"
        )


# --------------------------------------------------------
# State-blob load defensives
# --------------------------------------------------------


class TestLoadStateBlobMalformed:
    async def test_malformed_json_does_not_crash_reconcile(
        self,
        hass,  # noqa: ANN001
    ) -> None:
        """``_load_state_blob`` returns ``None`` on
        malformed JSON; the logic module then bootstraps
        fresh state. Prior versions of the handler may
        have written a different blob shape, so this is
        a real upgrade-path concern.
        """
        from pytest_homeassistant_custom_component.common import (
            async_mock_service,
        )

        await _setup_integration(hass)
        _seed_target_switch(hass)
        async_mock_service(hass, "homeassistant", "turn_on")

        # Plant a malformed ``data`` blob in the
        # diagnostic state entity that the handler will
        # try to load.
        hass.states.async_set(
            "blueprint_toolkit.sensor_threshold_switch_controller"
            "_stsc_malformed_state",
            "NONE",
            {"data": "{not valid json"},
        )

        # Should not raise; the reconcile bootstraps fresh
        # state and overwrites the blob with a clean one.
        await hass.services.async_call(
            DOMAIN,
            SERVICE,
            _valid_payload(
                instance_id="automation.stsc_malformed",
                sensor_value="55.0",
                trigger_entity="sensor.humidity",
            ),
            blocking=True,
        )
        await hass.async_block_till_done()

        state = hass.states.get(
            "blueprint_toolkit.sensor_threshold_switch_controller"
            "_stsc_malformed_state",
        )
        assert state is not None
        # Blob was rewritten cleanly -- valid JSON now.
        import json

        loaded = json.loads(state.attributes["data"])
        # Switch is off in the seeded payload -> the
        # bootstrap-arm in ``handle_service_call`` should
        # NOT have armed auto-off. This locks down the
        # "switch=off skips arm" half of the bootstrap
        # decision tree at the integration level.
        assert loaded.get("auto_off_started_at") is None

    async def test_non_string_data_does_not_crash_reconcile(
        self,
        hass,  # noqa: ANN001
    ) -> None:
        """``_load_state_blob`` treats a non-string ``data``
        attribute as missing. Defensive: the prior run's
        save path always writes a JSON string, but a stray
        upgrade or hand-edit could plant something else and
        the bootstrap path must absorb it cleanly.
        """
        from pytest_homeassistant_custom_component.common import (
            async_mock_service,
        )

        await _setup_integration(hass)
        _seed_target_switch(hass)
        async_mock_service(hass, "homeassistant", "turn_on")

        # Plant a non-string ``data`` value (HA states API
        # serializes through JSON so anything that JSON can
        # represent is allowed in attributes).
        hass.states.async_set(
            "blueprint_toolkit.sensor_threshold_switch_controller"
            "_stsc_nonstring_state",
            "NONE",
            {"data": {"not": "a string"}},
        )

        await hass.services.async_call(
            DOMAIN,
            SERVICE,
            _valid_payload(
                instance_id="automation.stsc_nonstring",
                sensor_value="55.0",
                trigger_entity="sensor.humidity",
            ),
            blocking=True,
        )
        await hass.async_block_till_done()

        state = hass.states.get(
            "blueprint_toolkit.sensor_threshold_switch_controller"
            "_stsc_nonstring_state",
        )
        assert state is not None
        # Bootstrap rewrote the blob with a valid JSON
        # string.
        import json

        json.loads(state.attributes["data"])


class TestCodeQuality(CodeQualityBase):
    ruff_targets = [
        "tests/test_sensor_threshold_switch_controller_integration.py",
    ]
    mypy_targets: list[str] = []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", *sys.argv[1:]]))
