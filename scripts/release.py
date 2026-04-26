#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
# This is AI generated code
"""Tag the manifest version at HEAD and create the GitHub release.

Reads ``custom_components/blueprint_toolkit/manifest.json``,
derives the tag ``vX.Y.Z``, and ensures three remote-side
artefacts converge on HEAD:

    1. local tag ``vX.Y.Z`` exists at HEAD
    2. remote tag ``vX.Y.Z`` matches local
    3. GitHub release ``vX.Y.Z`` exists

This script does NOT push commits -- run ``git push``
first. The split keeps "code is on origin" and "this
commit is a release" independent decisions: a manifest
bump can sit on master for a while before we publish it,
or never get published at all.

Each step is a no-op when its side effect is already in
place, so re-running after a partial failure picks up
where the previous run stopped.

Refuses when:
    - working tree is dirty (commit or stash first)
    - HEAD is not reachable from ``origin/master``
      (run ``git push`` first so the release tag will
      point to a commit that exists on origin)
    - local tag ``vX.Y.Z`` exists at a commit that is
      not an ancestor of HEAD (would require manual
      cleanup -- something is upside-down)
    - remote tag ``vX.Y.Z`` exists at a different SHA
      than local (manual cleanup required)

Skips tag + release entirely on commits that don't bump
the manifest -- in that case the version's tag already
exists at an older ancestor commit, and there is nothing
new to publish.

Release notes: HEAD's commit body. The title is the tag
name. ``--release-notes`` overrides the body if provided.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = (
    REPO_ROOT / "custom_components" / "blueprint_toolkit" / "manifest.json"
)


def _run(
    cmd: list[str],
    *,
    check: bool,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess and optionally die on non-zero exit."""
    r = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        capture_output=capture,
        text=True,
        check=False,
    )
    if check and r.returncode != 0:
        sys.stderr.write(f"$ {' '.join(cmd)}\n")
        if r.stdout:
            sys.stderr.write(r.stdout)
        if r.stderr:
            sys.stderr.write(r.stderr)
        sys.exit(1)
    return r


def _git(*args: str, check: bool = True, capture: bool = True) -> str:
    return _run(
        ["git", *args],
        check=check,
        capture=capture,
    ).stdout


def _gh(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return _run(["gh", *args], check=check, capture=True)


def _read_manifest_version() -> str:
    return str(json.loads(MANIFEST_PATH.read_text())["version"])


def _check_clean_tree() -> None:
    out = _git("status", "--porcelain").strip()
    if out:
        sys.stderr.write("error: working tree has uncommitted changes:\n")
        sys.stderr.write(out + "\n")
        sys.exit(1)


def _head_sha() -> str:
    return _git("rev-parse", "HEAD").strip()


def _head_commit_body() -> str:
    """Return HEAD's commit body (everything after the subject)."""
    return _git("log", "-1", "--format=%b").strip()


def _local_tag_sha(tag: str) -> str | None:
    r = _run(
        ["git", "rev-list", "-n", "1", tag, "--"],
        check=False,
    )
    return r.stdout.strip() if r.returncode == 0 else None


def _remote_tag_sha(tag: str) -> str | None:
    out = _git("ls-remote", "origin", f"refs/tags/{tag}")
    if not out.strip():
        return None
    return out.split()[0]


def _release_exists(tag: str) -> bool:
    r = _gh("release", "view", tag, check=False)
    return r.returncode == 0


def _ensure_local_tag(tag: str, head: str, *, dry_run: bool) -> bool:
    """Make sure the local annotated tag exists at HEAD.

    Returns True iff the tag is now (or was already) at
    HEAD. Returns False if a tag exists at an older
    commit (this push doesn't represent a new release).
    Errors out if the tag exists at a non-HEAD,
    non-ancestor commit (would require manual cleanup).
    """
    local = _local_tag_sha(tag)
    if local == head:
        print(f"local tag {tag} already at HEAD")
        return True
    if local is None:
        print(f"creating local annotated tag {tag} at HEAD")
        if not dry_run:
            _git("tag", "-a", tag, "-m", tag, capture=False)
        return True
    # Tag exists locally at some other commit. If HEAD descends
    # from that commit, the version simply wasn't bumped for
    # subsequent commits -- that's fine, just push without
    # re-tagging. If HEAD doesn't descend, something is wrong.
    is_ancestor = (
        _run(
            ["git", "merge-base", "--is-ancestor", local, head],
            check=False,
        ).returncode
        == 0
    )
    if is_ancestor:
        print(
            f"local tag {tag} at {local[:7]} (older than HEAD); "
            "manifest version unchanged, no new release",
        )
        return False
    sys.stderr.write(
        f"error: local tag {tag} at {local[:7]} is not an ancestor "
        f"of HEAD ({head[:7]}). Investigate before pushing.\n",
    )
    sys.exit(1)


def _check_head_on_origin() -> None:
    """Refuse if HEAD isn't reachable from origin/master.

    The release tag will point to HEAD, so HEAD must
    already be on the remote -- otherwise the tag (once
    pushed) would reference a commit that doesn't exist
    on origin until someone pushes master.
    """
    # Fetch origin/master without merging so the local
    # ref is fresh. Quiet mode -- we just want the side
    # effect of an updated remote-tracking ref.
    _git("fetch", "--quiet", "origin", "master", capture=False)
    r = _run(
        ["git", "merge-base", "--is-ancestor", "HEAD", "origin/master"],
        check=False,
    )
    if r.returncode != 0:
        sys.stderr.write(
            "error: HEAD is not reachable from origin/master. "
            "Run 'git push' first so the release tag points to "
            "a commit that exists on origin.\n",
        )
        sys.exit(1)


def _push_tag(tag: str, *, dry_run: bool) -> None:
    print(f"pushing tag {tag} to origin...")
    if dry_run:
        return
    _git("push", "origin", tag, capture=False)


def _create_release(tag: str, body: str, *, dry_run: bool) -> None:
    print(f"creating GitHub release {tag}...")
    if dry_run:
        return
    _gh(
        "release",
        "create",
        tag,
        "--title",
        tag,
        "--notes",
        body or tag,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(__doc__ or "").strip().splitlines()[0],
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would happen without modifying anything.",
    )
    parser.add_argument(
        "--release-notes",
        type=str,
        default=None,
        help=("Override release notes. Defaults to HEAD's commit body."),
    )
    args = parser.parse_args()

    _check_clean_tree()
    _check_head_on_origin()
    version = _read_manifest_version()
    tag = f"v{version}"
    head = _head_sha()

    print(f"manifest version: {version}")
    print(f"target tag      : {tag}")
    print(f"HEAD            : {head[:7]}")
    print()

    is_release = _ensure_local_tag(tag, head, dry_run=args.dry_run)

    # Pre-flight remote tag sanity check before pushing
    # anything: if remote already has the tag at a
    # different SHA than ours, refuse rather than risk a
    # tag-history scramble.
    remote_tag = _remote_tag_sha(tag)
    local_tag = (
        _local_tag_sha(tag)
        if not args.dry_run
        else (head if is_release else _local_tag_sha(tag))
    )
    if remote_tag is not None and local_tag is not None:
        if remote_tag != local_tag:
            sys.stderr.write(
                f"error: remote tag {tag} ({remote_tag[:7]}) differs "
                f"from local ({local_tag[:7]}). Manual cleanup "
                "required.\n",
            )
            return 1

    if is_release and remote_tag != head:
        _push_tag(tag, dry_run=args.dry_run)

    if is_release:
        if _release_exists(tag) and not args.dry_run:
            print(f"GitHub release {tag} already exists; skipping")
        else:
            body = (
                args.release_notes
                if args.release_notes is not None
                else _head_commit_body()
            )
            _create_release(tag, body, dry_run=args.dry_run)

    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
