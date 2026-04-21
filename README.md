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
  positives.

## Scripts

Standalone diagnostic and inspection tools that ship alongside
the automations. Live in `scripts/` and run from the HA host.

- [Z-Wave Network Info](scripts/zwave_network_info.py) -
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

1. Clone the repo into your HA config directory:

   ```bash
   cd /config
   git clone <repo-url> ha-pyscript-automations
   ```

2. Run the install script:

   ```bash
   /config/ha-pyscript-automations/scripts/install.sh /config
   ```

   This creates symlinks for the PyScript modules, services, and
   blueprints into the correct HA directories.

3. Restart Home Assistant (or reload the PyScript integration).

4. Go to **Settings > Automations & Scenes > Blueprints** to create
   automations from the installed blueprints.

## Development

See [Development Guide](DEVELOPMENT.md) for
architecture, coding conventions, and testing instructions.
