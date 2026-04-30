#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
# This is AI generated code
"""Deploy this repo's integration to a Home Assistant host.

Builds a fresh timestamped copy of the integration on the
host under ``<workspace>/<YYYYMMDD_HHMMSS>/blueprint_toolkit/``
(default workspace: ``/config/ha-blueprint-toolkit``),
flips ``/config/custom_components/blueprint_toolkit`` to a
symlink that points at the new build, runs the bundled
``scripts/dev-install.py`` to refresh the
``/config/blueprints/`` symlinks, and restarts HA.

The first run preserves the HACS-installed integration as
``<workspace>/<vX.Y.Z>/blueprint_toolkit/`` (using the
version from ``.storage/hacs.repositories``). Subsequent
runs leave that snapshot alone and just add another
timestamped build directory next to it.

``--restore`` reverses the process: it reinstates the
preserved HACS snapshot at
``/config/custom_components/blueprint_toolkit`` and removes
the workspace, leaving the host as if dev-deploy had never
run.

HA is always restarted after a deploy or restore. Integration
code changes (custom_components/.../*.py) require a Python-level
reload that the config-entry reload API does not provide; the
restart is the only reliable way.

By default the working tree must be clean. ``--allow-dirty``
ships uncommitted edits; tracked-modified files go through
unchanged, and untracked files not matching ``.gitignore`` are
included.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import io
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

DEFAULT_HOST = "root@homeassistant"
DEFAULT_HA_CONFIG = "/config"
DEFAULT_WORKSPACE = "/config/ha-blueprint-toolkit"
INTEGRATION_NAME = "blueprint_toolkit"
HACS_STORAGE_RELPATH = ".storage/hacs.repositories"
HACS_REPO_FULL_NAME = "epilatow/ha-blueprint-toolkit"


def _run_ssh(host: str, command: str, *, check: bool = True) -> str:
    """Run a shell snippet on the host and return stdout."""
    r = subprocess.run(
        ["ssh", host, command],
        capture_output=True,
        text=True,
    )
    if check and r.returncode != 0:
        raise RuntimeError(
            f"ssh {host} {command!r} exited {r.returncode}: "
            f"stderr={r.stderr.strip()!r}",
        )
    return r.stdout


def _git_root() -> Path:
    out = subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"],
        text=True,
    )
    return Path(out.strip())


def _check_clean_tree(root: Path) -> None:
    out = subprocess.check_output(
        ["git", "status", "--porcelain"],
        cwd=root,
        text=True,
    )
    if out.strip():
        sys.stderr.write(
            "error: working tree has uncommitted or untracked "
            "files; commit, stash, or re-run with --allow-dirty:\n",
        )
        sys.stderr.write(out)
        sys.exit(1)


def _timestamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def _read_hacs_version(host: str, ha_config: str) -> str | None:
    """Return the HACS-recorded version of this integration, if available.

    Returns ``None`` when HACS is not installed, the
    repository entry is absent, or the entry has no
    version_installed / installed_commit. Callers fall
    back to a generic ``"hacs"`` directory name when
    nothing usable is found.
    """
    storage_path = f"{ha_config.rstrip('/')}/{HACS_STORAGE_RELPATH}"
    out = _run_ssh(
        host,
        f"[ -f {shlex.quote(storage_path)} ] && "
        f"cat {shlex.quote(storage_path)} || true",
    )
    if not out.strip():
        return None
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return None
    repos = data.get("data") or {}
    for entry in repos.values():
        if not isinstance(entry, dict):
            continue
        if entry.get("full_name", "").lower() != HACS_REPO_FULL_NAME:
            continue
        version = entry.get("version_installed")
        if isinstance(version, str) and version:
            return version
        commit = entry.get("installed_commit")
        if isinstance(commit, str) and commit:
            return commit
        return None
    return None


def _list_workspace(host: str, workspace: str) -> list[str]:
    """Return immediate child names of the workspace dir, or [] if absent."""
    out = _run_ssh(
        host,
        f"[ -d {shlex.quote(workspace)} ] && "
        f"ls -1 {shlex.quote(workspace)} || true",
    )
    return sorted(line for line in out.splitlines() if line.strip())


def _existing_install_kind(host: str, install_path: str) -> str:
    """Classify what's at ``/config/custom_components/blueprint_toolkit``.

    Returns one of:
    - ``"absent"``: nothing there.
    - ``"symlink"``: already a symlink (presumably ours).
    - ``"directory"``: a real directory (HACS install or
      manual copy) that the first deploy must preserve.
    - ``"file"``: an unexpected regular file; aborts caller.
    """
    out = _run_ssh(
        host,
        (
            f"if [ -L {shlex.quote(install_path)} ]; then echo symlink; "
            f"elif [ -d {shlex.quote(install_path)} ]; then echo directory; "
            f"elif [ -e {shlex.quote(install_path)} ]; then echo file; "
            f"else echo absent; fi"
        ),
    )
    return out.strip()


def _local_integration_dir(root: Path) -> Path:
    return root / "custom_components" / INTEGRATION_NAME


# dev-install.py lives inside the integration
# (custom_components/blueprint_toolkit/scripts/) so each
# deployed copy ships the matching installer/reconciler
# implementation. The repo-root scripts/dev-install.py is
# a symlink to it. Restore deliberately runs the snapshot's
# copy, not the laptop's.
_DEV_INSTALL_REL_PATH = "scripts/dev-install.py"


def _filename_safe(name: str) -> str:
    """Strip any character outside [A-Za-z0-9._-]; never empty."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "", name)
    return cleaned or "hacs"


def _build_remote_layout(
    *,
    workspace: str,
    timestamp: str,
) -> dict[str, str]:
    build_dir = f"{workspace}/{timestamp}"
    integration_dir = f"{build_dir}/{INTEGRATION_NAME}"
    return {
        "build_dir": build_dir,
        "integration_dir": integration_dir,
        "dev_install_path": f"{integration_dir}/{_DEV_INSTALL_REL_PATH}",
    }


def _ship_integration(
    host: str,
    root: Path,
    *,
    target_integration_dir: str,
    allow_dirty: bool,
) -> None:
    """Tar the local integration directory onto the host."""
    src = _local_integration_dir(root)
    if not src.is_dir():
        raise RuntimeError(f"local integration dir missing: {src}")

    files = _list_integration_files(root, allow_dirty=allow_dirty)
    if not files:
        raise RuntimeError(
            f"no integration files to ship from {src} -- "
            "is custom_components/blueprint_toolkit/ on disk?",
        )

    tar_cmd = ["tar", "-cf", "-", "-C", str(src), *files]
    parent = target_integration_dir.rstrip("/").rsplit("/", 1)[0]
    remote = (
        f"rm -rf {shlex.quote(target_integration_dir)} && "
        f"mkdir -p {shlex.quote(target_integration_dir)} && "
        f"mkdir -p {shlex.quote(parent)} && "
        f"cd {shlex.quote(target_integration_dir)} && tar -xf -"
    )
    with subprocess.Popen(tar_cmd, stdout=subprocess.PIPE) as tar_proc:
        result = subprocess.run(
            ["ssh", host, remote],
            stdin=tar_proc.stdout,
            check=False,
        )
        if tar_proc.stdout is not None:
            tar_proc.stdout.close()
        rc = tar_proc.wait()
    if rc != 0:
        raise RuntimeError(f"local tar exited {rc}")
    if result.returncode != 0:
        raise RuntimeError(
            f"remote tar extraction exited {result.returncode}",
        )


def _list_integration_files(
    root: Path,
    *,
    allow_dirty: bool,
) -> list[str]:
    """Return git-managed paths under custom_components/blueprint_toolkit/.

    Paths are relative to that directory (so they extract
    correctly into the remote integration dir). Honours
    ``--allow-dirty`` by including untracked-and-not-ignored
    files.
    """
    rel_root = f"custom_components/{INTEGRATION_NAME}"
    args = ["git", "ls-files"]
    if allow_dirty:
        args.extend(["--cached", "--others", "--exclude-standard"])
    args.extend(["--", rel_root])
    out = subprocess.check_output(args, cwd=root, text=True)
    prefix = f"{rel_root}/"
    paths: list[str] = []
    for line in out.splitlines():
        if not line.startswith(prefix):
            continue
        rel = line[len(prefix) :]
        if (root / line).is_file():
            paths.append(rel)
    return sorted(paths)


def _swap_symlink(host: str, install_path: str, target: str) -> None:
    """Atomically point ``install_path`` at ``target``.

    Requires HA's container view of ``target`` to be a
    real directory (per the empirical test, the symlink
    target has to live under ``/config`` to be visible
    inside the container).
    """
    cmd = (
        f"if [ -L {shlex.quote(install_path)} ] || "
        f"[ -e {shlex.quote(install_path)} ]; then "
        f"rm -rf {shlex.quote(install_path)}; fi && "
        f"ln -s {shlex.quote(target)} {shlex.quote(install_path)}"
    )
    _run_ssh(host, cmd)


def _move_dir(host: str, src: str, dest: str) -> None:
    parent = dest.rstrip("/").rsplit("/", 1)[0]
    cmd = (
        f"mkdir -p {shlex.quote(parent)} && "
        f"mv {shlex.quote(src)} {shlex.quote(dest)}"
    )
    _run_ssh(host, cmd)


def _rmtree_remote(host: str, path: str) -> None:
    if not path.startswith("/") or path in {"/", "/config"}:
        raise RuntimeError(f"refusing to rm -rf {path!r}")
    _run_ssh(host, f"rm -rf {shlex.quote(path)}")


def _run_dev_install(
    host: str,
    *,
    dev_install_path: str,
    ha_config: str,
    cli_symlink_dir: str | None,
) -> None:
    """Run the deployed dev-install.py over ssh.

    The script auto-discovers its integration dir from
    ``__file__``, so the only required arg is ``--ha-config``.
    Restore deliberately invokes the snapshot's copy of
    dev-install.py; treat its CLI as the stable contract
    documented in dev-install.py.
    """
    cmd = (
        f"python3 {shlex.quote(dev_install_path)} "
        f"--ha-config {shlex.quote(ha_config)}"
    )
    if cli_symlink_dir is not None:
        cmd += f" --cli-symlink-dir {shlex.quote(cli_symlink_dir)}"
    subprocess.run(["ssh", host, cmd], check=True)


def _ha_restart(host: str, restart_cmd: str) -> None:
    subprocess.run(["ssh", host, restart_cmd], check=True)


# ---- High-level operations -----------------------------------


def _plan_deploy(
    host: str,
    *,
    workspace: str,
    install_path: str,
    ha_config: str,
    timestamp: str,
) -> dict[str, str | None]:
    """Compute the actions a deploy will take, without doing them."""
    layout = _build_remote_layout(workspace=workspace, timestamp=timestamp)
    existing_kind = _existing_install_kind(host, install_path)
    workspace_entries = _list_workspace(host, workspace)
    has_version_dir = any(name.startswith("v") for name in workspace_entries)

    preserve_to: str | None = None
    if existing_kind == "directory" and not has_version_dir:
        # First-time setup: snapshot the HACS install.
        version = _read_hacs_version(host, ha_config) or "hacs"
        version_safe = _filename_safe(version)
        preserve_to = f"{workspace}/{version_safe}/{INTEGRATION_NAME}"

    return {
        "existing_kind": existing_kind,
        "preserve_to": preserve_to,
        "build_dir": layout["build_dir"],
        "integration_dir": layout["integration_dir"],
        "dev_install_path": layout["dev_install_path"],
    }


def _print_deploy_plan(
    *,
    install_path: str,
    workspace: str,
    plan: dict[str, str | None],
) -> None:
    existing = plan["existing_kind"]
    preserve_to = plan["preserve_to"]
    build_dir = plan["build_dir"]
    integration_dir = plan["integration_dir"]
    dev_install_path = plan["dev_install_path"]
    if existing == "file":
        sys.stderr.write(
            f"error: {install_path} is a regular file; refusing to "
            "overwrite. Investigate manually.\n",
        )
        sys.exit(1)
    print(f"workspace: {workspace}")
    print(f"existing install: {existing}")
    if preserve_to is not None:
        print(f"preserve HACS install -> {preserve_to}")
    print(f"build: {build_dir}")
    print(f"ship integration -> {integration_dir}")
    print(f"symlink {install_path} -> {integration_dir}")
    print(f"run: {dev_install_path}")
    print("ha core restart")


def _do_deploy(
    host: str,
    *,
    root: Path,
    workspace: str,
    install_path: str,
    ha_config: str,
    cli_symlink_dir: str | None,
    plan: dict[str, str | None],
    allow_dirty: bool,
    ha_restart_cmd: str,
) -> None:
    preserve_to = plan["preserve_to"]
    build_dir = plan["build_dir"]
    integration_dir = plan["integration_dir"]
    dev_install_path = plan["dev_install_path"]
    if preserve_to is not None:
        _move_dir(host, install_path, preserve_to)
    assert build_dir is not None
    assert integration_dir is not None
    assert dev_install_path is not None
    _ship_integration(
        host,
        root,
        target_integration_dir=integration_dir,
        allow_dirty=allow_dirty,
    )
    # dev-install.py rides along inside the integration tar
    # at <integration>/scripts/dev-install.py; no separate
    # ship step needed.
    _swap_symlink(host, install_path, integration_dir)
    _run_dev_install(
        host,
        dev_install_path=dev_install_path,
        ha_config=ha_config,
        cli_symlink_dir=cli_symlink_dir,
    )
    _ha_restart(host, ha_restart_cmd)


def _plan_restore(
    host: str,
    *,
    workspace: str,
    install_path: str,
) -> dict[str, str]:
    """Compute the actions a restore will take, without doing them."""
    entries = _list_workspace(host, workspace)
    if not entries:
        sys.stderr.write(
            f"error: workspace {workspace} is missing or empty; "
            "nothing to restore.\n",
        )
        sys.exit(1)
    version_dirs = sorted(name for name in entries if name.startswith("v"))
    if not version_dirs:
        # Fall back to any non-timestamp dir (e.g. "hacs").
        version_dirs = sorted(
            name
            for name in entries
            if not re.fullmatch(r"\d{8}_\d{6}", name)
            and name != "dev-install.py"
        )
    if not version_dirs:
        sys.stderr.write(
            f"error: no preserved HACS snapshot found in {workspace}; "
            "expected a vX.Y.Z (or 'hacs') subdirectory.\n",
        )
        sys.exit(1)
    snapshot = version_dirs[-1]  # newest
    snapshot_path = f"{workspace}/{snapshot}/{INTEGRATION_NAME}"
    return {
        "snapshot": snapshot,
        "snapshot_path": snapshot_path,
        "install_path": install_path,
    }


def _print_restore_plan(
    *,
    workspace: str,
    plan: dict[str, str],
) -> None:
    print(f"workspace: {workspace}")
    print(f"snapshot: {plan['snapshot']}")
    print(f"remove symlink: {plan['install_path']}")
    print(f"restore: {plan['snapshot_path']} -> {plan['install_path']}")
    print(f"run: {plan['install_path']}/{_DEV_INSTALL_REL_PATH}")
    print(f"remove workspace: {workspace}")
    print("ha core restart")


def _do_restore(
    host: str,
    *,
    workspace: str,
    install_path: str,
    ha_config: str,
    cli_symlink_dir: str | None,
    plan: dict[str, str],
    ha_restart_cmd: str,
) -> None:
    snapshot_path = plan["snapshot_path"]
    # Remove the symlink (if any) before moving the
    # snapshot into its place.
    _run_ssh(
        host,
        f"if [ -L {shlex.quote(install_path)} ] || "
        f"[ -e {shlex.quote(install_path)} ]; then "
        f"rm -rf {shlex.quote(install_path)}; fi",
    )
    _move_dir(host, snapshot_path, install_path)
    # Re-run dev-install against the restored integration so
    # the bundled symlinks at /config/blueprints/ etc. point
    # at the HACS-installed bundle (and not at the workspace
    # that we are about to delete). Deliberately uses the
    # snapshot's own dev-install.py (now at
    # <install_path>/scripts/dev-install.py); see
    # dev-install.py's backward-compat comment.
    dev_install_remote = f"{install_path}/{_DEV_INSTALL_REL_PATH}"
    _run_dev_install(
        host,
        dev_install_path=dev_install_remote,
        ha_config=ha_config,
        cli_symlink_dir=cli_symlink_dir,
    )
    _rmtree_remote(host, workspace)
    _ha_restart(host, ha_restart_cmd)


# ---- argparse + main -----------------------------------------


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Deploy this repo's blueprint_toolkit integration "
            "to a Home Assistant host (or restore the "
            "preserved HACS install with --restore)."
        ),
    )
    p.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"ssh target user@host (default: {DEFAULT_HOST})",
    )
    p.add_argument(
        "--workspace",
        default=DEFAULT_WORKSPACE,
        help=(
            "directory on the host that holds the build "
            "snapshots and the on-host dev-install.py "
            f"(default: {DEFAULT_WORKSPACE})"
        ),
    )
    p.add_argument(
        "--ha-config",
        default=DEFAULT_HA_CONFIG,
        help=(
            "absolute path of HA's config dir on the host "
            f"(default: {DEFAULT_HA_CONFIG})"
        ),
    )
    p.add_argument(
        "--cli-symlink-dir",
        default=None,
        help=(
            "passed to dev-install.py --cli-symlink-dir. "
            "If unset, the CLI script is not symlinked "
            "anywhere on the host."
        ),
    )
    p.add_argument(
        "--restore",
        action="store_true",
        help=(
            "restore the preserved HACS snapshot to "
            "/config/custom_components/blueprint_toolkit "
            "and remove the workspace"
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="print the plan and exit without touching the host",
    )
    p.add_argument(
        "--allow-dirty",
        action="store_true",
        help=(
            "skip the clean-tree check and ship working-tree "
            "content (tracked files with local mods + "
            "untracked files not matching .gitignore)"
        ),
    )
    p.add_argument(
        "--ha-restart-cmd",
        default="ha core restart",
        help=(
            "shell command run on the host to restart HA "
            "after the deploy or restore. Override for test "
            "environments that don't ship the supervisor "
            "CLI (default: 'ha core restart')."
        ),
    )
    return p.parse_args()


def main() -> int:
    # Line-buffer stdout so the plan reliably prints
    # before any subprocess output (tar, ssh, dev-install.py)
    # when stdout is piped.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(line_buffering=True)

    args = _parse_args()
    install_path = (
        f"{args.ha_config.rstrip('/')}/custom_components/{INTEGRATION_NAME}"
    )

    if args.restore:
        plan = _plan_restore(
            args.host,
            workspace=args.workspace,
            install_path=install_path,
        )
        _print_restore_plan(workspace=args.workspace, plan=plan)
        if args.dry_run:
            return 0
        _do_restore(
            args.host,
            workspace=args.workspace,
            install_path=install_path,
            ha_config=args.ha_config,
            cli_symlink_dir=args.cli_symlink_dir,
            plan=plan,
            ha_restart_cmd=args.ha_restart_cmd,
        )
        return 0

    root = _git_root()
    if args.allow_dirty:
        sys.stderr.write(
            "warning: --allow-dirty set; deploying working-tree content\n",
        )
    else:
        _check_clean_tree(root)

    deploy_plan = _plan_deploy(
        args.host,
        workspace=args.workspace,
        install_path=install_path,
        ha_config=args.ha_config,
        timestamp=_timestamp(),
    )
    _print_deploy_plan(
        install_path=install_path,
        workspace=args.workspace,
        plan=deploy_plan,
    )
    if args.dry_run:
        return 0
    _do_deploy(
        args.host,
        root=root,
        workspace=args.workspace,
        install_path=install_path,
        ha_config=args.ha_config,
        cli_symlink_dir=args.cli_symlink_dir,
        plan=deploy_plan,
        allow_dirty=args.allow_dirty,
        ha_restart_cmd=args.ha_restart_cmd,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
