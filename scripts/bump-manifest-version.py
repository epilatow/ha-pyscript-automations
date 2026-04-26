#!/usr/bin/env python3
# This is AI generated code
"""Bump the patch component of manifest.json's version.

Run this **before committing** any change under
``custom_components/``. The companion test
``tests/test_manifest.py`` enforces the rule that the
manifest version must increment between commits whenever
``custom_components/`` files change, and stay equal
otherwise. The test failure message points at this
script.

Stages the modified ``manifest.json`` so the next
``git commit`` picks it up automatically.

For non-patch bumps (a deliberate minor or major version
graduation), edit ``manifest.json`` directly instead --
the test only checks that the new version is strictly
greater than the parent's, not that it's exactly +1.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = (
    REPO_ROOT / "custom_components" / "blueprint_toolkit" / "manifest.json"
)


def _bump_patch(version: str) -> str:
    parts = version.strip().split(".")
    if len(parts) != 3:
        msg = (
            f"unsupported version format {version!r}; "
            "expected MAJOR.MINOR.PATCH. For non-patch "
            "bumps edit manifest.json directly."
        )
        raise ValueError(msg)
    major, minor, patch = (int(p) for p in parts)
    return f"{major}.{minor}.{patch + 1}"


def main() -> int:
    data = json.loads(MANIFEST_PATH.read_text())
    old = str(data["version"])
    new = _bump_patch(old)
    data["version"] = new
    MANIFEST_PATH.write_text(json.dumps(data, indent=2) + "\n")
    subprocess.run(
        ["git", "add", str(MANIFEST_PATH)],
        cwd=REPO_ROOT,
        check=True,
    )
    print(f"manifest version: {old} -> {new}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
