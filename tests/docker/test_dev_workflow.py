#!/usr/bin/env python3
# This is AI generated code
"""Full-stack docker tests for the manual developer workflow.

Exercises the existing git-clone + install.sh path and the
laptop-to-host scripts/dev-deploy.py path against a real HA
container with pyscript pre-installed. See tests/docker/
README.md for manual invocation during interactive
development.

Gated by @pytest.mark.docker. The default pytest run excludes
this module (``addopts = -m 'not docker'`` in pyproject.toml).
Run manually with ``pytest -m docker tests/docker`` from the
repo root so tests/docker/conftest.py provides its session
fixtures.
"""

from __future__ import annotations

import json
import time

import pytest
from conftest import (
    DockerHA,
    copy_repo_into_container,
)

pytestmark = pytest.mark.docker

# The six blueprint entrypoint services registered by
# pyscript/ha_pyscript_automations.py. Verifying all six
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
        "ha_pyscript_automations/device_watchdog.yaml",
        "ha_pyscript_automations/entity_defaults_watchdog.yaml",
        "ha_pyscript_automations/reference_watchdog.yaml",
        "ha_pyscript_automations/sensor_threshold_switch_controller.yaml",
        "ha_pyscript_automations/trigger_entity_controller.yaml",
        "ha_pyscript_automations/zwave_route_manager.yaml",
    },
)

# Today's dev-deploy default; also where install.sh's
# repo-must-be-inside-config-dir check expects to find
# the cloned repo.
REPO_IN_CONFIG = "/config/ha-pyscript-automations"


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
    predicate,
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
    """Remove the install.sh-owned symlinks under /config.

    Leaves /config/ha-pyscript-automations (the cloned
    repo) and HA state alone -- those are caller-owned.
    """
    docker_ha.exec_shell(
        "rm -f /config/pyscript/ha_pyscript_automations.py && "
        "rm -rf /config/pyscript/modules /config/blueprints",
    )


class TestInstallShEndToEnd:
    """Exercise scripts/install.sh inside a live HA container."""

    def test_install_creates_symlinks_and_services_register(
        self,
        docker_ha: DockerHA,
        repo_in_config: str,
    ) -> None:
        # repo_in_config docker-cp's a fresh clone to
        # /config/ha-pyscript-automations. Clear any
        # symlinks a prior test left so we are actually
        # testing install.sh's CREATE path.
        _clear_installed_symlinks(docker_ha)

        r = docker_ha.exec_capture(
            "bash",
            f"{repo_in_config}/scripts/install.sh",
            "/config",
        )
        assert r.returncode == 0, (
            f"install.sh failed: stdout={r.stdout} stderr={r.stderr}"
        )

        check = docker_ha.exec_shell(
            "test -L /config/pyscript/ha_pyscript_automations.py",
            check=False,
        )
        assert check.returncode == 0, (
            "expected /config/pyscript/ha_pyscript_automations.py "
            "to be a symlink after install.sh"
        )
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

        # Reload pyscript + automation so HA discovers the
        # installed modules and blueprint YAML.
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
                "after install.sh + pyscript.reload"
            ),
        )
        _poll(
            _all_blueprints_registered,
            timeout=30,
            message=(
                "ha_pyscript_automations blueprints not visible to "
                "HA's blueprint loader after automation.reload"
            ),
        )


class TestDevDeployEndToEnd:
    """Exercise scripts/dev-deploy.py inside the container.

    Sets up a source clone (at HEAD, under /root/source)
    and a target (at HEAD~N, under /config/ha-pyscript-
    automations -- today's dev-deploy default). Runs
    install.sh on the target so /config symlinks resolve,
    then runs dev-deploy via ssh root@localhost to sync
    source -> target and trigger reload.
    """

    def test_dev_deploy_syncs_target_and_reloads(
        self,
        docker_ha: DockerHA,
    ) -> None:
        # Shared state from the previous test may have
        # symlinks installed. Remove them; the target
        # install below re-creates them pointing at the
        # rolled-back target repo.
        _clear_installed_symlinks(docker_ha)

        # Target: HEAD~5 clone at the dev-deploy default
        # install path. Inside /config so install.sh's
        # "repo must be inside HA config dir" check
        # succeeds.
        copy_repo_into_container(
            REPO_IN_CONFIG,
            reset_to="HEAD~5",
        )
        # Source: current HEAD under /root/source.
        copy_repo_into_container("/root/source")

        # git 2.35+ refuses cross-uid repos without this.
        docker_ha.exec_shell(
            "git config --global --add safe.directory '*'",
        )

        # Install the target so /config symlinks exist.
        r = docker_ha.exec_capture(
            "bash",
            f"{REPO_IN_CONFIG}/scripts/install.sh",
            "/config",
        )
        assert r.returncode == 0, (
            f"initial install.sh on target failed: {r.stdout}{r.stderr}"
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

        # Run dev-deploy with cwd=/root/source so
        # git rev-parse --show-toplevel returns that path.
        # The HA container's python is 3.14; dev-deploy's
        # uv shebang is irrelevant when invoked directly.
        r = docker_ha.exec_capture(
            "python3",
            "/root/source/scripts/dev-deploy.py",
            "--host",
            "root@localhost",
            "--install-path",
            REPO_IN_CONFIG,
            "--api-key-file",
            "/root/.ha_api_key",
            "--allow-dirty",
            cwd="/root/source",
        )
        assert r.returncode == 0, (
            f"dev-deploy.py failed: stdout={r.stdout} stderr={r.stderr}"
        )
        assert "reload: pyscript.reload" in r.stdout, (
            f"expected pyscript.reload in plan; got: {r.stdout}"
        )

        # Verify the target file content now matches
        # source. Picking a pyscript module that exists in
        # both HEAD~5 and HEAD.
        src = docker_ha.exec_capture(
            "sha256sum",
            "/root/source/pyscript/modules/zwave_route_manager.py",
        ).stdout.split()[0]
        tgt = docker_ha.exec_capture(
            "sha256sum",
            f"{REPO_IN_CONFIG}/pyscript/modules/zwave_route_manager.py",
        ).stdout.split()[0]
        assert src == tgt, "target file did not match source after dev-deploy"

        # pyscript services should still be present after
        # the reload dev-deploy triggered.
        def _all_services_present() -> bool:
            missing = EXPECTED_SERVICES - _pyscript_services(docker_ha)
            assert not missing, f"missing services: {sorted(missing)}"
            return True

        _poll(
            _all_services_present,
            timeout=30,
            message=("pyscript services not registered after dev-deploy"),
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
