#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
# This is AI generated code
"""Deploy this repo to a Home Assistant host.

Ships every git-tracked file to the install path on the HA
host (default /root/ha-blueprint-toolkit), removes files
the host has under owned top-level entries that git does
not, runs scripts/dev-install.py on the host to reconcile
the /config/... symlinks, then fires pyscript.reload and
automation.reload via the HA REST API.

Requires a clean working tree by default: refuses to run
if ``git status --porcelain`` is non-empty. Pass
``--allow-dirty`` to bypass that check and deploy
working-tree content as-is (tracked files with local
modifications ship as-is; untracked files matching
``.gitignore`` remain excluded; untracked files not
matching ship too). Without an API key the file deploy
and dev-install run normally but the reload calls are
skipped with a warning.

Use --dry-run to print the deploy + reload plan without
touching the host.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import shlex
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_HOST = "root@homeassistant"
# Outside of /config/ so the clone never collides with the
# HACS-installed tree under /config/custom_components/.
DEFAULT_INSTALL_PATH = "/root/ha-blueprint-toolkit"
DEFAULT_HA_CONFIG = "/config"


def git_root() -> Path:
    out = subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"],
        text=True,
    )
    return Path(out.strip())


def check_clean_tree(root: Path) -> None:
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


def list_tracked(root: Path, *, include_untracked: bool = False) -> list[str]:
    """List files to deploy.

    Normally tracked files only. With ``include_untracked``,
    also returns untracked files that ``.gitignore`` does not
    exclude -- ``git ls-files`` takes a matching ``--others
    --exclude-standard`` for that.
    """
    args = ["git", "ls-files"]
    if include_untracked:
        args.extend(["--cached", "--others", "--exclude-standard"])
    out = subprocess.check_output(args, cwd=root, text=True)
    return sorted(p for p in out.splitlines() if p)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def local_hashes(root: Path, tracked: list[str]) -> dict[str, str]:
    """Return ``{path: sha256}`` for files present on disk.

    Silently drops files that aren't on disk (staged deletes,
    untracked-then-deleted entries). They fall through to the
    "removed" bucket of the deploy diff because they're absent
    from the returned mapping.
    """
    return {p: sha256_file(root / p) for p in tracked if (root / p).is_file()}


def owned_top_level(tracked: list[str]) -> list[str]:
    """Top-level path segments that this deploy owns.

    These are the only parts of the remote install path
    we scan and prune; anything else on the host (``.git/``,
    ``.venv/``, editor droppings) is left untouched.
    """
    return sorted({p.split("/", 1)[0] for p in tracked})


def remote_hashes(
    host: str,
    install_path: str,
    owned: list[str],
) -> dict[str, str]:
    install_path = install_path.rstrip("/")
    targets = " ".join(shlex.quote(f"{install_path}/{p}") for p in owned)
    script = (
        f"for t in {targets}; do "
        f'  [ -e "$t" ] || continue; '
        f'  find "$t" -type f -print0 | xargs -0 -r sha256sum; '
        "done"
    )
    out = subprocess.check_output(
        ["ssh", host, script],
        text=True,
    )
    result: dict[str, str] = {}
    prefix = f"{install_path}/"
    for line in out.splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        digest, abspath = parts
        if abspath.startswith(prefix):
            result[abspath[len(prefix) :]] = digest
    return result


def diff_files(
    local: dict[str, str],
    remote: dict[str, str],
) -> tuple[list[str], list[str], list[str]]:
    installed: list[str] = []
    updated: list[str] = []
    for p, h in local.items():
        rh = remote.get(p)
        if rh is None:
            installed.append(p)
        elif rh != h:
            updated.append(p)
    removed = [p for p in remote if p not in local]
    return sorted(installed), sorted(updated), sorted(removed)


# After the HACS migration, the on-host repo has symlinks
# at its root (blueprints/, pyscript/, docs/) that resolve
# into custom_components/blueprint_toolkit/bundled/.
# Any file change under bundled/ affects HA's view via those
# symlinks, so we always fire both reload services when
# anything ships -- keeping the heuristic simple and
# impossible to get wrong in a way that leaves HA serving
# stale code.
_BUNDLED_PREFIX = "custom_components/blueprint_toolkit/bundled/"


def plan_reloads(
    changed: set[str],
    *,
    force_reloads: bool,
    ha_restart: bool,
) -> list[str]:
    """Return a list of reload actions to run in order.

    Actions: "pyscript.reload", "automation.reload",
    "ha core restart".
    """
    if ha_restart:
        return ["ha core restart"]
    if force_reloads:
        return ["pyscript.reload", "automation.reload"]
    # Any change under bundled/ or under the legacy top-
    # level pyscript/ or blueprints/ triggers both reloads.
    # Cheaper than trying to detect which kind of content
    # changed -- both services are fast.
    if any(
        p.startswith(_BUNDLED_PREFIX)
        or p.startswith("pyscript/")
        or p.startswith("blueprints/")
        for p in changed
    ):
        return ["pyscript.reload", "automation.reload"]
    return []


def print_plan(
    installed: list[str],
    updated: list[str],
    removed: list[str],
    run_dev_install: bool,
    reloads: list[str],
) -> None:
    for p in installed:
        print(f"installed: {p}")
    for p in updated:
        print(f"updated: {p}")
    for p in removed:
        print(f"removed: {p}")
    if run_dev_install:
        print("run: dev-install.py")
    for action in reloads:
        print(f"reload: {action}")


def deploy_files(
    host: str,
    install_path: str,
    root: Path,
    files: list[str],
) -> None:
    if not files:
        return
    tar_cmd = ["tar", "-cf", "-", "-C", str(root), *files]
    with subprocess.Popen(tar_cmd, stdout=subprocess.PIPE) as tar_proc:
        remote = (
            f"mkdir -p {shlex.quote(install_path)} && "
            f"cd {shlex.quote(install_path)} && tar -xf -"
        )
        result = subprocess.run(
            ["ssh", host, remote],
            stdin=tar_proc.stdout,
            check=False,
        )
        if tar_proc.stdout is not None:
            tar_proc.stdout.close()
        tar_rc = tar_proc.wait()
    if tar_rc != 0:
        raise RuntimeError(f"local tar exited {tar_rc}")
    if result.returncode != 0:
        raise RuntimeError(
            f"remote tar extraction exited {result.returncode}",
        )


def remove_remote(
    host: str,
    install_path: str,
    paths: list[str],
) -> None:
    if not paths:
        return
    abs_paths = " ".join(
        shlex.quote(f"{install_path.rstrip('/')}/{p}") for p in paths
    )
    subprocess.run(
        ["ssh", host, f"rm -f {abs_paths}"],
        check=True,
    )


def run_dev_install(
    host: str,
    install_path: str,
    ha_config: str,
    cli_symlink_dir: str | None,
) -> None:
    """Invoke scripts/dev-install.py on the host.

    dev-install.py reconciles /config/... symlinks against
    the shipped bundle. Always run after a file change;
    it is idempotent and fast.
    """
    script = f"{install_path.rstrip('/')}/scripts/dev-install.py"
    cmd = (
        f"{shlex.quote(script)} "
        f"--repo-dir {shlex.quote(install_path)} "
        f"--ha-config {shlex.quote(ha_config)}"
    )
    if cli_symlink_dir is not None:
        cmd += f" --cli-symlink-dir {shlex.quote(cli_symlink_dir)}"
    subprocess.run(["ssh", host, cmd], check=True)


def host_only(host: str) -> str:
    return host.split("@", 1)[-1]


def call_service_reload(host: str, api_key: str, service: str) -> None:
    domain, action = service.split(".", 1)
    url = f"http://{host_only(host)}:8123/api/services/{domain}/{action}"
    req = urllib.request.Request(
        url,
        data=b"",
        method="POST",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30):
            pass
    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f"{service} returned HTTP {e.code}: {e.reason}",
        ) from e


def do_ha_restart(host: str) -> None:
    subprocess.run(
        ["ssh", host, "ha core restart"],
        check=True,
    )


def execute_reloads(
    host: str,
    api_key: str | None,
    reloads: list[str],
) -> None:
    for action in reloads:
        if action == "ha core restart":
            do_ha_restart(host)
            continue
        if api_key is None:
            sys.stderr.write(
                f"warning: skipping '{action}' -- no api key provided\n",
            )
            continue
        call_service_reload(host, api_key, action)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=(
            "Deploy this repo to a Home Assistant host and run needed reloads."
        ),
    )
    p.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"ssh target user@host (default: {DEFAULT_HOST})",
    )
    p.add_argument(
        "--install-path",
        default=DEFAULT_INSTALL_PATH,
        help=(
            "absolute path of the repo clone on the host "
            f"(default: {DEFAULT_INSTALL_PATH})"
        ),
    )
    p.add_argument(
        "--ha-config",
        default=DEFAULT_HA_CONFIG,
        help=(
            "absolute path of HA's config dir on the host "
            f"(default: {DEFAULT_HA_CONFIG}). Passed to "
            "dev-install.py --ha-config."
        ),
    )
    p.add_argument(
        "--cli-symlink-dir",
        default=None,
        help=(
            "passed to dev-install.py --cli-symlink-dir. "
            "If unset, the CLI script is not symlinked anywhere."
        ),
    )
    p.add_argument(
        "--api-key-file",
        type=Path,
        default=None,
        help=(
            "file containing the HA long-lived access token; "
            "required to run pyscript.reload / automation.reload"
        ),
    )
    p.add_argument(
        "--force-reloads",
        action="store_true",
        help="run both pyscript.reload and automation.reload unconditionally",
    )
    p.add_argument(
        "--ha-restart",
        action="store_true",
        help=(
            "run 'ha core restart' after file changes (via ssh); "
            "replaces pyscript.reload / automation.reload"
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "print the deploy + reload plan and exit without touching the host"
        ),
    )
    p.add_argument(
        "--allow-dirty",
        action="store_true",
        help=(
            "skip the clean-tree check and deploy working-tree content; "
            "tracked files with local modifications ship as-is, and "
            "untracked files not matching .gitignore are included. "
            "intended for iterative dev -- avoid for production deploys."
        ),
    )
    args = p.parse_args()
    if args.force_reloads and args.ha_restart:
        p.error("--force-reloads and --ha-restart are mutually exclusive")
    return args


def main() -> int:
    # Line-buffer stdout so our plan header reliably prints
    # before any subprocess output (tar, ssh, dev-install.py)
    # when stdout is piped.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(line_buffering=True)

    args = parse_args()
    root = git_root()
    if args.allow_dirty:
        sys.stderr.write(
            "warning: --allow-dirty set; deploying working-tree content\n",
        )
    else:
        check_clean_tree(root)

    tracked = list_tracked(root, include_untracked=args.allow_dirty)
    owned = owned_top_level(tracked)
    local = local_hashes(root, tracked)
    remote = remote_hashes(args.host, args.install_path, owned)
    installed, updated, removed = diff_files(local, remote)

    changed = set(installed) | set(updated) | set(removed)
    reloads = plan_reloads(
        changed,
        force_reloads=args.force_reloads,
        ha_restart=args.ha_restart,
    )
    # dev-install.py runs whenever any file changed on the
    # host. It is idempotent and fast; no heuristic skip.
    run_install = bool(changed)

    print_plan(installed, updated, removed, run_install, reloads)

    if args.dry_run:
        return 0

    api_key: str | None = None
    if args.api_key_file is not None:
        api_key = args.api_key_file.read_text().strip()

    to_deploy = installed + updated
    deploy_files(args.host, args.install_path, root, to_deploy)
    remove_remote(args.host, args.install_path, removed)
    if run_install:
        run_dev_install(
            args.host,
            args.install_path,
            args.ha_config,
            args.cli_symlink_dir,
        )
    execute_reloads(args.host, api_key, reloads)
    return 0


if __name__ == "__main__":
    sys.exit(main())
