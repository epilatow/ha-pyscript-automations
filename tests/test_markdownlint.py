#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest", "pytest-cov", "ruff", "mypy"]
# ///
# This is AI generated code
"""Run markdownlint-cli2 across every committed *.md file.

The lint config lives in ``.markdownlint.json`` (rules) and
``.markdownlint-cli2.jsonc`` (globs + ignores).

Shells out to ``npx markdownlint-cli2``; ``npx`` and a
recent Node toolchain must be on ``$PATH`` (the project
already invokes ``npx difit`` for code review per
``CLAUDE.md``, so Node is assumed available on developer
machines and CI). On systems without ``npx``, the test
SKIPs rather than failing.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import CodeQualityBase  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


def _have_npx() -> bool:
    return shutil.which("npx") is not None


@pytest.mark.skipif(
    not _have_npx(),
    reason="npx not on PATH; install Node to run markdownlint",
)
def test_markdownlint_clean() -> None:
    """All committed *.md files satisfy ``.markdownlint.json``."""
    result = subprocess.run(
        ["npx", "--yes", "markdownlint-cli2"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        msg = (
            "markdownlint-cli2 reported violations. Run "
            "``npx markdownlint-cli2 --fix`` from the repo "
            "root to auto-fix what's fixable, then resolve "
            "any remaining lines manually.\n\n"
            f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
        )
        raise AssertionError(msg)


class TestCodeQuality(CodeQualityBase):
    ruff_targets = ["tests/test_markdownlint.py"]
    mypy_targets: list[str] = []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", *sys.argv[1:]]))
