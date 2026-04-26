#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest", "pytest-cov", "ruff", "mypy"]
# ///
# This is AI generated code
"""Tests for custom_components/blueprint_toolkit/reconciler.py.

All tests exercise pure planning against tempdirs; no HA,
no subprocess. Covers every ActionKind transition,
HACS-vs-MANUAL mode differences, cli_symlink_dir behaviour,
and each conflict classification.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest  # noqa: E402
from conftest import CodeQualityBase  # noqa: E402

from custom_components.blueprint_toolkit.reconciler import (  # noqa: E402
    BUNDLED_MARKER,
    Action,
    ActionKind,
    Conflict,
    Mode,
    ReconcilePlan,
    plan,
)


def _make_bundled(root: Path) -> Path:
    """Build a minimal bundled/ tree with one file per subdir."""
    bundled = root / "custom_components" / "blueprint_toolkit" / "bundled"
    (bundled / "blueprints" / "automation" / "blueprint_toolkit").mkdir(
        parents=True,
    )
    (bundled / "pyscript" / "modules").mkdir(parents=True)
    (bundled / "www" / "blueprint_toolkit" / "docs").mkdir(parents=True)
    (bundled / "cli").mkdir(parents=True)

    (
        bundled
        / "blueprints"
        / "automation"
        / "blueprint_toolkit"
        / "demo.yaml"
    ).write_text("blueprint: {}\n")
    (bundled / "pyscript" / "blueprint_toolkit.py").write_text(
        "# service wrapper\n",
    )
    (bundled / "pyscript" / "modules" / "demo.py").write_text("# module\n")
    (bundled / "www" / "blueprint_toolkit" / "docs" / "demo.html").write_text(
        "<html>demo</html>\n"
    )
    (bundled / "cli" / "demo_cli.py").write_text(
        "#!/usr/bin/env python3\n",
    )
    return bundled


def _action_for(plan_obj: ReconcilePlan, dest: Path) -> Action | None:
    for a in plan_obj.actions:
        if a.destination == dest:
            return a
    return None


def _kind_for(plan_obj: ReconcilePlan, dest: Path) -> ActionKind | None:
    action = _action_for(plan_obj, dest)
    return None if action is None else action.kind


# ---------------------------------------------------------------
# Fresh-install path
# ---------------------------------------------------------------


class TestFreshInstall:
    def test_installs_every_bundled_file(self, tmp_path: Path) -> None:
        bundled = _make_bundled(tmp_path / "repo")
        config = tmp_path / "config"
        config.mkdir()

        result = plan(
            bundled_root=bundled,
            config_root=config,
            prior_manifest=frozenset(),
        )

        kinds = {a.kind for a in result.actions}
        assert kinds == {ActionKind.INSTALL}
        assert not result.conflicts
        # bundled/www/ is intentionally NOT installed via
        # the reconciler -- HA's /local/ static handler
        # cannot serve symlinked files; the integration
        # registers an aiohttp static route directly at
        # bundled/www/... instead. The fixture still
        # creates a www/ html file to verify the
        # reconciler ignores it.
        expected_dests = {
            config / "blueprints/automation/blueprint_toolkit/demo.yaml",
            config / "pyscript/blueprint_toolkit.py",
            config / "pyscript/modules/demo.py",
        }
        assert {a.destination for a in result.actions} == expected_dests
        assert result.new_manifest == frozenset(expected_dests)

    def test_cli_not_installed_when_dir_unset(self, tmp_path: Path) -> None:
        bundled = _make_bundled(tmp_path / "repo")
        config = tmp_path / "config"
        config.mkdir()

        result = plan(
            bundled_root=bundled,
            config_root=config,
            prior_manifest=frozenset(),
        )
        for a in result.actions:
            assert "demo_cli.py" not in str(a.destination)

    def test_cli_installed_when_dir_set(self, tmp_path: Path) -> None:
        bundled = _make_bundled(tmp_path / "repo")
        config = tmp_path / "config"
        config.mkdir()
        cli_dir = tmp_path / "root"
        cli_dir.mkdir()

        result = plan(
            bundled_root=bundled,
            config_root=config,
            prior_manifest=frozenset(),
            cli_symlink_dir=cli_dir,
        )
        cli_dest = cli_dir / "demo_cli.py"
        assert _kind_for(result, cli_dest) == ActionKind.INSTALL


# ---------------------------------------------------------------
# Reinstall / rerun path
# ---------------------------------------------------------------


class TestReinstall:
    def _install_all(self, tmp_path: Path) -> tuple[Path, Path, ReconcilePlan]:
        bundled = _make_bundled(tmp_path / "repo")
        config = tmp_path / "config"
        config.mkdir()
        first = plan(
            bundled_root=bundled,
            config_root=config,
            prior_manifest=frozenset(),
        )
        # Materialize the plan's symlinks so the second
        # plan() call sees them on disk.
        for action in first.actions:
            assert action.target is not None
            action.destination.parent.mkdir(parents=True, exist_ok=True)
            action.destination.symlink_to(action.target)
        return bundled, config, first

    def test_second_run_is_all_keep(self, tmp_path: Path) -> None:
        bundled, config, first = self._install_all(tmp_path)

        second = plan(
            bundled_root=bundled,
            config_root=config,
            prior_manifest=first.new_manifest,
        )
        assert not second.conflicts
        assert all(a.kind == ActionKind.KEEP for a in second.actions)

    def test_removed_bundled_file_becomes_remove(self, tmp_path: Path) -> None:
        bundled, config, first = self._install_all(tmp_path)
        # Remove one source from bundled, keep manifest as-is.
        (bundled / "pyscript" / "modules" / "demo.py").unlink()

        second = plan(
            bundled_root=bundled,
            config_root=config,
            prior_manifest=first.new_manifest,
        )
        rm_dest = config / "pyscript/modules/demo.py"
        assert _kind_for(second, rm_dest) == ActionKind.REMOVE

    def test_retargeted_symlink_in_prior_manifest_is_update_in_hacs_mode(
        self,
        tmp_path: Path,
    ) -> None:
        bundled, config, first = self._install_all(tmp_path)
        victim = config / "pyscript/modules/demo.py"
        # Retarget the symlink to simulate someone editing it
        # out from under us.
        victim.unlink()
        other = tmp_path / "somewhere-else.py"
        other.write_text("# elsewhere\n")
        victim.symlink_to(other)

        second = plan(
            bundled_root=bundled,
            config_root=config,
            prior_manifest=first.new_manifest,
            mode=Mode.HACS,
        )
        # prior_manifest entry -> UPDATE in HACS mode.
        assert _kind_for(second, victim) == ActionKind.UPDATE
        assert not second.conflicts


# ---------------------------------------------------------------
# Mode differences
# ---------------------------------------------------------------


class TestModeHacsStrict:
    def test_unknown_symlink_is_conflict(self, tmp_path: Path) -> None:
        bundled = _make_bundled(tmp_path / "repo")
        config = tmp_path / "config"
        config.mkdir()
        # Pre-seed an unknown symlink at a destination we
        # would install.
        dest = config / "pyscript/blueprint_toolkit.py"
        dest.parent.mkdir(parents=True)
        other = tmp_path / "whatever.py"
        other.write_text("# other\n")
        dest.symlink_to(other)

        result = plan(
            bundled_root=bundled,
            config_root=config,
            prior_manifest=frozenset(),  # not ours per the manifest
            mode=Mode.HACS,
        )
        # No install action for the conflicted dest.
        assert _action_for(result, dest) is None
        # Conflict surfaced.
        assert any(
            c.destination == dest and c.kind == "unknown_symlink"
            for c in result.conflicts
        )


class TestModeManualLenient:
    def test_bundled_marker_symlink_is_recognized(self, tmp_path: Path) -> None:
        bundled = _make_bundled(tmp_path / "repo-new")
        old_bundled = _make_bundled(tmp_path / "repo-old")
        config = tmp_path / "config"
        config.mkdir()
        # Pre-seed a symlink that points into the OLD bundle.
        dest = config / "pyscript/blueprint_toolkit.py"
        dest.parent.mkdir(parents=True)
        dest.symlink_to(
            old_bundled / "pyscript" / "blueprint_toolkit.py",
        )

        # Prior manifest empty -- we have never run
        # dev-install against this HA config before.
        result = plan(
            bundled_root=bundled,
            config_root=config,
            prior_manifest=frozenset(),
            mode=Mode.MANUAL,
        )
        # Manual mode recognises the bundled marker in the
        # current target string and plans UPDATE.
        assert _kind_for(result, dest) == ActionKind.UPDATE
        assert not result.conflicts

    def test_foreign_symlink_is_still_conflict_in_manual(
        self,
        tmp_path: Path,
    ) -> None:
        bundled = _make_bundled(tmp_path / "repo")
        config = tmp_path / "config"
        config.mkdir()
        dest = config / "pyscript/blueprint_toolkit.py"
        dest.parent.mkdir(parents=True)
        other = tmp_path / "other-place.py"
        other.write_text("#\n")
        # Symlink target does not contain BUNDLED_MARKER.
        dest.symlink_to(other)

        result = plan(
            bundled_root=bundled,
            config_root=config,
            prior_manifest=frozenset(),
            mode=Mode.MANUAL,
        )
        assert _action_for(result, dest) is None
        assert any(
            c.destination == dest and c.kind == "unknown_symlink"
            for c in result.conflicts
        )


# ---------------------------------------------------------------
# Conflict classification
# ---------------------------------------------------------------


class TestConflicts:
    def test_regular_file_at_destination_is_conflict(
        self,
        tmp_path: Path,
    ) -> None:
        bundled = _make_bundled(tmp_path / "repo")
        config = tmp_path / "config"
        config.mkdir()
        dest = config / "pyscript/modules/demo.py"
        dest.parent.mkdir(parents=True)
        dest.write_text("# user file\n")  # regular file

        result = plan(
            bundled_root=bundled,
            config_root=config,
            prior_manifest=frozenset(),
        )
        assert any(
            c.destination == dest and c.kind == "regular_file"
            for c in result.conflicts
        )
        assert _action_for(result, dest) is None

    def test_regular_dir_at_destination_is_conflict(
        self,
        tmp_path: Path,
    ) -> None:
        bundled = _make_bundled(tmp_path / "repo")
        config = tmp_path / "config"
        config.mkdir()
        # A directory where we want a symlink.
        dest = config / "pyscript/blueprint_toolkit.py"
        dest.mkdir(parents=True)

        result = plan(
            bundled_root=bundled,
            config_root=config,
            prior_manifest=frozenset(),
        )
        assert any(
            c.destination == dest and c.kind == "regular_dir"
            for c in result.conflicts
        )


# ---------------------------------------------------------------
# Symlink target shape
# ---------------------------------------------------------------


class TestTargetShape:
    def test_targets_are_relative(self, tmp_path: Path) -> None:
        bundled = _make_bundled(tmp_path / "repo")
        config = tmp_path / "config"
        config.mkdir()

        result = plan(
            bundled_root=bundled,
            config_root=config,
            prior_manifest=frozenset(),
        )
        for action in result.actions:
            assert action.target is not None
            assert not action.target.is_absolute(), (
                f"expected relative target, got absolute: {action.target}",
            )

    def test_relative_target_resolves(self, tmp_path: Path) -> None:
        bundled = _make_bundled(tmp_path / "repo")
        config = tmp_path / "config"
        config.mkdir()

        result = plan(
            bundled_root=bundled,
            config_root=config,
            prior_manifest=frozenset(),
        )
        # Materialize the symlinks and verify each resolves
        # to the expected source under bundled/.
        for action in result.actions:
            assert action.target is not None
            action.destination.parent.mkdir(parents=True, exist_ok=True)
            action.destination.symlink_to(action.target)
        # Spot-check one resolved path lands in the bundle.
        sample = config / "pyscript/modules/demo.py"
        assert sample.is_symlink()
        resolved = os.path.realpath(sample)
        assert BUNDLED_MARKER in resolved


# ---------------------------------------------------------------
# Empty / degenerate inputs
# ---------------------------------------------------------------


class TestEmptyBundle:
    def test_empty_bundled_root_removes_prior(self, tmp_path: Path) -> None:
        # bundled/ exists but contains no installable content.
        bundled = (
            tmp_path
            / "repo"
            / "custom_components"
            / "blueprint_toolkit"
            / "bundled"
        )
        bundled.mkdir(parents=True)
        config = tmp_path / "config"
        config.mkdir()
        stale_dest = config / "pyscript/modules/old.py"

        result = plan(
            bundled_root=bundled,
            config_root=config,
            prior_manifest=frozenset({stale_dest}),
        )
        # Old entry should be planned for removal, no installs,
        # no conflicts.
        assert _kind_for(result, stale_dest) == ActionKind.REMOVE
        assert not any(
            a.kind in (ActionKind.INSTALL, ActionKind.UPDATE)
            for a in result.actions
        )

    def test_missing_subdir_is_skipped_silently(self, tmp_path: Path) -> None:
        # Build a partial bundle with only pyscript/.
        bundled = (
            tmp_path
            / "repo"
            / "custom_components"
            / "blueprint_toolkit"
            / "bundled"
        )
        (bundled / "pyscript" / "modules").mkdir(parents=True)
        (bundled / "pyscript" / "modules" / "only.py").write_text("#\n")
        config = tmp_path / "config"
        config.mkdir()

        result = plan(
            bundled_root=bundled,
            config_root=config,
            prior_manifest=frozenset(),
        )
        # One install; no blueprints/www/cli content to
        # surface.
        dests = [a.destination for a in result.actions]
        assert dests == [config / "pyscript/modules/only.py"]


# ---------------------------------------------------------------
# Dataclass sanity: plan output is hashable/frozen
# ---------------------------------------------------------------


class TestDataclassShapes:
    def test_action_is_frozen(self) -> None:
        a = Action(kind=ActionKind.KEEP, destination=Path("/x"), target=None)
        with pytest.raises((AttributeError, Exception)):
            a.kind = ActionKind.INSTALL  # type: ignore[misc]

    def test_conflict_is_frozen(self) -> None:
        c = Conflict(destination=Path("/x"), kind="regular_file", details="")
        with pytest.raises((AttributeError, Exception)):
            c.kind = "unknown_symlink"  # type: ignore[misc]


class TestCodeQuality(CodeQualityBase):
    ruff_targets = [
        "custom_components/blueprint_toolkit/reconciler.py",
        "custom_components/blueprint_toolkit/installer.py",
        "custom_components/blueprint_toolkit/__init__.py",
        "tests/test_reconciler.py",
    ]
    mypy_targets = [
        "custom_components/blueprint_toolkit/reconciler.py",
        "custom_components/blueprint_toolkit/installer.py",
    ]


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(
        test_file=__file__,
        script_path=(
            Path(__file__).parent.parent
            / "custom_components"
            / "blueprint_toolkit"
            / "reconciler.py"
        ),
        repo_root=Path(__file__).parent.parent,
    )
