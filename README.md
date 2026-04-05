# ha-pyscript-automations

Home Assistant automations built as native HA blueprints that call
[PyScript](https://github.com/custom-components/pyscript) actions.
All business logic lives in pure Python modules with zero HA
dependencies, making it fully testable with pytest.

## Automations

- [Sensor Threshold Switch Controller](docs/sensor_threshold_switch_controller.md) -
  Controls a switch based on sensor value spikes (e.g., humidity
  for a bathroom fan). Includes manual override protection,
  double-off disable, and auto-off timer.

- [Device Watchdog](docs/device_watchdog.md) -
  Monitors device health across integrations. Raises persistent
  notifications for unavailable or stale devices, clears them
  automatically on recovery.

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
