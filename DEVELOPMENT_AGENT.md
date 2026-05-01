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

## Push and review hygiene

- Never push without explicit per-action approval. Local commits + ff-merge to
  local master are fine; `git push` is not. The user reviews each commit and
  authorizes the push. This applies to every commit, including amended ones
  from code-review feedback.
- Stay in scope. Each commit edits only what its own description calls for.
  Adjacent cleanup that "would be nice to do anyway" goes into a separate
  commit, OR is bumped into a new entry in the relevant tracking doc.
