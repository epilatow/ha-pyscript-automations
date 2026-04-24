#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["markdown-it-py==3.0.0"]
# ///
# This is AI generated code
"""Render automation docs from markdown to HTML.

Walks every ``*.md`` under
``custom_components/ha_pyscript_automations/bundled/docs/`` and
writes a corresponding ``<stem>.html`` into
``custom_components/ha_pyscript_automations/bundled/www/ha_pyscript_automations/docs/``.
HACS ships the ``bundled/`` subtree; when the integration's
reconciler installs it, the HTML directory lands under
``/config/www/ha_pyscript_automations/docs/`` and becomes
reachable at ``/local/ha_pyscript_automations/docs/<stem>.html``
-- the URL the blueprint descriptions link to.

Usage
-----

``scripts/render_docs.py``
    Render every markdown file whose output differs. Writes
    are skipped when the target already matches byte-for-byte,
    so the command is a no-op on a clean checkout.

``scripts/render_docs.py --check``
    Render into a tempdir, diff against the committed HTML,
    and exit non-zero if anything differs. The drift test
    (``tests/test_docs_rendered.py``) runs this mode; a
    failing test means you edited markdown without
    re-running the renderer.

Markdown flavor
---------------

Uses ``markdown-it-py`` in CommonMark mode with the table
extension enabled. Output is deterministic: same input,
pinned markdown-it-py version (see the script header),
same output.

Template
--------

A minimal single-file HTML wrapper with an inline stylesheet
kept small enough to read at a glance. No HA frontend styling
-- this renders as a stand-alone documentation page, not
inside the HA UI, because the ``/local/`` redirect the
blueprint description targets serves static files outside the
frontend's theming.
"""

from __future__ import annotations

import argparse
import difflib
import sys
import tempfile
from pathlib import Path

from markdown_it import MarkdownIt

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_SRC = (
    REPO_ROOT
    / "custom_components"
    / "ha_pyscript_automations"
    / "bundled"
    / "docs"
)
DOCS_DST = (
    REPO_ROOT
    / "custom_components"
    / "ha_pyscript_automations"
    / "bundled"
    / "www"
    / "ha_pyscript_automations"
    / "docs"
)

# Minimal HTML wrapper: inline CSS for a readable default
# without pulling any framework. Kept short by design;
# changes here change every rendered page, so keep the
# footprint modest.
HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
body {{
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
    Roboto, "Helvetica Neue", Arial, sans-serif;
  max-width: 760px;
  margin: 2em auto;
  padding: 0 1em;
  line-height: 1.55;
  color: #222;
}}
h1, h2, h3, h4 {{ line-height: 1.25; }}
h1 {{ border-bottom: 1px solid #ddd; padding-bottom: .2em; }}
h2 {{ border-bottom: 1px solid #eee; padding-bottom: .15em; }}
code {{
  background: #f2f2f2;
  padding: .1em .3em;
  border-radius: 3px;
  font-size: .92em;
}}
pre {{
  background: #f6f6f6;
  padding: .8em 1em;
  border-radius: 4px;
  overflow-x: auto;
}}
pre code {{ background: none; padding: 0; font-size: .9em; }}
table {{ border-collapse: collapse; margin: 1em 0; }}
th, td {{
  border: 1px solid #ccc;
  padding: .4em .7em;
  text-align: left;
  vertical-align: top;
}}
th {{ background: #f2f2f2; }}
blockquote {{
  border-left: 3px solid #ccc;
  margin: 1em 0;
  padding: .2em 1em;
  color: #555;
}}
a {{ color: #0366d6; }}
</style>
</head>
<body>
{body}</body>
</html>
"""


def _make_markdown() -> MarkdownIt:
    return MarkdownIt("commonmark").enable("table")


def _title_from_markdown(text: str, fallback: str) -> str:
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
        if stripped:
            # First non-blank, non-H1 line ends the search.
            break
    return fallback


def render_file(md_path: Path, md: MarkdownIt) -> str:
    text = md_path.read_text()
    body = md.render(text)
    title = _title_from_markdown(text, md_path.stem)
    return HTML_TEMPLATE.format(title=title, body=body)


def _list_sources(src: Path) -> list[Path]:
    # Skip editor backup files like foo.md~ even though
    # the glob wouldn't match '*.md~' directly; guard
    # belt-and-suspenders in case someone invokes with a
    # different glob.
    return sorted(p for p in src.glob("*.md") if not p.name.endswith("~"))


def _dest_for(source: Path, dst_root: Path) -> Path:
    return dst_root / f"{source.stem}.html"


def render_all(src: Path, dst: Path, md: MarkdownIt) -> list[Path]:
    """Render every markdown file in ``src`` into ``dst``.

    Returns the list of output paths that were written
    (either created or updated). Unchanged outputs are
    skipped so mtimes stay stable for reproducibility.
    """
    dst.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for source in _list_sources(src):
        out_path = _dest_for(source, dst)
        new_content = render_file(source, md)
        if out_path.exists() and out_path.read_text() == new_content:
            continue
        out_path.write_text(new_content)
        written.append(out_path)
    return written


def _display(path: Path) -> str:
    # Render paths relative to the repo root when possible
    # for compact diagnostics; fall back to absolute paths
    # so unit tests that pass tempdirs work unchanged.
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def check_drift(src: Path, dst: Path, md: MarkdownIt) -> list[str]:
    """Render into a tempdir and diff against ``dst``.

    Returns a list of human-readable diagnostic messages.
    Empty list means no drift. A non-empty list signals
    content drift, missing HTML, or an orphan HTML with
    no matching markdown source.
    """
    diagnostics: list[str] = []
    sources = _list_sources(src)

    # 1. Missing HTML: markdown source has no corresponding
    # output.
    for source in sources:
        out_path = _dest_for(source, dst)
        if not out_path.exists():
            diagnostics.append(
                f"missing output: {_display(out_path)} "
                f"(source: {_display(source)})",
            )

    # 2. Orphan HTML: output exists with no corresponding
    # markdown source.
    expected_stems = {p.stem for p in sources}
    if dst.exists():
        for out_path in sorted(dst.glob("*.html")):
            if out_path.stem not in expected_stems:
                diagnostics.append(
                    f"orphan output: {_display(out_path)} "
                    "(no matching .md source)"
                )

    # 3. Content drift: render fresh into a tempdir and
    # compare byte-for-byte with what's checked in.
    with tempfile.TemporaryDirectory(prefix="render-docs-check-") as tmp:
        tmp_dst = Path(tmp)
        for source in sources:
            expected = render_file(source, md)
            staging = tmp_dst / _dest_for(source, dst).name
            staging.write_text(expected)
            committed = _dest_for(source, dst)
            if not committed.exists():
                continue  # already flagged above
            actual = committed.read_text()
            if actual != expected:
                delta = "".join(
                    difflib.unified_diff(
                        actual.splitlines(keepends=True),
                        expected.splitlines(keepends=True),
                        fromfile=_display(committed),
                        tofile=f"{_display(committed)} (expected)",
                        n=3,
                    )
                )
                diagnostics.append(
                    f"content drift in {_display(committed)}:\n{delta}",
                )

    return diagnostics


def main() -> int:
    p = argparse.ArgumentParser(
        description=(
            "Render markdown automation docs to HTML. "
            "Reads from bundled/docs/, writes to bundled/www/.../docs/."
        ),
    )
    p.add_argument(
        "--check",
        action="store_true",
        help=(
            "Render into a tempdir and diff against the "
            "committed HTML. Exit non-zero if anything "
            "differs. Used by tests/test_docs_rendered.py."
        ),
    )
    args = p.parse_args()

    md = _make_markdown()

    if args.check:
        diagnostics = check_drift(DOCS_SRC, DOCS_DST, md)
        if diagnostics:
            for msg in diagnostics:
                sys.stderr.write(msg + "\n")
            sys.stderr.write(
                "\nRe-run scripts/render_docs.py to update the "
                "committed HTML.\n",
            )
            return 1
        return 0

    written = render_all(DOCS_SRC, DOCS_DST, md)
    if written:
        for out in written:
            print(f"rendered: {out.relative_to(REPO_ROOT)}")
    else:
        print("no changes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
