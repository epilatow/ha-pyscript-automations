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

## Push and review hygiene

- Never push without explicit per-action approval. Local commits + ff-merge to
  local master are fine; `git push` is not. The user reviews each commit and
  authorizes the push. This applies to every commit, including amended ones
  from code-review feedback.
- Stay in scope. Each commit edits only what its own description calls for.
  Adjacent cleanup that "would be nice to do anyway" goes into a separate
  commit, OR is bumped into a new entry in the relevant tracking doc.
