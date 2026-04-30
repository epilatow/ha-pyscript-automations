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
blueprints, docs, and CLI script the installer ships to
their user-visible `/config/...` paths via symlinks.

```
custom_components/blueprint_toolkit/bundled/
    blueprints/automation/blueprint_toolkit/*.yaml
    cli/zwave_network_info.py
    docs/*.md
```

The repo root keeps two committed symlinks into the
bundle for path-typing convenience and so existing test
paths keep resolving:

```
blueprints -> custom_components/blueprint_toolkit/bundled/blueprints
docs       -> custom_components/blueprint_toolkit/bundled/docs
```

These are real git symlinks (mode 120000). They live
outside HACS's download path and are never shipped to
users.

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

1. **Blueprint** (`bundled/blueprints/automation/blueprint_toolkit/<service>.yaml`)
   -- defines HA triggers and user-configurable inputs.
   Calls the integration's
   `blueprint_toolkit.<service>` action. Contains no
   logic.
2. **Handler** (`<service>/handler.py`) -- the HA wiring
   layer. Registered as the `blueprint_toolkit.<service>`
   service via `helpers.register_blueprint_handler`.
   Validates inputs (voluptuous schema + cross-field
   checks against `hass.states` / `hass.services`),
   loads/saves state, calls the logic module, and
   executes the returned action against HA. No business
   logic.
3. **Logic module** (`<service>/logic.py`) -- pure
   business logic. Testable with pytest. Does not import
   `homeassistant.*` (cannot call `state.get()`,
   `homeassistant.turn_on()`, etc.). May reference HA
   concepts (entity IDs, integration names, device
   classes) as data.

**Execution model**: purely reactive. No sleeping, no
waiting, no scheduling. Trigger fires, logic evaluates,
action executes, exit. Timeouts use the "record timestamp,
check on periodic trigger" pattern.

## File Conventions

### AI generated code header

All Python files begin with `# This is AI generated code`.

### Shebangs

- **Executable scripts** (test files, `run_all.py`) use the
  PEP 723 shebang `#!/usr/bin/env -S uv run --script` with
  inline dependency declarations.
- **Module files** (handler.py, logic.py, helpers.py,
  etc.) have no shebang. They are imported, not executed
  directly.

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

- **Logic modules** (`<service>/logic.py`) -- fully
  typed, checked by mypy strict.
- **Handlers** (`<service>/handler.py`) -- fully typed,
  checked by mypy strict.
- **mypy configuration** lives in `pyproject.toml`.

## Notifications

Use friendly names (not raw entity IDs) in all user-facing
notification messages. The handler resolves friendly names
via `helpers.automation_friendly_name(hass, instance_id)`
and passes them to the logic module.

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
3. **Requirements** -- prerequisite HA config.
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

`tests/docker/` spins up a real HA container and
exercises `scripts/dev-install.py` and
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

### Standalone checks

```bash
uvx ruff check .
uvx ruff format .
```

mypy is run via each test file's
`TestCodeQuality(CodeQualityBase)` class, which installs
Home Assistant + voluptuous via PEP 723 inline deps.
Bare `uvx mypy` from a checkout without those deps
cannot resolve `homeassistant.*`; run
`./tests/run_all.py` instead.

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
  `/config/blueprints/`, `/config/www/blueprint_toolkit/`,
  and optionally `<cli-symlink-dir>/` as symlinks
  pointing back into the bundled subtree. Idempotent;
  tracks state in
  `<ha-config>/.blueprint_toolkit.manifest.json`
  so stale symlinks from renamed or removed bundled
  files get cleaned up on the next run. Refuses to
  overwrite regular files at any destination; existing
  symlinks whose targets match the bundled marker are
  treated as ours and rewritten. Invoked by
  `dev-deploy.py` on every push, but can also be run
  directly on the host during debugging.

- **Run `automation.reload` after blueprint changes.**
  Re-reads `automations.yaml` *and* re-renders
  blueprint-backed automation actions from the current
  blueprint YAML. Needed when you touch any
  `blueprints/automation/blueprint_toolkit/*.yaml`
  file. Without this, HA keeps dispatching the cached
  rendered action, so if a blueprint input was renamed
  or removed the service call still arrives with the
  stale kwarg and the handler's schema rejects it:
  ```bash
  curl -s -X POST \
    -H "Authorization: Bearer $API_KEY" \
    http://$HA_HOST:8123/api/services/automation/reload
  ```

- **Fetch the diagnostic state entity.** Each blueprint-
  backed automation writes a state entity on each
  successful run with `last_run`, `runtime`, and
  per-automation diagnostic attributes. The entity ID
  lives under
  `blueprint_toolkit.<service>_<slug>_state`. Read it
  with:
  ```bash
  curl -s -H "Authorization: Bearer $API_KEY" \
    http://$HA_HOST:8123/api/states/blueprint_toolkit.<service>_<slug>_state
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

