#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest", "pytest-cov", "ruff", "mypy"]
# ///
# This is AI generated code
"""Ruff + mypy coverage for the docker test harness files.

The docker harness lives under ``tests/docker/`` and its own
test classes are gated behind ``@pytest.mark.docker`` (skipped
in default runs). That means a TestCodeQuality class inside
``tests/docker/test_dev_workflow.py`` would also be skipped by
default, leaving the harness Python files without automated
lint/format/type-check coverage.

This file sits at ``tests/`` (not under ``tests/docker/``) so
``tests/run_all.py``'s non-recursive ``test_*.py`` glob picks
it up, and it is NOT docker-marked so it runs in the default
suite. It does not import the harness modules -- it just runs
ruff/mypy over the files as subprocesses via the
``CodeQualityBase`` helpers.
"""

from __future__ import annotations

from pathlib import Path

from conftest import CodeQualityBase


class TestCodeQuality(CodeQualityBase):
    ruff_targets = [
        "tests/docker/conftest.py",
        "tests/docker/test_dev_workflow.py",
        "tests/docker/_harness/ha_onboard.py",
    ]
    mypy_targets = [
        "tests/docker/conftest.py",
        "tests/docker/test_dev_workflow.py",
        "tests/docker/_harness/ha_onboard.py",
    ]


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(
        test_file=__file__,
        script_path=Path(__file__).parent.parent / "tests" / "docker",
        repo_root=Path(__file__).parent.parent,
    )
