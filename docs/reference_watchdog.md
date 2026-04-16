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
| Scan sources | Source types to scan. Leave empty to scan all. Dedicated types: `automations`, `scripts`, `template`, `customize`, `config_entries`, `lovelace`. The `generic_yaml` catch-all handles every other YAML file reachable via `!include` directives from `configuration.yaml`. |
| Exclude paths | File-path globs to skip. Matched against the source's relative path (e.g. `plants.yaml`, `.storage/lovelace.old_dashboard`). Use this for legacy-YAML integrations not reachable by integration exclusion. |
| Exclude integrations | Integrations to skip. Matches the `Integration:` line shown at the top of each notification — anything rendered there can be silenced here. Built-in adapters (`automation`, `script`, `template`, `customize`, `lovelace`) are listed as quick-picks in the blueprint UI; config-entry integration domains (e.g. `group`, `homekit`, or whatever is installed in HA) can be added as custom values. Legacy YAML integrations that don't register entities (e.g. `plant`) have no `Integration:` line and **aren't filterable here** — use `Exclude paths` for those. |
| Exclude entities | Entities to exclude, applied symmetrically to source and target sides. |
| Exclude entity regex | Multi-line regex, matched against entity and device reference values, applied symmetrically to source and target sides. |
| Check disabled entities | When enabled, references to entities that exist in the registry but are disabled are reported as "Disabled-but-existing references". |
| Check interval (minutes) | Minutes between reference-integrity evaluations (default 60 — reference scans do more file I/O than the other watchdogs). |
| Max source notifications | Per-owner notification cap. 0 = unlimited. |
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

**Rule of thumb:** by file → paths. By config entry domain → integrations. By entity → entities.

### Owner attribution

Every finding is attributed to an **owner** — the
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
- JSON-backed sources (`.storage/*`): no block path —
  these aren't hand-edited

Owner type → URL target:

| Owner | URL |
|---|---|
| Automation | `/config/automation/edit/<id>` |
| Script | `/config/script/edit/<id>` |
| Config entry | `/config/entities/?config_entry=<entry_id>` |
| Dashboard | `/<url_path>` from the dashboards index |
| Template entities & blocks, customize entries, generic YAML, plants, utility meters | **no URL** — edit the file directly |

### YAML-only helpers

Some helpers are visible in HA's **Settings > Helpers**
page but can only be edited via YAML — typically when
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

Some legacy YAML integrations — notably `plant` — don't
register their entities in the entity registry at all,
so `exclude_integrations: plant` has no effect on them.
To silence plant findings:

- Preferred: `exclude_paths: plants.yaml` — kills the
  whole scanner for that file
- Alternative: `exclude_entity_regex: '^sensor\.plant_sensor_'`
  — narrower, keeps scanning the file but suppresses
  specific broken sensor prefixes

### Notification panel ordering

The order of notifications in the HA notification panel
may change between evaluation runs. Each run re-creates
active notifications (to update content if findings
changed), which updates their timestamps. Since all
creates happen within milliseconds, the panel's display
order is effectively random. The same owners are shown
— only the panel ordering varies.

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
`domain.name` shape — `light.turn_on` is a service,
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
| `owners_without_refs` | Owners scanned but no references detected — surfaces detection gaps |
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

**Invariants:**

- `owners_total = owners_with_refs + owners_without_refs`
- `owners_with_issues ⊆ owners_with_refs`
- `total_findings = broken_entity_count + broken_device_count + disabled_entity_count`
- `refs_total = refs_structural + refs_jinja + refs_sniff`
  (service-skipped sniff hits are not counted)
- `paths_included + paths_excluded = paths considered`

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
  svc_skipped=13)
```

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
  false positives in comments and descriptions — we
  intentionally draw the line at "constant strings we
  can prove statically."
- **`!include` content substitution** is not performed
  — `!include`/`!include_dir_*`/`!secret`/`!env_var`
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
