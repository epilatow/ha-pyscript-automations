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
- Circuit-breaker fields: `bridge_error_streak` (consecutive
  reconciles that ended with a bridge-timeout error) and
  `circuit_open_until` (ISO timestamp -- the circuit is open
  until this time; empty when closed). Both persist across
  reconciles so other error paths don't reset them.

### Bridge-timeout circuit breaker

The Z-Wave controller's serial interface has been observed
(once so far, 2026-04-23) to wedge under a burst of
`getPriorityRoute` / equivalent queries. Once wedged, every
subsequent priority-route query times out and the zwave-js
driver loops through unresponsive-controller recovery. Each
reconcile's own burst of per-node route queries re-triggers
the wedge on the next recovery attempt, so the automation
itself is a contributor to the failure mode.

The circuit breaker in the reconcile wrapper stops this
amplification:

- After `CIRCUIT_BREAKER_THRESHOLD` (3) consecutive
  reconciles end with a bridge-timeout error (matched by
  `TimeoutError` in the captured error string), the breaker
  opens for `CIRCUIT_BREAKER_COOLDOWN` (15 min).
- While the breaker is open, the reconcile returns early
  without calling the bridge. `bridge_error: "circuit
  breaker open"` is written to the state entity so the
  skip reason is visible.
- A manual trigger (service tool / dev tools) bypasses the
  breaker so the user can force a retry at any point.
- On the first successful bridge call after the cooldown,
  the breaker resets (streak = 0, `circuit_open_until =
  ""`) and the notification is cleared by the normal
  notification sweep.
- Non-timeout bridge errors (`ConnectionError`,
  `OSError`) don't count toward the streak, since those
  are usually transient addon-boot conditions.

When the breaker opens, a persistent notification titled
"Z-Wave Route Manager: controller unresponsive" is raised
with the cooldown duration and absolute resume time. If
the controller stays wedged and the cooldown slides
forward, the notification is re-emitted in place with the
new resume time -- the user never sees a stale "resume at
HH:MM" that has already passed. Its ID
(`<prefix>circuit_breaker`) is outside the notification
sweep's `keep_pattern`, so it is auto-cleared by the first
successful reconcile after the cooldown.

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

### Known failure mode: controller serial-interface wedge

Observed once so far, 2026-04-23, after inclusion of two
Zooz ZSE11 Q Sensors as Z-Wave Long Range nodes (IDs 273
and 274). ZRM and the `zwave_network_info.py` probe were
both issuing `getPriorityRoute` for every node including
LR ones; upstream zwave-js-ui already skips LR for its own
startup interview, so our code was the last remaining
LR-priority-route caller in this install. At some point
during that window the controller (HA Z-Wave JS addon
1.2.0) stopped ACKing the driver's serial commands
entirely -- every call timed out, priority-route queries
on mesh nodes and LR alike. zwave-js cycled
unresponsive -> soft-reset-failed -> reopen-serial -> next
command hangs, and the reconcile's own burst of queries
re-triggered the wedge on each recovery, so the automation
itself became part of what kept the stick wedged.

What exactly triggered the wedge isn't conclusively known.
Plausible candidates: an LR-node `getPriorityRoute` call
hitting a firmware bug, the volume of concurrent
priority-route commands saturating the controller queue,
or an interaction with the LR nodes' first post-inclusion
check-ins. We never reproduced a clean trigger.

The two-part fix (shipped):

1. **Skip LR nodes in the per-node route refresh**
   (`zwave_js_ui_bridge.get_nodes_with_fresh_routes` and the
   mirror in `scripts/zwave_network_info.py`). LR is a
   direct-star topology with no mesh, no priority routes,
   and no SUC return routes, so the queries are meaningless
   regardless of any firmware issue. If LR priority-route
   calls were part of the trigger, this removes our
   contribution; if not, the calls were still wasted
   round-trips worth eliminating.
2. **Circuit breaker** (see above) so a wedge from any
   future trigger doesn't get amplified by subsequent
   reconciles.

#### Recovery findings from the 2026-04-23 incident

Tried, in order:

1. **Addon restart** (`ha addons restart core_zwave_js`) --
   made things worse. Pre-restart the driver was initialised
   and only `getPriorityRoute` calls hung. Post-restart the
   driver couldn't complete its own capability-query
   handshake; every attempt timed out on the ACK for
   `ZWaveController.queryCapabilities`. A driver-level
   reconnect is not enough to recover the stick.
2. **USB port "re-authorize" via sysfs, 1-second off-window**
   (`echo 0 > /sys/bus/usb/devices/<port>/authorized` then
   `echo 1 > ...`, ~1 s between) -- unreliable. Worked once
   early in the incident and gave ~10 s of healthy operation
   before the wedge re-triggered, but a second attempt ~8 h
   later with the same short window did nothing -- the driver
   couldn't even complete `queryCapabilities`.
3. **USB port re-authorize with a 10-second off-window** --
   this is the one that worked. After idling the stick (addon
   stopped, nothing touching `/dev/ttyACM0` for ~30 s),
   toggling authorize 0 -> 1 with a 10 s gap let the driver
   come up cleanly and stay up. At the time of writing the
   controller has been healthy and processing traffic
   (including LR) for several minutes with no wedge
   recurrence.

The off-window length is the most obvious difference
between the unreliable and successful attempts, but with
only three data points (two 1 s attempts, one 10 s
attempt) we can't conclusively say it's the causal factor.
One conjecture: the stick's MCU firmware has internal
state that needs time to drain; an instant re-authorize
preserves that state, a longer off-window lets it reset.
Same sysfs path, same kernel API, different outcomes.

Unknowns that remain:

- **What actually triggered the self-sustaining wedge.** Our
  best guess is the probe script's burst of `getPriorityRoute`
  calls against the LR nodes we'd just included, but the
  wedge persisted and re-triggered for hours after all our
  code stopped issuing any commands. Something ambient (a
  sleepy mesh node's periodic check-in, or an LR node's
  wake-up frame) kept re-triggering the firmware bug on its
  own, on a ~30 s cadence. We couldn't isolate which
  specific frame from the silly-level driver log because
  the controller was too hung to produce useful traffic
  records.
- **Whether the 10 s off-window is deterministic or just
  lucky.** We have one data point where it worked. Needs
  more evidence across different wedge depths before we
  trust it as a reliable remediation.
- **Whether controller firmware version matters.** This
  incident was on a Nabu Casa ZWA-2 (800-series Silicon
  Labs). Older 500/700-series sticks may behave differently.
- **Whether `ha host reboot`** (which cycles the USB
  subsystem at the OS level) would also clear the state
  when authorize-toggle doesn't. Not evaluated since the
  10 s authorize window worked first.

Until we have more data, treat recovery as an escalation
ladder: (a) addon restart -- cheap but insufficient in the
one instance we tried; (b) USB authorize toggle with a
>=10 s off-window -- this is new and promising; (c) `ha
host reboot` -- untested for this failure mode; (d)
physical unplug/replug -- untested in this incident but
the canonical fallback for any USB device in an unknown
state; requires hardware access.

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
- **Auto-reset of a wedged controller**. When the circuit
  breaker trips repeatedly (e.g. N opens within M hours),
  we would like a software-only self-heal action. We have
  one empirically-confirmed recovery recipe from the
  2026-04-23 incident (see "Recovery findings" above):

  1. Stop the addon (`hassio.addon_stop`).
  2. Idle for ~30 s with nothing touching `/dev/ttyACM0`.
  3. Toggle the stick's USB port authorize:
     `echo 0 > /sys/bus/usb/devices/<port>/authorized`,
     wait **at least 10 s**, then
     `echo 1 > .../authorized`. See "Recovery findings"
     above for why the off-window length appears to matter.
  4. Wait ~3 s for the bus to settle.
  5. Start the addon (`hassio.addon_start`).
  6. Poll the driver-ready signal; give up after ~60 s.

  All of this is automatable from pyscript. The HA core
  container has write access to
  `/sys/bus/usb/devices/<port>/authorized` (confirmed
  during debugging -- it's root-owned 0644, and pyscript
  has `allow_all_imports: true`), and
  `hassio.addon_stop`/`addon_start` are first-class
  services. **Do not use `hassio.addon_restart`** -- that's
  what we tried first and it made things worse.

  Design sketch for the implementation:

  - Blueprint input `reset_wedged_controllers` (default
    `false`) so this is opt-in and the user has explicitly
    acknowledged what the automation is allowed to do.
  - Blueprint input for the stick's USB sysfs port path
    (e.g. `1-1.1`) -- the path is hardware/topology
    specific and can't be auto-detected reliably across
    systems. Default empty means "disabled regardless of
    the reset_wedged_controllers toggle."
  - State fields `last_auto_reset_at` (datetime) and
    `auto_reset_count_today` to cap attempts.
  - Cooldown (e.g. 6 hours between resets, max 3 per 24
    hours) to prevent a reset loop if the action stops
    working.
  - Separate persistent notification (distinct id outside
    the sweep's `keep_pattern`) that describes what was
    attempted and when, and is **not** auto-cleared -- the
    user should see the history.
  - Detection signal is the breaker's `open` transition;
    auto-reset fires when the transition count within the
    last X minutes exceeds a threshold.
  - Post-action validation: after the addon starts, poll
    for a successful `getNodes` response within ~60 s. If
    still wedged, **do not** escalate automatically --
    surface a notification describing the failed recovery
    and stop.
  - Skipped from v1 for two reasons: (a) the shipped
    LR-filter fix removes our code's possible contribution
    to the trigger (we don't know for sure LR priority-route
    calls were the trigger, but if they were, our code no
    longer issues them); (b) the recovery recipe has
    exactly one successful data point so far, which isn't
    enough to trust it for unattended operation. Revisit
    once either the wedge recurs despite Fix B (so
    we know the recipe is worth automating) or we gather
    more data points confirming the recipe is reliable.
