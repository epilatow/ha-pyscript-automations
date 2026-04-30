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

# Workspace path used by the new dev-deploy. Same as the
# script's DEFAULT_WORKSPACE; hard-coded here so the
# assertion is independent of dev-deploy's import surface.
WORKSPACE = "/config/ha-blueprint-toolkit"
INSTALL_PATH = "/config/custom_components/blueprint_toolkit"
# dev-install.py now lives inside the integration; each
# deployed copy ships its own dev-install.py at this path
# under <integration>.
DEV_INSTALL_REL = "scripts/dev-install.py"

# Test stub for `ha core restart`. The HA container shipped
# in this harness does not include the supervisor CLI, and
# even if it did we can't restart HA from inside its own
# container. dev-deploy ships the stub command verbatim
# over ssh so the value just has to be a noop on the host.
NOOP_RESTART = "echo restart-stub"

# Pyscript blueprint entrypoint services that should still
# be registered. Verifying these all appear is our "pyscript
# wrapper loaded" signal. The native integration handlers
# (Trigger Entity Controller at
# ``blueprint_toolkit.trigger_entity_controller``, Z-Wave
# Route Manager at ``blueprint_toolkit.zwave_route_manager``,
# Reference Watchdog at
# ``blueprint_toolkit.reference_watchdog``, Entity Defaults
# Watchdog at ``blueprint_toolkit.entity_defaults_watchdog``)
# are NOT pyscript entrypoints and so don't appear here.
EXPECTED_SERVICES = frozenset(
    {
        "device_watchdog_blueprint_entrypoint",
        "sensor_threshold_switch_controller_blueprint_entrypoint",
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

# Where dev-install.py reads the integration from in the
# TestDevInstallEndToEnd standalone scenario. Mirrors the
# layout dev-deploy.py creates so dev-install.py's
# auto-discovery (Path(__file__).resolve().parent.parent)
# resolves to the integration in this dir.
TEST_INTEGRATION_DIR = f"{WORKSPACE}/test_install/blueprint_toolkit"


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


def _full_workspace_reset(docker_ha: DockerHA) -> None:
    """Wipe everything dev-deploy / dev-install can touch."""
    _clear_installed_symlinks(docker_ha)
    docker_ha.exec_shell(
        f"rm -rf {WORKSPACE} {INSTALL_PATH}",
    )


def _seed_repo_into_container(staging: str) -> None:
    """Land the working tree inside the container at ``staging``."""
    copy_repo_into_container(staging)


def _seed_test_integration(docker_ha: DockerHA, source_clone: str) -> None:
    """Stage a fresh integration copy at TEST_INTEGRATION_DIR.

    The integration tree includes ``scripts/dev-install.py``
    so the staged copy is self-contained.
    """
    parent = TEST_INTEGRATION_DIR.rsplit("/", 1)[0]
    docker_ha.exec_shell(
        f"mkdir -p {parent} && "
        f"rm -rf {TEST_INTEGRATION_DIR} && "
        f"cp -a {source_clone}/custom_components/blueprint_toolkit "
        f"{TEST_INTEGRATION_DIR}",
    )


class TestDevInstallEndToEnd:
    """Run dev-install.py against a live HA container.

    Deploys the integration to a dev-deploy-style workspace
    layout (``<workspace>/<build>/blueprint_toolkit/``) and
    invokes the dev-install.py inside that staged copy --
    the same shape dev-deploy.py uses in production.
    """

    def test_install_creates_symlinks_and_services_register(
        self,
        docker_ha: DockerHA,
    ) -> None:
        _full_workspace_reset(docker_ha)
        source_clone = "/root/source"
        _seed_repo_into_container(source_clone)
        _seed_test_integration(docker_ha, source_clone)

        r = docker_ha.exec_capture(
            "python3",
            f"{TEST_INTEGRATION_DIR}/{DEV_INSTALL_REL}",
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

    Verifies the full deploy machinery: first run preserves
    a pre-existing HACS-style install at
    ``/config/custom_components/blueprint_toolkit`` as a
    versioned snapshot under the workspace, every run
    creates a new timestamped build, the install symlink
    points at the latest build, dev-install runs against
    the deployed integration, and ``--restore`` reverses
    the process.

    The HA test container has no supervisor CLI, so the
    deploy script's restart step is stubbed via
    ``--ha-restart-cmd``. HA itself is not restarted; this
    test exercises the deploy workflow, not the
    integration's runtime behavior.
    """

    SOURCE_CLONE = "/root/source"

    def _setup_ssh(self, docker_ha: DockerHA) -> None:
        """Provision a localhost ssh login for dev-deploy.py."""
        docker_ha.exec_shell(
            "mkdir -p /root/.ssh && "
            "cp /test/id_ed25519 /root/.ssh/id_ed25519 && "
            "chmod 600 /root/.ssh/id_ed25519 && "
            "ssh-keyscan -p 22 -H localhost "
            "> /root/.ssh/known_hosts 2>/dev/null",
        )

    def _seed_fake_hacs_install(self, docker_ha: DockerHA) -> None:
        """Place a directory at INSTALL_PATH so deploy must preserve it.

        Uses the integration tree from the source clone so
        the snapshot is a faithful copy of "what HACS would
        have installed" minus the .storage entry. Without
        the entry, dev-deploy falls back to a "hacs" dir
        name; the test exercises that fallback path.
        """
        docker_ha.exec_shell(
            f"mkdir -p $(dirname {INSTALL_PATH}) && "
            f"rm -rf {INSTALL_PATH} && "
            f"cp -a {self.SOURCE_CLONE}/custom_components/"
            f"blueprint_toolkit {INSTALL_PATH}",
        )

    def _list_workspace(self, docker_ha: DockerHA) -> set[str]:
        r = docker_ha.exec_shell(
            f"ls -1 {WORKSPACE} 2>/dev/null || true",
            check=False,
        )
        return {line for line in r.stdout.splitlines() if line.strip()}

    def _readlink(self, docker_ha: DockerHA, path: str) -> str:
        r = docker_ha.exec_shell(f"readlink {path}", check=False)
        return r.stdout.strip()

    def _run_deploy(
        self,
        docker_ha: DockerHA,
        *extra_args: str,
    ) -> str:
        """Run dev-deploy from the source clone; return stdout."""
        r = docker_ha.exec_capture(
            "python3",
            f"{self.SOURCE_CLONE}/scripts/dev-deploy.py",
            "--host",
            "root@localhost",
            "--workspace",
            WORKSPACE,
            "--ha-config",
            "/config",
            "--ha-restart-cmd",
            NOOP_RESTART,
            "--allow-dirty",
            *extra_args,
            cwd=self.SOURCE_CLONE,
        )
        assert r.returncode == 0, (
            f"dev-deploy {extra_args} failed: "
            f"stdout={r.stdout!r} stderr={r.stderr!r}"
        )
        return r.stdout

    def test_deploy_preserves_hacs_then_supports_restore(
        self,
        docker_ha: DockerHA,
    ) -> None:
        _full_workspace_reset(docker_ha)
        _seed_repo_into_container(self.SOURCE_CLONE)
        # git 2.35+ refuses cross-uid repos without this.
        docker_ha.exec_shell(
            "git config --global --add safe.directory '*'",
        )
        self._setup_ssh(docker_ha)
        self._seed_fake_hacs_install(docker_ha)

        # ---- First deploy -----------------------------------
        out1 = self._run_deploy(docker_ha)
        assert "preserve HACS install" in out1, (
            f"expected preserve-on-first-deploy in plan; got: {out1}"
        )
        assert "ha core restart" in out1, out1

        ws_after_first = self._list_workspace(docker_ha)
        # Expect: hacs/ (snapshot) + one timestamped build.
        # dev-install.py is NOT at workspace root anymore;
        # it ships inside each integration tree.
        version_dirs = {n for n in ws_after_first if not n[0].isdigit()}
        ts_dirs_first = {n for n in ws_after_first if n[0].isdigit()}
        assert version_dirs == {"hacs"}, (
            f"expected single 'hacs' snapshot; got: {ws_after_first}"
        )
        assert len(ts_dirs_first) == 1, (
            f"expected one timestamped build; got: {ws_after_first}"
        )
        first_ts = next(iter(ts_dirs_first))
        check = docker_ha.exec_shell(
            f"test -x {WORKSPACE}/{first_ts}/blueprint_toolkit/"
            f"{DEV_INSTALL_REL}",
            check=False,
        )
        assert check.returncode == 0, (
            "expected dev-install.py inside the build's integration tree"
        )

        # The install path is a symlink pointing at the new build.
        symlink_target = self._readlink(docker_ha, INSTALL_PATH)
        assert symlink_target == (
            f"{WORKSPACE}/{first_ts}/blueprint_toolkit"
        ), f"symlink target wrong: {symlink_target}"

        # The dev-install symlinks should now exist under
        # /config/blueprints/ etc.
        check = docker_ha.exec_shell(
            "test -L /config/blueprints/automation/"
            "blueprint_toolkit/trigger_entity_controller.yaml",
            check=False,
        )
        assert check.returncode == 0, (
            "dev-install did not create blueprint symlinks after first deploy"
        )

        # The HACS snapshot was actually moved (it's a
        # directory under <ws>/hacs/blueprint_toolkit/).
        check = docker_ha.exec_shell(
            f"test -f {WORKSPACE}/hacs/blueprint_toolkit/manifest.json",
            check=False,
        )
        assert check.returncode == 0, "HACS snapshot manifest missing"

        # ---- Second deploy (no edit) ------------------------
        # Sleep so the second timestamp is distinct from the
        # first (deploys at second-resolution).
        time.sleep(1)
        out2 = self._run_deploy(docker_ha)
        assert "preserve HACS install" not in out2, (
            f"second deploy must not re-preserve HACS; got: {out2}"
        )

        ws_after_second = self._list_workspace(docker_ha)
        ts_dirs_second = {n for n in ws_after_second if n[0].isdigit()}
        assert len(ts_dirs_second) == 2, (
            f"expected two timestamped builds; got: {ws_after_second}"
        )
        new_ts = (ts_dirs_second - ts_dirs_first).pop()
        symlink_target = self._readlink(docker_ha, INSTALL_PATH)
        assert symlink_target == (f"{WORKSPACE}/{new_ts}/blueprint_toolkit"), (
            f"symlink not updated to new build: {symlink_target}"
        )

        # ---- Restore ----------------------------------------
        out_restore = self._run_deploy(docker_ha, "--restore")
        assert "snapshot: hacs" in out_restore, out_restore
        assert "ha core restart" in out_restore, out_restore

        # The install path should once again be a real
        # directory holding the original integration.
        check = docker_ha.exec_shell(
            f"test -d {INSTALL_PATH} && test ! -L {INSTALL_PATH} && "
            f"test -f {INSTALL_PATH}/manifest.json",
            check=False,
        )
        assert check.returncode == 0, (
            f"restore did not put a real directory back at {INSTALL_PATH}"
        )

        # And the workspace should be gone.
        check = docker_ha.exec_shell(
            f"test ! -e {WORKSPACE}",
            check=False,
        )
        assert check.returncode == 0, (
            f"restore did not remove the workspace at {WORKSPACE}"
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
