# Sensor Threshold Switch Controller

## Summary

Controls a switch-like entity based on sensor value spikes, with manual
override protection, auto-off functionality, and notification support.

## Features

- **Threshold-based Control**: Turns a switch ON when sensor values
  spike above a trigger threshold, OFF when they drop below a release
  threshold.
- **Manual Override Protection**: Re-activates the switch if it is
  manually turned off while sensor thresholds are still exceeded.
  Turning the switch off twice within a configurable window disables
  this behavior.
- **Auto-Off Timer**: Automatically turns the switch off after a
  configurable duration when manually activated.
- **Startup Recovery**: If Home Assistant restarts with the switch
  already ON, starts the auto-off timer.
- **Notifications**: Optionally sends notifications for all actions via
  a configurable notification service.

## Requirements

PyScript must be configured with:

```yaml
pyscript:
  allow_all_imports: true
  hass_is_global: true
```

## Usage

1. Go to **Settings > Automations & Scenes > Blueprints**.
2. Find **Sensor Threshold Switch Controller** and click **Create
   Automation**.
3. Configure the required and optional parameters.
4. Save.

The automation will appear in the **Used By** list for all sensor
and switch entities.

## Configuration

### Required

| Parameter | Description |
|---|---|
| **Target Switch Entity** | The switch, fan, light, or input_boolean entity to control (e.g., `switch.bathroom_fan`). |
| **Sensor Entities** | One or more sensors to monitor (e.g., `sensor.bathroom_humidity`). All sensors feed into a shared sampling window. Any sensor spike triggers the switch; all must settle to release. |
| **Trigger Threshold** | Spike amount (max - min in sampling window) to turn the switch ON. Must be positive. |
| **Release Threshold** | Amount above baseline to keep the switch ON. Must be <= trigger threshold. |

### Optional

| Parameter | Default | Description |
|---|---|---|
| **Sampling Window** | 300s | Rolling window (in seconds) for min/max calculation. |
| **Disable Window** | 10s | Time window for double-off detection. Two manual switch-off operations within this window disable the sensor override. Set to 0 to disable. |
| **Auto-Off Timeout** | 30m | Minutes before auto-off after manual switch activation. Set to 0 to disable. |
| **Notification Service** | *(empty)* | Service name for notifications (e.g., `notify` or `notify.mobile_app_phone`). Leave empty to disable. |
| **Notification Prefix** | `STSC: ` | Text prepended to notifications. Supports timestamp tokens (see below). |
| **Notification Suffix** | ` at YYYY-MM-DD HH:mm:ss` | Text appended to notifications. Supports timestamp tokens (see below). |

### Timestamp Tokens

The notification prefix and suffix support these tokens, which are
replaced with the current time when the notification is sent:

`YYYY`, `YY`, `MM`, `DD`, `HH`, `mm`, `ss`

## Usage notes

### Example: Bathroom Fan Control

```
Target Switch Entity:  switch.bathroom_fan
Sensor Entities:       sensor.bathroom_humidity
                       sensor.bathroom_vent_humidity
Trigger Threshold:     10
Release Threshold:     5
Sampling Window:       300 seconds (5 minutes)
Auto-Off Timeout:      30 minutes
```

When someone showers, humidity spikes on one or both sensors and the
fan turns ON. When humidity returns to normal across all sensors, the
fan turns OFF. If someone manually turns off the fan while humidity is
still high, it turns back on. If they manually turn off the fan twice
in a row (within 10 seconds), the sensor override is disabled. If
they turn the fan on manually (with no humidity spike), it turns off
automatically after 30 minutes.

## Developer notes

Three complementary layers provide visibility into the automation's
decisions without requiring ad-hoc instrumentation.

### Entity attributes (always on)

After every invocation, the automation writes decision metadata to the
`pyscript.*_state` entity as attributes. These are visible in
**Developer Tools > States** with no configuration.

| Attribute | Description |
|---|---|
| `last_action` | `TURN_ON`, `TURN_OFF`, or `NONE` |
| `last_reason` | Human-readable reason for the action (or `n/a`) |
| `last_event` | `SENSOR`, `SWITCH`, or `TIMER` |
| `last_run` | ISO timestamp of the invocation |
| `last_sensor` | Parsed sensor value (or `n/a` for non-sensor events) |

To view:

1. Go to **Developer Tools > States**.
2. Search for `pyscript.` and find your `*_state` entity.
3. Expand the attributes to see the latest decision context.

### Debug logging (opt-in)

The blueprint includes a **Debug Logging** toggle (default: off). When
enabled, the service emits a `log.warning` message with full decision
context on every invocation.

To enable:

1. Go to **Settings > Automations & Scenes**.
2. Open the automation using this blueprint.
3. Set **Debug Logging** to on.
4. Save.

To view logs:

- **Settings > System > Logs** -- search for
  `sensor_threshold_switch_controller`.
- Or via SSH: `ha core logs 2>&1 | grep sensor_threshold`.

Example output for an automation named "Main Bath Fan Controller":
```
[STSC: Main Bath Fan Controller] event=TIMER sw=on baseline=None
  auto_off=2026-02-21T15:19:00 samples=5 -> TURN_OFF
  "Auto-off after 1 minute(s)"
```

Uses `log.warning` (not `log.info`) because HA's default log level
for custom components is WARNING. Toggling the flag produces immediate
output without editing `configuration.yaml`.

### Logger configuration (optional)

For more verbose PyScript output without the debug flag, add the
following to `configuration.yaml`:

```yaml
logger:
  default: warning
  logs:
    custom_components.pyscript: info
```

This enables `log.info` level messages from all PyScript scripts.
