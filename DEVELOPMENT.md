# Development Guide

## Repo layout

This repo ships as a HACS-distributed custom integration.
Two external constraints drive the top-level layout;
everything below that is our own convention.

Home Assistant's custom-integration loader looks for
integrations at `/config/custom_components/<domain>/`,
where `<domain>` matches the integration's `manifest.json`
domain. That's why the repo's installable code lives
under `custom_components/blueprint_toolkit/` --
when HACS (or a developer's manual install) puts that
subtree at `/config/custom_components/blueprint_toolkit/`,
HA finds it.

HACS itself only downloads files from the repo that live
under `custom_components/<name>/`; it discards everything
outside that subtree at install time. So every file we
want users to receive has to live inside
`custom_components/blueprint_toolkit/`.

Integration code (`__init__.py`, `manifest.json`,
`reconciler.py`, `installer.py`, `config_flow.py`,
`repairs.py`, etc.) lives at the
`custom_components/blueprint_toolkit/` level. The
`bundled/` subdirectory is our own convention for the
blueprints, pyscript modules, docs, and CLI script the
installer ships to their user-visible `/config/...` paths
via symlinks.

```
custom_components/blueprint_toolkit/bundled/
    blueprints/automation/blueprint_toolkit/*.yaml
    cli/zwave_network_info.py
    docs/*.md
    pyscript/blueprint_toolkit.py
    pyscript/modules/*.py
```

The repo root keeps three committed symlinks into the
bundle for path-typing convenience and so existing test
paths keep resolving:

```
blueprints -> custom_components/blueprint_toolkit/bundled/blueprints
pyscript   -> custom_components/blueprint_toolkit/bundled/pyscript
docs       -> custom_components/blueprint_toolkit/bundled/docs
```

These are real git symlinks (mode 120000). They live
outside HACS's download path and are never shipped to
users. When referring to files, either path works --
`pyscript/modules/foo.py` and
`custom_components/blueprint_toolkit/bundled/pyscript/modules/foo.py`
resolve to the same file.

**POSIX filesystem required for development.** The repo
uses mode-120000 symlinks that Windows (with default git
settings) checks out as text files containing the target
path, which breaks tests, install scripts, and deploys.
Use macOS or Linux; WSL2 works too.

Brand assets for the HA integration live at `assets/` at
the repo root (source SVG + 256/512 PNG renders). They
are only consumed by a future PR to `home-assistant/brands`;
they do not ship inside the bundle because HA's UI only
reads brand assets from the brands CDN.

## Architecture

All automations follow a three-layer architecture:

1. **Blueprint** (`blueprints/.../name.yaml`) -- defines HA
   triggers and user-configurable inputs. Calls a pyscript
   service. Contains no logic.
2. **Service wrapper** (`pyscript/blueprint_toolkit.py`)
   -- runs under PyScript's AST evaluator. Has access to
   PyScript-injected globals (`state`, `hass`,
   `homeassistant`, `service`, `log`, etc.). Parses inputs,
   loads/saves state, calls the logic module, and executes
   the returned action. No business logic.
3. **Logic module** (`pyscript/modules/name.py`) -- runs
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
  `pyscript/blueprint_toolkit.py`) have no shebang.
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
Service wrapper functions do not duplicate defaults -- the
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

- **Logic modules** (`pyscript/modules/*.py`) -- fully
  typed, checked by mypy strict.
- **Service wrapper** (`pyscript/blueprint_toolkit.py`)
  -- fully typed, with `TYPE_CHECKING` stubs for
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

### Three-layer dispatch model

Every service is implemented as three independently
callable layers. Each layer either emits a persistent
notification and returns, or calls the next layer
directly — no layer propagates a return value.

1. **Entrypoint — `<service>_blueprint_entrypoint(**kwargs)`**
   `@service`-decorated. The blueprint's `action:` calls
   this. Body is a one-line call to
   `_dispatch_blueprint_service(service_label, kwargs)`,
   which looks up the registered argparse function and
   expected-keys set from the `_BLUEPRINT_SERVICES`
   module dict. On missing or extra kwargs it emits a
   `blueprint_mismatch` notification and returns;
   otherwise it calls the argparse layer.

2. **Argparse — `<service>_blueprint_argparse(...typed _raw kwargs)`**
   Parses every `_raw` blueprint input into a native
   Python type and runs every config-validation check:
   pure parsing, set-math on the parsed values, and
   read-only HA state reads such as entity existence
   or service registration. On any errors it emits a
   `config_error` notification and returns; otherwise
   it calls the service layer directly with native-
   typed kwargs. Argparse has no side effects beyond
   reads and its error notification.

3. **Service — `<service>(...native-typed kwargs)`**
   Takes strongly typed native Python parameters
   (`list[str]`, `int`, `bool`, etc.). No parsing, no
   validation. Executes the business logic: evaluate,
   act, save state, emit domain-specific findings
   notifications.

Notification ownership follows the layers: entrypoint
owns `blueprint_mismatch`, argparse owns `config_error`,
service owns findings. Only the service uses
`_sweep_and_process_notifications` — its orphan sweep
handles findings that no longer apply (device removed,
reference fixed, etc.). Entrypoint and argparse each
call `_process_persistent_notifications` with their
single owned notification (active on failure, inactive
on success as a specific-ID dismiss); they never
orphan-sweep, because that would collateral-dismiss
findings emitted by the service layer.

The blueprint `action:` line calls
`pyscript.<service>_blueprint_entrypoint` (not
`pyscript.<service>`).

Each service defines a module-level
`_<SVC>_SERVICE_LABEL` constant (the human-readable
label used for the registry key, the notification-
prefix builder, and `_build_config_error_notification`)
and registers itself in `_BLUEPRINT_SERVICES` right
after its argparse function is defined. The expected-
kwargs frozenset is inlined in the registry entry:

```python
_DW_SERVICE_LABEL = "Device Watchdog"


def device_watchdog_blueprint_argparse(...):
    notif_prefix = _notification_prefix(
        _DW_SERVICE_LABEL, instance_id,
    )
    config_error = _build_config_error_notification(
        errors, instance_id, _DW_SERVICE_LABEL,
    )
    ...


_BLUEPRINT_SERVICES[_DW_SERVICE_LABEL] = (
    "device_watchdog.yaml",
    frozenset([
        "instance_id",
        "watched_integrations_raw",
        # ...
    ]),
    device_watchdog_blueprint_argparse,
)


@service
async def device_watchdog_blueprint_entrypoint(**kwargs):
    await _dispatch_blueprint_service(_DW_SERVICE_LABEL, kwargs)
```

(Trigger Entity Controller used to follow this pattern but
was ported to a native HA service handler -- see
``custom_components/blueprint_toolkit/tec/`` for the new
shape that future ports should adopt.)

The entrypoint is ``async def`` because ``_dispatch_blueprint_service``
awaits the module-reload read lock. See "Module reload coordination" in
``pyscript/blueprint_toolkit.py`` for why the lock is async.

The registry is the single source of truth wired by
both the dispatcher and the `TestBlueprintExpectedKeys`
drift test — registering a service is all that's
needed for dispatch to route through it and for the
drift test to catch any mismatch between its inlined
expected-kwargs frozenset and the live argparse
signature (pyscript's AST evaluator can't introspect
real parameter names at runtime, which is why the
frozenset exists; CPython's `inspect.signature` still
works normally in tests).

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

1. **Summary** -- one paragraph describing what the
   automation does.
2. **Features** -- bulleted list of capabilities.
3. **Requirements** -- prerequisite HA / PyScript config.
4. **Usage** -- install + enable steps.
5. **Configuration** -- blueprint input table.
6. **Usage notes** -- examples, exclusion cheatsheets,
   behavior gotchas, and any user-facing detail that
   doesn't fit under Configuration.
7. **Developer notes** -- state attributes, debug log
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

### Rendered HTML

The markdown sources under `docs/` are rendered to HTML
that ships alongside the integration so HACS users can
click a "Full documentation" link from each blueprint
directly to a local page.

- Source: `custom_components/blueprint_toolkit/bundled/docs/*.md`
  (also reachable via the `docs/` symlink at the repo
  root).
- Rendered:
  `custom_components/blueprint_toolkit/bundled/www/blueprint_toolkit/docs/*.html`.
  Committed alongside the source.
- Renderer: `scripts/render_docs.py` (CommonMark +
  tables, minimal inline-CSS template, `markdown-it-py`
  pinned for deterministic output). Idempotent -- it
  only writes files whose content changed.

After editing any `*.md`, **re-run the renderer and
commit the regenerated HTML in the same commit**:

```bash
scripts/render_docs.py
```

A drift test (`tests/test_docs_rendered.py`) runs
`scripts/render_docs.py --check` and fails if the
committed HTML is out of date, if a markdown source is
missing its HTML counterpart, or if an orphan HTML has
no matching source. The test's failure message points
at the render command.

#### How rendered HTML reaches the user's browser

The rendered HTML is **not** installed under
`/config/www/`. HA's default `/local/` static handler:

- Is only registered at HA startup if `/config/www/`
  already exists (our integration runs after that).
- Refuses to follow symlinks whose targets escape
  `/config/www/`, which is exactly what an installed
  symlink-into-bundled would do.

Instead, the integration's `async_setup_entry`
registers its own aiohttp static route at
`/local/blueprint_toolkit/docs/` pointing
directly at the bundled docs directory. Real files,
no symlinks, no dependency on `/config/www/`.

**dev-install limitation**: users on the
`scripts/dev-install.py` path don't load the HA
integration, so the static route is never registered
and the blueprints' `/local/.../docs/...html` links
404. Read the markdown sources under
`bundled/docs/` directly during dev work.

## Comments

Do not number steps in comments (e.g., `# 1. Parse state`).
Numbering is unnecessary and adding/removing steps requires
renumbering.

## PyScript AST Constraints

All pyscript files -- both the service wrapper
(`blueprint_toolkit.py`) and logic modules
(`pyscript/modules/*.py`) -- run under PyScript's
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
# All tests (fast, mock-based)
./tests/run_all.py
./tests/run_all.py --verbose
./tests/run_all.py --coverage

# Single test file
./tests/test_sensor_threshold_switch_controller.py
./tests/test_sensor_threshold_switch_controller.py --verbose
```

### Docker test harness (opt-in, slow)

`tests/docker/` spins up a real HA container with pyscript
pre-installed and exercises `scripts/dev-install.py` and
`scripts/dev-deploy.py` end-to-end. Not run by
`tests/run_all.py`; opt in with `pytest -m docker
tests/docker`. See `tests/docker/README.md` for details
and for how to bring the same environment up for
interactive browser-based development.

### pytest-homeassistant-custom-component harness

`tests/test_hacc_harness.py` uses
[`pytest-homeassistant-custom-component`](https://pypi.org/project/pytest-homeassistant-custom-component/)
to stand up an in-process HA instance for tests that need
real HA machinery. Pinned to a specific HA release in the
file's PEP 723 dependency block; first run downloads HA
into a uv-script env (~minute), subsequent runs are
cached. Runs in the default suite via `tests/run_all.py`.

`tests/test_integration.py` uses the same harness to
exercise the integration's async lifecycle (config flow,
options flow, async_setup_entry, async_remove_entry).

### Manifest version bump rule

The integration version in
`custom_components/blueprint_toolkit/manifest.json`
must increment with every commit that changes anything
under `custom_components/`, and stay equal otherwise.
`tests/test_manifest.py` enforces both this rule and the
canonical formatting (`json.dumps(..., indent=2)` plus a
trailing newline) in the default test run.

Before committing any change under `custom_components/`,
bump the patch:

```bash
scripts/bump-manifest-version.py
```

The script rewrites `manifest.json` and re-stages it. For
non-patch bumps (a deliberate minor or major graduation),
edit the manifest by hand instead -- the test only
checks that the version is strictly greater than the
parent's, not that it's exactly +1.

### Releasing (tag + GitHub release)

HACS surfaces installable versions from GitHub Releases.
Once at least one Release exists in the repo, HACS shows
only Releases as version candidates -- commits pushed to
master between Releases are invisible to users until the
next Release is published. (Pre-first-Release, HACS
falls back to tracking the latest commit SHA, but that
state ends as soon as the first Release lands and never
returns.) Tagging and releasing are kept separate from
`git push` so a manifest bump can sit on master without
auto-publishing -- run `scripts/release.py` only when you
want HACS users to see the new version:

```bash
git push                       # land commits on origin
scripts/release.py             # then publish the version at HEAD
scripts/release.py --dry-run   # see what it would do
```

The script reads the version from HEAD's `manifest.json`,
creates the local annotated tag `vX.Y.Z` if missing,
pushes the tag, and creates the GitHub release via `gh`
(release notes default to HEAD's commit body). Each step
is idempotent, so re-running after a partial failure
picks up where the previous run stopped.

Refuses if HEAD isn't reachable from `origin/master` --
push commits before publishing the release.

Non-bumping commits at HEAD (docs-only follow-ups,
comment fixes, etc.) are detected (the version's tag
already exists at an older ancestor commit) and the
script exits cleanly without creating a duplicate
release.

### Code quality

Lint, format, and type checks are included in each test
file's `TestCodeQuality` class and run automatically as
part of the test suite:

- `test_ruff_lint` -- ruff linting
- `test_ruff_format` -- ruff formatting
- `test_mypy_strict` -- mypy strict type checking

### PyScript compatibility

Two complementary mechanisms catch PyScript AST
evaluator incompatibilities before deployment:

- **Static scan**
  (`TestPyScriptCompatibility` in
  `test_blueprint_toolkit.py`) -- walks the AST
  of every `pyscript/**/*.py` file and flags known-bad
  patterns (generators, lambda, yield, bare `open()`,
  `sorted(key=func)`, etc.). Fast and gives precise
  file:line errors. When adding a new static ban, add
  a paired negative test in `TestHarnessSanity` (see
  below) to verify the evaluator actually rejects it.

- **Real evaluator**
  (`test_pyscript_eval_compat.py`) -- instantiates
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

## HA deployment testing

- **Deploy with `scripts/dev-deploy.py`.** Ships every
  git-tracked file to the install path on the HA host
  (default `/root/ha-blueprint-toolkit`; outside
  `/config/` so it never collides with a HACS install),
  removes files the host has under owned top-level
  entries that git does not, then invokes
  `scripts/dev-install.py` on the host to reconcile the
  `/config/...` symlinks, and finally runs the reload
  services. Refuses to run if the working tree has any
  uncommitted or untracked files. `--dry-run` prints the
  plan without touching the host, `--force-reloads` runs
  both API reloads unconditionally, `--ha-restart` runs
  `ha core restart` instead of the API reloads,
  `--allow-dirty` skips the clean-tree check and ships
  working-tree content as-is (tracked files with local
  edits plus any untracked files `.gitignore` does not
  exclude) -- intended for iterative dev, not production
  deploys. `--cli-symlink-dir` is passed through to
  `dev-install.py` when set; if omitted, the CLI
  script is not symlinked anywhere.

- **`scripts/dev-install.py` runs on the HA host.** Plain
  Python (no uv). Reads the checked-out repo under
  `--repo-dir` and reconciles its bundled payload into
  `/config/blueprints/`, `/config/pyscript/`,
  `/config/www/blueprint_toolkit/`, and optionally
  `<cli-symlink-dir>/` as symlinks pointing back into the
  bundled subtree. Idempotent; tracks state in
  `<ha-config>/.blueprint_toolkit.manifest.json`
  so stale symlinks from renamed or removed bundled
  files get cleaned up on the next run. Refuses to
  overwrite regular files at any destination; existing
  symlinks whose targets match the bundled marker are
  treated as ours and rewritten. Invoked by
  `dev-deploy.py` on every push, but can also be run
  directly on the host during debugging.

- **Run `pyscript.reload` after pyscript changes.**
  Re-imports every file under `pyscript/`. Needed when
  you touch `pyscript/blueprint_toolkit.py` or any
  `pyscript/modules/*.py`. Reload picks up the current
  `@service` signatures and module contents:
  ```bash
  curl -s -X POST \
    -H "Authorization: Bearer $API_KEY" \
    http://$HA_HOST:8123/api/services/pyscript/reload
  ```

- **Run `automation.reload` after blueprint changes.**
  Re-reads `automations.yaml` *and* re-renders
  blueprint-backed automation actions from the current
  blueprint YAML. Needed when you touch any
  `blueprints/automation/blueprint_toolkit/*.yaml`
  file. Without this, HA keeps dispatching the cached
  rendered action, so if a blueprint input was renamed
  or removed the service call still arrives with the
  stale kwarg and pyscript raises
  `TypeError: <service>() called with unexpected keyword
  arguments`:
  ```bash
  curl -s -X POST \
    -H "Authorization: Bearer $API_KEY" \
    http://$HA_HOST:8123/api/services/automation/reload
  ```

- **Fetch `pyscript.automation_<slug>_state`.** Every
  blueprint-backed automation writes a state entity on
  each successful run with `last_run`, `runtime`, and
  per-automation findings attributes. Read it with:
  ```bash
  curl -s -H "Authorization: Bearer $API_KEY" \
    http://$HA_HOST:8123/api/states/pyscript.automation_<slug>_state
  ```
  A fresh `last_run` plus a nonzero `runtime` and no
  error attributes mean the run completed. Persistent
  notifications are no longer exposed as `/api/states`
  entities in HA 2026.4+; fetch them via the websocket
  `persistent_notification/get` command when you need to
  verify notification content.

## Commit Messages

- Use `- component: Summary of change.` format.
- Include a `Co-Authored-By: <AI Model XXX>` trailer for AI-assisted commits.

