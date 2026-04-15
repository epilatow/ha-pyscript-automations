# Entity Defaults Watchdog

## Summary

Detects entity IDs and names that have drifted from their
defaults. Creates a persistent notification per device
with repair instructions. Clears notifications automatically
when drift is resolved.

## Features

- Detect entity ID drift (when a device is renamed after
  its entities already exist)
- Detect name-override drift (stale name overrides left
  behind when an integration's naming conventions change)
- Detect redundant name prefixes (overrides that include
  the device name when HA would add it automatically)
- Per-device persistent notifications with auto-clear
  on drift resolution
- Selectable drift checks (ID only, name only, or both)
- Include/exclude integration filtering
- Regex-based device and entity exclusion filters
- Notification cap to limit per-device notifications
- Repair instructions tailored to the drift combination
  (two-cycle fix for name + ID drift)
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
3. Click **Entity Defaults Watchdog**
4. Configure integrations and exclusions
5. Save and enable

## Configuration

| Parameter | Description |
|---|---|
| Drift checks | Which checks to run (ID, name, or both). Empty means all. |
| Include integrations | Integration IDs to check. Empty means all integrations. |
| Exclude integrations | Integration IDs to skip even if included. |
| Device exclude regex | Skip devices whose name matches. One pattern per line. |
| Exclude entities | Specific entities to exclude from all checks. |
| Entity ID exclude regex | Skip entities whose ID matches. One pattern per line. |
| Entity name exclude regex | Skip entities whose name matches. One pattern per line. |
| Check interval (minutes) | Minutes between drift evaluations. |
| Debug Logging | Log debug info to HA logs. |

See the blueprint UI for default values.

## Usage notes

### What is entity drift?

Home Assistant assigns entity IDs and names when entities
are first created. These can drift from their expected
values over time:

- **ID drift** -- when a device is renamed after its
  entities already exist, the entity IDs still reflect
  the original device name. For example, renaming
  "Kitchen Multisensor" to "Kitchen Sensor" leaves
  `sensor.kitchen_multisensor_temperature` instead of
  `sensor.kitchen_sensor_temperature`.
- **Name drift** -- when an integration changes its
  naming conventions (e.g., during an upgrade), HA
  preserves the old name as a "name override" to avoid
  breaking automations. The override becomes stale when
  the integration's new name is what you actually want.

### How drift is detected

The watchdog uses Home Assistant's entity and device
registries to compare current values against expected
defaults:

- **Entity ID drift**: compares each entity's current ID
  against the value returned by
  `async_regenerate_entity_id`, which computes the ID
  that HA would assign today based on the current device
  name and entity name.
- **Name drift**: checks whether `entry.name` (a name
  override) differs from `entry.original_name` (the
  integration-provided default). A name override exists
  only when someone (or HA's migration logic) has
  explicitly set a custom name.
- **Redundant prefix**: for entities with
  `has_entity_name=True`, HA automatically prepends the
  device name to the entity name. A name override that
  starts with the device name is redundant.

### Two-cycle fix sequence

When a device has both name overrides and non-default
entity IDs, the fix must happen in two steps:

1. **First cycle**: clear or edit name overrides in each
   entity's settings. The watchdog re-evaluates and may
   surface new non-default entity IDs (because clearing
   the name override changes the expected ID).
2. **Second cycle**: use **Recreate entity IDs** on the
   device page repeatedly until no more changes occur.
   Collisions between entity IDs can cause temporary
   numeric suffixes (e.g., `_2`) that resolve on
   subsequent passes as other entities free up their IDs.

The watchdog notification explains this sequence when
both types of drift are present, and will continue to
flag non-default IDs until all collisions are resolved.

### Disabled entities

The watchdog only checks enabled entities. Disabled
entities (e.g., diagnostic entities disabled by their
integration) may also have drifted IDs or names but
are not flagged. When you use **Recreate entity IDs**
on a device page, HA will also rename disabled entities,
so the number of renames shown may be higher than what
the watchdog reported.

### Notification format

Each device with drift gets its own persistent
notification. The notification body contains up to three
sections depending on what kind of drift was found:

**Name overrides to clear** -- entities where the name
override should be removed:

```
- `sensor.kitchen_temp`: "Old Temp"
```

**Name overrides with redundant device name** -- the
override includes the device name, which HA already
adds automatically:

```
- `sensor.kitchen_co2`: "Kitchen Sensor CO2" -> "CO2"
```

**Non-default entity IDs** -- entities whose ID does
not match what HA would assign today:

```
- `sensor.old_kitchen_temperature`
```

### Notification panel ordering

The order of notifications in the HA notification panel
may change between evaluation runs. This is because each
run re-creates all active notifications (to update
content if drift changed), which updates their
timestamps. Since all creates happen within milliseconds,
the panel's display order is effectively random. The
same devices are shown — only the panel ordering varies.

### Exclusion configuration

Use exclusion settings to suppress drift notifications
for entities with intentionally customized names or IDs:

- **Exclude entities**: pick specific entity IDs from
  a list.
- **Entity ID exclude regex**: match entity IDs by
  pattern (e.g., `battery` to skip all battery
  entities).
- **Entity name exclude regex**: match entity names by
  pattern.
- **Device exclude regex**: skip entire devices by
  name.

## Developer notes

### Entity attributes

After each evaluation, attributes are written to
`pyscript.<automation-name>_state` (e.g.,
`pyscript.automation_entity_defaults_watchdog_state`).
Search for `pyscript.*_state` in Developer Tools >
States to find it.

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
- `entity_name_issues`: Entities with name drift
- `entity_id_issues`: Entities with ID drift

### Debug logging

Enable the **Debug Logging** toggle in the blueprint.
Debug output appears in **Settings > System > Logs**.
Uses `log.warning` level (HA's default for custom
components).

Example output for an automation named
"Entity Defaults Watchdog":

```
[EDW: Entity Defaults Watchdog] integrations=12
  devices=45 entities=320 device_issues=2
  entity_issues=5
```
