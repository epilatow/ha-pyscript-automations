# Development Guide

## Architecture

All automations follow a three-layer architecture:

1. **Blueprint** (`blueprints/.../name.yaml`) — defines HA
   triggers and user-configurable inputs. Calls a pyscript
   service. Contains no logic.
2. **Service wrapper** (`pyscript/ha_pyscript_automations.py`)
   — runs under PyScript's AST evaluator. Has access to
   PyScript-injected globals (`state`, `hass`,
   `homeassistant`, `service`, `log`, etc.). Parses inputs,
   loads/saves state, calls the logic module, and executes
   the returned action. No business logic.
3. **Logic module** (`pyscript/modules/name.py`) — runs
   under PyScript's AST evaluator (not standard Python
   import). Testable with pytest. Does not use PyScript-
   injected globals (cannot call `state.get()`,
   `homeassistant.turn_on()`, etc.). Must follow the
   PyScript AST constraints listed below. May reference
   HA concepts (entity IDs, integration names, device
   classes) as data.

**Execution model**: purely reactive. No sleeping, no
waiting, no scheduling. Trigger fires, logic evaluates,
action executes, exit. Timeouts use the "record timestamp,
check on periodic trigger" pattern.

**State persistence**: service wrappers persist state as
JSON in HA entity attributes (not entity state, which is
limited to 255 chars). Use `_state_key(instance_id)` to
build the persistence key.

## File Conventions

### AI generated code header

All Python files begin with `# This is AI generated code`.

### Shebangs

- **Executable scripts** (test files, `run_all.py`) use the
  PEP 723 shebang `#!/usr/bin/env -S uv run --script` with
  inline dependency declarations.
- **Module files** (`pyscript/modules/*.py`,
  `pyscript/ha_pyscript_automations.py`) have no shebang.
  They are imported, not executed directly.

### Test files

- Must be executable (`chmod +x`) with a `__main__`
  entry point calling `conftest.run_tests()`.
- Use pytest.
- Use `autospec=True` for all mocks.
- Include a `TestCodeQuality(CodeQualityBase)` class
  that specifies `ruff_targets` and `mypy_targets` for
  the module under test.

## Naming Conventions

### Service wrapper inputs

Parameters that get transformed (parsed, cast, or
normalized) use a `_raw` suffix. The parsed local variable
uses the same name without the suffix:

```python
def my_service(
    auto_off_minutes_raw: str,   # transformed
    instance_id: str,            # pass-through
) -> None:
    auto_off_minutes = int(auto_off_minutes_raw)
```

Parameters that pass through unchanged (e.g.,
`instance_id`, `trigger_entity_id`, `notification_service`)
have no suffix.

### Booleans

Use `_parse_bool()` for boolean inputs. Never compare
against strings like `"true"` inline:

```python
# Good
debug_logging = _parse_bool(debug_logging_raw)

# Bad
debug_logging = str(debug_logging_raw).lower() == "true"
```

### Time units

Use full words for time unit suffixes: `_seconds`,
`_minutes`. Never abbreviate to `_s`, `_m`, or `_min`.

### Debug toggle

The debug logging toggle is named `debug_logging` in all
blueprints, service wrappers, and documentation.

### User-facing enum values

User-facing enum values (exposed in blueprints) use dashes:
`"night-time"`, `"day-time"`, `"triggered-on"`,
`"auto-off"`.

## Blueprint Conventions

### Metadata

All blueprints include `author: epilatow` in the blueprint
metadata block.

### Defaults

Parameter defaults are defined only in the blueprint YAML.
Service wrapper functions do not duplicate defaults — the
blueprint is the single source of truth.

### Input validation

Validate all blueprint inputs in the service wrapper and
generate persistent notifications for configuration errors.
See `_validate_entities()` and
`_manage_config_error_persistent_notification()`.

### Entity domain filtering

Use the `domain:` selector in blueprints to restrict entity
selection. Also validate domains at runtime via
`_validate_entities()` with `EntityType.CONTROLLABLE` or
`EntityType.BINARY`.

## Type Annotations

All code has type annotations and mypy strict enforcement:

- **Logic modules** (`pyscript/modules/*.py`) — fully
  typed, checked by mypy strict.
- **Service wrapper** (`pyscript/ha_pyscript_automations.py`)
  — fully typed, with `TYPE_CHECKING` stubs for
  PyScript-injected globals (`state`, `homeassistant`,
  `service`, `log`, `persistent_notification`, `hass`).
- **mypy configuration** lives in `pyproject.toml`. The
  `mypy_path` setting includes `pyscript/modules` so mypy
  can resolve module imports.

## Service Wrapper Conventions

### @service decorator

Service entry points use the `@service` decorator
(PyScript-provided) with `# noqa: F821` since it's not
importable at lint time.

### Debug log tag format

Debug log messages use a tag with abbreviated service
initials and the user-assigned automation name:

```python
auto_name = _automation_name(instance_id)
tag = "[TEC: " + auto_name + "]"
log.warning("%s event=%s ...", tag, ...)
```

Current abbreviations: `STSC` (Sensor Threshold Switch
Controller), `DW` (Device Watchdog), `EDW` (Entity
Defaults Watchdog), `TEC` (Trigger Entity Controller),
`RW` (Reference Watchdog).

## Notifications

Use friendly names (not raw entity IDs) in all user-facing
notification messages. The service wrapper resolves friendly
names via `state.getattr()` and passes them to the
logic module.

## Implementation Details in Code

Implementation details ("how it works" internals) belong in
code comments (e.g., the `evaluate()` docstring), not in
user-facing documentation. User docs should describe what
the automation does and how to configure it.

## Automation Docs

User-facing documentation for each automation lives in
`docs/<automation_name>.md`. Every automation doc follows
the same top-level section order so users find the same
information in the same place across automations:

1. **Summary** — one paragraph describing what the
   automation does.
2. **Features** — bulleted list of capabilities.
3. **Requirements** — prerequisite HA / PyScript config.
4. **Usage** — install + enable steps.
5. **Configuration** — blueprint input table.
6. **Usage notes** — examples, exclusion cheatsheets,
   behavior gotchas, and any user-facing detail that
   doesn't fit under Configuration.
7. **Developer notes** — state attributes, debug log
   format, detection-mechanism internals, known
   limitations, and follow-ups.

User-facing sections come first so users don't have to
scroll past developer notes to find their config.
Developers read the whole file, so the ordering has no
cost for them.

Don't introduce new top-level sections. Anything that
doesn't fit an existing bucket goes under "Usage notes"
(if user-facing) or "Developer notes" (if internal) as a
sub-heading.

## Comments

Do not number steps in comments (e.g., `# 1. Parse state`).
Numbering is unnecessary and adding/removing steps requires
renumbering.

## PyScript AST Constraints

All pyscript files — both the service wrapper
(`ha_pyscript_automations.py`) and logic modules
(`pyscript/modules/*.py`) — run under PyScript's
custom AST evaluator, which has restrictions. Even
though some logic modules are called via
`@pyscript_executor` (native Python in a worker
thread), they may also be imported directly by the
wrapper, so all code must be compatible with the AST
evaluator. These are enforced via the
`TestPyScriptCompatibility` test class:

- No generator expressions (use list comprehensions)
- No `@classmethod` or `@property`
- No `lambda`
- No `yield` / `yield from`
- No `print()` (use `log.warning()`)
- No `match`/`case`
- No `sort(key=func)` or `sorted(key=func)` (PyScript
  wraps function calls as coroutines; use tuple-based
  sorting instead)
- No bare `open()` (use `io.open()`)
- No unquoted `TYPE_CHECKING`-only names in local
  variable annotations. PyScript evaluates function-
  body annotations at runtime, unlike standard Python.
  Either remove the annotation or quote it.

## Testing

Always add new tests when adding new functionality.

All tool configuration lives in `pyproject.toml`. Tool
caches are redirected to `/tmp/` to avoid polluting the
repo.

### No global pip installs

Never run `pip install`. All dependencies are declared
inline via PEP 723 script metadata and resolved
automatically by `uv`. Run tools via `uvx <tool> [args]`.

### Running tests

```bash
# All tests
./tests/run_all.py
./tests/run_all.py --verbose
./tests/run_all.py --coverage

# Single test file
./tests/test_sensor_threshold_switch_controller.py
./tests/test_sensor_threshold_switch_controller.py --verbose
```

### Code quality

Lint, format, and type checks are included in each test
file's `TestCodeQuality` class and run automatically as
part of the test suite:

- `test_ruff_lint` — ruff linting
- `test_ruff_format` — ruff formatting
- `test_mypy_strict` — mypy strict type checking

### PyScript compatibility

Two complementary mechanisms catch PyScript AST
evaluator incompatibilities before deployment:

- **Static scan**
  (`TestPyScriptCompatibility` in
  `test_ha_pyscript_automations.py`) — walks the AST
  of every `pyscript/**/*.py` file and flags known-bad
  patterns (generators, lambda, yield, bare `open()`,
  `sorted(key=func)`, etc.). Fast and gives precise
  file:line errors. When adding a new static ban, add
  a paired negative test in `TestHarnessSanity` (see
  below) to verify the evaluator actually rejects it.

- **Real evaluator**
  (`test_pyscript_eval_compat.py`) — instantiates
  PyScript's actual `AstEval` interpreter and executes
  every top-level statement of every pyscript file.
  Catches issues the static scan can't express (e.g.
  coroutine comparison from `sort(key=func)`, lambda
  closure capture failures). `TestHarnessSanity`
  pairs each static ban with a negative test through
  the real evaluator; if a future pyscript release
  starts accepting a banned construct, the paired
  test fails and signals the ban can be removed.

### Standalone checks

```bash
uvx ruff check .
uvx ruff format .
uvx mypy pyscript/ --strict
```

### Python code style

- Line wrap at 80 characters.
- Be strongly typed. Use dataclasses for structured data,
  not `Dict[str, Any]`.

## Commit Messages

- Use `- component: Summary of change.` format.
- Include a `Co-Authored-By: <AI Model XXX>` trailer for AI-assisted commits.

