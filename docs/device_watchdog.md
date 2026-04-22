# Device Watchdog

## Summary

Monitors device health across Home Assistant integrations. Raises
a persistent notification whenever monitored devices have
unavailable entities or stop reporting state within a
configurable window. Clears notifications automatically when
devices recover.

## Features

- Monitor devices across multiple integrations (Z-Wave,
  Matter, BLE, Shelly, etc.)
- Detect unavailable or unknown entity states
- Detect stale devices (no state report within threshold)
- Per-device persistent notifications with auto-clear
  on recovery
- Include/exclude integration filtering (empty include
  means all integrations)
- Regex-based device and entity exclusion filters
- Configurable entity domain filtering
- Configurable check interval and staleness threshold
- Notification cap to limit per-device notifications
- Diagnostic entity check: notifies when recommended
  diagnostic entities (e.g., Last seen, Node status)
  are disabled
- Per-check selection so exclusion lists can be scoped
  per check (instantiate the blueprint once per check)
- Optional debug logging

## Requirements

PyScript must be configured with:

```yaml
pyscript:
  allow_all_imports: true
  hass_is_global: true
```

## Usage

1. Install the automation (see main README)
2. Go to **Settings > Automations & Scenes > Blueprints**
3. Click **Device Watchdog**
4. Configure integrations and thresholds
5. Save and enable

## Configuration

| Parameter | Description |
|---|---|
| Include integrations | Integration IDs to monitor. Empty means all. |
| Exclude integrations | Integration IDs to skip even if included. |
| Device exclude regex | Skip devices whose name matches. One pattern per line. |
| Entity ID exclude regex | Skip entities whose ID matches. One pattern per line. |
| Entity domains to monitor | Only check entities in these domains |
| Check interval (minutes) | Minutes between watchdog evaluations |
| Dead device threshold (minutes) | Staleness threshold for state reports |
| Enabled checks | Which checks to run (`unavailable-entities`, `device-updates`, `disabled-diagnostics`). Empty means all. |
| Max device notifications | Cap on per-device notifications. 0 = unlimited. |
| Debug logging | Log debug info to HA logs |

See the blueprint UI for default values.

## Usage notes

### Notifications

Each device with health issues gets its own persistent
notification. Notifications are automatically dismissed
when devices recover.

### Notification panel ordering

The order of notifications in the HA notification panel
may change between evaluation runs. This is because each
run re-creates all active notifications (to update
content if health changed), which updates their
timestamps. Since all creates happen within milliseconds,
the panel's display order is effectively random. The
same devices are shown -- only the panel ordering varies.

## Developer notes

### Entity attributes

After each evaluation, attributes are written to
`pyscript.<automation-name>_state` (e.g.,
`pyscript.automation_device_watchdog_state`). Search
for `pyscript.*_state` in Developer Tools > States
to find it.

- `last_run`: ISO timestamp of last evaluation
- `runtime`: Evaluation time in seconds
- `integrations`: Total integrations discovered
- `devices`: Total devices discovered
- `entities`: Total entities discovered for included devices
- `integrations_excluded`: Integrations excluded by filters
- `devices_excluded`: Devices excluded by device filters
- `entities_excluded`: Entities excluded by entity filters
- `device_issues`: Devices with issues
- `entity_issues`: Entities with issues
- `device_stale_issues`: Devices flagged as stale

### Debug logging

Enable the **Debug Logging** toggle in the blueprint. Debug
output appears in **Settings > System > Logs**. Uses
`log.warning` level (HA's default for custom components).

Example output for an automation named "Device Watchdog":

```
[DW: Device Watchdog] integrations=12 devices=45
  entities=320 device_issues=2 entity_issues=5
```
