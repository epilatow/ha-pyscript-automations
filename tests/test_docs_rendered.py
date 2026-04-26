#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pytest",
#     "pytest-cov",
#     "ruff",
#     "mypy",
#     "markdown-it-py==3.0.0",
# ]
# ///
# This is AI generated code
"""Drift test for the rendered automation docs.

Runs ``scripts/render_docs.py --check`` as a subprocess and
asserts exit 0. The renderer itself enforces three things:

    1. Every markdown source under bundled/docs/ has a
       corresponding rendered HTML under
       bundled/www/blueprint_toolkit/docs/.
    2. No orphan HTML exists without a matching markdown
       source.
    3. The committed HTML matches what the renderer
       produces today (byte-for-byte).

A failing test means someone edited a doc markdown file
without re-running ``scripts/render_docs.py``, or added/
removed a doc without regenerating HTML. The renderer's
stderr names the exact command to run.

Also exercises the renderer's pure functions directly to
catch regressions in rendering logic without spinning up
a subprocess.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from conftest import CodeQualityBase

REPO_ROOT = Path(__file__).parent.parent
RENDER_SCRIPT = REPO_ROOT / "scripts" / "render_docs.py"

# Ensure scripts/ is on sys.path so we can import the
# renderer's internals for direct unit tests. The script
# has a uv-script shebang with an inline markdown-it-py
# dep; the test inherits that dep via its own uv-script
# dependency list at the top of this file.
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import render_docs  # noqa: E402


class TestRenderDocsDriftCheck:
    """End-to-end drift check via subprocess."""

    def test_check_passes_on_clean_repo(self) -> None:
        r = subprocess.run(
            [str(RENDER_SCRIPT), "--check"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        assert r.returncode == 0, (
            "render_docs --check reported drift. This usually "
            "means a docs markdown file was edited without "
            "re-running 'scripts/render_docs.py'. Run it, "
            "review the diff, and commit.\n\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )


class TestRenderDocsMapping:
    """One-to-one mapping between sources and rendered outputs."""

    def test_every_md_has_html(self) -> None:
        mds = sorted(
            p.stem
            for p in render_docs.DOCS_SRC.glob("*.md")
            if not p.name.endswith("~")
        )
        htmls = sorted(p.stem for p in render_docs.DOCS_DST.glob("*.html"))
        missing = sorted(set(mds) - set(htmls))
        assert not missing, (
            f"markdown sources without rendered html: {missing}. "
            "Run 'scripts/render_docs.py' to generate them."
        )

    def test_no_orphan_html(self) -> None:
        mds = sorted(
            p.stem
            for p in render_docs.DOCS_SRC.glob("*.md")
            if not p.name.endswith("~")
        )
        htmls = sorted(p.stem for p in render_docs.DOCS_DST.glob("*.html"))
        orphans = sorted(set(htmls) - set(mds))
        assert not orphans, (
            f"rendered html files with no matching markdown source: "
            f"{orphans}. Delete them, or add the matching .md."
        )


class TestRenderFileUnit:
    """Direct unit tests against renderer internals."""

    def test_title_comes_from_first_h1(self) -> None:
        assert (
            render_docs._title_from_markdown(
                "# Hello World\n\nbody\n",
                fallback="ignored",
            )
            == "Hello World"
        )

    def test_title_falls_back_when_no_h1(self) -> None:
        assert (
            render_docs._title_from_markdown(
                "no heading here\n",
                fallback="stem-name",
            )
            == "stem-name"
        )

    def test_render_file_wraps_title_and_body(self, tmp_path: Path) -> None:
        src = tmp_path / "sample.md"
        src.write_text("# Sample\n\nOne paragraph.\n")
        out = render_docs.render_file(src, render_docs._make_markdown())
        assert "<title>Sample</title>" in out
        assert "<p>One paragraph.</p>" in out
        assert out.startswith("<!DOCTYPE html>")
        assert out.rstrip().endswith("</html>")

    def test_render_file_renders_tables(self, tmp_path: Path) -> None:
        src = tmp_path / "t.md"
        src.write_text("# T\n\n| a | b |\n| - | - |\n| 1 | 2 |\n")
        out = render_docs.render_file(src, render_docs._make_markdown())
        assert "<table>" in out
        assert "<th>a</th>" in out
        assert "<td>1</td>" in out


class TestRenderAllIsIdempotent:
    """Running the renderer twice never touches the second write."""

    def test_rerun_is_noop(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        (src / "a.md").write_text("# A\n\ntext\n")
        md = render_docs._make_markdown()

        first = render_docs.render_all(src, dst, md)
        assert len(first) == 1

        second = render_docs.render_all(src, dst, md)
        assert second == []


class TestCheckDriftDetects:
    """--check-equivalent API reports missing, orphan, and drift."""

    def test_missing_html_is_flagged(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        (src / "missing.md").write_text("# M\n")
        diags = render_docs.check_drift(src, dst, render_docs._make_markdown())
        assert any("missing output" in d for d in diags)

    def test_orphan_html_is_flagged(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        (dst / "ghost.html").write_text("<!-- no source -->")
        diags = render_docs.check_drift(src, dst, render_docs._make_markdown())
        assert any("orphan output" in d for d in diags)

    def test_content_drift_is_flagged(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        dst.mkdir()
        (src / "a.md").write_text("# A\n\nfresh\n")
        (dst / "a.html").write_text("<html>stale</html>")
        diags = render_docs.check_drift(src, dst, render_docs._make_markdown())
        assert any("content drift" in d for d in diags)

    def test_clean_state_reports_no_drift(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        (src / "a.md").write_text("# A\n\nhello\n")
        md = render_docs._make_markdown()
        render_docs.render_all(src, dst, md)
        diags = render_docs.check_drift(src, dst, md)
        assert diags == []


class TestCodeQuality(CodeQualityBase):
    ruff_targets = [
        "scripts/render_docs.py",
        "tests/test_docs_rendered.py",
    ]
    mypy_targets = [
        "scripts/render_docs.py",
    ]


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(
        test_file=__file__,
        script_path=RENDER_SCRIPT,
        repo_root=REPO_ROOT,
    )
