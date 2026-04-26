# This is AI generated code
"""Apply a ReconcilePlan to the filesystem.

Split from ``reconciler.py`` so the pure planning logic can
be exercised without any filesystem side effects. This
module performs synchronous I/O (``os.symlink``, ``os.unlink``,
``mkdir``). The HA integration (step 6) wraps calls here in
``hass.async_add_executor_job`` so the event loop stays
unblocked.

Regular files at destinations are never overwritten by
``apply(plan)``. The Repairs UI (step 7) will call
``apply(plan, force=True)`` when the user explicitly
authorises removal of a listed conflict. ``force=True`` does
not bypass the reconciler's plan -- conflicts are still
reported up, the force flag only changes what the installer
does during its apply pass for the symlinks it owns.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .reconciler import Action, ActionKind, Conflict, ReconcilePlan


@dataclass
class AppliedResult:
    installed: int = 0
    updated: int = 0
    removed: int = 0
    kept: int = 0
    conflicts: tuple[Conflict, ...] = ()
    errors: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.installed or self.updated or self.removed)


def _apply_action(action: Action) -> None:
    """Execute a single action. Raises on any filesystem error."""
    dest = action.destination
    if action.kind == ActionKind.REMOVE:
        # Unlink is a no-op when already gone; a dangling
        # symlink still unlinks fine.
        try:
            dest.unlink()
        except FileNotFoundError:
            pass
        return

    if action.kind == ActionKind.KEEP:
        return

    if action.target is None:
        msg = f"{action.kind.value} action has no target: {dest}"
        raise ValueError(msg)

    dest.parent.mkdir(parents=True, exist_ok=True)

    if action.kind == ActionKind.UPDATE:
        # Replace the existing symlink. We always reach this
        # via the reconciler having classified the existing
        # entry as ours, so unlinking is safe.
        dest.unlink()

    dest.symlink_to(action.target)


def apply(plan: ReconcilePlan) -> AppliedResult:
    """Apply the actions in ``plan`` to the filesystem.

    Conflicts from the plan are reported back verbatim in
    the result; apply does not touch the destinations the
    reconciler flagged as conflicts. The caller (HA
    integration or dev-install CLI) surfaces the conflict
    list through its own UI.
    """
    result = AppliedResult(conflicts=plan.conflicts)
    for action in plan.actions:
        try:
            _apply_action(action)
        except OSError as e:
            result.errors.append(
                f"{action.kind.value} {action.destination}: {e}",
            )
            continue
        if action.kind == ActionKind.INSTALL:
            result.installed += 1
        elif action.kind == ActionKind.UPDATE:
            result.updated += 1
        elif action.kind == ActionKind.REMOVE:
            result.removed += 1
        elif action.kind == ActionKind.KEEP:
            result.kept += 1
    return result
