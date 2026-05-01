# Development Guide for Agent Sessions

Guidance specific to agent-driven development on this repo. Read the companion
`DEVELOPMENT.md` and `AUTOMATIONS.md` for the project's general dev-process
content and automation conventions.

## Working in this repo

- Before modifying any code or utility, check for associated tests and run
  those tests to get a baseline before making any changes. Flag any testing
  problems before implementing planned changes.
- After modifying any code or utility, always run the associated tests before
  considering any changes complete.
- Always look at the contents of a script to see what kind of script it is. Do
  not rely on file name extensions (or lack thereof). Scripts that have
  `uv run --script` in their shebang are Python scripts, not shell scripts
  (regardless of the file extension).

## ASCII output in chat

Persistent content (files, code, comments, commit messages, PR bodies, docs)
is ASCII only -- see `DEVELOPMENT.md` for the rule and the common
slip-replacements. Replies in the chat itself are display-only and ephemeral,
so non-ASCII there is fine; the rule only applies to anything written to disk
or sent to GitHub.

## Doc-sync is non-negotiable

The Doc-sync rule in `DEVELOPMENT.md` is mandatory for every agent-authored
commit. **Every code change is a potential doc change.** Before finalizing the
commit message, grep the repo for any symbol, flag, convention, or behavior
the diff touched -- CLI surfaces, helper APIs, schema shapes, naming rules,
lifecycle wiring, test layout, notification formats, architectural patterns,
anything -- and update every doc that mentions it. If a doc references stale
state, it is part of the bug, not separate from it.

## Finish the work everywhere it applies

When a change addresses a problem that exists at more than one site -- a
duplicated pattern, a shared invariant, a contract that holds across parallel
modules -- the change addresses **every** site, not the surfacing instance.
The duplication is a symptom; the fix targets the underlying cause.

Three concrete shapes:

- **Same bug in N places: fix all N, or lift to shared code.** If a defensive
  guard belongs around one call site, it belongs around every parallel call
  site -- or, better, the guard belongs baked into the shared helper they all
  reach. "Module X was the surfacing port; the rest stays open as remaining
  scope" is a half-job, not a complete commit.
- **Same test in N places: extract a generic base.** If you find yourself
  adding the same regression test to one module's test file, the test belongs
  in shared test infrastructure -- a base class, a fixture -- consumed by
  every parallel module rather than copy-pasted across them.
- **Documented invariant violated in code: fix the code, not just the doc.**
  Adding docs that describe the canonical pattern while leaving the code
  divergent -- with a note that "the mechanical refactor stays open as
  remaining scope" -- does not deliver the invariant. Update the code in the
  same commit, or don't add the doc.

Re-tagging deferred work as "remaining P\<n> scope" / "stays open as followup"
/ "tracked separately" is paperwork, not progress. It's the same half-job,
dressed up. If you genuinely cannot finish a piece in this commit (a real
dependency, an in-flight refactor elsewhere, a deliberate incremental
rollout), call out the specific blocker -- not just a hand-wave -- and confirm
it's separable before deferring.

Before declaring a change complete, enumerate every site / instance / module
the change should affect and verify all are touched. Anything left as "stays
open" is a flag the change isn't actually done; check whether the deferral is
real or a rationalization.

This is the upstream form of the rejection-rationale rule under "Code review"
below: the two address the same failure (underscoping) at different stages.
Get it right before commit; the review is a backstop, not a license to
half-finish.

## Commit-message hygiene

`DEVELOPMENT.md`'s "Commit messages" section is the canonical rule list. Two
patterns recur in agent-authored messages despite being on the do-NOT list, so
flagging them again here:

- **No "Touched:" / "Files changed:" / "Affected:" lists.** The diff
  enumerates every file; restating that as a labelled list duplicates it and
  rots whenever an amend changes the file set.
- **No references to symbols the same diff removes.** A subject like "Replaces
  the per-module `_FooHandler.bar` with a generic `BarHandlerBase`" is a trap:
  future readers grep for the named symbol and find nothing because the same
  commit deleted it. State the new artifact on its own terms.
- **No commit-history references.** "The followup notes ...", "the
  tmp/<slug>-... scope", "as discussed in the earlier review" all point at
  ephemeral agent-facing scratch (followups files, tmp/ scopes, code-review
  threads). None of that survives in `git log`. If a constraint matters,
  restate it inline.

Numbered step comments in code (`# 1. Parse input`, `# 2. Validate`, ...) are
forbidden by `DEVELOPMENT.md`'s "Comments" subsection. Adding or removing a
step forces renumbering, and the function name + code structure already convey
ordering. This applies even when describing a canonical pipeline of steps --
the named operation is its own label.

## Comment-message hygiene

A code comment is read by someone looking at the *current* version of the
file. It must describe what is there now -- not what was there before, what
was deleted, what got renamed, or what got lifted into a helper. The canonical
rule lives in `DEVELOPMENT.md`'s "Comments" subsection; the agent-specific
failure mode is repeating the commit-message rationale inside the source.

Concretely, never write comments like:

- `# The legacy _FooBar shim is gone -- now uses helpers.foo.`
- `# Wrappers have all been deleted; the dispatcher derives this directly.`
- `# This used to live in module_x.py; lifted to shared.py in the cleanup.`
- `# Replaced the per-call-site try/except with the shared guard.`

The diff and commit message capture migrations. The comment captures the
*current* code only -- describe what the function does now and the constraint
it enforces. If the comment cannot be written without referencing something
that no longer exists, the comment isn't earning its keep; delete it.

The same applies to docstrings ("formerly known as `_FooBar`", "ported from
the legacy framework"), CHANGELOG-style banners at the top of files, and "//
TODO: remove once X" markers that name something already removed. If a
comment's content reads like a footnote on the diff, it belongs in the commit
message, not the file.

Example lists in this file (the bullets above, the "Concretely, never include"
list under Commit-message hygiene, the dev-deploy "Expected baseline noise"
bullets, etc.) are illustrative, not exhaustive. They're samples of patterns
to recognise, not authoritative enumerations -- when a similar-but-not-
included entry is created, renamed, or hits in the wild, you don't have to
extend the list. Same applies to example lists in `AUTOMATIONS.md`,
`DEVELOPMENT.md`, and the per-automation user docs.

## File markers

All agent-generated Python files begin with the literal comment
`# This is AI generated code` directly under the shebang (or at the top of the
file for non-shebang modules). The marker identifies which files in the repo
are agent-generated for future audit / review purposes.

## Code review

After each agent-driven develop / test / commit cycle, the default is to spawn
a code-review subagent against the just-committed branch -- doc-only and
lint-config commits included. Agent-driven reviews like this run BEFORE the
user reviews the commit; if the user reviews and lands their own feedback, an
additional agent-driven re-review is not the default -- only run one if the
user explicitly asks for it.

After the review returns, address each finding directly in the commit (amend).
Findings the agent chooses NOT to address get appended to
`tmp/<slug>-code-review-rejected.md` with reasoning, so the rejected set stays
visible for the user's review.

### Zero-context review

The review subagent must start with **zero context inherited from the calling
agent**. It does not see the calling agent's conversation, prior plans,
working notes, or any pre-framing of which decisions are "intentional". It
receives only what the review request explicitly hands it: a path to a
standalone review-input file containing the authored scope of the change, plus
the SHA of the commit under review. It then evaluates the commit on its own.

This matters because pre-framing decisions as "intentional" is exactly how
regressions slip past review. The calling agent's job is to state scope
neutrally; the review agent's job is to evaluate independently.

### Protocol

1. **Locate or extract the change scope.** If a written scope exists -- a
   followups entry, a plan file in `tmp/`, an issue body, etc. -- copy it
   verbatim into `tmp/<slug>-review-input.md`. Don't paraphrase or summarise;
   the review agent reads the authored scope directly. If no written scope
   exists for this commit (ad-hoc work, hot fix), skip the input file -- the
   review agent will read the commit message as the only authored intent.
   Never synthesize a scope file from the commit message; that just launders
   the implementing agent's framing.
2. **Spawn the review subagent with the prompt below**, substituting
   `<INPUT_LINE>` and `<SHA>`. Use the "Review-input file:" form when a scope
   file exists; use the "Commit message only" form otherwise. Hand the agent
   nothing else -- no extra framing, no "we already decided X", no hints about
   which findings would be welcome.
3. **Save the review** to `tmp/<slug>-code-review.md` (or `-code-review-N.md`
   for amend cycles).
4. **Address findings.** For findings, either fix them in the commit (amend)
   or append rejected findings to `tmp/<slug>-code-review-rejected.md` with
   reasoning on why they were rejected.

### The review prompt (verbatim)

Use exactly this prompt. Do not edit it to add context, reassurance, or
guidance about which decisions are intentional.

```text
You are reviewing a single commit on this repo. You have
zero context from any prior conversation -- evaluate the
commit on its own merits using only the inputs below and
the repo state.

Inputs:
- <INPUT_LINE>
- Commit SHA: <SHA>.

You are free to read any file in the repo you need to
understand the broader context. A code review against the
diff alone misses regressions that only surface when the
change is read against its callers, consumers, and
surrounding invariants. Read the full affected file(s),
not just the diff. Run the local test suite as part of
the review.

Answer two distinct questions, separately:

1. Does the commit solve the problem it was supposed to
   solve? Is the diff in scope? Complete? Anything the
   authored intent called for that wasn't addressed?

2. Did the commit avoid regressing or breaking anything
   else? Specifically:
   - Local test suite green?
   - Any changes that go beyond the authored intent?
   - Any deleted or modified content the intent didn't
     call for?
   - Any docstrings or comments touched that are no longer
     accurate post-change?
   - Commit message: does it accurately describe what the
     diff does? Any rationalizations, omissions, or claims
     that don't match the actual change? Do the summary
     lines match the changes they describe?
   - Doc-sync: did the diff touch anything with a doc
     footprint -- CLI surfaces, helper APIs, schema
     shapes, naming rules, lifecycle wiring, test
     layout, notification formats, architectural
     patterns, conventions, behaviors -- that should
     have triggered a doc update but didn't? Read
     `DEVELOPMENT.md` "Doc-sync rule" for the policy,
     then grep `*.md` for the changed symbols /
     conventions and flag any stale references.
   - For code diffs, evaluate the broader logic the
     changed code participates in. Read the affected
     file(s) in full plus any callers / consumers
     reachable from the changed symbols. Test-suite green
     is necessary but not sufficient.

Tag findings P1 (blocks) / P2 (must-fix-before-shipping) /
P3 (nice-to-have).
```

`<INPUT_LINE>` takes one of two forms.

When a scope file exists:

```text
Review-input file: <INPUT_PATH>. This is the authored
  scope of the change. Read it as the source of truth
  for what the commit was supposed to do.
```

When no scope file exists, fall back to the commit message:

```text
Authored intent: the commit message itself is the only
  authored statement of what this commit was supposed to
  do. Read it as the source of truth, but be aware it
  was written by the implementing agent after the fact
  and may rationalize choices that don't match the
  underlying problem.
```

### Valid vs. invalid rejection rationales

A code-review finding can be appended to `tmp/<slug>-code-review-rejected.md`
only when the reasoning holds up on its own merits. Examples of *valid*
rejections:

- The finding is genuinely out of the diff's blast radius (a different file
  the diff didn't touch, behavior the change doesn't affect).
- The finding contradicts an explicit authored constraint in the review-input
  file.
- The finding's "fix" would re-introduce a regression that an earlier commit
  already resolved.
- The finding is genuinely cosmetic and the fix would meaningfully enlarge the
  diff for negligible value (e.g. reflowing untouched surrounding lines just
  to follow a style guideline the existing code already violates).

The following rationales are NEVER valid for rejecting a finding -- they are
rationalizations for shipping a half-job:

- "The existing X is already incomplete / stale / broken, so fixing only the
  new piece would be inconsistent and a thorough sweep is out of scope." Past
  staleness is never a license for new staleness. If the change touched the
  stale surface (added entries to a list, modified a classification, edited a
  section), do the full work to leave it correct, including the pre-existing
  gaps the diff exposed.
- "It's only nice-to-have / P3, so it's optional." The P-tag indicates
  ship-blocking severity, not whether to do the work. P3 findings local to the
  diff still get fixed.
- "Adding it would be defensive against an unrelated future regression." If
  the surface is in the diff's blast radius, the agent owns making it correct
  now, not punting it to a hypothetical future agent.
- "Doing it thoroughly is out of scope." If the work is in the diff's blast
  radius, scope expanded the moment the diff touched the surface. Either do
  the full work or be specific about *which sub-task* is genuinely separable
  and offer a follow-up.

If a finding genuinely belongs in a separate follow-up commit (not just a
rejection), surface that as an explicit suggestion to the user with the
proposed scope, rather than self-rejecting. The user decides whether to fold
it in or defer.

## Dev-deploy verification

When the user asks for a `./scripts/dev-deploy.py` test cycle, the deploy is
NOT verified until each step below has cleared. Skipping any step risks a
"clean deploy" that silently changed user-visible behavior.

### Pre-deploy: baselines

Before running `./scripts/dev-deploy.py`, capture two snapshots so the
post-deploy diffs in steps 6 + 7 have something to compare against:

- **Persistent notifications** to `tmp/dev-deploy-pn-baseline.json`. PNs
  aren't exposed as `/api/states` entities in HA 2026.4+; fetch via the
  websocket `persistent_notification/get` command.
- **Diagnostic state entity attributes** to
  `tmp/dev-deploy-state-baseline.json`. For every `blueprint_toolkit.*_state`
  entity, save the full attribute dict (`/api/states/<entity_id>`).

### Post-deploy

Run all seven checks in order. Checks 1-4 are integration-level sanity; 5-7
are user-visible-behavior validation against the pre-deploy baselines.

1. **HA back online.** Poll via SSH+internal-HTTP (the dev-machine HTTPS path
   fails during restart):

   ```bash
   until ssh root@homeassistant \
       curl -sf http://homeassistant:8123/api/ >/dev/null; do
       sleep 2
   done
   ```

2. **Config entry loaded.** `blueprint_toolkit` config entry shows
   `state: loaded` with no `reason` set. `setup_error` here means the
   integration raised at setup.

3. **Symlinks correct.** `/config/custom_components/blueprint_toolkit` points
   at the new timestamped snapshot; the
   `/config/blueprints/automation/blueprint_toolkit/*.yaml` symlinks point at
   bundle paths under the same snapshot.

4. **No new HA log errors.** Read the live HA log via
   `ssh root@homeassistant 'ha core logs --lines 5000'` -- NOT by tailing
   `/homeassistant/home-assistant.log*` on the host, which is stale and does
   not reflect the current run. Pass `--lines` with a window large enough to
   span both the restart timestamp and a few minutes of post-restart activity
   (5000 is a safe default; bump it if the deploy fired many entries or the
   restart was a while ago). Filter for `custom_components.blueprint_toolkit`
   at WARNING / ERROR / EXCEPTION level since the restart timestamp.

   Expected baseline noise that is NOT a regression:

   - `homeassistant.loader` WARNING "We found a custom integration
     blueprint_toolkit which has not been tested by Home Assistant" --
     standard HA notice for any custom integration, fires on every restart.
   - Per-handler WARNING summaries from any automation configured with
     `debug_logging=true`. These are intentional one-line run summaries (e.g.
     `[ZRM: ...] configured=N applied=N pending=0 errored=0 ...`); compare
     against the pre-deploy baseline if unsure. WARNING is the per-handler
     debug log level (HA's default for custom-component INFO is silent), so
     any toggled-on debug log will surface here.
   - Pre-existing unrelated errors that survived the deploy (e.g. stale entity
     references from other integrations). Compare against the pre-deploy log
     capture: an entry that was already present and unchanged is not a
     regression. New stack traces or "failed to register service" messages
     from `custom_components.blueprint_toolkit` ARE regressions.

5. **All diagnostic state entities populated.** Every
   `blueprint_toolkit.<service_tag>_<slug>_state` entity has a `last_run`
   post-restart and a valid `state`:

   - Watchdog handlers (DW, EDW, RW, ZRM): `state="ok"` with non-zero
     `runtime`.
   - Trigger-driven handlers (STSC, TEC): `state` is one of the handler's
     `Action` enum values (`NONE`, `TURN_ON`, `TURN_OFF`, ...); `runtime` may
     be `0.0` for no-op evaluations.

   Force a run via `automation.trigger` instead of waiting for periodic ticks.
   The state entity is the per-instance liveness signal -- a loaded config
   entry can still leave individual handlers broken (recovery kick failed,
   periodic timer didn't arm, instance state never built).

6. **Persistent notifications match baseline.** After every blueprint-backed
   automation has run at least once, re-fetch `blueprint_toolkit_*` PNs and
   diff against the pre-deploy baseline:

   - **In post but not pre** (newly fired): regression UNLESS the deploy added
     a new check or notification category. Verify the new body shape matches
     the design.
   - **In pre but not post** (cleared): regression UNLESS the deploy removed a
     check or fixed a bug whose finding cleared.
   - **In both, content changed**: investigate. Usually a notification-body or
     instance-ID-prefix drift.

   This diff catches stream-level regressions that steps 1-5 miss -- an
   automation that runs cleanly to completion but produces a different
   notification stream than before. Common case: a notification-ID prefix
   change leaves the old PN orphaned AND emits a new one with a different ID;
   checks 1-5 see only "ran cleanly" but the user sees two notifications where
   there was one.

7. **Diagnostic state attributes match baseline (sanity check).** Step 5 only
   confirms the entities are populated; this step confirms the values make
   sense. After every blueprint-backed automation has run, re-fetch all
   `blueprint_toolkit.*_state` entities and diff their attribute dicts against
   `tmp/dev-deploy-state-baseline.json`. Any change should be explainable by
   the deploy:

   - **For watchdogs** (DW, EDW, RW, ZRM): per-handler stat counters should be
     unchanged unless the deploy modified detection / scan / reconcile logic.
     Concrete examples of expected drift:
     - DW's `disabled_diagnostic_count` drops by 1 -> a deploy that enabled an
       entity, OR the user enabled it manually between baseline and now.
     - RW's `refs_total` jumps by 467 -> a deploy that added new reference-set
       sources (the audit-style change).
     - ZRM's `routes_applied` increments by 1 -> a deploy that fixed a
       reconcile bug AND a route was successfully applied this run.
   - **For trigger-driven handlers** (STSC, TEC): only compare `last_run` and
     `runtime`. Their state attributes (`switch_state`, `auto_off_at`,
     `controlled_on`, etc.) change naturally on every trigger and aren't a
     regression signal across a deploy.

   Unexpected drift is a regression signal in the same class as a new
   persistent notification appearing. Debug it: which handler? which
   attribute? which code path could have moved the value? If the answer isn't
   immediate, the deploy isn't verified -- it just looks clean.

### Feature-specific verification

On top of the seven standard checks, exercise the specific code path the
deploy carries. **Conditional on the deploy actually changing that component**
-- a deploy that only touched ZRM doesn't need DW / EDW / RW exercised, and
vice versa.

- **ZRM**: add or remove a priority route on a FLiRS or non-battery-powered
  device, then wait for the next reconcile (or trigger manually) and confirm
  ZRM applied or cleared the route as expected. Battery-powered devices don't
  work for this test -- their route changes don't apply until the device wakes
  up, which can be hours. The user's environment has FLiRS locks (preferred)
  and Z-Wave repeaters available.
- **DW / EDW / RW**: trigger a synthetic finding (disable a diagnostic entity
  for DW; rename an entity to drift it for EDW; introduce a broken reference
  in a YAML file for RW), then verify the expected notification fires with the
  right ID and body.
- **STSC / TEC**: trigger the sensor or state change that should fire the
  controller, then verify the right action (`turn_on` / `turn_off` / no-op)
  ran and the diagnostic state entity reflects the decision.

### Blueprint-compatibility breakage during dev-deploy testing

If a deploy lands a blueprint-input rename or schema change that breaks the
user's existing automation YAML (input keys no longer recognised, missing
required key error in argparse, stale enum value, etc.), the right move is to
**update the user's automation config on the host to match the new blueprint
shape** -- not treat it as a verification failure. The dev environment is the
only live deployment; rolling a new blueprint shape forward is the expected
workflow for renames and schema changes.

Concretely: edit `/config/automations.yaml` (or the relevant include file) on
the host so the existing automation matches the new input names, save, reload
automations. Then re-run the relevant verification checks. Surface what was
changed and why in the dev-deploy report.

This is a deliberate carve-out from the "no edits beyond `tmp/` on the host"
rule below: rename and schema-change deploys may require fixing the in-tree
YAML on the host.

### What I do NOT do without explicit ask

`./scripts/dev-deploy.py --restore` (reverses the deploy), restart HA again
after verification, push commits, edit anything beyond `tmp/` on the host
(with the carve-out for blueprint-compatibility breakage above). Even if a
verification step fails, surface the specific failure (which check + the exact
log line / state-entity attribute / notification body) and let the user
decide.

## Push and review hygiene

- Never push without explicit per-action approval. Local commits + ff-merge to
  local master are fine; `git push` is not. The user reviews each commit and
  authorizes the push. This applies to every commit, including amended ones
  from code-review feedback.
- Stay in scope. Each commit edits only what its own description calls for.
  Adjacent cleanup that "would be nice to do anyway" goes into a separate
  commit, OR is bumped into a new entry in the relevant tracking doc.
