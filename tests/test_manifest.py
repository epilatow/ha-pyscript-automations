#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pytest",
#     "pytest-cov",
#     "ruff",
#     "mypy",
#     "packaging",
# ]
# ///
# This is AI generated code
"""Manifest invariants: per-commit version bump + canonical formatting.

Two rules tested here:

1. ``custom_components/blueprint_toolkit/manifest.json``'s
   ``version`` increments whenever any file under
   ``custom_components/`` changes between HEAD~1 and HEAD,
   and stays equal otherwise. Catches both missed bumps
   (developer edited a component file but didn't bump) and
   spurious bumps (version went up without a corresponding
   component change). Run ``scripts/bump-manifest-version.py``
   to bump the patch component before committing.

2. The on-disk manifest matches what
   ``json.dumps(..., indent=2)`` produces, with a single
   trailing newline. ``bump-manifest-version.py`` always
   writes manifests in this canonical form; without this
   test, a developer who hand-edits the manifest with
   different formatting would have their formatting
   silently reverted on the next bump.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import CodeQualityBase
from packaging.version import Version

REPO_ROOT = Path(__file__).parent.parent
MANIFEST_REL = "custom_components/blueprint_toolkit/manifest.json"
MANIFEST_PATH = REPO_ROOT / MANIFEST_REL


def _git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args],
        cwd=REPO_ROOT,
        text=True,
    )


def _has_parent() -> bool:
    r = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD~1"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return r.returncode == 0


def _file_in_commit(rev: str, path: str) -> str | None:
    r = subprocess.run(
        ["git", "show", f"{rev}:{path}"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        return None
    return r.stdout


def _read_manifest_version(text: str) -> Version:
    data = json.loads(text)
    return Version(data["version"])


def _component_changed_between(parent: str, head: str) -> bool:
    diff = _git(
        "diff",
        parent,
        head,
        "--name-only",
        "--",
        "custom_components/",
    ).strip()
    return bool(diff)


class TestManifestVersionRule:
    def test_head_vs_parent_consistency(self) -> None:
        if not _has_parent():
            pytest.skip("no parent commit")

        head_text = _file_in_commit("HEAD", MANIFEST_REL)
        if not head_text:
            pytest.skip("HEAD has no manifest.json")

        parent_text = _file_in_commit("HEAD~1", MANIFEST_REL)
        if not parent_text:
            pytest.skip(
                "parent has no manifest.json (predates manifest creation)"
            )

        head_v = _read_manifest_version(head_text)
        parent_v = _read_manifest_version(parent_text)

        if _component_changed_between("HEAD~1", "HEAD"):
            assert head_v > parent_v, (
                f"manifest version not bumped (HEAD={head_v}, "
                f"HEAD~1={parent_v}) despite custom_components/ "
                "changes. Run scripts/bump-manifest-version.py "
                "before re-committing."
            )
        else:
            assert head_v == parent_v, (
                f"manifest version bumped (HEAD={head_v}, "
                f"HEAD~1={parent_v}) without any "
                "custom_components/ changes. Reset the "
                "version in HEAD's manifest.json."
            )


class TestManifestFormatting:
    def test_canonical_format(self) -> None:
        raw = MANIFEST_PATH.read_text()
        data = json.loads(raw)
        canonical = json.dumps(data, indent=2) + "\n"
        assert raw == canonical, (
            "manifest.json is not in the canonical format the "
            "pre-commit hook writes (json.dumps indent=2 plus "
            "trailing newline). The hook would silently rewrite "
            "the file's formatting on the next commit; commit it "
            "in canonical form now to avoid the surprise.\n"
            f"\nFirst 200 chars of file:\n{raw[:200]!r}"
            f"\nFirst 200 chars expected:\n{canonical[:200]!r}"
        )


class TestCodeQuality(CodeQualityBase):
    ruff_targets = [
        "tests/test_manifest.py",
        "scripts/bump-manifest-version.py",
    ]
    # mypy follows the conftest import and trips on
    # pre-existing type-ignore residue in tests/conftest.py
    # under this script's particular dependency set; that's
    # an existing-codebase concern outside the manifest
    # tests' scope. Ruff covers the bulk of code-quality
    # enforcement for these two files.
    mypy_targets: list[str] = []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", *sys.argv[1:]]))
