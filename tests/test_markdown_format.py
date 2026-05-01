#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pytest",
#     "pytest-cov",
#     "ruff",
#     "mypy",
#     "mdformat==0.7.22",
#     "mdformat-gfm==0.4.1",
#     "mdformat-tables==1.0.0",
# ]
# ///
# This is AI generated code
"""Enforce canonical markdown formatting via ``mdformat --check``.

mdformat is the deterministic formatter for every committed
``*.md`` file; markdownlint covers content rules (broken
anchors, missing fence languages, etc.). The two tools agree
on every formatting rule by design (mdformat defaults match
the formatting rules in ``.markdownlint.json``).

Pinning mdformat + mdformat-gfm + mdformat-tables versions in
the script's PEP 723 deps keeps the formatter behaviour
locked across machines -- a future plugin update that
re-canonicalises something would otherwise fail this test
silently on whichever dev runs it next.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from conftest import CodeQualityBase  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent

# Mirror ``.markdownlint-cli2.jsonc``'s scope so the two
# linters cover the same files. Walk every ``*.md`` under
# the repo root and exclude the same directory prefixes
# cli2 ignores -- enumerating explicitly here would silently
# miss any new top-level doc.
_IGNORED_DIR_PREFIXES = (
    "node_modules/",
    ".venv/",
    ".pytest_cache/",
    ".git/",
    "tmp/",
    # ``docs/`` is a repo-root symlink into bundled/docs;
    # cli2 ignores it to avoid double-linting.
    "docs/",
)


def _markdown_targets() -> list[str]:
    targets: list[str] = []
    for path in REPO_ROOT.rglob("*.md"):
        rel = path.relative_to(REPO_ROOT).as_posix()
        if any(
            rel.startswith(p) or f"/{p}" in f"/{rel}"
            for p in _IGNORED_DIR_PREFIXES
        ):
            continue
        # Skip ``**/.cache/**`` (cli2 ignore) explicitly --
        # rare in this repo but keeps the test scope honest.
        if "/.cache/" in rel or rel.startswith(".cache/"):
            continue
        targets.append(rel)
    return sorted(targets)


def test_mdformat_check_clean() -> None:
    """Every committed ``*.md`` matches the canonical format."""
    targets = _markdown_targets()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "mdformat",
            "--wrap=78",
            "--number",
            "--check",
            *targets,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        msg = (
            "mdformat reported drift. Run "
            "``uvx --with mdformat-gfm --with mdformat-tables "
            "mdformat --wrap=78 --number <path>`` "
            "from the repo root on the listed file(s) to "
            "canonicalise them.\n\n"
            f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
        )
        raise AssertionError(msg)


class TestCodeQuality(CodeQualityBase):
    ruff_targets = ["tests/test_markdown_format.py"]
    mypy_targets: list[str] = []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", *sys.argv[1:]]))
