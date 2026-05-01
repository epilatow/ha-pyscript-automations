# Automations Guide

Conventions and patterns for the automations shipped by this integration. Read
this when writing a new automation, modifying an existing one, or reviewing
such a change.

The companion `DEVELOPMENT.md` covers dev-process content (code review, doc
hygiene, testing, releases). This file documents conventions and patterns
specific to the automations themselves.

## Architecture

Each automation is a three-layer split: a Home Assistant blueprint that
dispatches to the integration's service handler, the handler that wires HA
into business logic, and the logic module that has no HA dependencies.

### Module layout

Paths use the full service name. The subpackage directory under
`custom_components/blueprint_toolkit/`, the test files under `tests/`, and any
path string in code or docs match `_SERVICE` exactly -- no abbreviations like
`tec/` or `zrm/`. Abbreviations are reserved for log tags
(`_SERVICE_TAG = "TEC"`) and ergonomic Python local aliases
(`from .trigger_entity_controller import handler as tec_handler`).

```text
custom_components/blueprint_toolkit/
+-- __init__.py                # async_setup_entry / async_unload_entry
|                              # initialises entry.runtime_data
|                              # imports + delegates to each handler
+-- helpers.py                 # all shared helpers (see below)
+-- const.py                   # DOMAIN, OPTION_*, STORAGE_*
+-- <service>/                 # one subpackage per automation
|   +-- __init__.py            # docstring; no exports
|   +-- logic.py               # decision tree, no HA imports
|   +-- handler.py             # HA wiring (vol.Schema, service
|                              # handlers, lifecycle mutators,
|                              # spec, register/unregister)
+-- bundled/
    +-- blueprints/automation/blueprint_toolkit/<service>.yaml
    +-- docs/<service>.md
```

### Three-layer dispatch

The handler always splits responsibilities into three async layers. Each layer
either emits a persistent notification and returns, or calls the next layer
directly -- no layer propagates a return value.

1. **Entrypoint** -- the per-handler `_async_entrypoint(hass, call)` function
   that `helpers.register_blueprint_handler` registers as the
   `blueprint_toolkit.<service>` service callback. Receives the raw
   `ServiceCall`. Sole responsibility is to hand off to argparse.
2. **Argparse** -- runs `vol.Schema` (catching `vol.MultipleInvalid`
   separately from `vol.Invalid` so every error surfaces at once, not just the
   first), then accumulates cross-field + HA-state errors, emits config-error
   notification via `emit_config_error` (which dispatches an
   `active=bool(errors)` spec -- empty errors becomes a dismiss spec, so
   callers call this unconditionally). Builds a `logic.Config` on success.
3. **Service layer** -- reads `hass.states` to populate `logic.Inputs`, calls
   `logic.evaluate(config, inputs)`, applies the `Result` (turn_on/turn_off
   propagating `call.context` for logbook attribution, schedule/cancel
   `async_call_later`, send notification, write diagnostic state via
   `update_instance_state`).

### `BlueprintHandlerSpec` -- per-port lifecycle config

Every handler defines a single `_SPEC = BlueprintHandlerSpec(...)` that the
shared `register_blueprint_handler` / `unregister_blueprint_handler` consume.
Fields:

```python
service: str            # slug; "trigger_entity_controller"
service_tag: str        # short tag for logs/notifs; "TEC"
service_name: str       # human-readable; "Trigger Entity Controller"
blueprint_path: str     # "blueprint_toolkit/<service>.yaml"
service_handler         # async (hass, ServiceCall) -> None
kick                    # async (hass, entity_id) -> None
on_reload               # callback (hass) -> None
on_entity_remove        # callback (hass, entity_id) -> None
on_entity_rename        # callback (hass, old_id, new_id) -> None
on_teardown             # callback (hass) -> None
```

All hooks default to `None` and are independently optional. Watchdogs that
don't track per-instance state pass nothing beyond the four required fields
plus the service handler.

### Per-entry runtime data

Per-handler state lives in `entry.runtime_data.handlers[<service>]`, a dict
the shared helpers populate lazily via `spec_bucket(entry, service)`. The
bucket stores:

- `unsubs: list[Callable]` -- bus listener unsubs (the shared dispatcher
  manages this).
- Per-handler keys (e.g. TEC's `instances` map) added by the handler via the
  same bucket accessor.

Cross-reload state (Repairs flow handoff for force-confirmed destinations)
lives separately at `hass.data[DOMAIN]` because it must survive entry unload.

## Shared helpers (`helpers.py`)

All handlers consume these. Don't reimplement; if a new pattern keeps
recurring, hoist it here.

Schema validators:

- `cv_ha_domain_list(value)` -- voluptuous validator for a list-of-string
  blueprint input where each item must match HA's actual domain charset
  (`homeassistant.core.valid_domain`). Rejects hyphens, uppercase,
  leading/trailing underscores, and double-underscores; accepts leading-digit
  names like `3_day_blinds`. Produces a config-error message that names the
  offending value(s) and explains the charset.

Notification + formatting:

- `format_timestamp(template, dt)` -- `YYYY/MM/DD/HH/mm/ss` token expansion in
  user-supplied prefix/suffix strings.
- `format_notification(text, prefix, suffix, current_time)` -- wrap a
  notification body with a formatted prefix + suffix.
- `parse_notification_service(service)` -- split `notify.foo` / `foo` into
  `(domain, name)`.
- `md_escape(s)` -- escape `\\`, `[`, `]` for safe interpolation into
  notification bodies; apply to every user-controlled string.
- `slugify(text)` -- derive an HA-safe slug from arbitrary text (used to build
  state-entity IDs).
- `matches_pattern(text, pattern)` -- case-insensitive substring or regex
  pattern test; safe on bad regex (returns False).
- `validate_and_join_regex_patterns(field, raw)` -- the canonical multi-line
  regex parser for blueprint fields with
  `selector: text: { multiline: true }`. Splits on newlines, validates each
  pattern, ORs them, rejects empty-matching patterns. Use for every regex-list
  input -- a naive single `re.compile(raw)` substitute silently fails on
  multi-line input.

Notifications:

- `PersistentNotification` (dataclass) -- spec for create/dismiss;
  `instance_id` field drives the `Automation: [name](edit-link)\n` prefix the
  dispatcher prepends.
- `process_persistent_notifications(hass, [spec])` -- dispatcher;
  create/dismiss + automation-link prefix.
- `process_persistent_notifications_with_sweep(...)` -- sweep variant;
  dismisses any prior-run notifications matching `sweep_prefix` not in the
  current batch.
- `make_config_error_notification(...)` -- builder; `md_escape`s every error
  bullet; empty errors -> dismiss spec.
- `emit_config_error(...)` -- builder + dispatcher convenience wrapper; safe
  to call unconditionally.
- `make_emit_config_error(*, service, service_tag)` -- factory returning a
  per-handler `_emit_config_error(hass, instance_id, errors)` closure.
- `validate_payload_or_emit_config_error(hass, raw, schema, emit)` -- run a
  `vol.Schema` over `raw`; on `MultipleInvalid` / `Invalid`, emit a
  config-error notification and return `None`; caller short-circuits.
- `instance_id_for_config_error(raw_data)` -- best-effort instance_id
  extraction for config-error paths where schema validation failed before the
  field could be parsed.
- `prepare_notifications(...)` -- sort + cap helper consuming `CappableResult`
  objects; emits clean-result notifications when the cap is exceeded; always
  emits a cap-summary slot.

Diagnostic state:

- `instance_state_entity_id(service, instance_id)` -- derive
  `blueprint_toolkit.<service>_<slug>_state`.
- `update_instance_state(hass, ...)` -- write diagnostic state. Common attrs:
  `instance_id`, `last_run`, `runtime`. Per-handler adds via
  `extra_attributes`.
- `automation_friendly_name(hass, instance_id)` -- resolve automation
  entity_id to user-set friendly name (used for `[ZRM: My Cool Automation]`
  log tags).

Lifecycle wiring:

- `BlueprintHandlerSpec` (dataclass) -- per-handler config.
- `spec_bucket(entry, service)` -- per-handler slot under
  `entry.runtime_data.handlers[service]`.
- `register_blueprint_handler(hass, entry, spec)` -- wire up service +
  listeners + restart-recovery; idempotent.
- `unregister_blueprint_handler(hass, entry, spec)` -- tear down service +
  listeners + on_teardown.
- `parse_entity_registry_update(event_data)` -- extract
  `(action, old_id, new_id)` for automation entities.
- `discover_automations_using_blueprint(hass, blueprint_path)` -- walk
  `DATA_COMPONENT.entities`.
- `recover_at_startup(hass, *, service_tag, blueprint_path, kick)` --
  discovery + standardized "kicking N for catch-up" log + per-entity `kick`
  (best-effort).
- `schedule_periodic_with_jitter(...)` -- per-instance jittered periodic
  scheduling that hands the action through an entry-scoped task.

## Schema + argparse

### Aggregate, never bail-on-first

Every validation path in argparse must accumulate problems into a single
`errors` list and emit them all in one `config_error` notification. Bailing on
the first failure forces the user to play whack-a-mole.

- Schema-level errors come back via `vol.MultipleInvalid.errors` -- iterate
  the whole list. Catch `vol.MultipleInvalid` BEFORE `vol.Invalid`.
  (Voluptuous accumulates all field-level errors automatically; `vol.All`
  within a single field is short-circuit, which is fine.)
- Cross-field validation (no overlapping entity sets, etc.) appends to
  `errors`; never `return` mid-validation.
- HA-state validation (entities exist, notification service is registered,
  sun.sun is available if any time-of-day input is non-`always`) appends to
  the same list.
- Single `await emit_config_error(...)` at the end of argparse with the
  accumulated `errors`. Empty list dismisses any prior notification.

### Schema shape

Use `vol.Schema({...}, extra=vol.ALLOW_EXTRA)`. `extra=vol.ALLOW_EXTRA` is
intentional for forward-compat with future blueprint inputs -- document, don't
silently flip to `PREVENT_EXTRA`.

Schema covers shape only. Cross-field rules + HA-state validation belong in
argparse, not in the schema.

Period / event / enum value lists derive from the logic-side enums (no
hardcoded duplicate string lists).

Run schema validation through
`helpers.validate_payload_or_emit_config_error(hass, raw, _SCHEMA, _emit_config_error)`
and short-circuit on `None`. Don't write the try / except block manually --
the helper catches `vol.MultipleInvalid` BEFORE `vol.Invalid` so the user sees
every schema error in one notification, not just the first.

### Common argparse landmines

Each one of these shipped a regression in at least one automation:

- **Multi-line text inputs.** Blueprint fields backed by
  `selector: text: { multiline: true }` arrive as a single string with literal
  `\n` chars. Naive parses (`re.compile(raw)` for a regex list,
  `raw.split(",")` for a comma list) silently fail because the whole
  multi-line string is treated as one token. Use
  `helpers.validate_and_join_regex_patterns` for regex lists; for other
  multi-line inputs split + strip + drop empties explicitly, then validate
  each line.
- **Regex inputs that match the empty string.** `.*`, `|||||`, `a?` all match
  `""` and would silently exclude every entity.
  `helpers.validate_and_join_regex_patterns` rejects these.
- **Synthetic-trigger `variables` overrides.** HA's `automation.trigger`
  strips the `trigger` key from caller- supplied variables. Pass overrides as
  flat top-level keys, NOT under `trigger.*`. See "Synthetic-trigger
  overrides" below.
- **Solution-oriented error messages.** When a missing dependency can be
  installed (sun.sun, an addon), tell the user *how* to fix it. "X is missing"
  is bad; "X is missing -- to fix, install Y or change Z" is good.

## Service layer

The service layer's call flow is uniform across handlers:

1. Capture `started = time.monotonic()` at top, for the `runtime` diagnostic
   attr.

2. Read `hass.states` into a `logic.Inputs` dataclass.

3. Call `logic.evaluate(config, inputs)`. The logic layer is pure; this call
   is synchronous and never reaches HA.

4. Apply the returned `Result`: dispatch `homeassistant.turn_on` / `turn_off`,
   schedule / cancel auto-off via `async_call_later`, post any notification.

5. Persist the outcome via `update_instance_state` (the only diagnostic-state
   write):

   ```python
   update_instance_state(
       hass,
       service=_SERVICE,
       instance_id=instance_id,
       last_run=now,
       runtime=time.monotonic() - started,
       state=result.action.name,           # or "ok"
       extra_attributes={...},             # per-handler
   )
   ```

### Action dispatch

`homeassistant.turn_on` / `turn_off` calls propagate `context=call.context`
(`blocking=False`); inline the call (no per-handler `_do_call` wrapper).

Auto-off scheduling (if applicable) cancels the prior wakeup before arming a
new one.

Notify-service dispatch via `helpers.parse_notification_service` plus
`hass.services.async_call`. Notify failures: prefer fail-loud unless the call
is in a bath-fan-flap-style "save state before notify" path, in which case
`try / except / log + continue` (the state save MUST land regardless of notify
outcome).

### Diagnostic state

After every evaluation, call:

```python
update_instance_state(
    hass,
    service=_SERVICE,
    instance_id=...,
    last_run=now,
    runtime=time.monotonic() - started,
    state=...,
    extra_attributes={...},
)
```

Per-handler attrs go in `extra_attributes`.

Common state attrs are exactly three: `instance_id`, `last_run`, `runtime`.
Everything else is per-handler.

State value defaults to `"ok"`. Trigger-driven handlers override with the
decision name (e.g. `result.action.name`); periodic / watchdog handlers leave
it alone.

### Async tasks must be entry-scoped

Async tasks scheduled by the handler must be entry-scoped, not hass-scoped.
Use `entry.async_create_background_task(hass, coro, name)` so HA cancels
in-flight work on entry unload; never `hass.async_create_task(coro)` (which
leaves work running detached against a torn-down service registration).

When arming `async_track_time_interval` directly, pass a sync `@callback`
wrapper that creates the entry-scoped task -- passing the async action
directly routes subsequent ticks through HA's internal
`hass.async_create_task`, defeating the scoping.
`helpers.schedule_periodic_with_jitter` already does the wrapping; if you
reach for `async_track_time_interval` directly, do it yourself.

## Spec + lifecycle

- **Per-instance state dataclass** (e.g. `<Service>InstanceState`) with
  `instance_id`, `cancel_wakeup` (if applicable), and ONLY transient state.
  Diagnostic fields go through `update_instance_state`, not on the dataclass.
- **`_instances(hass)` accessor** that resolves the single entry via
  `hass.config_entries.async_entries(DOMAIN)[0]`, then
  `spec_bucket(entry, _SERVICE).setdefault("instances", {})`. Returns `{}`
  when no entry is loaded.
- **`_kick_for_recovery(hass, entity_id)`** if the handler needs
  restart-recovery (sends a synthetic event the automation reacts to; for TEC
  it's a TIMER).
- **Mutator callbacks**: `_on_reload`, `_on_entity_remove`,
  `_on_entity_rename`, `_on_teardown`. Each is a small `@callback` function
  that touches the `_instances` map.
- **`_SPEC = BlueprintHandlerSpec(...)`** -- only set the hooks the handler
  actually needs.
- **`async_register(hass, entry)`** and **`async_unregister(hass, entry)`**
  are one-line delegations to `register_blueprint_handler` /
  `unregister_blueprint_handler`.

For the late-imported HA `@callback` decorator, suppress mypy's
`untyped-decorator` warning at the use site:

```python
@callback  # type: ignore[untyped-decorator]
def _on_reload(hass: HomeAssistant) -> None:
    ...
```

## Blueprint YAML

- **Periodic scheduling is integration-owned, not blueprint- owned.** Don't
  add `time_pattern` / `time` triggers to the blueprint -- the handler arms
  its own periodic timer via `helpers.schedule_periodic_with_jitter` (or
  `async_track_time_interval` directly for non-jittered cases). When a
  blueprint has only synthetic triggers (no reactive `state` / `event` / etc.
  triggers), still emit an empty `triggers: []` block: a blueprint with no
  `triggers:` key at all parses but HA renders the resulting automations as
  `unavailable`, the recovery kick never fires, and no scan runs after deploy.
- **No `homeassistant: start` / `homeassistant: shutdown` triggers.** The
  integration's `recover_at_startup` already kicks every discovered automation
  when HA fires `EVENT_HOMEASSISTANT_STARTED`, and the reload listener handles
  `EVENT_AUTOMATION_RELOADED`.
- **`action:` calls `blueprint_toolkit.<service>`.**
- **Synthetic-trigger overrides are flat top-level variables.** HA's
  `automation.trigger` service unconditionally overwrites the `trigger` key in
  caller-supplied `variables` with `{"platform": None}` (see
  `homeassistant/components/automation/__init__.py`'s
  `trigger_service_handler`), so anything passed as
  `variables: {"trigger": {...}}` is silently dropped. Pass flat keys instead
  (e.g. `trigger_id`, `trigger_entity_id`); have the blueprint action read
  them via `is defined` / `default(...)` patterns, falling back to `trigger.*`
  for real native-trigger paths. Concrete examples: ZRM's
  `trigger_id: "{{ trigger_id | default('manual', true) }}"`; TEC's
  `trigger_entity_id` / `trigger_to_state` is-defined chain.
- **Add a regression test** in `tests/test_<service>_handler.py` for every
  `automation.trigger` call site (periodic callback, restart-recovery kick,
  any other synthetic invocation): assert the `variables` payload's shape AND
  that `"trigger"` is NOT a key in it. Locks down the fix so a future refactor
  can't silently re-introduce the broken nesting.
- **`automation.trigger` re-fire MUST NOT pass `context=`**. HA's automation
  runner needs to generate a fresh per-run context for proper logbook
  attribution.
- **Document any `mode: queued` / `max:` in a YAML comment.** Silent drops
  above the cap surprise users.
- **Selector restrictions are UI-only; argparse validates domains
  independently.** A blueprint's
  `selector: entity: { domain: [switch, light, fan] }` restricts what the HA
  UI shows in the entity picker, but a hand-edited automation YAML can pass
  any entity. Argparse must independently validate the domain of every entity
  input -- either via `vol.In([...])` against the expected domain set in the
  schema, or via a cross-field check that walks `hass.states.get(entity_id)`
  and inspects its domain. STSC uses an explicit `_CONTROLLABLE_DOMAINS`
  frozenset (switch / fan / light / input_boolean / climate / cover / etc.)
  and rejects out-of-set entities with a "does not support on/off"
  config-error message. Skipping the runtime check means a YAML-edited entity
  gets passed through to `homeassistant.turn_on` and silently no-ops.

## Notifications

- Use friendly names (not raw entity IDs) in all user-facing notification
  messages. Resolve via `helpers.automation_friendly_name(hass, instance_id)`
  for log tags.
- **Every `PersistentNotification` spec sets
  `instance_id=<the automation entity_id>`.** The dispatcher uses it to
  prepend `Automation: [name](edit-link)\n` to every active notification body
  so users can click through to the automation that emitted the notification;
  an unset `instance_id` silently skips the prefix. Concretely:
  `make_config_error_notification` does it for you (just pass `instance_id=`
  to the wrapper). For other categories you build directly (multi-category
  handlers like RW: per-owner, source-orphans summary; ZRM: api_unavailable,
  apply\_<node>, timeout\_\<...>, circuit_breaker), pass
  `instance_id=instance_id` to every `PersistentNotification(...)` call. For
  `prepare_notifications`, pass `instance_id=instance_id` so the cap-summary
  spec gets stamped too.
- **Apply `helpers.md_escape(...)` to every user-controlled string going into
  a notification body.** Persistent notifications render through
  `<ha-markdown>`, so stray `[` / `]` / `\` in body text can corrupt the
  rendering -- garbled markdown, dropped content, or a chunk of body rewritten
  as a link the user didn't expect. Apply to friendly_names, vol.Invalid
  messages (which can echo the offending input value back), error messages
  from external APIs, YAML location strings, etc. Constants and values inside
  backtick code spans are exempt (constrained character set / markdown
  suppressed). Notification TITLES are exempt -- HA renders titles as plain
  text, only `message` goes through markdown.
- **Notification IDs follow
  `blueprint_toolkit_{service}__{instance_id}__{kind}`.** `__` is the reserved
  field separator; HA entity_ids can never contain `__` so the format stays
  parseable.
- **Pick the right dispatcher.** `process_persistent_notifications_with_sweep`
  is the right choice when the caller is asserting the COMPLETE per-instance
  notification state for this run -- it dismisses any prior-run notifications
  matching the per-instance prefix that aren't in the current batch. Use the
  bare `process_persistent_notifications` when touching a single known
  notification ID (e.g. `emit_config_error` against a fixed `__config_error`
  slot), so the call doesn't collateral-dismiss findings emitted by other
  categories.

## Debug logging

Each handler honours a per-instance `debug_logging` blueprint input. When
true, the service layer emits one `_LOGGER.warning` line summarising the run
-- event, action, key state values, reason -- using the service's tag prefix:

```python
auto_name = automation_friendly_name(hass, instance_id)
tag = f"[{_SERVICE_TAG}: {auto_name}]"
if debug_logging:
    _LOGGER.warning("%s event=%s ...", tag, ...)
```

Log level is `WARNING` because Home Assistant's default log level for custom
components is `WARNING`; `_LOGGER.info` would be silenced by default and the
user wouldn't see the toggle's effect.

## State persistence

Per-instance state lives in memory. Each handler keeps its state in
`_instances(hass)` -- a dict on
`entry.runtime_data.handlers[<service>]["instances"]` -- and the dict is
volatile across HA restarts. The mutator callbacks (`_on_reload`,
`_on_teardown`, etc.) tear down + rebuild it predictably.

The diagnostic state entity (`update_instance_state`) is for
**observability**, not authoritative state. Operators read it to confirm a run
completed (`last_run`, `runtime`) and to see the latest decision context. Its
`data` attribute is sometimes used to round-trip state across calls (STSC's
controller-state JSON blob is the example), but most handlers treat it as
write-only.

When the in-memory state is lost (HA restart, integration reload), handlers
re-bootstrap on the next call. The bootstrap path should:

1. Recognise the lost-state condition (typically the instance isn't in
   `_instances(hass)`, or the persisted blob in the diagnostic entity is
   `None` / malformed).
2. Re-arm any safety-relevant timers (e.g. STSC's auto-off bootstrap-arm: if
   the controlled entity is currently `on` and auto-off is enabled, arm
   `auto_off_started_at` immediately so the device doesn't get stuck on
   indefinitely).
3. Continue with the normal evaluation.

If the bootstrap path schedules anything (e.g. arming `async_call_later` for
an auto-off wakeup), the entry-scoping rule from "Async tasks must be
entry-scoped" above still applies -- use `entry.async_create_background_task`
(or a helper that does so internally), never `hass.async_create_task`.

## Testing

### File layout per handler

```text
tests/
+-- test_<service>_logic.py          # logic.py unit tests
+-- test_<service>_handler.py        # handler-side wiring + mutators
+-- test_<service>_integration.py    # pytest-HACC end-to-end
```

### Schema-drift test

In `tests/test_<service>_handler.py`, subclass `BlueprintSchemaDriftBase` from
`tests/conftest.py`. Two class vars: `handler = handler` and
`blueprint_filename = "<service>.yaml"`. The base provides both tests:

- `test_yaml_data_keys_match_schema_required_keys` -- symmetric set diff
  between blueprint YAML's first `action: data:` keys and `_SCHEMA`'s
  `vol.Required` keys.
- `test_blueprint_action_targets_registered_service` -- blueprint's `action:`
  line is `blueprint_toolkit.{_SERVICE}`.

This is the single test that catches the most bugs across the handlers; add it
to every new handler.

### Cross-port service-registration test

Add `<service>` to the `expected` set in
`tests/test_integration.py::TestSetupEntry::test_setup_registers_services`.
That test asserts every handler's service registers on `async_setup_entry`.
The set is hard-coded so each new handler has to update it.

### Integration test coverage

For each handler, `tests/test_<service>_integration.py` should cover at
minimum:

- Schema-rejection emits persistent notification.
- Cross-field overlap / missing-entity / missing-notify-service all emit
  notifications with the right ID + message.
- Successful call dismisses any prior config-error notification.
- Notification body starts with
  `Automation: [name](/config/automation/edit/<id>)\n` when the automation
  entity is registered.
- `md_escape` lands end-to-end (e.g. `[` in friendly name becomes `\[` in
  body).
- Service layer dispatches the right downstream call (`homeassistant.turn_on`,
  etc.).
- Diagnostic state entity created with common attrs (`instance_id`,
  `last_run`, `runtime`) + per-handler extras.
- `EVENT_AUTOMATION_RELOADED` triggers a fresh discovery scan.
- `EVENT_HOMEASSISTANT_STARTED` recovery log fires on setup.

### Code quality

- Every handler / logic / new module is covered by a
  `TestCodeQuality(CodeQualityBase)` class somewhere (typically the
  per-handler logic test) -- `ruff_targets` for lint + format, `mypy_targets`
  for strict.
- Both `logic.py` and `handler.py` go in `tests/test_<service>_logic.py`'s
  `TestCodeQuality.mypy_targets` so CI enforces mypy on every change. Manual
  one-off mypy runs go stale.
- No `# mypy: ignore-errors` in handler.py before considering complete.

## Naming conventions

- `_SERVICE` -- snake_case slug, e.g. `"trigger_entity_controller"`.
- `_SERVICE_TAG` -- short tag for log lines + notification titles, e.g.
  `"TEC"`.
- `_SERVICE_NAME` -- human-readable, e.g. `"Trigger Entity Controller"`.
- Subpackage directory matches `_SERVICE` exactly:
  `trigger_entity_controller/`.
- Test file basename matches `_SERVICE` exactly:
  `tests/test_trigger_entity_controller_*.py`.
- Notification ID: `blueprint_toolkit_{service}__{instance_id}__{kind}`, e.g.
  `blueprint_toolkit_dw__automation.bath_fan__config_error`.
- State entity ID: `blueprint_toolkit.{service}_{slug}_state`, e.g.
  `blueprint_toolkit.tec_kitchen_lights_state`.
- `_raw` suffix applied to schema-validated input fields whose parsed form is
  rebound without the suffix in argparse, e.g. `default_route_speed_raw` ->
  `default_route_speed`.

Booleans use `helpers`-side coercion via `cv.boolean`; never hand-roll string
comparison.

Time units in input names + variable names use full words: `_seconds`,
`_minutes`. Never `_s` / `_m` / `_min`.

User-facing enum values (exposed in blueprints) use dashes: `"night-time"`,
`"day-time"`, `"triggered-on"`, `"auto-off"`.

## User-facing docs

Each automation has a user-facing markdown doc at
`custom_components/blueprint_toolkit/bundled/docs/<service>.md`, rendered to
HTML at `bundled/www/blueprint_toolkit/docs/<service>.html` and served from
the HA frontend at `/local/blueprint_toolkit/docs/<service>.html` so the
blueprint can link to it from its `description`. After editing any `*.md`
source under `bundled/docs/`, re-run `scripts/render_docs.py` and commit the
regenerated HTML in the same commit (the `tests/test_docs_rendered.py` drift
check enforces this).

### Section order

Every automation doc follows the same top-level section order so users find
the same information in the same place across automations:

1. **Summary** -- one paragraph describing what the automation does.
2. **Features** -- bulleted list of capabilities.
3. **Requirements** -- prerequisite HA config.
4. **Usage** -- install + enable steps.
5. **Configuration** -- blueprint input table.
6. **Usage notes** -- examples, exclusion cheatsheets, behavior gotchas, and
   any user-facing detail that doesn't fit under Configuration.
7. **Developer notes** -- state attributes, debug log format,
   detection-mechanism internals, known limitations, and follow-ups.

User-facing sections come first so users don't have to scroll past developer
notes to find their config. Developers read the whole file, so the ordering
has no cost for them.

Don't introduce new top-level sections. Anything that doesn't fit an existing
bucket goes under "Usage notes" (if user-facing) or "Developer notes" (if
internal) as a sub-heading.

### Tables in user docs

Configuration / attribute reference tables in user docs stay as markdown
tables; they render cleanly in HTML and on GitHub, which is where users read
them. (The "prefer lists over tables" rule in `DEVELOPMENT.md` applies to
developer-facing docs that are read in plain text more often than in a
browser.)
