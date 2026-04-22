# Reference Watchdog

## Summary

Scans your Home Assistant configuration for broken entity
and device references. Every automation, script, template
helper, config-entry helper, and dashboard is checked
against the live entity registry and device registry.
Each source (automation, script, dashboard, helper, YAML
entry) that holds a broken reference gets its own
persistent notification with a direct link into HA's UI
where available, and repair hints when the source can
only be edited by hand. Notifications are cleared
automatically when the broken references are fixed.

Also detects *source orphans* -- entity-registry entries
whose backing YAML block or UI-helper record has been
removed or renamed, leaving the registry entry behind.
Those are surfaced in a single summary notification with
links to each orphan's integration-filtered entities
page for deletion.

## Features

- Scans automations, scripts, template helpers, config
  entry helpers, lovelace dashboards, and every other
  YAML file reachable via `!include` directives from
  `configuration.yaml` through a single generic-YAML
  catch-all adapter
- Per-owner persistent notifications with clickable
  URLs into the HA config UI where possible (automation
  editor, script editor, helpers page, dashboard path)
- YAML-only helpers are flagged in the notification
  body with a "YAML-only, edit `<file>`" note so users
  know to open their editor rather than waiting for a
  broken link
- Three complementary detection mechanisms (structural
  walk, Jinja AST, string sniff) with a service-name
  negative truth set to eliminate false positives
- Optional detection of references to disabled-but-
  existing entities (toggleable from the blueprint)
- Detection of **source orphans** -- registry entries
  whose backing YAML or UI-helper record has been
  removed -- with a single summary notification linking
  each entry to its filtered entities page for deletion
- Unified entity exclusion list that applies to both
  sides (source owner and target value)
- Source integration exclusion for bulk-silencing of
  specific config-entry integrations
- Path-glob exclusion for skipping whole files or
  dashboards
- Notification cap to limit the number of per-owner
  notifications
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
3. Click **Reference Watchdog**
4. Configure exclusions and cap
5. Save and enable

## Configuration

| Parameter | Description |
|---|---|
| Exclude paths | File-path globs to skip. Matched against the source's relative path (e.g. `plants.yaml`, `.storage/lovelace.old_dashboard`). Use this for legacy-YAML integrations not reachable by integration exclusion. |
| Exclude integrations | Integrations to skip. Matches the `Integration:` line shown at the top of each notification -- anything rendered there can be silenced here. Built-in adapters (`automation`, `script`, `template`, `customize`, `lovelace`) are listed as quick-picks in the blueprint UI; config-entry integration domains (e.g. `group`, `homekit`, or whatever is installed in HA) can be added as custom values. Legacy YAML integrations that don't register entities (e.g. `plant`) have no `Integration:` line and **aren't filterable here** -- use `Exclude paths` for those. |
| Exclude entities | Entities to exclude, applied symmetrically to source and target sides. Also silences source-orphan findings. |
| Exclude entity regex | Multi-line regex, matched against entity and device reference values, applied symmetrically to source and target sides. Also silences source-orphan findings. |
| Check disabled entities | When enabled, references to entities that exist in the registry but are disabled are reported as "Disabled-but-existing references". |
| Check interval (minutes) | Minutes between reference-integrity evaluations (default 60 -- reference scans do more file I/O than the other watchdogs). |
| Max source notifications | Per-owner notification cap. 0 = unlimited. The source-orphan summary is always emitted separately and is not subject to this cap. |
| Debug logging | Log a warning-level stat line on every evaluation. |

See the blueprint UI for default values.

## Usage notes

### Exclusion cheatsheet

Three exclusion axes, each for a specific purpose:

| Want to silence | Use |
|---|---|
| A specific file (e.g. `plants.yaml`, an old dashboard) | `exclude_paths` |
| All config entries from an integration (e.g. `hacs`) | `exclude_integrations` |
| A specific entity ID you don't want flagged | `exclude_entities` |
| A family of entity IDs matching a pattern | `exclude_entity_regex` |

**Rule of thumb:** by file -> paths. By config entry domain -> integrations. By entity -> entities.

### Owner attribution

Every finding is attributed to an **owner** -- the
automation, script, config block, template entity,
dashboard, or generic YAML entry that holds the broken
reference. Notifications are one per owner with a header
that includes:

- An `Owner:` line identifying the owner by
  ``config-block[N][.subkey[M]?]`` position in its
  file, optionally suffixed with ``- <friendly-name>``
  when a human name is available
- An `Entity:` line with the registered entity ID
  when one exists
- An `Integration:` line when the owner belongs to an
  adapter that knows its integration (automation,
  script, template, customize, lovelace, or a config
  entry domain)
- A `File:` line with the source path

Block-path format:

- Top-level YAML list item (automations, template
  config blocks): `config-block[N]`
- Top-level YAML dict entry (scripts, customize,
  plants, utility meters): `config-block[N]` using
  dict insertion order
- Sub-key list item inside a template config block:
  `config-block[N].<subkey>[M]` (e.g.
  `config-block[0].sensor[1]`,
  `config-block[0].trigger[0]`)
- Sub-key dict inside a template config block (only
  `variables:` today): `config-block[N].variables`
- JSON-backed sources (`.storage/*`): no block path --
  these aren't hand-edited

Owner type -> URL target:

| Owner | URL |
|---|---|
| Automation | `/config/automation/edit/<id>` |
| Script | `/config/script/edit/<id>` |
| Config entry | `/config/entities/?config_entry=<entry_id>` |
| Dashboard | `/<url_path>` from the dashboards index |
| Template entities & blocks, customize entries, generic YAML, plants, utility meters | **no URL** -- edit the file directly |

### YAML-only helpers

Some helpers are visible in HA's **Settings > Helpers**
page but can only be edited via YAML -- typically when
they're defined in a YAML block like `utility_meter:
!include utility_meters.yaml` rather than through the
HA UI's config flow. The watchdog detects these by
checking the entity registry's `config_entry_id` field:
entries with `config_entry_id: null` are YAML-only.

When an owner is YAML-only, its notification body
includes a note like:

```
Entity: `sensor.air_filters_energy_monthly`
  (YAML-only, edit `utility_meters.yaml`)
```

No clickable URL is generated because HA has no edit
page for these helpers. Open the YAML file in your
editor and fix the reference there, then reload the
integration or restart HA.

### Plants and other legacy YAML integrations

Some legacy YAML integrations -- notably `plant` -- don't
register their entities in the entity registry at all,
so `exclude_integrations: plant` has no effect on them.
To silence plant findings:

- Preferred: `exclude_paths: plants.yaml` -- kills the
  whole scanner for that file
- Alternative: `exclude_entity_regex: '^sensor\.plant_sensor_'`
  -- narrower, keeps scanning the file but suppresses
  specific broken sensor prefixes

### Source orphans

A *source orphan* is a registry entry whose backing
YAML block (or UI-helper storage record) has been
removed or renamed, leaving the registry entry behind.
These entries are invisible to the broken-reference
scan because they still resolve -- the dead entity is
still in `entity_ids` -- but nothing currently creates
them.

The watchdog emits a single summary notification titled
"Reference watchdog: source orphans (N)". Orphans are
grouped by `platform` (e.g. `utility_meter`,
`input_boolean`, `automation`); larger groups are shown
first. Disabled entities are tagged *(disabled)* next
to the link.

Each orphan links to
`/config/entities?domain=<platform>` -- HA's entities
page filtered to that integration's rows. Find your
orphan in the narrowed list, click it to open the
settings dialog, and click Delete.

HA's entities page doesn't support filtering to a
single `entity_id` via URL params (`?search=` isn't
wired up) and there's no direct entity-settings URL,
so the integration filter is the closest one-click
landing available today.

The detector restricts to registry entries with
`config_entry_id = null` -- entries managed via the HA
config flow are never flagged. The `pyscript` platform
is excluded unconditionally because pyscript-created
entities live in runtime state and don't have a
file-based definer.

To silence specific findings:

| Want to silence | Use |
|---|---|
| A single orphan you want to keep | `exclude_entities` |
| A family of orphans matching a pattern | `exclude_entity_regex` |

There's no per-platform silencing toggle -- the full
set of known UI-helper storage files
(`input_boolean`, `input_number`, `input_text`,
`input_select`, `input_datetime`, `input_button`,
`counter`, `timer`, `person`, `zone`, `schedule`,
and the less-common `automation` / `script` /
`scene` / `group` storage records) is loaded
unconditionally on every run. Missing files are
silently skipped.

### Notification panel ordering

The order of notifications in the HA notification panel
may change between evaluation runs. Each run re-creates
active notifications (to update content if findings
changed), which updates their timestamps. Since all
creates happen within milliseconds, the panel's display
order is effectively random. The same owners are shown
-- only the panel ordering varies.

## Developer notes

### Detection mechanisms

Three complementary mechanisms run in parallel over
every parsed source tree. The module-level docstring
in `pyscript/modules/reference_watchdog.py` documents
the strategy in detail; the summary below is for
quickly sanity-checking the stat attributes.

1. **Structural walk.** Dict keys in `_ENTITY_KEYS`
   (`entity`, `entity_id`, `entities`, `source`,
   `target_entity`, ...) emit entity references
   directly. Dict keys in `_DEVICE_KEYS` emit
   device references, validated against a
   32-char-lowercase-hex regex to filter non-HA
   device identifiers (mobile-app UDIDs, DLNA UPnP
   UUIDs, `/dev/` serial paths).
2. **Jinja AST extraction.** Any string leaf containing
   `{{` or `{%` is parsed as a Jinja template. Constant
   string literals (`states('sensor.foo')`) and
   attribute chains (`states.sensor.foo`) are extracted
   and validated. Non-constant expressions
   (`states('sensor.' ~ name)`) are intentionally
   skipped.
3. **String sniff.** String leaves that are neither
   under a known `_ENTITY_KEYS` position nor inside a
   `_SERVICE_KEYS` subtree are checked against the
   entity-id regex with a domain filter. Catches
   blueprint inputs where the parent key name is
   custom (`controlled_entities`, `notification_service`).

### Service-name negative truth set

HA service names and entity IDs share the same
`domain.name` shape -- `light.turn_on` is a service,
`light.kitchen` is an entity. The string sniff can't
distinguish them by syntax alone.

The wrapper pulls HA's service registry
(`hass.services.async_services()`) into
`TruthSet.service_names`. When a sniff-emitted
reference matches a service name, the finding is
dropped before it becomes a notification (tracked as
`refs_service_skipped` for coverage reporting). Without
this backstop, every `notification_service:
notify.mobile_app_*` blueprint input would surface as a
broken-entity false positive.

### Entity attributes

After each evaluation, attributes are written to
`pyscript.<automation-name>_state` (e.g.,
`pyscript.automation_reference_watchdog_state`). Search
for `pyscript.*_state` in **Developer Tools > States**
to find it.

| Attribute | Meaning |
|---|---|
| `last_run` | ISO timestamp of the most recent successful evaluation |
| `runtime` | Wall-clock seconds the evaluation took |
| `paths_included` | Source files actually scanned (after `exclude_paths` filtering) |
| `paths_excluded` | Source files skipped by `exclude_paths` |
| `owners_total` | Total owners discovered across scanned sources (including owners with zero refs) |
| `owners_with_refs` | Owners where at least one reference was detected |
| `owners_without_refs` | Owners scanned but no references detected -- surfaces detection gaps |
| `owners_with_issues` | Owners with at least one broken-or-disabled finding |
| `total_findings` | Broken-or-disabled findings across all owners |
| `broken_entity_count` | Findings where the target entity is missing from the registry + states |
| `broken_device_count` | Findings where the target device ID is missing |
| `disabled_entity_count` | Findings where the target exists but is disabled (only populated when `check_disabled_entities = true`) |
| `refs_total` | All references detected (valid + broken + disabled) |
| `refs_structural` | References found via the `_ENTITY_KEYS` structural walk |
| `refs_jinja` | References found via the Jinja AST extraction pass |
| `refs_sniff` | References found via the string sniff pass |
| `refs_service_skipped` | Sniff hits dropped by the service-name negative truth set |
| `source_orphan_count` | Source orphans detected this run |
| `source_orphan_candidates` | Registry entries eligible for orphan evaluation (`config_entry_id=null`, platform not in the runtime-excluded list). `source_orphan_count` is always a subset. |

**Invariants:**

- `owners_total = owners_with_refs + owners_without_refs`
- `owners_with_issues is a subset of owners_with_refs`
- `total_findings = broken_entity_count + broken_device_count + disabled_entity_count`
- `refs_total = refs_structural + refs_jinja + refs_sniff`
  (service-skipped sniff hits are not counted)
- `paths_included + paths_excluded = paths considered`

### Source-orphan detection

The detector runs after the main reference scan and
reuses the parsed YAML tree already produced during
discovery from `configuration.yaml`. An entity is
classified as a source orphan when:

1. Its registry entry has `config_entry_id = null`.
2. Its `platform` is not in the runtime-excluded set
   (`pyscript`).
3. Neither its object_id (portion after the dot) nor
   its unique_id appears as a **lowercased string**
   in the platform-appropriate **definer pool**.

The definer pool is platform-scoped so that consumer-
side mentions can't hide orphans:

| Platform | Pool |
|---|---|
| `automation` | `automations.yaml` + `.storage/automation` |
| `script` | `scripts.yaml` + `.storage/script` |
| `template` | `template.yaml` + generic YAML |
| Everything else | Generic YAML + matching `.storage/<helper>` file |

Where "generic YAML" is every YAML file reachable from
`configuration.yaml` via `!include` *except*
`customize.yaml`, `automations.yaml`, `scripts.yaml`,
and `template.yaml` (which have dedicated pools above).
`customize.yaml` is never a definer -- it's an overlay,
and treating it as a definer would mask orphans that
are still being customized.

The `.storage/<helper>` file set is a closed list of
known UI-helper storage files. Adding a new HA-core
helper that uses a `.storage/<name>` file means adding
its filename to `_STORAGE_HELPER_DEFINER_FILES` in
`pyscript/modules/reference_watchdog.py`. A missing
entry produces systematic false positives for that
platform; an extra entry that doesn't exist on a given
host is a no-op.

Each pool is populated by walking the **parsed** YAML
or JSON tree of every contributing file and harvesting:

- every mapping **key** (strings only, lowercased)
- every **value** whose key is in `_DEFINER_ID_KEYS`
  (`id`, `unique_id`, `object_id`)

Walking the parsed tree -- instead of tokenizing raw
text -- is deliberate. Comments are already stripped
by the YAML parser, so a stale `# old id:` comment in
an unrelated file cannot contribute to the pool.
Free-text fields like `description:`, `alias:`, or
`friendly_name:` are also ignored, because their keys
are not in `_DEFINER_ID_KEYS`. That stops a lingering
"old name" reference in an automation `description`
from falsely marking the dead entity as defined.

Membership is exact-string, not substring. The YAML
key `garage_central_heater_energy_daily` is harvested
as a single string; looking up the shorter object_id
`central_heater_energy_daily` returns no hit, so the
orphan (left over from a rename) is correctly flagged.

Identifier values are also kept verbatim, so non-slug
unique_ids (e.g. MAC-style `aa:bb:cc:dd:ee:ff` stored
as `"id": "aa:bb:cc:dd:ee:ff"` in `.storage/person`)
are matched directly without being split into tokens.

This works reliably because every HA platform that
stores definitions in YAML or `.storage` lays the
identifier down as either a mapping key or an
identifier-field value:

- `utility_meter` YAML uses the top-level dict key as
  the object_id (and as a prefix of the unique_id --
  e.g. object_id `central_heater_energy_daily`,
  unique_id `central_heater_energy_daily_single_tariff`).
  The YAML key is harvested.
- `input_*`, `counter`, `timer`, `person` UI helpers
  store `"id": "<value>"` in their `.storage/<helper>`
  file, and that value equals the registry unique_id.
- `template` entities have their `unique_id` written
  verbatim in YAML, and the `name:` slug equals the
  object_id (and the YAML dict key structure includes
  the object_id for legacy-style templates).
- `automation` entries in `automations.yaml` have
  `id: <value>` matching the registry unique_id.

There is no toggle to disable source-orphan detection
globally. Silence noisy entries via `exclude_entities`
(exact match) or `exclude_entity_regex` (pattern); both
inputs apply symmetrically to broken references and
source orphans.

**Known limitations:**

- Object_ids derived by slugifying a scene/group
  `name:` (not written verbatim in the YAML) are not
  harvested. A `scene:` block without an explicit
  `id:` will appear as a false positive. Fix by giving
  the scene an explicit `id:` (recommended), or by
  adding the entity to `exclude_entities`.
- An integration that lays its definer identifier
  down under a key other than `id`, `unique_id`, or
  `object_id` will false-positive. None of the core HA
  integrations do this today, but custom integrations
  might.

### Debug logging

Enable the **Debug logging** toggle in the blueprint.
Debug output appears in **Settings > System > Logs**.
Uses `log.warning` level (HA's default for custom
components).

Example output for an automation named
"Reference Watchdog":

```
[RW: Reference Watchdog] owners=338 with_issues=43
  findings=85 refs=819 (struct=641 jinja=59 sniff=119
  svc_skipped=13) orphans=9/184
```

`orphans=9/184` means 9 of 184 orphan-eligible registry
entries were flagged as source orphans.

### Known limitations

Documented gaps that won't be fixed in v1 without a
design change:

- **Runtime-computed entity IDs** embedded as string
  literals inside a YAML scalar (e.g. a multi-line
  Python-list-literal `monitored_automations:
  "['automation.foo', 'automation.bar', ...]"` that's
  consumed via a runtime `in` check) aren't caught.
  Neither the sniff nor the Jinja AST pass matches
  substrings inside non-template strings. Catching
  them would require a regex fallback that introduces
  false positives in comments and descriptions -- we
  intentionally draw the line at "constant strings we
  can prove statically."
- **`!include` content substitution** is not performed
  -- `!include`/`!include_dir_*`/`!secret`/`!env_var`
  tags are replaced by opaque placeholder strings in
  the parsed tree. However, `!include` and
  `!include_dir_*` targets are followed recursively to
  discover and scan the referenced files as their own
  sources.

### Follow-ups

Features worth adding in a later pass:

- **Label and area reference validation.** Walk
  `label_id:`/`labels:`/`area_id:`/`areas:` keys and
  validate against the respective registries.
- **Per-view dashboard attribution.** Drill down
  inside lovelace configs to attribute findings to the
  specific view rather than the whole dashboard.
- **File Editor integration URLs.** If the File Editor
  addon URL pattern can be constructed deterministically,
  generate clickable "open in editor" links for
  YAML-only owners.
