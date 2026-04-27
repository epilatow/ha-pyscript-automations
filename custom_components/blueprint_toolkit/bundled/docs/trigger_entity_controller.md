# Trigger Entity Controller

## Summary

Controls entities (lights, switches, fans, etc.) with optional
trigger-based activation and auto-off timer. Supports
time-of-day restrictions, disabling entities, force-on behavior,
and configurable notifications.

## Features

- Control any entity type (lights, switches, input_booleans, etc.)
- Optional trigger activation from any binary sensor (motion,
  door, occupancy, etc.)
- Auto-off timer that starts when triggers clear (or immediately
  for manual turn-on)
- Time-of-day gating: restrict triggers to day or night only
- Trigger disabling: suppress triggers when a boolean entity is
  on (e.g., "bedroom occupied")
- Auto-off disabling: suppress auto-off for full manual control
  (e.g., when a room is occupied)
- Force-on: re-enable controlled entities if turned off while
  triggers are active
- Configurable notifications for turn-on, force-on, and auto-off
  events
- Entity validation: alerts via persistent notification if
  configured entities are missing or renamed
- Optional debug logging

## Requirements

The Blueprint Toolkit integration must be installed (via HACS or
manual install). No pyscript dependency.

## Usage

1. Install the automation (see main README)
2. Go to **Settings > Automations & Scenes > Blueprints**
3. Click **Trigger Entity Controller**
4. Configure controlled entities and desired options
5. Save and enable

## Configuration

### Required

| Parameter | Description |
|---|---|
| Controlled entities | Entities to turn on and off |

### Optional

| Parameter | Description |
|---|---|
| Auto-off delay | Minutes after triggers clear to turn off. Resets on new trigger. |
| Auto-off disabling entities | When any is "on", suppresses auto-off for full manual control |
| Trigger entities | Binary sensors that trigger activation |
| Trigger period | When triggers activate: always, night-time, day-time |
| Trigger forces on | Re-enable if turned off while trigger active |
| Trigger disabling entities | When any is "on", suppresses triggers |
| Trigger disabling period | When disabling entities are checked |
| Notification service | e.g., "notify.mobile_app_phone" |
| Notification prefix | Prepended to messages (supports timestamp tokens) |
| Notification suffix | Appended to messages (supports timestamp tokens) |
| Notification events | Which actions notify: triggered-on, forced-on, auto-off |
| Debug logging | Log debug info to HA logs |

See the blueprint UI for default values.

## Usage notes

### Example: Hallway Motion Light

A hallway light that always turns on with motion, except at
night when the bedroom is occupied:

- **Controlled entities**: `light.hallway`
- **Auto-off delay**: 2 minutes
- **Trigger entities**: `binary_sensor.hallway_motion`
- **Trigger period**: always
- **Trigger disabling entities**: `input_boolean.bedroom_occupied`
- **Trigger disabling period**: night-time

Result:
- Daytime: motion always turns on the light
- Night + bedroom not occupied: motion turns on the light
- Night + bedroom occupied: motion is suppressed

### Example: Motion Keep-Alive (no auto turn-on)

A device you turn on manually, but you want auto-off to
pause while a room is occupied and resume its countdown
after motion clears. Configure the motion sensor as an
auto-off disabling entity (not a trigger entity), so it
resets the timer without also turning the device on:

- **Controlled entities**: `switch.office_fan`
- **Auto-off delay**: 10 minutes
- **Auto-off disabling entities**: `binary_sensor.office_motion`
- (No trigger entities)

Result:
- Manual on while motion is active: timer stays paused
- Manual on while motion is inactive: timer starts
  immediately
- Motion becomes active mid-countdown: timer is cleared
- Motion clears: fresh 10-minute countdown starts
- Motion returns mid-countdown: timer is cleared again,
  then restarts when motion next clears

This pattern uses `auto_off_disabling_entities` as a
keep-alive: while any listed entity is "on", auto-off is
held off; the timer (re)starts when they all return to
"off".

## Developer notes

### Diagnostic state entity

After each evaluation, a diagnostic state entry is written at
`blueprint_toolkit.trigger_entity_controller_<slug>_state`,
where `<slug>` is the automation's entity_id stripped of its
`automation.` prefix. The state value is `last_action` (NONE,
TURN_ON, or TURN_OFF); attributes:

- `instance_id`: the automation entity_id
- `last_run`: ISO timestamp of last evaluation
- `last_event`: TRIGGER_ON, TRIGGER_OFF, CONTROLLED_ON,
  CONTROLLED_OFF, DISABLING_CHANGED, or TIMER
- `last_reason`: human-readable reason for the decision
- `auto_off_at`: ISO timestamp of pending auto-off, or null

View in **Developer Tools > States** (filter on
`blueprint_toolkit.`), query from templates, or surface on a
dashboard via the entity card.

### Debug logging

Enable the **Debug Logging** toggle in the blueprint. Debug
output appears in **Settings > System > Logs**. Uses
`log.warning` level (HA's default for custom components).

Example output for an automation named "Hallway Motion Light":

```
[TEC: Hallway Motion Light] event=TRIGGER_ON action=TURN_ON
  reason='trigger activated' auto_off_at=none
  trigger_on=True controlled_on=False is_day_time=True
```
