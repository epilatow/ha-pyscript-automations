#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest", "pytest-cov", "ruff", "mypy"]
# ///
# This is AI generated code
"""Tests for scripts/install.sh.

Creates temporary directory structures that mimic a
Home Assistant config directory with the repo cloned
inside it, then runs install.sh and verifies symlink
creation, idempotency, and error handling.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import CodeQualityBase

REPO_ROOT = Path(__file__).parent.parent
INSTALL_SCRIPT = REPO_ROOT / "scripts" / "install.sh"

# Discover installable files the same way install.sh does.
INSTALLED_FILES = sorted(
    [
        str(p.relative_to(REPO_ROOT))
        for p in REPO_ROOT.joinpath("pyscript").rglob("*.py")
    ]
    + [
        str(p.relative_to(REPO_ROOT))
        for p in REPO_ROOT.joinpath("blueprints").rglob(
            "*.yaml",
        )
    ],
)


# -- Helpers -----------------------------------------


def _make_ha_env(tmp_path: Path) -> tuple[Path, Path]:
    """Build a fake HA config dir with the repo inside.

    Creates dummy source files at every path listed in
    the install script's FILES array, plus a copy of the
    real install.sh (so the script under test is always
    the current version).

    Returns (ha_config, repo_dir).
    """
    ha_config = tmp_path / "config"
    repo_name = "ha-pyscript-automations"
    repo_dir = ha_config / repo_name

    # Copy install script
    scripts_dir = repo_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    shutil.copy2(INSTALL_SCRIPT, scripts_dir / "install.sh")

    # Create dummy source files
    for file_rel in INSTALLED_FILES:
        src = repo_dir / file_rel
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text(f"# dummy: {file_rel}\n")

    return ha_config, repo_dir


def _run_install(
    repo_dir: Path,
    ha_config: Path,
) -> subprocess.CompletedProcess[str]:
    """Run install.sh and return the result."""
    return subprocess.run(
        [
            str(repo_dir / "scripts" / "install.sh"),
            str(ha_config),
        ],
        capture_output=True,
        text=True,
    )


def _expected_target(
    repo_name: str,
    file_rel: str,
) -> str:
    """Compute the expected relative symlink target.

    Mirrors the relative_target() function in install.sh.
    """
    parts = Path(file_rel).parent.parts
    depth = len(parts)
    prefix = "../" * depth
    return f"{prefix}{repo_name}/{file_rel}"


# -- Fixtures ----------------------------------------


@pytest.fixture
def ha_env(tmp_path: Path) -> tuple[Path, Path]:
    """Provide a fresh HA config + repo directory."""
    return _make_ha_env(tmp_path)


# -- Tests -------------------------------------------


class TestFreshInstall:
    """First run with no existing targets."""

    def test_exit_code(
        self,
        ha_env: tuple[Path, Path],
    ) -> None:
        ha_config, repo_dir = ha_env
        r = _run_install(repo_dir, ha_config)
        assert r.returncode == 0, r.stderr

    def test_symlinks_created(
        self,
        ha_env: tuple[Path, Path],
    ) -> None:
        ha_config, repo_dir = ha_env
        _run_install(repo_dir, ha_config)
        for file_rel in INSTALLED_FILES:
            dst = ha_config / file_rel
            assert dst.is_symlink(), f"{file_rel} not a symlink"

    def test_symlink_targets_are_relative(
        self,
        ha_env: tuple[Path, Path],
    ) -> None:
        ha_config, repo_dir = ha_env
        _run_install(repo_dir, ha_config)
        for file_rel in INSTALLED_FILES:
            dst = ha_config / file_rel
            raw = os.readlink(dst)
            assert not os.path.isabs(raw), (
                f"{file_rel} symlink is absolute: {raw}"
            )

    def test_symlink_targets_correct(
        self,
        ha_env: tuple[Path, Path],
    ) -> None:
        ha_config, repo_dir = ha_env
        _run_install(repo_dir, ha_config)
        repo_name = repo_dir.name
        for file_rel in INSTALLED_FILES:
            dst = ha_config / file_rel
            raw = os.readlink(dst)
            expected = _expected_target(
                repo_name,
                file_rel,
            )
            assert raw == expected, f"{file_rel}: {raw!r} != {expected!r}"

    def test_symlinks_resolve_to_source(
        self,
        ha_env: tuple[Path, Path],
    ) -> None:
        ha_config, repo_dir = ha_env
        _run_install(repo_dir, ha_config)
        for file_rel in INSTALLED_FILES:
            dst = ha_config / file_rel
            resolved = dst.resolve()
            src = (repo_dir / file_rel).resolve()
            assert resolved == src, (
                f"{file_rel}: resolves to {resolved}, expected {src}"
            )

    def test_output_shows_linked(
        self,
        ha_env: tuple[Path, Path],
    ) -> None:
        ha_config, repo_dir = ha_env
        r = _run_install(repo_dir, ha_config)
        for file_rel in INSTALLED_FILES:
            assert f"{file_rel} (linked)" in r.stdout

    def test_parent_dirs_created(
        self,
        ha_env: tuple[Path, Path],
    ) -> None:
        ha_config, repo_dir = ha_env
        _run_install(repo_dir, ha_config)
        for file_rel in INSTALLED_FILES:
            dst = ha_config / file_rel
            assert dst.parent.is_dir()


class TestIdempotent:
    """Second run with correct symlinks already in place."""

    def test_exit_code(
        self,
        ha_env: tuple[Path, Path],
    ) -> None:
        ha_config, repo_dir = ha_env
        _run_install(repo_dir, ha_config)
        r = _run_install(repo_dir, ha_config)
        assert r.returncode == 0, r.stderr

    def test_output_shows_already_linked(
        self,
        ha_env: tuple[Path, Path],
    ) -> None:
        ha_config, repo_dir = ha_env
        _run_install(repo_dir, ha_config)
        r = _run_install(repo_dir, ha_config)
        for file_rel in INSTALLED_FILES:
            assert f"{file_rel} (already linked)" in r.stdout

    def test_symlinks_unchanged(
        self,
        ha_env: tuple[Path, Path],
    ) -> None:
        ha_config, repo_dir = ha_env
        _run_install(repo_dir, ha_config)
        targets_before = {
            f: os.readlink(ha_config / f) for f in INSTALLED_FILES
        }
        _run_install(repo_dir, ha_config)
        for file_rel in INSTALLED_FILES:
            assert (
                os.readlink(ha_config / file_rel) == (targets_before[file_rel])
            )


class TestErrorRegularFile:
    """Target exists as a regular file (not a symlink)."""

    def test_fails(
        self,
        ha_env: tuple[Path, Path],
    ) -> None:
        ha_config, repo_dir = ha_env
        # Pre-create target as a regular file
        target = ha_config / INSTALLED_FILES[0]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("not a symlink")
        r = _run_install(repo_dir, ha_config)
        assert r.returncode != 0

    def test_error_message(
        self,
        ha_env: tuple[Path, Path],
    ) -> None:
        ha_config, repo_dir = ha_env
        file_rel = INSTALLED_FILES[0]
        target = ha_config / file_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("not a symlink")
        r = _run_install(repo_dir, ha_config)
        assert "not a symlink" in r.stdout
        assert file_rel in r.stdout

    def test_other_files_still_installed(
        self,
        ha_env: tuple[Path, Path],
    ) -> None:
        ha_config, repo_dir = ha_env
        target = ha_config / INSTALLED_FILES[0]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("not a symlink")
        _run_install(repo_dir, ha_config)
        # Remaining files should still be linked
        for file_rel in INSTALLED_FILES[1:]:
            dst = ha_config / file_rel
            assert dst.is_symlink(), f"{file_rel} should still be installed"


class TestErrorWrongSymlink:
    """Target is a symlink pointing to the wrong place."""

    def test_fails(
        self,
        ha_env: tuple[Path, Path],
    ) -> None:
        ha_config, repo_dir = ha_env
        file_rel = INSTALLED_FILES[0]
        target = ha_config / file_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to("/some/wrong/path")
        r = _run_install(repo_dir, ha_config)
        assert r.returncode != 0

    def test_error_message_shows_both_paths(
        self,
        ha_env: tuple[Path, Path],
    ) -> None:
        ha_config, repo_dir = ha_env
        file_rel = INSTALLED_FILES[0]
        target = ha_config / file_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to("/some/wrong/path")
        r = _run_install(repo_dir, ha_config)
        assert "/some/wrong/path" in r.stdout
        expected = _expected_target(
            repo_dir.name,
            file_rel,
        )
        assert expected in r.stdout


class TestErrorDanglingSymlink:
    """Symlink whose target doesn't exist on disk."""

    def test_wrong_dangling_link_fails(
        self,
        ha_env: tuple[Path, Path],
    ) -> None:
        ha_config, repo_dir = ha_env
        file_rel = INSTALLED_FILES[0]
        target = ha_config / file_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to("../nonexistent/path")
        r = _run_install(repo_dir, ha_config)
        assert r.returncode != 0
        assert "links to" in r.stdout


class TestErrorRepoNotInsideConfig:
    """Repo is not a subdirectory of the HA config dir."""

    def test_fails(self, tmp_path: Path) -> None:
        ha_config = tmp_path / "config"
        ha_config.mkdir()
        repo_dir = tmp_path / "elsewhere" / "repo"
        repo_dir.mkdir(parents=True)
        scripts_dir = repo_dir / "scripts"
        scripts_dir.mkdir()
        shutil.copy2(INSTALL_SCRIPT, scripts_dir)
        r = _run_install(repo_dir, ha_config)
        assert r.returncode != 0
        assert "must be inside" in r.stdout


class TestErrorConfigDirMissing:
    """HA config directory does not exist."""

    def test_fails(self, tmp_path: Path) -> None:
        ha_config, repo_dir = _make_ha_env(tmp_path)
        bogus = tmp_path / "nonexistent"
        r = _run_install(repo_dir, bogus)
        assert r.returncode != 0
        assert "not found" in r.stdout


class TestMultipleErrors:
    """Multiple files have issues -- all are reported."""

    def test_error_count(
        self,
        ha_env: tuple[Path, Path],
    ) -> None:
        ha_config, repo_dir = ha_env
        # Make two files fail: both regular files
        for file_rel in INSTALLED_FILES[:2]:
            target = ha_config / file_rel
            target.parent.mkdir(
                parents=True,
                exist_ok=True,
            )
            target.write_text("not a symlink")
        r = _run_install(repo_dir, ha_config)
        assert r.returncode != 0
        assert "2 error(s)" in r.stdout


class TestSymlinkedConfigDir:
    """HA config dir reached through a symlink."""

    def test_succeeds_when_config_is_symlink(
        self,
        tmp_path: Path,
    ) -> None:
        """Reproduces HA container layout where
        /config is a symlink to /root/config.
        """
        real_config = tmp_path / "real_config"
        ha_env = _make_ha_env(tmp_path)
        # _make_ha_env puts config at tmp_path/config
        ha_config, repo_dir = ha_env

        # Move real dir and create symlink
        ha_config.rename(real_config)
        ha_config.symlink_to(real_config)

        r = _run_install(repo_dir, ha_config)
        assert r.returncode == 0, f"stdout: {r.stdout}\nstderr: {r.stderr}"
        for file_rel in INSTALLED_FILES:
            dst = ha_config / file_rel
            assert dst.is_symlink()

    def test_succeeds_when_repo_parent_is_symlink(
        self,
        tmp_path: Path,
    ) -> None:
        """Config dir is real but reached via a symlinked
        parent (e.g., /root/config -> /config, script
        run from /root/config/repo).
        """
        ha_config, repo_dir = _make_ha_env(tmp_path)

        # Create an alias to the config dir
        alias = tmp_path / "alias"
        alias.symlink_to(ha_config)

        # Run install using the alias as the config path
        r = _run_install(repo_dir, alias)
        assert r.returncode == 0, f"stdout: {r.stdout}\nstderr: {r.stderr}"


class TestCodeQuality(CodeQualityBase):
    ruff_targets = [
        "tests/test_install.py",
    ]
    mypy_targets: list[str] = []


class TestInstallScriptQuality:
    """Install script-specific quality checks."""

    def test_bash_syntax(self) -> None:
        """Verify install.sh has valid bash syntax."""
        r = subprocess.run(
            ["bash", "-n", str(INSTALL_SCRIPT)],
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, r.stderr


# -- Entry point -------------------------------------

if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", *sys.argv[1:]]))
