#!/usr/bin/env python3
# This is AI generated code
"""Full-stack docker tests for the manual developer workflow.

Exercises scripts/dev-install.py (the HA-node-side
installer) and scripts/dev-deploy.py (the laptop-side push)
against a real HA container with pyscript pre-installed.
See tests/docker/README.md for manual invocation during
interactive development.

Gated by @pytest.mark.docker. The default pytest run excludes
this module (``addopts = -m 'not docker'`` in pyproject.toml).
Run manually with ``pytest -m docker tests/docker`` from the
repo root so tests/docker/conftest.py provides its session
fixtures.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable

import pytest
from conftest import (
    DockerHA,
    copy_repo_into_container,
)

pytestmark = pytest.mark.docker

# The six blueprint entrypoint services registered by
# pyscript/blueprint_toolkit.py. Verifying all six
# appear is our "load succeeded" signal.
EXPECTED_SERVICES = frozenset(
    {
        "device_watchdog_blueprint_entrypoint",
        "entity_defaults_watchdog_blueprint_entrypoint",
        "reference_watchdog_blueprint_entrypoint",
        "sensor_threshold_switch_controller_blueprint_entrypoint",
        "trigger_entity_controller_blueprint_entrypoint",
        "zwave_route_manager_blueprint_entrypoint",
    },
)

# HA returns blueprint paths relative to its
# blueprints/<domain>/ directory. Verifying these are
# registered with HA's blueprint loader (via the WS
# query) is the real signal; the per-file symlink
# checks in the test below run first as a fast-fail
# debugging aid (if the symlink isn't on disk, the WS
# query will definitely come back without that
# blueprint, and the message points at the install
# side rather than HA's loader).
EXPECTED_BLUEPRINTS = frozenset(
    {
        "blueprint_toolkit/device_watchdog.yaml",
        "blueprint_toolkit/entity_defaults_watchdog.yaml",
        "blueprint_toolkit/reference_watchdog.yaml",
        "blueprint_toolkit/sensor_threshold_switch_controller.yaml",
        "blueprint_toolkit/trigger_entity_controller.yaml",
        "blueprint_toolkit/zwave_route_manager.yaml",
    },
)

# Matches scripts/dev-deploy.py DEFAULT_INSTALL_PATH.
REPO_ON_HOST = "/root/ha-blueprint-toolkit"


def _registered_blueprints(docker_ha: DockerHA) -> set[str]:
    """Return blueprint paths HA has indexed under blueprints/automation/."""
    reply = docker_ha.ws_query(
        {"type": "blueprint/list", "domain": "automation"},
    )
    if not reply.get("success", False):
        msg = f"blueprint/list returned error: {reply!r}"
        raise AssertionError(msg)
    result = reply.get("result", {})
    if not isinstance(result, dict):
        msg = f"blueprint/list result is not a dict: {result!r}"
        raise AssertionError(msg)
    return set(result.keys())


def _poll(
    predicate: Callable[[], bool],
    *,
    timeout: float,
    interval: float = 1.0,
    message: str,
) -> None:
    deadline = time.monotonic() + timeout
    last_err: BaseException | None = None
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except AssertionError as e:
            last_err = e
        time.sleep(interval)
    raise AssertionError(f"{message}; last_err={last_err!r}")


def _pyscript_services(docker_ha: DockerHA) -> set[str]:
    code, body = docker_ha.api_get("/api/services")
    assert code == 200, f"/api/services returned {code}"
    data = json.loads(body)
    for domain in data:
        if domain["domain"] == "pyscript":
            return set(domain["services"].keys())
    return set()


def _clear_installed_symlinks(docker_ha: DockerHA) -> None:
    """Remove the dev-install-owned symlinks under /config.

    Leaves the repo clone and HA state alone.
    """
    docker_ha.exec_shell(
        "rm -f /config/pyscript/blueprint_toolkit.py && "
        "rm -rf /config/pyscript/modules /config/blueprints "
        "/config/www/blueprint_toolkit && "
        "rm -f /config/.blueprint_toolkit.manifest.json",
    )


class TestDevInstallEndToEnd:
    """Run dev-install.py against a live HA container."""

    def test_install_creates_symlinks_and_services_register(
        self,
        docker_ha: DockerHA,
    ) -> None:
        _clear_installed_symlinks(docker_ha)
        copy_repo_into_container(REPO_ON_HOST)

        r = docker_ha.exec_capture(
            "python3",
            f"{REPO_ON_HOST}/scripts/dev-install.py",
            "--repo-dir",
            REPO_ON_HOST,
            "--ha-config",
            "/config",
        )
        assert r.returncode == 0, (
            f"dev-install.py failed: stdout={r.stdout} stderr={r.stderr}"
        )

        check = docker_ha.exec_shell(
            "test -L /config/pyscript/blueprint_toolkit.py",
            check=False,
        )
        assert check.returncode == 0, (
            "expected /config/pyscript/blueprint_toolkit.py "
            "to be a symlink after dev-install"
        )
        # Note: bundled/www/ is not installed by the
        # reconciler (HA's /local/ static handler refuses
        # to follow our symlinks; the integration
        # registers its own aiohttp static route
        # instead). dev-install users see broken
        # /local/ doc links -- a documented dev-install
        # limitation.
        # Filesystem fast-fail check for blueprints; the
        # authoritative check is the WS query below.
        for bp in EXPECTED_BLUEPRINTS:
            check = docker_ha.exec_shell(
                f"test -L /config/blueprints/automation/{bp}",
                check=False,
            )
            assert check.returncode == 0, (
                f"expected blueprint symlink missing: {bp}"
            )

        code, _ = docker_ha.api_post("/api/services/pyscript/reload")
        assert code == 200, f"pyscript.reload failed: {code}"
        code, _ = docker_ha.api_post("/api/services/automation/reload")
        assert code == 200, f"automation.reload failed: {code}"

        def _all_services_present() -> bool:
            missing = EXPECTED_SERVICES - _pyscript_services(docker_ha)
            assert not missing, f"missing services: {sorted(missing)}"
            return True

        def _all_blueprints_registered() -> bool:
            missing = EXPECTED_BLUEPRINTS - _registered_blueprints(docker_ha)
            assert not missing, f"missing blueprints in HA: {sorted(missing)}"
            return True

        _poll(
            _all_services_present,
            timeout=30,
            message=(
                "pyscript blueprint entrypoint services not registered "
                "after dev-install + pyscript.reload"
            ),
        )
        _poll(
            _all_blueprints_registered,
            timeout=30,
            message=(
                "blueprint_toolkit blueprints not visible to "
                "HA's blueprint loader after automation.reload"
            ),
        )


class TestDevDeployEndToEnd:
    """Exercise scripts/dev-deploy.py inside the container.

    Models the common iterative-dev cycle: source and target
    both start at HEAD, an edit is made locally, dev-deploy
    diffs, ships the changed file, runs dev-install.py on the
    host, and triggers reload.
    """

    def test_dev_deploy_ships_edit_and_reloads(
        self,
        docker_ha: DockerHA,
    ) -> None:
        _clear_installed_symlinks(docker_ha)

        # Target: /root/ha-blueprint-toolkit (matches
        # dev-deploy's default install path).
        copy_repo_into_container(REPO_ON_HOST)
        # Source: /root/source (separate clone we can edit
        # without touching the target).
        copy_repo_into_container("/root/source")

        # git 2.35+ refuses cross-uid repos without this.
        docker_ha.exec_shell(
            "git config --global --add safe.directory '*'",
        )

        # Initial install on the target so /config symlinks
        # exist before we deploy edits.
        r = docker_ha.exec_capture(
            "python3",
            f"{REPO_ON_HOST}/scripts/dev-install.py",
            "--repo-dir",
            REPO_ON_HOST,
            "--ha-config",
            "/config",
        )
        assert r.returncode == 0, (
            f"initial dev-install on target failed: {r.stdout}{r.stderr}"
        )

        # Edit a file in source to give dev-deploy
        # something to ship.
        marker = "# dev-deploy-test-marker\n"
        edited_path = (
            "custom_components/blueprint_toolkit/bundled/"
            "pyscript/modules/helpers.py"
        )
        docker_ha.exec_shell(
            f"printf '%s' '{marker}' >> /root/source/{edited_path}",
        )

        # ssh setup for dev-deploy. The ssh client inside
        # the container reaches sshd on the same container
        # at port 22; the 127.0.0.1:2222 host-side map is
        # only for host-initiated ssh and is not used here.
        docker_ha.exec_shell(
            "mkdir -p /root/.ssh && "
            "cp /test/id_ed25519 /root/.ssh/id_ed25519 && "
            "chmod 600 /root/.ssh/id_ed25519 && "
            "ssh-keyscan -p 22 -H localhost "
            "> /root/.ssh/known_hosts 2>/dev/null",
        )

        docker_ha.exec_shell(
            f"printf '%s' '{docker_ha.token}' > /root/.ha_api_key "
            "&& chmod 600 /root/.ha_api_key",
        )

        # Run dev-deploy with cwd=/root/source so git
        # rev-parse --show-toplevel returns that path.
        r = docker_ha.exec_capture(
            "python3",
            "/root/source/scripts/dev-deploy.py",
            "--host",
            "root@localhost",
            "--install-path",
            REPO_ON_HOST,
            "--ha-config",
            "/config",
            "--api-key-file",
            "/root/.ha_api_key",
            "--allow-dirty",
            cwd="/root/source",
        )
        assert r.returncode == 0, (
            f"dev-deploy.py failed: stdout={r.stdout} stderr={r.stderr}"
        )
        assert f"updated: {edited_path}" in r.stdout, (
            f"expected edited file in plan; got: {r.stdout}"
        )
        assert "run: dev-install.py" in r.stdout, (
            f"expected dev-install.py invocation in plan; got: {r.stdout}"
        )
        assert "reload: pyscript.reload" in r.stdout, (
            f"expected pyscript.reload in plan; got: {r.stdout}"
        )

        # Verify the target's copy now contains the marker
        # we appended to source.
        r = docker_ha.exec_capture(
            "grep",
            "-c",
            marker.rstrip(),
            f"{REPO_ON_HOST}/{edited_path}",
        )
        assert r.returncode == 0 and r.stdout.strip() == "1", (
            "marker did not propagate to target: "
            f"stdout={r.stdout!r} stderr={r.stderr!r}"
        )

        # The /config symlink should resolve to the same
        # file (through the repo-root symlink) and thus
        # pick up the same content.
        via_symlink = docker_ha.exec_capture(
            "grep",
            "-c",
            marker.rstrip(),
            "/config/pyscript/modules/helpers.py",
        )
        assert (
            via_symlink.returncode == 0 and via_symlink.stdout.strip() == "1"
        ), (
            "edit did not reach HA via /config symlink: "
            f"stdout={via_symlink.stdout!r} stderr={via_symlink.stderr!r}"
        )


if __name__ == "__main__":
    raise SystemExit(
        pytest.main(
            [
                __file__,
                "-v",
                "-m",
                "docker",
            ],
        ),
    )
