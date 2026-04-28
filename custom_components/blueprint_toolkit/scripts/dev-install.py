#!/usr/bin/env python3
# This is AI generated code
"""HA-node-side installer for blueprint_toolkit.

Lives inside the integration (``custom_components/
blueprint_toolkit/scripts/``) so each deployed copy of the
integration ships with the matching installer / reconciler
implementation. ``scripts/dev-deploy.py`` invokes this
script remotely on the HA host; ``--restore`` runs the copy
inside the preserved HACS snapshot.

Not a ``uv run --script`` target; HA nodes are not expected
to have ``uv`` installed. Python 3.11+ stdlib only.

Usage
-----

    dev-install.py [--ha-config /config] \
                   [--cli-symlink-dir /root/] \
                   [--dry-run]

The script discovers the integration directory from
``__file__`` -- it lives at
``<integration>/scripts/dev-install.py``, so the integration
root is one level up. The reconciler imports the integration
package off ``<integration>/..``; the integration's
``__init__.py`` keeps its module-level imports HA-free for
exactly this case.

The reconciler runs in ``MANUAL`` mode so symlinks pointing
into any deployed copy of the integration's bundled subtree
are treated as ours. That lets the developer redeploy from a
different build directory without tripping the
unknown-symlink conflict path.

Backward-compatibility contract
-------------------------------

Because ``--restore`` invokes the dev-install.py inside the
HACS snapshot (which can be older than the installed
dev-deploy.py), this script's CLI is treated as a stable
contract:

- New flags MUST be optional and have a sensible default;
  older copies of dev-install.py will not understand them
  and dev-deploy must not pass them unconditionally.
- Existing flags MUST keep their meaning. Don't repurpose,
  rename, or remove a flag without ratcheting through a
  release cycle.
- Existing exit-code semantics MUST stay (0=ok, 1=error,
  2=usage error, 3=conflicts).

dev-deploy is allowed to assume the modern shape only when
it invokes the dev-install.py inside the *current* build
(not the snapshot copy used by --restore).

State
-----

A small JSON manifest is written to
``<ha-config>/.blueprint_toolkit.manifest.json`` listing
the destination paths we currently own. Subsequent runs diff
against this file so stale symlinks (from files that used to
be bundled but aren't anymore) get removed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

MANIFEST_FILENAME = ".blueprint_toolkit.manifest.json"

# This file lives at <integration>/scripts/dev-install.py;
# resolved so a symlink in the repo (scripts/dev-install.py
# -> ../custom_components/.../scripts/dev-install.py) still
# points discovery at the real integration directory.
_INTEGRATION_DIR = Path(__file__).resolve().parent.parent


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
            "Reconcile this integration's bundled payload into "
            "the HA config dir via symlinks."
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


def main() -> int:
    args = _parse_args()
    ha_config = args.ha_config.resolve()

    if _INTEGRATION_DIR.name != "blueprint_toolkit":
        # Sanity check on the auto-discovery: this script
        # has to be moved under another integration's
        # scripts/ dir for this to fire.
        sys.stderr.write(
            f"error: integration dir basename must be "
            f"'blueprint_toolkit'; got: {_INTEGRATION_DIR.name}\n"
        )
        return 2
    if not ha_config.is_dir():
        sys.stderr.write(f"error: --ha-config not a directory: {ha_config}\n")
        return 2

    bundled_root = _INTEGRATION_DIR / "bundled"
    if not bundled_root.is_dir():
        sys.stderr.write(
            f"error: bundled payload not found under {bundled_root}\n"
        )
        return 2

    # Importing the reconciler + installer requires
    # ``blueprint_toolkit`` to be importable as a package.
    # The integration's ``__init__.py`` keeps its module-level
    # imports HA-free for exactly this case; putting the
    # parent of the integration dir on sys.path makes
    # ``from .reconciler import ...`` inside installer.py
    # resolve correctly.
    sys.path.insert(0, str(_INTEGRATION_DIR.parent))
    from blueprint_toolkit import installer  # noqa: PLC0415
    from blueprint_toolkit.reconciler import (  # noqa: PLC0415
        ActionKind,
        Mode,
        plan,
    )

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
