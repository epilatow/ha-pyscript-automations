# ha-pyscript-automations

Home Assistant automations built as native HA blueprints that call
[PyScript](https://github.com/custom-components/pyscript) actions.
All business logic lives in Python modules with no PyScript
runtime dependencies, making it fully testable with pytest.

## Automations

- [Sensor Threshold Switch Controller](docs/sensor_threshold_switch_controller.md) -
  Controls a switch based on sensor value spikes (e.g., humidity
  for a bathroom fan). Includes manual override protection,
  double-off disable, and auto-off timer.

- [Device Watchdog](docs/device_watchdog.md) -
  Monitors device health across integrations. Raises persistent
  notifications for unavailable or stale devices, clears them
  automatically on recovery.

- [Entity Defaults Watchdog](docs/entity_defaults_watchdog.md) -
  Detects entity IDs and names that have drifted from their
  defaults. Creates persistent notifications per device with
  repair instructions, clears them automatically when drift
  is resolved.

- [Trigger Entity Controller](docs/trigger_entity_controller.md) -
  Controls entities (lights, switches, fans, etc.) with optional
  trigger-based activation and auto-off timer. Supports
  time-of-day gating, trigger disabling, force-on, and
  configurable notifications.

- [Reference Watchdog](docs/reference_watchdog.md) -
  Scans HA config (YAML includes and `.storage` JSON) for
  broken entity and device references. Per-owner persistent
  notifications with clickable links into the HA config UI
  where available, YAML-only helper marking, and a
  negative service-name truth set to eliminate false
  positives. Also detects source orphans -- registry
  entries whose backing YAML block or UI-helper record
  has been removed -- and lists them in a single summary
  notification for cleanup.

- [Z-Wave Route Manager](docs/zwave_route_manager.md) -
  Reconciles Z-Wave priority routes against a declarative
  YAML config file. Reconciles on HA startup, on manual
  trigger, when the YAML config is edited, and periodically
  (default every 5 minutes) to catch out-of-band route
  changes. Failed reconciles retry automatically on the next
  minute-granularity tick. Auto-resolves route speed to the
  slowest hop's `maxDataRate`. Optional clear-unmanaged mode
  makes the config file the single source of truth. Requires
  the `core_zwave_js` addon.

## Scripts

Standalone diagnostic and inspection tools that ship alongside
the automations. Live in the bundled payload and run from the
HA host.

- [Z-Wave Network Info](custom_components/ha_pyscript_automations/bundled/cli/zwave_network_info.py) -
  Tabular per-node view of the Z-Wave mesh: protocol (Mesh/LR),
  signal-strength quality, configured priority routes, and
  opt-in stat columns (RX/TX counts, drop counts, drop rates,
  RTT, battery, status, neighbors, firmware, etc.). Historical
  columns pull from HA's recorder; current state comes from
  zwave-js-ui. Self-bootstraps a venv on first run; see `--help`
  for the full column list and aliases.

## Prerequisites

- Home Assistant with the
  [PyScript integration](https://github.com/custom-components/pyscript)
  installed
- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (for development)

## Installation

1. Install [PyScript](https://github.com/custom-components/pyscript)
   via HACS if you don't already have it.

2. Add the following to your `configuration.yaml`. The two options
   are required for our pyscript modules to load:

   ```yaml
   pyscript:
     allow_all_imports: true
     hass_is_global: true
   ```

3. In Home Assistant, go to HACS, then the menu in the top right
   and choose **Custom repositories**. Add this repo's URL with
   the **Integration** type.

4. Find **HA PyScript Automations** in HACS and click
   **Download**.

5. Restart Home Assistant.

6. Go to **Settings > Devices & Services > Add Integration** and
   add **HA PyScript Automations**. The integration installs the
   bundled blueprints, pyscript modules, and rendered docs into
   your `/config/` directory.

7. Optionally, open the integration's **Configure** dialog to set
   **CLI symlink directory** if you want the
   `zwave_network_info.py` shell tool symlinked into a directory
   on your `$PATH` (e.g. `/root`). Leave blank to skip it.

8. Go to **Settings > Automations & Scenes > Blueprints** to
   create automations from the installed blueprints.

## Development

See [Development Guide](DEVELOPMENT.md) for architecture, coding
conventions, and testing instructions.
