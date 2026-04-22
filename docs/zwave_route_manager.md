# Z-Wave Route Manager

## Summary

Reconciles Z-Wave priority routes (controller<->node) against a
declarative YAML config file. Reconciles on HA startup, on
manual trigger, when the YAML config is edited, and periodically
(default every 5 minutes) to catch out-of-band route changes.
Failed reconciles retry automatically on the next
minute-granularity tick. See "Reconcile trigger semantics" under
Developer notes for the full gate. Surfaces broken config,
unreachable API, and apply failures as persistent notifications.

## Features

- Declarative YAML config: one source of truth for every
  route you want managed
- Two Z-Wave route directions managed per configured device:
  controller->node (application route) AND node->controller
  (priority SUC return route)
- Automatic route-speed resolution (picks the slowest max rate
  across all hops in the route)
- Per-entry and group overrides for speed
- Unmanaged-route cleanup: removes routes not in the config so
  editing the file is authoritative
- Pending-state tracking for sleeping nodes whose route
  commands queue until wake
- Generates configuration errors for devices that don't
  support routing (Z-Wave Long Range devices)
- Distinct notification categories: config errors, API
  unavailable, apply failures, pending timeouts

## Requirements

- Home Assistant **Z-Wave JS** addon (`core_zwave_js`), which
  bundles `zwave-js-ui`. The socket.io backend on port 8091 is
  the automation's target -- tested against addon v1.2.0
  (bundling zwave-js-ui v11.16.0).
- PyScript configured with:

  ```yaml
  pyscript:
    allow_all_imports: true
    hass_is_global: true
  ```

## Usage

1. Install the blueprint + pyscript modules (see main README).
2. Create `/config/zwave_route_manager.yaml` with your route
   definitions (see Configuration below).
3. Go to **Settings -> Automations & Scenes -> Create Automation
   -> From blueprint -> Z-Wave Route Manager**.
4. Fill in the inputs. Most installations only need to set
   `clear_unmanaged_routes` if you want manually-set routes to
   persist.
5. Save the automation. The next reconcile tick picks up your
   config and applies routes.

## Configuration

| Parameter | Description |
|---|---|
| Config file path | YAML file path relative to `/config` (default `zwave_route_manager.yaml`) |
| Z-Wave JS UI host | Usually `core-zwave-js` (default) |
| Z-Wave JS UI port | Usually `8091` (default) |
| Z-Wave JS UI auth token | Leave empty unless you've enabled auth |
| Clear routes not in config | If true, removes routes on nodes not listed in the config file |
| Reconcile interval (minutes) | How often to check for out-of-band route drift (e.g. someone editing a route via the zwave-js-ui web UI). Lower values catch drift sooner. |
| Pending timeout (hours) | Time before raising a notification on a node whose route hasn't been reflected |
| Default route speed | `auto`, `100k`, `40k`, or `9600` (used when neither repeater nor client specifies one) |
| Max notifications | Cap on per-run issue notifications; 0 = unlimited |
| Debug logging | Per-reconcile log.warning line under `[ZRM: ...]` |

## Usage notes

### Config file format

```yaml
# /config/zwave_route_manager.yaml
routes:
  # A single repeater with three clients. The repeater default
  # is 100k; one client overrides to 40k; a group of two
  # shares their own 40k override.
  - repeater: sensor.hallway_extender_node_status
    route_speed: 100k
    clients:
      # Bare string form -> inherits repeater default.
      - lock.front_door
      # Dict form with an override.
      - entity: sensor.porch_motion
        route_speed: 40k
      # Group form: one override, multiple entities.
      - entities:
          - binary_sensor.back_door
          - binary_sensor.side_door
        route_speed: 40k

  - repeater: sensor.loft_repeater_node_status
    clients:
      - binary_sensor.kitchen_motion
```

**Supported value forms for `route_speed`:**
`"auto"` | `"9600"` | `"40k"` | `"100k"` | bare integers
`9600`, `40000`, `100000`.

### Entity conventions

Any entity on the target Z-Wave device works -- the automation
resolves entity -> device -> node ID internally. Suggested
conventions for stability:

- **Repeaters:** use `sensor.<name>_node_status` -- always
  present for zwave_js devices, diagnostic category so rarely
  disabled by users, and its name is predictable.
- **Client devices:** use the "primary" entity: `lock.X` for
  locks, `binary_sensor.X_contact` for door/window sensors,
  `binary_sensor.X_motion` for motion sensors.

Disabling an entity used in the config breaks that config line
(surfaces as a config-error notification). Pick another entity
on the same device to fix it.

### Route-speed precedence

Most specific wins:

```
per-client override
  > group override (dict with `entities:`)
    > repeater `route_speed:`
      > blueprint `Default route speed`
        > auto: min(maxDataRate across source, repeater(s), controller)
```

`auto` fails with a config error if any hop has an unknown
maxDataRate. Explicit speed values skip that check -- use them
if you have an older device that reports an unusual rate but
you know what works.

### Line-powered nodes vs. sleepy nodes

Only line-powered nodes (extenders, plug-in sensors, wall
switches) can act as repeaters. Battery devices and FLiRS
("frequently listening routing slave" -- most locks) can't
repeat for others. The automation validates this upfront.

Route commands to sleeping battery nodes queue in zwave-js
until the node wakes. During that window the automation
tracks the command as **pending** and suppresses retries. If
the node doesn't wake within the configured pending timeout
(default 24h) a persistent notification surfaces. FLiRS nodes
like locks wake every second, so commands typically apply
within seconds.

A UI edit to a sleeping node's SUC return route takes up
to two wake cycles to revert: we can't see the queued UI
command, so only after the device wakes and accepts the
new route do we notice the drift and issue another update.
That update then has to wait for the next wake.

### `clear_unmanaged_routes` caveat

When `clear_unmanaged_routes` is enabled, the reconcile
iterates **every** Z-Wave node and removes any application
route and any SUC return route that isn't covered by your
config.

**Important:** The zwave-js-ui API for clearing SUC return
routes (`deleteSUCReturnRoutes`) is blunt -- it clears **all**
SUC return routes on the node, including custom routes you
set manually via the Z-Wave JS UI's *Return routes -> ADD*
button. Once you commit to `clear_unmanaged_routes=true`,
treat the config file as the single source of truth and
stop using the UI panels to set routes.

If you need to temporarily edit routes via the UI, disable
`clear_unmanaged_routes` first, edit, then re-enable once
you've mirrored your changes in the config file.

### Notification categories

Four distinct categories of persistent notification:

- **Config error.** YAML parse problems and entity-
  resolution problems (missing devices, devices that don't
  support routing, etc.) bundled into one notification per
  reconcile. Clears automatically once the config and entity
  registry agree.
- **API unavailable.** Surfaced when the Z-Wave JS UI
  socket.io API can't be reached or returns an incompatible
  response. Clears automatically once the addon is back.
- **Apply failed.** One per node that returned an error
  when sending a route command. Clears automatically once a
  later reconcile sends the same route successfully.
- **Pending timeout.** Each time a route command sits
  pending longer than the configured timeout, the route is
  re-issued automatically and a *new* notification is
  emitted for that timeout event. These notifications stay
  until you dismiss them -- they're a stream of "this route
  isn't landing" events, not a single status.

  The `Pending timeout (hours)` blueprint input doubles as
  the retry interval: a route that's been pending that long
  is re-sent, and the timer starts over. If a device is
  permanently unable to accept the route (e.g. it's been
  removed from the network, or it's a device class that
  silently drops priority-route commands), each retry will
  fail again and another notification will appear. The way
  to stop the noise is to remove that device from the YAML
  config.

## Developer notes

### Two route requests per configured route

Most YAML route entries managed by the automation result in
*two* underlying Z-Wave route requests -- one for each
direction:

- **Controller -> node** (priority application route): how
  the controller reaches the device when sending commands
- **Node -> controller** (priority SUC return route): how the
  device reaches the controller when reporting state

Both directions are managed, tracked, and counted
independently. Hence a YAML config with 5 routes typically
shows `routes_in_config: 10`, the `pending` and `applied`
state attributes can each carry up to two entries per node
(one per direction), and a device with one direction landed
but the other still in flight ("half-applied") appears in
*both* the `applied` and `pending` dicts.

("Most" rather than "always" because future YAML knobs may
opt a device into one-way-only management -- devices that
won't accept one of the two route types fall into this
category.)

### Entity attributes

Attributes written to `pyscript.<automation_name>_state`:

- `last_run`: ISO timestamp of the most recent tick
- `runtime`: evaluation time in seconds
- `last_reconcile`: ISO timestamp of the last reconcile
- `last_config_mtime`: the mtime seen last run (drives
  file-change detection)
- `last_trigger`: trigger ID (`"periodic"`, `"ha_start"`,
  `"manual"`)
- `reconcile_pending`: true when a reconcile is deferred
  (config error, API unreachable, controller not ready, or
  the previous reconcile had apply failures)
- `routes_in_config`: count of route directions after
  parsing + resolving (typically 2 x number of YAML route
  entries)
- `routes_applied`: count of route directions currently
  applied
- `routes_pending`: count of route directions that have been
  requested but not yet confirmed
- `routes_errored`: count of route directions whose apply
  failed
- `pending` / `applied`: nested dicts keyed by node ID,
  keeping the per-direction state alive across reconciles:

  ```yaml
  "<node_id>":
    entity_id: lock.front_door
    paths:
      - type: priority_app   # or priority_suc
        repeaters:
          - id: 23
            entity_id: sensor.hallway_extender_node_status
        speed: auto          # or 9600 / 40k / 100k
        requested_at: "2026-04-21T12:00:00+00:00"
        confirmed_at: ""     # empty while pending
        timeout_count: 0     # bumped on each timeout retry
  ```

  The same node can appear in both `pending` and `applied`
  when only one of the two directions has landed. Once a
  set lands, `timeout_count` carries forward into the
  applied entry so you can see how many retries it took.

  `pending` also carries *clear* requests issued by the
  `clear_unmanaged_routes` path. A clear entry has an
  empty `repeaters` list and `speed: "-"`. Clears fall
  out of `pending` entirely once they land -- they do
  not enter `applied`, since `applied` tracks only routes
  currently at a specific non-default value.
- Error-state diagnostic fields (zeroed/empty on a
  successful reconcile): `config_errors`, `resolve_errors`,
  `api_error`, `bridge_error`. Useful for figuring out why
  `reconcile_pending` is true when no notification is up.

### Debug logging

Set the blueprint's *Debug logging* to on. Output appears in
Settings -> System -> Logs with the tag `[ZRM: <automation
alias>]`.

Example:

```
[ZRM: Z-Wave Route Manager] configured=6 applied=4 pending=2
  errored=0 new_timeouts=0 actions_executed=2
```

### Reconcile trigger semantics

The blueprint fires two triggers: `time_pattern` every minute
and `homeassistant: start`. The service wrapper's gate
decides whether each tick warrants a reconcile:

| Signal | Action |
|---|---|
| `trigger.id == "ha_start"` | Reconcile |
| `trigger.id == "manual"` (service tool / dev tools) | Reconcile |
| Config file mtime changed since last run | Reconcile |
| `now - last_reconcile > reconcile_interval_minutes` | Reconcile |
| `reconcile_pending == true` from prior tick | Reconcile |
| Otherwise | Update `last_run` only, exit |

### Why zwave-js-ui and not zwave-js-server directly?

The priority-route command family was added to
zwave-js-server schema 47 (PR #1513, merged 2026-03-10) but
not yet released as of this writing. The HA Z-Wave JS addon
bundles zwave-js-ui, which drives zwave-js core's route APIs
directly via its own socket.io `ZWAVE_API` event -- no schema
negotiation, and the APIs we need (`setPriorityRoute`,
`assignPrioritySUCReturnRoute`, etc.) are all allow-listed in
its ZwaveClient. Shipping through zwave-js-ui avoids waiting
for the upstream release chain.

When zwave-js-server schema 47 ships and HA core surfaces
priority-route services natively, migration is one file:
`pyscript/modules/zwave_js_ui_bridge.py`. Its public API
(ZwaveJsUiClient + typed methods) stays the same; the
implementation swaps from socket.io to the HA client.

### Future work

- N-hop repeater chains. The logic module's data structures
  already accept `repeater_node_ids: list[NodeID]`; v1 just
  enforces `len == 1`.
- `direction:` override (controller->node only vs.
  node->controller only). v1 always sets both.
- `defaults:` top-level YAML block. Reserved by having
  `routes:` as the only recognised top-level key.
- Migration from the socket.io bridge to zwave-js-server-
  python once PR #1417 merges and schema 47 ships.
