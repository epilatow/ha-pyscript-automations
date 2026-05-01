# Development Guide

Process content for working on this repo: code review, doc hygiene, testing,
deploys, releases. The companion [AUTOMATIONS.md](AUTOMATIONS.md) covers
conventions + patterns specific to writing or modifying an automation -- read
that when adding a new automation, modifying an existing one, or reviewing
such a change.

## Repo layout

This repo ships as a HACS-distributed custom integration. Two external
constraints drive the top-level layout; everything below that is our own
convention.

Home Assistant's custom-integration loader looks for integrations at
`/config/custom_components/<domain>/`, where `<domain>` matches the
integration's `manifest.json` domain. That's why the repo's installable code
lives under `custom_components/blueprint_toolkit/` -- when HACS (or a
developer's manual install) puts that subtree at
`/config/custom_components/blueprint_toolkit/`, HA finds it.

HACS itself only downloads files from the repo that live under
`custom_components/<name>/`; it discards everything outside that subtree at
install time. So every file we want users to receive has to live inside
`custom_components/blueprint_toolkit/`.

Integration code (`__init__.py`, `manifest.json`, `reconciler.py`,
`installer.py`, `config_flow.py`, `repairs.py`, etc.) lives at the
`custom_components/blueprint_toolkit/` level. Each per-automation handler
lives in a subpackage (`<service>/handler.py`, `<service>/logic.py`). The
`bundled/` subdirectory ships blueprints, docs, and CLI scripts to
user-visible `/config/...` paths via symlinks. See
[AUTOMATIONS.md](AUTOMATIONS.md) for the full per-automation layout.

The repo root keeps two committed symlinks into the bundle for path-typing
convenience:

```text
blueprints -> custom_components/blueprint_toolkit/bundled/blueprints
docs       -> custom_components/blueprint_toolkit/bundled/docs
```

These are real git symlinks (mode 120000). They live outside HACS's download
path and are never shipped to users.

**POSIX filesystem required for development.** The repo uses mode-120000
symlinks that Windows (with default git settings) checks out as text files
containing the target path, which breaks tests, install scripts, and deploys.
Use macOS or Linux; WSL2 works too.

Brand assets for the HA integration live at `assets/` at the repo root (source
SVG + 256/512 PNG renders). They are only consumed by a future PR to
`home-assistant/brands`; they do not ship inside the bundle because HA's UI
only reads brand assets from the brands CDN.

## File conventions

### Shebangs

- **Executable scripts** (test files, `run_all.py`) use the PEP 723 shebang
  `#!/usr/bin/env -S uv run --script` with inline dependency declarations.
- **Module files** (handler.py, logic.py, helpers.py, etc.) have no shebang.
  They are imported, not executed directly.

### Test files

- Must be executable (`chmod +x`) with a `__main__` entry point calling
  `conftest.run_tests()`.
- Use pytest.
- Use `autospec=True` for all mocks.
- Include a `TestCodeQuality(CodeQualityBase)` class that specifies
  `ruff_targets` and `mypy_targets` for the module under test.

### Comments

Comments should not repeat what the code is doing. When used they should
augment the code: provide additional context, explain the why, document
non-obvious requirements or side effects, flag invariants the type system
can't enforce.

- Don't number steps in comments (`# 1. Parse state`). Numbering is
  unnecessary and adding / removing steps requires renumbering.
- Don't reference user-reported bugs ("Regression guard for the user-reported
  bug where ..."). Describe what the code does and the constraint it enforces;
  bug history belongs in the commit message.
- Don't talk about deleted, replaced, or formerly-existing code. Comments
  document the *current* code -- the version a future reader is looking at.
  Phrases like "the per-handler `_FooBar` shim is gone", "wrappers have all
  been deleted", "this used to live in helpers.py" make sense to whoever wrote
  the diff but are noise (or actively misleading) to anyone reading the file
  later. If a comment names a symbol or behavior, that thing must exist now.
  Migration history belongs in the commit message that did the migration.

### ASCII only

Source files, comments, commit messages, docs, PR bodies, etc. are ASCII only.
The only carve-out is test fixtures that simulate non-ASCII input that the
code under test must handle. Common slips to watch for and the ASCII
replacements:

- Em / en dashes (`--` instead).
- Curly quotes (straight `'` and `"`).
- Ellipsis (`...` instead).
- Unicode arrows (`->` instead).
- Unicode bullets (`-` or `*` instead).

## Doc-sync rule

**Documentation is part of the change, not a follow-up.** When code changes,
every doc that describes that code changes in the same commit. No exceptions,
no "I'll do the docs in a follow-up" -- doc and code commit together so
reviewers see both at once.

Before committing a code change, walk through every markdown file the change
could touch and verify it still matches reality. The pre-commit checklist:

- `bundled/docs/<service>.md` -- if the automation's inputs, attributes, or
  behavior changed, update the service doc and re-run `scripts/render_docs.py`
  to regenerate the HTML. The drift test (`tests/test_docs_rendered.py`)
  enforces the markdown + HTML pair.
- `AUTOMATIONS.md` -- update when conventions, shared helpers, naming rules,
  or per-automation file layout change.
- `DEVELOPMENT.md` -- update when dev-process tooling changes: new test
  conventions, new lint rules, new release script, **renamed or removed CLI
  flags on `dev-deploy.py` / `dev-install.py` / `release.py`**, new required
  steps in the develop / test / commit cycle.
- `DEVELOPMENT_AGENT.md` -- update when agent-specific workflow changes
  (review protocol, file markers, etc.).
- `README.md` -- update when an automation is added, removed, or its one-line
  blurb changes.

Stale, invalid, and broken docs waste every reader's time -- users follow
steps that no longer work, devs chase behaviors the code stopped doing.
**Every code change is a potential doc change.** This repo documents not just
CLI surfaces and behaviors but also conventions: architectural patterns,
naming rules, schema shapes, helper APIs, test layout, lifecycle wiring,
notification formats, and more. Before finalizing the commit message, grep the
repo for any symbol, flag, convention, or behavior the diff touched and update
every doc that mentions it.

## Per-automation docs

The per-automation user-doc structure (Summary / Features / Requirements /
etc.) lives in `AUTOMATIONS.md`. This section covers the rendering pipeline.

### Rendered HTML

The markdown sources under `docs/` are rendered to HTML that ships alongside
the integration so HACS users can click a "Full documentation" link from each
blueprint directly to a local page.

- Source: `custom_components/blueprint_toolkit/bundled/docs/*.md` (also
  reachable via the `docs/` symlink at the repo root).
- Rendered:
  `custom_components/blueprint_toolkit/bundled/www/blueprint_toolkit/docs/*.html`.
  Committed alongside the source.
- Renderer: `scripts/render_docs.py` (CommonMark + tables, minimal inline-CSS
  template, `markdown-it-py` pinned for deterministic output). Idempotent --
  it only writes files whose content changed.

After editing any `*.md`, **re-run the renderer and commit the regenerated
HTML in the same commit**:

```bash
scripts/render_docs.py
```

A drift test (`tests/test_docs_rendered.py`) runs
`scripts/render_docs.py --check` and fails if the committed HTML is out of
date, if a markdown source is missing its HTML counterpart, or if an orphan
HTML has no matching source. The test's failure message points at the render
command.

#### How rendered HTML reaches the user's browser

The rendered HTML is **not** installed under `/config/www/`. HA's default
`/local/` static handler:

- Is only registered at HA startup if `/config/www/` already exists (our
  integration runs after that).
- Refuses to follow symlinks whose targets escape `/config/www/`, which is
  exactly what an installed symlink-into-bundled would do.

Instead, the integration's `async_setup_entry` registers its own aiohttp
static route at `/local/blueprint_toolkit/docs/` pointing directly at the
bundled docs directory. Real files, no symlinks, no dependency on
`/config/www/`.

**dev-install limitation**: users on the `scripts/dev-install.py` path don't
load the HA integration, so the static route is never registered and the
blueprints' `/local/.../docs/...html` links 404. Read the markdown sources
under `bundled/docs/` directly during dev work.

## Markdown style

Two complementary tools enforce markdown style:

- **`mdformat`** is the canonical formatter. Pinned via PEP 723 deps in
  `tests/test_markdown_format.py`; runs `mdformat --wrap=78 --number --check`
  with the `mdformat-gfm` and `mdformat-tables` plugins. Owns every mechanical
  formatting decision: line wrap at 78 chars, ordered-list numbering,
  bullet/emphasis/strong markers, blank-line spacing, table alignment. To
  re-canonicalise a doc after editing:

  ```bash
  uvx --with mdformat-gfm --with mdformat-tables \
      mdformat --wrap=78 --number <path>
  ```

- **`markdownlint-cli2`** covers content rules `mdformat` can't see: required
  fence languages (MD040), broken anchor links (MD051), reference-link
  consistency (MD052/MD053), missing alt text (MD045), duplicate headings
  (MD024), and similar. Config lives in `.markdownlint.json`; enforced by
  `tests/test_markdownlint.py`.

The two tools agree on every formatting rule by design -- `mdformat`'s
defaults match the formatting rules in `.markdownlint.json` (dash bullets,
asterisk emphasis, fenced code blocks, etc.). If they ever disagree, the
formatter wins (it's mechanical) and `.markdownlint.json` gets adjusted to
match.

Notable rules from the linter side:

- Line wrap at 78 chars (MD013). Tables, headings, and code blocks are exempt;
  reference-style links (`[label][ref]` ... `[ref]: url`) handle long URLs.
- Numbered lists use ordered prefixes (`1./2./3./...`) per MD029.
- Code fences require a language tag (MD040). Use `text` for plain output,
  `console` for shell sessions with prompts, `bash` / `python` / etc. for
  actual code.
- Tables use the `aligned` style (cells space-padded so column pipes line up
  vertically) per MD060. mdformat-tables produces this layout automatically;
  markdownlint validates it.

Conventions worth knowing about for source layout (mdformat enforces these as
a side effect):

- Reference-style link definitions go at the bottom of the file, not
  interleaved with the list items they're referenced from. Otherwise mdformat
  splits the surrounding list into multiple sub-lists with mismatched bullet
  markers.
- Avoid lines starting with `>` followed by a non-space character (e.g.
  `>=10`) -- a markdown parser treats those as blockquotes, and source reflow
  can land them at line starts.
- Don't put `#<digit>` (issue references like `#1234`) at the start of a
  reflowed line -- they'd be interpreted as malformed ATX headings.

### Prefer lists over tables

In developer-facing markdown (this file, `AUTOMATIONS.md`,
`DEVELOPMENT_AGENT.md`, `README.md`), prefer bulleted lists over markdown
tables. Tables are unreadable in plain text: the columns wrap on narrow
terminals, the cells run together, and the headers blend into the body. We
read these files in `vim` / `less` / `git diff` more than in a rendered
viewer, so the source-readability matters more than the rendered prettiness.

User-facing docs under `bundled/docs/<service>.md` are exempt -- those are
read in HTML / on GitHub more than in plain text, and tabular layout works
well for the configuration / attribute reference sections those docs carry.

## Testing

Always add new tests when adding new functionality.

All tool configuration lives in `pyproject.toml`. Tool caches are redirected
to `/tmp/` to avoid polluting the repo.

### No global pip installs

Never run `pip install`. All dependencies are declared inline via PEP 723
script metadata and resolved automatically by `uv`. Run tools via
`uvx <tool> [args]`.

### Running tests

```bash
# All tests (fast, mock-based)
./tests/run_all.py
./tests/run_all.py --verbose
./tests/run_all.py --coverage

# Single test file
./tests/test_<service>_logic.py
./tests/test_<service>_logic.py --verbose
```

### Docker test harness (opt-in, slow)

`tests/docker/` spins up a real HA container and exercises
`scripts/dev-install.py` and `scripts/dev-deploy.py` end-to-end. Not run by
`tests/run_all.py`; opt in with `pytest -m docker tests/docker`. See
`tests/docker/README.md`.

### pytest-homeassistant-custom-component harness

`tests/test_hacc_harness.py` uses
[`pytest-homeassistant-custom-component`](https://pypi.org/project/pytest-homeassistant-custom-component/)
to stand up an in-process HA instance for tests that need real HA machinery.
Pinned to a specific HA release in the file's PEP 723 dependency block; first
run downloads HA into a uv-script env (~minute), subsequent runs are cached.
Runs in the default suite via `tests/run_all.py`.

`tests/test_integration.py` and per-automation
`tests/test_<service>_integration.py` files use the same harness to exercise
the integration's async lifecycle and each handler's full call path (config
flow, service registration, argparse error paths, state-entity attributes).

### Manifest version bump rule

The integration version in `custom_components/blueprint_toolkit/manifest.json`
must increment with every commit that changes anything under
`custom_components/`, and stay equal otherwise. `tests/test_manifest.py`
enforces both this rule and the canonical formatting
(`json.dumps(..., indent=2)` plus a trailing newline) in the default test run.

Before committing any change under `custom_components/`, bump the patch:

```bash
scripts/bump-manifest-version.py
```

The script rewrites `manifest.json` and re-stages it. For non-patch bumps (a
deliberate minor or major graduation), edit the manifest by hand instead --
the test only checks that the version is strictly greater than the parent's,
not that it's exactly +1.

### Releasing (tag + GitHub release)

HACS surfaces installable versions from GitHub Releases. Once at least one
Release exists in the repo, HACS shows only Releases as version candidates --
commits pushed to master between Releases are invisible to users until the
next Release is published. (Pre-first-Release, HACS falls back to tracking the
latest commit SHA, but that state ends as soon as the first Release lands and
never returns.) Tagging and releasing are kept separate from `git push` so a
manifest bump can sit on master without auto-publishing -- run
`scripts/release.py` only when you want HACS users to see the new version:

```bash
git push                       # land commits on origin
scripts/release.py             # then publish the version at HEAD
scripts/release.py --dry-run   # see what it would do
```

The script reads the version from HEAD's `manifest.json`, creates the local
annotated tag `vX.Y.Z` if missing, pushes the tag, and creates the GitHub
release via `gh` (release notes default to HEAD's commit body). Each step is
idempotent, so re-running after a partial failure picks up where the previous
run stopped.

Refuses if HEAD isn't reachable from `origin/master` -- push commits before
publishing the release.

Non-bumping commits at HEAD (docs-only follow-ups, comment fixes, etc.) are
detected (the version's tag already exists at an older ancestor commit) and
the script exits cleanly without creating a duplicate release.

### Code quality

Lint, format, and type checks are included in each test file's
`TestCodeQuality` class and run automatically as part of the test suite:

- `test_ruff_lint` -- ruff linting
- `test_ruff_format` -- ruff formatting
- `test_mypy_strict` -- mypy strict type checking

### Standalone checks

```bash
uvx ruff check .
uvx ruff format .
```

mypy is run via each test file's `TestCodeQuality(CodeQualityBase)` class,
which installs Home Assistant + voluptuous via PEP 723 inline deps. Bare
`uvx mypy` from a checkout without those deps cannot resolve
`homeassistant.*`; run `./tests/run_all.py` instead.

## HA deployment testing

The repo ships two scripts that move local edits onto a running HA host:
`scripts/dev-deploy.py` (run from the dev machine) and
`scripts/dev-install.py` (run on the HA host itself; invoked by
`dev-deploy.py`). The pair is for iterative dev work against a real HA
instance, not for end-user installs (those go through HACS).

### `scripts/dev-deploy.py` (dev-machine side)

Builds a fresh timestamped copy of the integration on the HA host under
`<workspace>/<YYYYMMDD_HHMMSS>/blueprint_toolkit/` (default workspace:
`/config/ha-blueprint-toolkit`), flips
`/config/custom_components/blueprint_toolkit` to a symlink pointing at the new
build, runs the bundled `scripts/dev-install.py` on the host to refresh the
`/config/blueprints/` symlinks, and restarts HA via the configured restart
command. The first run preserves the HACS-installed integration as
`<workspace>/<vX.Y.Z>/blueprint_toolkit/`; subsequent runs leave that snapshot
alone.

HA is **always restarted** after a deploy (or restore). Integration code
changes (`custom_components/.../*.py`) require a Python-level reload that the
config-entry reload API doesn't provide; the restart is the only reliable way.

By default the working tree must be clean. Useful flags:

- `--restore` -- reverse a prior deploy. Reinstates the preserved HACS
  snapshot at `/config/custom_components/blueprint_toolkit` and removes the
  workspace, leaving the host as if dev-deploy had never run.
- `--allow-dirty` -- skip the clean-tree check and ship working-tree content
  as-is (tracked files with local edits plus untracked files not matching
  `.gitignore`). For iterative dev only.
- `--dry-run` -- print the plan and exit without touching the host.
- `--host <user@host>` -- ssh target. Default `root@homeassistant`.
- `--workspace <path>` -- on-host directory for build snapshots. Default
  `/config/ha-blueprint-toolkit`.
- `--ha-config <path>` -- HA's config dir on the host. Default `/config`.
- `--cli-symlink-dir <path>` -- passed to `dev-install.py`'s
  `--cli-symlink-dir`; controls where the bundled CLI scripts get symlinked.
  Omit to skip CLI install.
- `--ha-restart-cmd <cmd>` -- shell command run on the host after deploy.
  Default `ha core restart`. Override for test environments without the
  supervisor CLI.

### `scripts/dev-install.py` (HA-host side)

Plain Python (no uv). Reads the checked-out repo under `--repo-dir` and
reconciles its bundled payload into `/config/blueprints/` (and optionally
`<cli-symlink-dir>/`) as symlinks pointing back into the bundled subtree.
Idempotent; tracks state in `<ha-config>/.blueprint_toolkit.manifest.json` so
stale symlinks from renamed or removed bundled files get cleaned up on the
next run. Refuses to overwrite regular files at any destination; existing
symlinks whose targets match the bundled marker are treated as ours and
rewritten. Invoked by `dev-deploy.py` on every push, but can also be run
directly on the host during debugging.

Flags: `--ha-config <path>` (default `/config`), `--cli-symlink-dir <path>`
(omit to skip CLI), `--dry-run`.

### Reading state from a deployed instance

Each blueprint-backed automation writes a diagnostic state entity on every
successful run with `last_run`, `runtime`, and per-automation attributes. The
entity ID lives under `blueprint_toolkit.<service_tag>_<slug>_state`:

```bash
curl -s -H "Authorization: Bearer $API_KEY" \
  http://$HA_HOST:8123/api/states/blueprint_toolkit.<service_tag>_<slug>_state
```

Persistent notifications aren't exposed as `/api/states` entities in HA
2026.4+; fetch them via the websocket `persistent_notification/get` command
when you need to verify notification content.

## Commit messages

- Use `- component: Summary of change.` format.
- Include a `Co-Authored-By: <AI Model XXX>` trailer for AI-assisted commits.
- Don't restate the manifest version bump in the commit message; the bump is
  automatic and restating is noise.

**Explain the why, not the what.** The diff already shows what changed. The
commit message should give a future reader the context they can't derive from
the diff: the motivating problem, the constraint or invariant the change
satisfies, and any non-obvious tradeoffs or alternatives considered. Aim for a
one-line subject plus one to three short paragraphs. Past three paragraphs and
you're almost certainly over-explaining.

Specifically, do NOT include:

- Lists of every file or call site touched. The diff enumerates them; if the
  scope is "every site of pattern X" just say so once. This applies to any
  prefix variant -- `Touched:`, `Files changed:`, `Affected:`, `Sites:`, etc.
  -- which is just the same list with a label.
- Test inputs, fixture values, or the specific bad-shape cases a regression
  test exercises -- the test code is the source of truth.
- Sub-decisions that are obvious from the code (which field type was used,
  which helper the code now calls, how a loop is structured).
- Restatements of points already in an earlier paragraph of the same message.
- References to symbols / tests / functions / files that the same diff
  *removes* or replaces. A subject like "Replaces the per-handler
  `_FooHandler.bar` with a generic base" is a trap: future readers grep for
  the named symbol and find nothing because the same commit deleted it. State
  the new artifact on its own terms; the diff already shows the deletion.
- Pointers to ephemeral agent-facing scratch -- "The followup notes ...", "the
  tmp/<slug>-... scope", "as discussed in the earlier review", "the P\<n>
  tracker entry says ...". Followups files, `tmp/` scopes, code-review
  threads, and review-input files are working state that does not survive in
  `git log`. If a constraint matters, restate it inline; if it's just a paper
  trail, drop it.
