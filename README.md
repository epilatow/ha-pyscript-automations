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

All tool configuration lives in `pyproject.toml`. Tool caches are
redirected to `/tmp/` to avoid polluting the repo.

### Automation architecture:

- **Purely reactive execution.** No sleeping, no waiting, no
  scheduling. Trigger fires, logic evaluates, action executes, exit.
- **Three layers:** Blueprint (triggers) ->
  ha_pyscript_automations.py (thin wrapper) -> modules/*.py
  (pure testable logic).
- **Pure logic modules** have zero PyScript/HA dependencies, receive
  `current_time` as an input (never call `datetime.now()`), and are
  fully testable with pytest.
- **Thin service wrappers** only parse inputs, load/save state, call
  the controller, and execute the returned action. No business logic.
- **Timeouts** use the "record timestamp, check on periodic trigger"
  pattern. PyScript never sleeps or schedules.

### **No global pip installs. Use uv for everything.**

- Never run `pip install` (globally or otherwise). All dependencies
  are declared inline via PEP 723 script metadata and resolved
  automatically by `uv`.
- All Python files have a `uv run --script` shebang and a PEP 723
  `# /// script` metadata block declaring their dependencies, so
  they can be run directly (e.g., `./tests/test_sensor_threshold_switch_controller.py`).
- Run tools (ruff, mypy, etc.): `uvx <tool> [args]`

### Python code:

- Be consistent in form and layout with existing code in the repo.
- Line wrap at 80 characters.
- Be ruff and mypy compliant (enforced via tests in each test file).
- Be strongly typed. Avoid `Dict[str, Any]` for structured data;
  use dataclasses or typed dicts instead.
- Tests use pytest. When mocking, always use `autospec=True`.
- Ensure all tests pass and no warnings are generated.

### Run Tests

```bash
# All tests (discovers and runs every test_*.py file)
./tests/run_all.py
./tests/run_all.py --verbose
./tests/run_all.py --coverage

# Single test file
./tests/test_sensor_threshold_switch_controller.py
./tests/test_sensor_threshold_switch_controller.py --verbose
./tests/test_sensor_threshold_switch_controller.py --coverage
```

Lint, format, and type checks are included in each test file's
`TestCodeQuality` class and run automatically as part of the test
suite.

### Lint, Format, and Type Checks (standalone)

```bash
uvx ruff check .
uvx ruff format .
uvx mypy pyscript/modules/ --strict
```
