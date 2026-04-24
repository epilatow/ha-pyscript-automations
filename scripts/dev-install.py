#!/usr/bin/env python3
# This is AI generated code
"""HA-node-side installer for ha-pyscript-automations.

Runs directly on the HA host (invoked remotely by
``scripts/dev-deploy.py``) to reconcile the checked-out
repo's bundled payload into the user-visible
``/config/...`` paths via symlinks.

Not a ``uv run --script`` target; HA nodes are not expected
to have ``uv`` installed. Python 3.11+ stdlib only.

Usage
-----

    dev-install.py --repo-dir /root/ha-pyscript-automations \
                   --ha-config /config \
                   [--cli-symlink-dir /root/] \
                   [--dry-run]

The script runs the reconciler in ``MANUAL`` mode so
symlinks pointing into any clone of this repo's bundled
subtree are treated as ours. That lets a developer reinstall
from a different clone location (e.g., switching from
``/root/old`` to ``/root/new``) without tripping the
unknown-symlink conflict path.

State
-----

A small JSON manifest is written to
``<ha-config>/.ha_pyscript_automations.manifest.json`` listing
the destination paths we currently own. Subsequent runs diff
against this file so stale symlinks (from files that used to
be bundled but aren't anymore) get removed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# The reconciler + installer live under custom_components/;
# make the repo root importable so we can pull them in as a
# package. This script is always invoked from inside a clone
# of this repo, so the path to sys.path is well-defined.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

from custom_components.ha_pyscript_automations import installer  # noqa: E402
from custom_components.ha_pyscript_automations.reconciler import (  # noqa: E402
    ActionKind,
    Mode,
    plan,
)

MANIFEST_FILENAME = ".ha_pyscript_automations.manifest.json"


def _load_manifest(ha_config: Path) -> frozenset[Path]:
    path = ha_config / MANIFEST_FILENAME
    if not path.exists():
        return frozenset()
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        # Broken manifest is treated as "no prior state";
        # next reconcile rebuilds from scratch.
        return frozenset()
    destinations = data.get("destinations", [])
    return frozenset(Path(p) for p in destinations)


def _write_manifest(ha_config: Path, manifest: frozenset[Path]) -> None:
    path = ha_config / MANIFEST_FILENAME
    data = {
        "version": 1,
        "destinations": sorted(str(p) for p in manifest),
    }
    path.write_text(json.dumps(data, indent=2) + "\n")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Reconcile this repo's bundled payload into the "
            "HA config dir via symlinks."
        ),
    )
    p.add_argument(
        "--repo-dir",
        type=Path,
        required=True,
        help=(
            "Absolute path to the ha-pyscript-automations "
            "clone on this host (not the HA config dir)."
        ),
    )
    p.add_argument(
        "--ha-config",
        type=Path,
        default=Path("/config"),
        help=("Absolute path to HA's config directory (default: /config)."),
    )
    p.add_argument(
        "--cli-symlink-dir",
        type=Path,
        default=None,
        help=(
            "If set, install bundled CLI scripts into this "
            "directory. Omit to skip CLI install entirely."
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan without changing anything.",
    )
    return p.parse_args()


def _bundled_root(repo_dir: Path) -> Path:
    return (
        repo_dir / "custom_components" / "ha_pyscript_automations" / "bundled"
    )


def main() -> int:
    args = _parse_args()
    repo_dir = args.repo_dir.resolve()
    ha_config = args.ha_config.resolve()

    if not repo_dir.is_dir():
        sys.stderr.write(f"error: --repo-dir not a directory: {repo_dir}\n")
        return 2
    if not ha_config.is_dir():
        sys.stderr.write(f"error: --ha-config not a directory: {ha_config}\n")
        return 2

    bundled_root = _bundled_root(repo_dir)
    if not bundled_root.is_dir():
        sys.stderr.write(
            f"error: bundled payload not found under {bundled_root}\n"
        )
        return 2

    prior_manifest = _load_manifest(ha_config)
    the_plan = plan(
        bundled_root=bundled_root,
        config_root=ha_config,
        prior_manifest=prior_manifest,
        mode=Mode.MANUAL,
        cli_symlink_dir=(
            args.cli_symlink_dir.resolve() if args.cli_symlink_dir else None
        ),
    )

    # Always print the plan so the operator can inspect.
    for action in the_plan.actions:
        if action.kind == ActionKind.KEEP:
            continue
        line = f"{action.kind.value}: {action.destination}"
        if action.target is not None:
            line += f" -> {action.target}"
        print(line)
    for conflict in the_plan.conflicts:
        print(
            f"conflict: {conflict.destination} "
            f"({conflict.kind}) {conflict.details}"
        )

    if args.dry_run:
        return 0

    result = installer.apply(the_plan)
    print(
        f"installed={result.installed} updated={result.updated} "
        f"removed={result.removed} kept={result.kept} "
        f"conflicts={len(result.conflicts)} errors={len(result.errors)}"
    )
    for err in result.errors:
        sys.stderr.write(f"error: {err}\n")

    # Persist the manifest only if the apply succeeded; if
    # any action errored we leave the manifest untouched so
    # the next run re-attempts the missing work.
    if not result.errors:
        _write_manifest(ha_config, the_plan.new_manifest)

    if result.errors:
        return 1
    if result.conflicts:
        # Conflicts block reconcile but are not "errors"
        # per se -- surface them to the caller with a
        # distinct exit code.
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
