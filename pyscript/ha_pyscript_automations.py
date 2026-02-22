#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
# This is AI generated code
"""PyScript service wrappers.

Thin layer bridging Home Assistant and pure logic modules.
All business logic lives in modules/ and is tested
separately.

IMPORTANT: No sleeping, no waiting. Services are purely
reactive: trigger -> evaluate -> act -> exit.
"""

import json
from datetime import datetime


def _state_key(instance_id: str) -> str:
    """Build persistence key for an automation instance."""
    safe = instance_id.replace(".", "_")
    return f"pyscript.{safe}_state"


def _debug_dict(result, now, sensor_value):
    """Build debug info dict from a ServiceResult."""
    sv = result.sensor_value
    return {
        "last_action": result.action.name,
        "last_reason": result.reason or "n/a",
        "last_event": result.event_type,
        "last_run": now.isoformat(),
        "last_sensor": str(sv) if sv is not None else "n/a",
    }


@service  # noqa: F821
def sensor_threshold_switch_controller(
    instance_id,
    target_switch_entity,
    sensor_value,
    switch_state,
    trigger_entity,
    trigger_threshold,
    release_threshold,
    sampling_window_s,
    disable_window_s,
    auto_off_min,
    notification_service,
    notification_prefix,
    notification_suffix,
    debug="false",
):
    """Evaluate sensor threshold switch controller.

    Called by blueprint-generated automation.
    Purely reactive: evaluate -> act -> exit.
    No sleeping, no waiting.
    """
    from sensor_threshold_switch_controller import (  # noqa: F821
        Action,
        handle_service_call,
    )

    now = datetime.now()

    # 1. Load state from HA entity attribute
    #    (entity state is limited to 255 chars; attributes
    #    have no practical limit)
    key = _state_key(instance_id)
    state_data = None
    try:
        attrs = state.getattr(key)  # noqa: F821
        raw = attrs.get("data", "")
        if raw:
            state_data = json.loads(raw)
    except Exception:
        pass

    # 2. Resolve friendly name
    switch_name = target_switch_entity
    try:
        attrs = state.getattr(  # noqa: F821
            target_switch_entity,
        )
        name = attrs.get("friendly_name", "")
        if name:
            switch_name = name
    except Exception:
        pass

    # 3. Evaluate (pure logic)
    result = handle_service_call(
        state_data=state_data,
        switch_name=switch_name,
        current_time=now,
        target_switch_entity=target_switch_entity,
        sensor_value=sensor_value,
        switch_state=switch_state,
        trigger_entity=trigger_entity,
        trigger_threshold=float(trigger_threshold),
        release_threshold=float(release_threshold),
        sampling_window_s=int(sampling_window_s),
        disable_window_s=int(disable_window_s),
        auto_off_min=int(auto_off_min),
        notification_service=notification_service,
        notification_prefix=notification_prefix,
        notification_suffix=notification_suffix,
    )

    # 4. Execute action
    if result.action == Action.TURN_ON:
        homeassistant.turn_on(  # noqa: F821
            entity_id=target_switch_entity,
        )
    elif result.action == Action.TURN_OFF:
        homeassistant.turn_off(  # noqa: F821
            entity_id=target_switch_entity,
        )

    # 5. Send notification
    if result.notification and result.notification_service:
        parts = result.notification_service.split(".")
        service.call(  # noqa: F821
            parts[0],
            parts[1],
            message=result.notification,
        )

    # 6. Save state + debug attributes to entity
    info = _debug_dict(result, now, sensor_value)
    state.set(key, "ok")  # noqa: F821
    state.setattr(  # noqa: F821
        key + ".data",
        json.dumps(result.state_dict),
    )
    for attr_name, attr_val in info.items():
        state.setattr(  # noqa: F821
            key + "." + attr_name,
            attr_val,
        )

    # 7. Debug logging (opt-in via blueprint)
    #    debug may arrive as bool or string depending on
    #    how HA resolves the blueprint !input tag.
    if str(debug).lower() == "true":
        log.warning(  # noqa: F821
            "[sensor_threshold_switch_controller]"
            " event=%s sw=%s baseline=%s"
            " auto_off=%s samples=%s"
            " -> %s %r",
            info["last_event"],
            switch_state,
            result.state_dict.get("baseline"),
            result.state_dict.get("auto_off_started_at"),
            len(result.state_dict.get("samples", [])),
            info["last_action"],
            info["last_reason"],
        )
