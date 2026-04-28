#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest", "pytest-cov", "ruff", "mypy"]
# ///
# This is AI generated code
"""Tests for scripts/dev-install.py.

Replaces tests/test_install.py (bash-based install.sh is
deleted in this commit). Invokes dev-install.py as a
subprocess against tempdir-based repo + config layouts,
verifying symlink creation, idempotency, manifest
persistence, error handling, and CLI install.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from conftest import CodeQualityBase

REPO_ROOT = Path(__file__).parent.parent
INTEGRATION_SRC = REPO_ROOT / "custom_components" / "blueprint_toolkit"
DEV_INSTALL_REL = "scripts/dev-install.py"
MANIFEST_FILENAME = ".blueprint_toolkit.manifest.json"


# ---- Helpers --------------------------------------------------


def _make_fake_integration(root: Path) -> Path:
    """Build a minimal blueprint_toolkit/ layout and return the integration dir.

    Copies the real integration's top-level Python sources
    (so dev-install.py can import installer / reconciler)
    plus the real ``scripts/dev-install.py``, then replaces
    ``bundled/`` with a fake fixture tree. The returned
    path is the integration directory itself; tests invoke
    ``<integration>/scripts/dev-install.py`` directly so
    auto-discovery resolves to this fake integration.
    """
    integration_dir = root / "blueprint_toolkit"
    integration_dir.mkdir(parents=True)

    # Real Python sources (HA-free at module level, so they
    # import outside of HA via dev-install.py's sys.path
    # injection).
    for name in ("__init__.py", "installer.py", "reconciler.py", "const.py"):
        shutil.copy2(INTEGRATION_SRC / name, integration_dir / name)

    scripts_dir = integration_dir / "scripts"
    scripts_dir.mkdir()
    shutil.copy2(
        INTEGRATION_SRC / "scripts" / "dev-install.py",
        scripts_dir / "dev-install.py",
    )
    (scripts_dir / "dev-install.py").chmod(0o755)

    bundled = integration_dir / "bundled"
    (bundled / "blueprints" / "automation" / "blueprint_toolkit").mkdir(
        parents=True
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
    (bundled / "pyscript" / "blueprint_toolkit.py").write_text("# svc\n")
    (bundled / "pyscript" / "modules" / "demo.py").write_text("# module\n")
    (bundled / "www" / "blueprint_toolkit" / "docs" / "demo.html").write_text(
        "<html>demo</html>\n"
    )
    (bundled / "cli" / "demo_cli.py").write_text(
        "#!/usr/bin/env python3\n",
    )
    return integration_dir


def _run_dev_install(
    *,
    integration_dir: Path,
    ha_config: Path,
    cli_symlink_dir: Path | None = None,
    dry_run: bool = False,
) -> subprocess.CompletedProcess[str]:
    cmd = [
        str(integration_dir / DEV_INSTALL_REL),
        "--ha-config",
        str(ha_config),
    ]
    if cli_symlink_dir is not None:
        cmd.extend(["--cli-symlink-dir", str(cli_symlink_dir)])
    if dry_run:
        cmd.append("--dry-run")
    return subprocess.run(cmd, capture_output=True, text=True)


def _installed_paths(ha_config: Path) -> list[Path]:
    return sorted(p for p in ha_config.rglob("*") if p.is_symlink())


def _load_manifest(ha_config: Path) -> list[str]:
    path = ha_config / MANIFEST_FILENAME
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    return sorted(data.get("destinations", []))


# ---- Fresh install --------------------------------------------


class TestFreshInstall:
    def test_creates_expected_symlinks(self, tmp_path: Path) -> None:
        integration = _make_fake_integration(tmp_path / "build")
        config = tmp_path / "config"
        config.mkdir()

        r = _run_dev_install(integration_dir=integration, ha_config=config)
        assert r.returncode == 0, (
            f"dev-install failed: stdout={r.stdout} stderr={r.stderr}"
        )

        created = {str(p.relative_to(config)) for p in _installed_paths(config)}
        # bundled/www/ is not installed by the reconciler;
        # the integration registers an aiohttp static
        # route directly at the bundled docs dir instead.
        assert created == {
            "blueprints/automation/blueprint_toolkit/demo.yaml",
            "pyscript/blueprint_toolkit.py",
            "pyscript/modules/demo.py",
        }

    def test_symlinks_resolve_to_bundled(self, tmp_path: Path) -> None:
        integration = _make_fake_integration(tmp_path / "build")
        config = tmp_path / "config"
        config.mkdir()

        _run_dev_install(integration_dir=integration, ha_config=config)
        src = config / "pyscript/modules/demo.py"
        assert src.is_symlink()
        assert os.path.realpath(src) == str(
            (
                integration / "bundled" / "pyscript" / "modules" / "demo.py"
            ).resolve()
        )

    def test_manifest_persisted(self, tmp_path: Path) -> None:
        integration = _make_fake_integration(tmp_path / "build")
        config = tmp_path / "config"
        config.mkdir()

        _run_dev_install(integration_dir=integration, ha_config=config)
        paths = _load_manifest(config)
        assert (
            len(paths) == 3
        )  # blueprints, pyscript wrapper, pyscript module (no www, no cli)
        for p in paths:
            assert p.startswith(str(config))


# ---- Idempotency ----------------------------------------------


class TestIdempotent:
    def test_second_run_no_changes(self, tmp_path: Path) -> None:
        integration = _make_fake_integration(tmp_path / "build")
        config = tmp_path / "config"
        config.mkdir()

        _run_dev_install(integration_dir=integration, ha_config=config)
        r = _run_dev_install(integration_dir=integration, ha_config=config)
        assert r.returncode == 0
        # No install/update/remove lines in stdout (all KEEP).
        for marker in ("install:", "update:", "remove:"):
            assert marker not in r.stdout, (
                f"unexpected {marker!r} on idempotent rerun: {r.stdout}"
            )


# ---- CLI option -----------------------------------------------


class TestCliSymlinkDir:
    def test_cli_installed_when_option_set(self, tmp_path: Path) -> None:
        integration = _make_fake_integration(tmp_path / "build")
        config = tmp_path / "config"
        config.mkdir()
        cli_dir = tmp_path / "root"
        cli_dir.mkdir()

        _run_dev_install(
            integration_dir=integration,
            ha_config=config,
            cli_symlink_dir=cli_dir,
        )
        expected = cli_dir / "demo_cli.py"
        assert expected.is_symlink()

    def test_cli_skipped_by_default(self, tmp_path: Path) -> None:
        integration = _make_fake_integration(tmp_path / "build")
        config = tmp_path / "config"
        config.mkdir()

        _run_dev_install(integration_dir=integration, ha_config=config)
        # No CLI files should have been produced anywhere
        # under config (dev-install without cli-dir does not
        # install cli into /config either).
        created = {p.name for p in _installed_paths(config)}
        assert "demo_cli.py" not in created


# ---- Dry run --------------------------------------------------


class TestDryRun:
    def test_dry_run_creates_nothing(self, tmp_path: Path) -> None:
        integration = _make_fake_integration(tmp_path / "build")
        config = tmp_path / "config"
        config.mkdir()

        r = _run_dev_install(
            integration_dir=integration,
            ha_config=config,
            dry_run=True,
        )
        assert r.returncode == 0
        assert not _installed_paths(config)
        assert not (config / MANIFEST_FILENAME).exists()
        # Plan should still be printed.
        assert "install:" in r.stdout


# ---- Conflict handling ----------------------------------------


class TestConflicts:
    def test_regular_file_at_dest_is_reported_not_overwritten(
        self,
        tmp_path: Path,
    ) -> None:
        integration = _make_fake_integration(tmp_path / "build")
        config = tmp_path / "config"
        config.mkdir()
        # Pre-seed a user file at a destination.
        conflict_path = config / "pyscript/modules/demo.py"
        conflict_path.parent.mkdir(parents=True)
        conflict_path.write_text("# user content\n")

        r = _run_dev_install(integration_dir=integration, ha_config=config)
        assert r.returncode == 3  # conflicts exit code
        assert "conflict:" in r.stdout
        assert "regular_file" in r.stdout
        # File unchanged.
        assert conflict_path.read_text() == "# user content\n", (
            "dev-install must not overwrite regular files"
        )
        # Other files still got installed.
        assert (config / "pyscript/blueprint_toolkit.py").is_symlink()


# ---- Stale-removal path ---------------------------------------


class TestStaleRemoval:
    def test_removed_bundled_file_removes_symlink(
        self,
        tmp_path: Path,
    ) -> None:
        integration = _make_fake_integration(tmp_path / "build")
        config = tmp_path / "config"
        config.mkdir()

        _run_dev_install(integration_dir=integration, ha_config=config)
        victim = config / "pyscript/modules/demo.py"
        assert victim.is_symlink()

        # Remove the source from bundled and re-run.
        (integration / "bundled" / "pyscript" / "modules" / "demo.py").unlink()

        r = _run_dev_install(integration_dir=integration, ha_config=config)
        assert r.returncode == 0
        assert not victim.exists(), "stale symlink should have been removed"


# ---- Argument errors ------------------------------------------


class TestArgErrors:
    def test_integration_dir_basename_must_match(self, tmp_path: Path) -> None:
        # Auto-discovery uses Path(__file__).resolve().parent.parent;
        # putting dev-install.py one level deep under a
        # mis-named directory triggers the basename guard.
        integration = _make_fake_integration(tmp_path / "build")
        wrong_name = tmp_path / "build" / "wrong_name"
        integration.rename(wrong_name)
        config = tmp_path / "config"
        config.mkdir()
        r = _run_dev_install(integration_dir=wrong_name, ha_config=config)
        assert r.returncode == 2
        assert "basename must be 'blueprint_toolkit'" in r.stderr

    def test_missing_ha_config(self, tmp_path: Path) -> None:
        integration = _make_fake_integration(tmp_path / "build")
        r = _run_dev_install(
            integration_dir=integration,
            ha_config=tmp_path / "no-such-config",
        )
        assert r.returncode == 2
        assert "--ha-config not a directory" in r.stderr


class TestCodeQuality(CodeQualityBase):
    ruff_targets = [
        "custom_components/blueprint_toolkit/scripts/dev-install.py",
        "tests/test_dev_install.py",
    ]
    mypy_targets = [
        "custom_components/blueprint_toolkit/scripts/dev-install.py",
    ]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", *sys.argv[1:]]))
