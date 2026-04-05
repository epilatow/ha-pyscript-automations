# Device Watchdog

Monitors device health across Home Assistant integrations. Raises
a persistent notification whenever monitored devices have
unavailable entities or stop reporting state changes within a
configurable window. Clears notifications automatically when
devices recover.

## Features

- Monitor devices across multiple integrations (Z-Wave, Matter,
  BLE, Shelly, etc.)
- Detect unavailable or unknown entity states
- Detect stale devices (no state change within threshold)
- Per-device persistent notifications with auto-clear on recovery
- Regex-based device and entity exclusion filters
- Configurable entity domain filtering
- Configurable check interval and staleness threshold
- Optional debug logging

## Configuration

| Parameter | Description |
|---|---|
| Integrations to monitor | Integration IDs whose devices should be monitored |
| Device exclude regex | Skip devices whose name matches this pattern |
| Entity exclude regex | Skip entities whose ID matches this pattern |
| Entity domains to monitor | Only check entities in these domains |
| Check interval (minutes) | Minutes between watchdog evaluations |
| Dead device threshold (minutes) | Staleness threshold for state changes |
| Enable debug output | Log debug info to HA logs |

See the blueprint UI for default values.

## Requirements

PyScript must be configured with:

```yaml
pyscript:
  allow_all_imports: true
  hass_is_global: true
```

If `hass_is_global` is not enabled, the automation will create
a persistent notification explaining how to fix the configuration.

## Usage

1. Install the automation (see main README)
2. Go to **Settings > Automations & Scenes > Blueprints**
3. Click **Device Watchdog**
4. Configure integrations and thresholds
5. Save and enable

## Debugging

### Entity attributes

After each evaluation, attributes are written to
`pyscript.<instance_id>_state`:

- `last_run`: ISO timestamp of last evaluation
- `devices_checked`: Number of devices evaluated
- `devices_with_issues`: Number of devices with issues
- `integrations`: JSON list of monitored integrations

View in **Developer Tools > States**.

### Debug logging

Enable the **Debug Logging** toggle in the blueprint. Debug
output appears in **Settings > System > Logs**. Uses
`log.warning` level (HA's default for custom components).

Example output for an automation named "Device Watchdog":

```
[DW: Device Watchdog] checked=12 issues=2
  integrations=['zwave_js', 'matter']
  devices_with_issues=['Kitchen Sensor', 'Garage Door']
```
