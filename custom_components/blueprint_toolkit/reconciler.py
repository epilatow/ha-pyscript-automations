# This is AI generated code
"""Pure-function planner for the blueprint_toolkit install.

Given the bundled payload, the target config directory, a
prior-run manifest, and a mode (HACS vs manual dev-install),
return a ``ReconcilePlan`` describing the symlink
``install`` / ``update`` / ``remove`` / ``keep`` actions
that should happen next. Destinations occupied by unexpected
content (regular files, unknown symlinks) are surfaced as
``Conflict``s; the installer refuses to overwrite them unless
explicitly forced via the Repairs UI.

No HA imports; no side effects beyond read-only filesystem
probes (``exists``, ``is_symlink``, ``readlink``). This
module is safe to import outside of HA and is reused by
``scripts/dev-install.py``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

# Path marker that identifies "ours" in manual mode: any
# symlink whose target contains this segment is treated as
# a blueprint_toolkit-owned symlink, regardless of
# which clone path the target goes through. Lets
# dev-install.py safely repoint symlinks when the dev
# clone moves (e.g., /root/old -> /root/new).
BUNDLED_MARKER = "/custom_components/blueprint_toolkit/bundled/"


class ActionKind(Enum):
    """What the installer should do at a destination."""

    INSTALL = "install"  # dest missing; create a new symlink
    UPDATE = "update"  # dest is our symlink, target changed; replace
    REMOVE = "remove"  # dest in prior manifest but no longer bundled
    KEEP = "keep"  # dest already correct; no-op


class Mode(Enum):
    """How strict to be about recognizing existing destinations.

    HACS: a non-matching symlink is "ours" only if recorded
    in prior_manifest. Otherwise it's a conflict; the Repairs
    UI decides whether to overwrite.

    MANUAL: dev-install.py sees its own symlinks plus symlinks
    from prior clone layouts. Any symlink whose target goes
    through ``BUNDLED_MARKER`` is treated as ours so the dev
    loop can freely switch between clone locations.
    """

    HACS = "hacs"
    MANUAL = "manual"


@dataclass(frozen=True)
class Action:
    kind: ActionKind
    destination: Path  # absolute
    target: Path | None = None  # None for REMOVE; relative for others


@dataclass(frozen=True)
class Conflict:
    destination: Path
    kind: str  # "regular_file" | "regular_dir" | "unknown_symlink" | "other"
    details: str  # e.g. readlink output for unknown_symlink


@dataclass(frozen=True)
class ReconcilePlan:
    actions: tuple[Action, ...]
    new_manifest: frozenset[Path]
    conflicts: tuple[Conflict, ...]


def _destination_mapping(
    bundled_root: Path,
    config_root: Path,
    cli_symlink_dir: Path | None,
) -> dict[Path, Path]:
    """Return ``{destination: source}`` for every installable file.

    Destinations outside ``config_root`` are valid -- the
    optional ``cli_symlink_dir`` supports an out-of-tree
    install location for the shell CLI.
    """
    mapping: dict[Path, Path] = {}

    # bundled/blueprints/... -> /config/blueprints/...
    src_dir = bundled_root / "blueprints"
    if src_dir.is_dir():
        for src in sorted(src_dir.rglob("*.yaml")):
            rel = src.relative_to(src_dir)
            mapping[config_root / "blueprints" / rel] = src

    # bundled/pyscript/... -> /config/pyscript/...
    src_dir = bundled_root / "pyscript"
    if src_dir.is_dir():
        for src in sorted(src_dir.rglob("*.py")):
            rel = src.relative_to(src_dir)
            mapping[config_root / "pyscript" / rel] = src

    # NB: bundled/www/... is NOT installed via the
    # filesystem. HA's /local/ static handler refuses to
    # follow symlinks whose targets escape /config/www/,
    # and is only registered at startup if /config/www/
    # already exists. The integration's async_setup_entry
    # registers its own static route at
    # /local/blueprint_toolkit/docs/ pointing
    # directly into bundled/www/, which neither requires
    # /config/www/ to exist nor needs to traverse a
    # symlink. dev-install users who don't load the HA
    # integration will see broken /local/ doc links;
    # that's documented as a dev-install limitation.

    # bundled/cli/*.py -> <cli_symlink_dir>/*.py (flat; optional)
    if cli_symlink_dir is not None:
        src_dir = bundled_root / "cli"
        if src_dir.is_dir():
            for src in sorted(src_dir.glob("*.py")):
                mapping[cli_symlink_dir / src.name] = src

    return mapping


def _compute_symlink_target(destination: Path, source: Path) -> Path:
    """Compute the relative symlink target from destination to source.

    Relative targets survive Docker path rebinding (where the
    same data appears at different absolute paths inside and
    outside the container) as long as the relative traversal
    stays on the same logical filesystem tree. Falls back to
    an absolute path only when relpath computation fails, which
    on POSIX does not happen for two absolute paths on the same
    root.
    """
    try:
        return Path(os.path.relpath(source, destination.parent))
    except ValueError:
        return source


def _classify_destination(
    destination: Path,
    expected_target: Path,
    prior_manifest: frozenset[Path],
    mode: Mode,
    *,
    force_overwrite: bool = False,
) -> tuple[ActionKind | None, Conflict | None]:
    """Inspect the current state of ``destination`` and decide.

    Exactly one of the return values is non-None. ``ActionKind``
    values INSTALL, UPDATE, KEEP mean the installer should act
    (or no-op for KEEP); a ``Conflict`` means the destination
    is occupied by something we refuse to overwrite.

    REMOVE actions are not produced here; they're synthesized
    by ``plan`` from ``prior_manifest`` entries that fall out
    of the current bundled set.

    ``force_overwrite=True`` (used by the Repairs Overwrite
    flow) treats any existing destination as ours-to-replace:
    same-target symlinks are still KEEP, anything else
    (wrong-target symlink, regular file, dir, special) gets
    an UPDATE action. The installer's UPDATE handler unlinks
    + recreates; on a directory destination the unlink raises
    IsADirectoryError, which surfaces as an install_failure
    repair issue rather than silently destroying the dir.
    """
    # Missing destination (including a broken dangling symlink
    # target) counts as absent for install purposes. But a
    # dangling symlink at the destination is_symlink() True
    # while exists() False -- we must detect symlinks before
    # this branch or we'd try to create another in its place.
    if destination.is_symlink():
        current_target = os.readlink(destination)
        expected_target_str = str(expected_target)
        if current_target == expected_target_str:
            return ActionKind.KEEP, None

        if force_overwrite:
            return ActionKind.UPDATE, None

        recognized = destination in prior_manifest
        if mode == Mode.MANUAL and not recognized:
            # Accept symlinks whose target string references
            # the bundled subtree, or whose resolved path
            # lands inside it. Covers same-host clone moves.
            if BUNDLED_MARKER in current_target:
                recognized = True
            else:
                try:
                    resolved = (destination.parent / current_target).resolve(
                        strict=False
                    )
                    if BUNDLED_MARKER in str(resolved):
                        recognized = True
                except OSError:
                    pass

        if recognized:
            return ActionKind.UPDATE, None
        return None, Conflict(
            destination=destination,
            kind="unknown_symlink",
            details=f"target={current_target!r}",
        )

    if not destination.exists():
        return ActionKind.INSTALL, None

    if force_overwrite:
        return ActionKind.UPDATE, None

    # Exists but not a symlink: some kind of real path we
    # will not clobber without an explicit Repairs action.
    if destination.is_file():
        return None, Conflict(
            destination=destination,
            kind="regular_file",
            details="",
        )
    if destination.is_dir():
        return None, Conflict(
            destination=destination,
            kind="regular_dir",
            details="",
        )
    return None, Conflict(
        destination=destination,
        kind="other",
        details="",
    )


def plan(
    *,
    bundled_root: Path,
    config_root: Path,
    prior_manifest: frozenset[Path],
    mode: Mode = Mode.HACS,
    cli_symlink_dir: Path | None = None,
    force_destinations: frozenset[Path] = frozenset(),
) -> ReconcilePlan:
    """Compute a reconcile plan.

    Args:
        bundled_root: Absolute path to
            ``.../custom_components/blueprint_toolkit/bundled/``.
        config_root: Absolute path to HA's ``/config/`` dir.
        prior_manifest: Set of destination paths the installer
            recorded after its last successful apply. Empty on
            first install.
        mode: ``HACS`` (strict) or ``MANUAL`` (lenient --
            symlinks pointing into any bundled subtree are
            recognized).
        cli_symlink_dir: If given, install ``bundled/cli/*.py``
            into this directory. If None (default), CLI files
            are not installed.
        force_destinations: Destinations the caller explicitly
            wants to overwrite (the Repairs Overwrite flow
            passes the previously-flagged conflict dests
            here). Any of these that already have something
            other than the expected symlink become UPDATE
            actions instead of conflicts.
    """
    mapping = _destination_mapping(bundled_root, config_root, cli_symlink_dir)

    actions: list[Action] = []
    conflicts: list[Conflict] = []
    new_manifest: set[Path] = set()

    for dest in sorted(mapping):
        src = mapping[dest]
        target = _compute_symlink_target(dest, src)
        kind, conflict = _classify_destination(
            destination=dest,
            expected_target=target,
            prior_manifest=prior_manifest,
            mode=mode,
            force_overwrite=dest in force_destinations,
        )
        if conflict is not None:
            conflicts.append(conflict)
            continue
        assert kind is not None
        new_manifest.add(dest)
        actions.append(
            Action(kind=kind, destination=dest, target=target),
        )

    # Any destination we recorded previously but that falls
    # out of the current mapping gets a REMOVE action. We do
    # not REMOVE destinations that are now conflicts; those
    # will be listed in the Repairs UI and the user decides.
    conflict_dests = {c.destination for c in conflicts}
    for dest in sorted(prior_manifest - new_manifest - conflict_dests):
        actions.append(
            Action(
                kind=ActionKind.REMOVE,
                destination=dest,
                target=None,
            ),
        )

    return ReconcilePlan(
        actions=tuple(actions),
        new_manifest=frozenset(new_manifest),
        conflicts=tuple(conflicts),
    )
