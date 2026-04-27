# Entity Defaults Watchdog

## Summary

Detects entity IDs and names that have drifted from their
defaults. Covers both device-attached entities (one
notification per drifted device) and deviceless entities
like automations, scripts, helpers, and template sensors
(one aggregate notification).  Clears notifications
automatically when drift is resolved.

## Features

- Detect device entity ID drift (when a device is renamed
  after its entities already exist)
- Detect device name-override drift (stale name overrides
  left behind when an integration's naming conventions
  change)
- Detect deviceless entity ID drift -- automations,
  scripts, helpers, template sensors, scenes, groups, and
  other user-named entities whose IDs no longer match
  their current names
- Detect stale HA collision suffixes (`_2`, `_3`, ...) left
  over from naming conflicts whose original peers were
  removed
- Detect redundant name prefixes (overrides that include
  the device name when HA would add it automatically)
- Per-device persistent notifications with auto-clear on
  drift resolution; single aggregate notification for
  deviceless entities
- Selectable drift checks (device entity ID, device
  entity name, deviceless ID, or any combination)
- Include/exclude integration filtering
- Regex-based device and entity exclusion filters
- Notification cap to limit per-device notifications
- Repair instructions tailored to the drift combination
  (two-cycle fix for device name + ID drift)
- Optional debug logging

## Usage

1. Install the automation (see main README)
2. Go to **Settings > Automations & Scenes > Blueprints**
3. Click **Entity Defaults Watchdog**
4. Configure integrations and exclusions
5. Save and enable

## Configuration

| Parameter | Description |
|---|---|
| Drift checks | Which checks to run: `device-entity-id`, `device-entity-name`, `entity-id` (deviceless), or any combination. Empty means all. |
| Include integrations | Integration IDs to check. Empty means all integrations. Applies to device-backed entities and to registry-backed deviceless entries (e.g. `template`, `rachio`). State-only entities have no platform to filter on. |
| Exclude integrations | Integration IDs to skip even if included. Same scope as Include integrations. |
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

- **Device entity ID drift** -- when a device is renamed
  after its entities already exist, the entity IDs still
  reflect the original device name. For example,
  renaming "Kitchen Multisensor" to "Kitchen Sensor"
  leaves `sensor.kitchen_multisensor_temperature` instead
  of `sensor.kitchen_sensor_temperature`.
- **Device entity name drift** -- when an integration
  changes its naming conventions (e.g., during an
  upgrade), HA preserves the old name as a "name
  override" to avoid breaking automations. The override
  becomes stale when the integration's new name is what
  you actually want.
- **Deviceless ID drift** -- automations, scripts,
  helpers, template sensors, and similar user-named
  entities have entity IDs derived from their name at
  creation. Renaming the entity later doesn't update the
  entity ID, leaving (for example) `automation.foo`
  still running after you renamed its alias to "Bar".

### How drift is detected

The watchdog uses Home Assistant's entity and device
registries to compare current values against expected
defaults:

- **Device entity ID drift**: compares each device-
  attached entity's current ID against the value returned
  by `async_regenerate_entity_id`, which computes the ID
  that HA would assign today based on the current device
  name and entity name.
- **Device entity name drift**: checks whether
  `entry.name` (a name override) differs from
  `entry.original_name` (the integration-provided
  default). A name override exists only when someone (or
  HA's migration logic) has explicitly set a custom name.
- **Redundant prefix**: for entities with
  `has_entity_name=True`, HA automatically prepends the
  device name to the entity name. A name override that
  starts with the device name is redundant.
- **Deviceless entity ID drift**: for registry entries
  whose `device_id` is null and whose domain is a
  user-named one (automation, script, scene, group,
  schedule, timer, counter, input helpers,
  sensor/binary_sensor/switch/light), compares
  `slugify(entry.name or entry.original_name)` to the
  entity ID's object part.  Also walks the state list
  for entities in those domains that don't have a
  registry entry (YAML-defined without `unique_id:`) as
  a safety net -- those are caught only when the user has
  set an explicit `name:` whose slug differs from the
  entity ID.  HA's collision-suffix convention (`_2`,
  `_3`, ...) is accepted when at least one peer with the
  un-suffixed (or lower-`_N`) object ID exists;
  otherwise the suffix is classified as stale and
  reported in a dedicated section with a "rename to"
  suggestion.

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

Device-attached drift creates one persistent notification
per device.  The notification body contains up to three
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

Deviceless drift is reported as a single aggregate
notification with up to two sections:

**Entity IDs do not match their names** -- one bullet per
drifted entity, with a pointer to the most useful edit
surface:

```
- `automation.driveway_lights_on_at_sunset` -> expected `automation.auto_on_sunset_lights_sunset_to_8pm`
  [Auto-On: Sunset Lights: Sunset to 8pm](/config/automation/edit/1669687974816)
- `sensor.template_sensor` -> expected `sensor.grid_import_power`
  Grid Import Power  -  integration [template](/config/integrations/integration/template)
- `sensor.old_yaml_thing` -> expected `sensor.grid_import_power`
  Grid Import Power  -  add `unique_id:` to make this entity manageable
```

For automations and scripts the friendly name itself is
the link to that entity's editor.  Registry-backed
entries from other integrations show the friendly name in
plain text followed by the owning integration's name as a
link to its config page -- scan the integration column to
see when several flagged entities share a single source
(e.g. five rows all tagged ` -  integration rachio`) and
can be suppressed together via `exclude_integrations`.
State-only entities (YAML blocks without `unique_id:`)
have no owning integration, so they show a nudge to add
one instead.

**Stale collision suffixes** -- entities whose ID ends
in `_N` (N >= 2) but no un-suffixed or lower-`_N` peer
exists:

```
- `automation.front_porch_light_2` -> rename to `automation.front_porch_light`
  [Front Porch Light](/config/automation/edit/1234567890)
```

### Recommendation: add `unique_id:` to YAML entities

The deviceless check only catches YAML-defined entities
that have an `entry.name` + entity registry entry, which
only exists when the block has a `unique_id:` set.  For
`template:`, `rest:`, `mqtt:`, `command_line:`, etc.,
adding `unique_id:` on every block has two benefits:

- The entity is persisted in the registry, so renames,
  customizations, and cross-restart stability just work.
- The watchdog can compare names to entity IDs and flag
  drift -- without a registry entry, it has no authoritative
  name to compare against.

### Notification panel ordering

The order of notifications in the HA notification panel
may change between evaluation runs. This is because each
run re-creates all active notifications (to update
content if drift changed), which updates their
timestamps. Since all creates happen within milliseconds,
the panel's display order is effectively random. The
same devices are shown -- only the panel ordering varies.

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
- `deviceless_entities`: Deviceless entities scanned
- `deviceless_excluded`: Deviceless entities skipped by
  exclusion filters
- `deviceless_drift`: Deviceless entities flagged as
  drifted (excludes stale-suffix cases)
- `deviceless_stale`: Deviceless entities with stale
  collision suffixes

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
