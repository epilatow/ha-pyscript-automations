# This is AI generated code
"""Session-level pytest fixtures for the docker test harness.

Brings up a single HA container shared across the session
and provisions it for tests: pyscript pre-installed, ssh
keypair authorized, onboarding completed, an auth token
stashed. Tests re-reset /config state as needed rather than
bouncing the container between each test.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HARNESS_DIR = Path(__file__).resolve().parent
ONBOARD_SCRIPT = HARNESS_DIR / "_harness" / "ha_onboard.py"

HA_PORT = 8123
SSH_PORT = 2222
HA_BASE_URL = f"http://127.0.0.1:{HA_PORT}"
CONTAINER_NAME = "ha-blueprint-toolkit-test"

# Cache for the pyscript custom component. Lives under
# tests/docker/.cache/pyscript/ (gitignored) so it stays
# adjacent to the harness that uses it. Populated on
# first fixture use via git clone --depth 1; reused
# across runs.
PYSCRIPT_CACHE_DIR = HARNESS_DIR / ".cache" / "pyscript"
PYSCRIPT_REPO_URL = "https://github.com/custom-components/pyscript"


def _have_docker() -> bool:
    try:
        r = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    """Skip docker-marked tests when docker isn't reachable."""
    if _have_docker():
        return
    skip = pytest.mark.skip(
        reason="docker daemon not reachable; skipping docker-harness tests",
    )
    for item in items:
        if "docker" in item.keywords:
            item.add_marker(skip)


@dataclass
class DockerHA:
    """Handle returned by the session fixture.

    Tests exec commands inside the running container via
    ``exec_`` and ``exec_capture``. HA API calls go through
    ``api_get`` / ``api_post``. File provisioning into the
    container is done via ``docker cp`` through the helper
    ``copy_repo_into_container`` in this module.

    The access_token ``token`` is short-lived (~30min). The
    api_* methods auto-refresh on 401 using refresh_token.
    """

    config_dir: Path  # bind-mounted at /config
    test_dir: Path  # bind-mounted at /test (read-only)
    token: str  # access token, rotated on 401
    refresh_token: str  # long-lived, for refresh
    client_id: str  # required for /auth/token refresh

    @staticmethod
    def _exec_argv(cwd: str | None) -> list[str]:
        argv = ["docker", "exec"]
        if cwd:
            argv.extend(["-w", cwd])
        argv.append(CONTAINER_NAME)
        return argv

    def exec_(
        self,
        *cmd: str,
        check: bool = True,
        cwd: str | None = None,
    ) -> None:
        r = subprocess.run([*self._exec_argv(cwd), *cmd])
        if check and r.returncode != 0:
            raise RuntimeError(f"docker exec {cmd!r} exited {r.returncode}")

    def exec_capture(
        self,
        *cmd: str,
        check: bool = True,
        cwd: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        r = subprocess.run(
            [*self._exec_argv(cwd), *cmd],
            capture_output=True,
            text=True,
        )
        if check and r.returncode != 0:
            raise RuntimeError(
                f"docker exec {cmd!r} exited {r.returncode}: "
                f"stdout={r.stdout!r} stderr={r.stderr!r}",
            )
        return r

    def exec_shell(
        self,
        script: str,
        *,
        check: bool = True,
        cwd: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return self.exec_capture(
            "sh",
            "-c",
            script,
            check=check,
            cwd=cwd,
        )

    def _refresh_access_token(self) -> None:
        import urllib.parse

        form = urllib.parse.urlencode(
            {
                "client_id": self.client_id,
                "grant_type": "refresh_token",
                "refresh_token": self.refresh_token,
            }
        )
        req = urllib.request.Request(
            f"{HA_BASE_URL}/auth/token",
            data=form.encode(),
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        self.token = data["access_token"]

    def _do(
        self,
        req_factory: Callable[[str], urllib.request.Request],
        *,
        timeout: float,
    ) -> tuple[int, bytes]:
        # One retry on 401: refresh the access token and
        # rebuild the request. Other HTTP errors propagate.
        for attempt in (0, 1):
            req = req_factory(self.token)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return resp.getcode(), resp.read()
            except urllib.error.HTTPError as e:
                if e.code == 401 and attempt == 0:
                    self._refresh_access_token()
                    continue
                return e.code, e.read()
        # Loop always returns; mypy requires a terminal.
        raise AssertionError("unreachable")

    def api_get(self, path: str) -> tuple[int, bytes]:
        def factory(token: str) -> urllib.request.Request:
            return urllib.request.Request(
                f"{HA_BASE_URL}{path}",
                headers={"Authorization": f"Bearer {token}"},
            )

        return self._do(factory, timeout=30)

    def api_post(
        self,
        path: str,
        payload: dict[str, object] | None = None,
    ) -> tuple[int, bytes]:
        body = b"" if payload is None else json.dumps(payload).encode()

        def factory(token: str) -> urllib.request.Request:
            return urllib.request.Request(
                f"{HA_BASE_URL}{path}",
                data=body,
                method="POST",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )

        return self._do(factory, timeout=60)

    def ws_query(self, command: dict[str, object]) -> dict[str, object]:
        """Send a one-shot WS command via the in-container helper.

        HA exposes some endpoints (notably ``blueprint/list``)
        only over its WebSocket API. Rather than pull a WS
        client into the test runner, we exec ``ws_query.py``
        inside the container; that script uses aiohttp which
        the HA image already ships.
        """
        r = self.exec_capture(
            "python3",
            "/test/_harness/ws_query.py",
            self.token,
            json.dumps(command),
        )
        try:
            reply: dict[str, object] = json.loads(r.stdout)
        except json.JSONDecodeError as e:
            msg = (
                f"ws_query returned non-JSON: stdout={r.stdout!r} "
                f"stderr={r.stderr!r}: {e}"
            )
            raise RuntimeError(msg) from e
        return reply


def _ensure_pyscript_cache() -> Path:
    PYSCRIPT_CACHE_DIR.parent.mkdir(parents=True, exist_ok=True)
    if not (PYSCRIPT_CACHE_DIR / ".git").exists():
        subprocess.run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                PYSCRIPT_REPO_URL,
                str(PYSCRIPT_CACHE_DIR),
            ],
            check=True,
        )
    return PYSCRIPT_CACHE_DIR / "custom_components" / "pyscript"


def _generate_test_key(test_dir: Path) -> Path:
    # Ed25519 keypair used by dev-deploy.py's ssh inside
    # the container. The public half is exposed via the
    # /test bind mount; cont-init.d/00-sshd-setup.sh
    # appends it to /root/.ssh/authorized_keys.
    key = test_dir / "id_ed25519"
    subprocess.run(
        [
            "ssh-keygen",
            "-t",
            "ed25519",
            "-N",
            "",
            "-C",
            "hapa-test",
            "-f",
            str(key),
        ],
        check=True,
        capture_output=True,
    )
    authorized = test_dir / "authorized_keys"
    authorized.write_text((test_dir / "id_ed25519.pub").read_text())
    return key


def _wait_port(host: str, port: int, timeout: float) -> None:
    import socket

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                return
        except OSError:
            time.sleep(0.5)
    raise RuntimeError(f"timeout waiting for {host}:{port}")


def _compose(*args: str, cwd: Path, env: dict[str, str]) -> None:
    full_env = {**os.environ, **env}
    subprocess.run(
        ["docker", "compose", *args],
        cwd=cwd,
        env=full_env,
        check=True,
    )


def _compose_down(cwd: Path, env: dict[str, str]) -> None:
    full_env = {**os.environ, **env}
    subprocess.run(
        ["docker", "compose", "down", "-v", "--remove-orphans"],
        cwd=cwd,
        env=full_env,
        check=False,
    )


@pytest.fixture(scope="session")
def docker_ha(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[DockerHA]:
    session_dir = tmp_path_factory.mktemp("docker-ha")
    config_dir = session_dir / "config"
    test_dir = session_dir / "test"
    config_dir.mkdir()
    test_dir.mkdir()

    # Minimal HA configuration.yaml so HA starts cleanly.
    (config_dir / "configuration.yaml").write_text(
        "default_config:\n"
        "pyscript:\n"
        "  allow_all_imports: true\n"
        "  hass_is_global: true\n",
    )

    # Seed pyscript under custom_components/ before HA
    # first-starts so it loads on initial boot.
    pyscript_src = _ensure_pyscript_cache()
    dst = config_dir / "custom_components" / "pyscript"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(pyscript_src, dst)

    _generate_test_key(test_dir)

    # Stage the in-container helpers so DockerHA.ws_query
    # can exec them from /test/_harness/. Bind-mounting
    # tests/docker/_harness/ directly would also work but
    # would expose the whole harness tree to the
    # container; copying just what we need is tighter.
    harness_dst = test_dir / "_harness"
    harness_dst.mkdir(exist_ok=True)
    shutil.copy2(
        HARNESS_DIR / "_harness" / "ws_query.py",
        harness_dst / "ws_query.py",
    )

    env = {
        "HAPA_CONFIG_DIR": str(config_dir),
        "HAPA_TEST_DIR": str(test_dir),
    }

    _compose_down(HARNESS_DIR, env)  # belt and suspenders
    _compose("up", "-d", "--build", cwd=HARNESS_DIR, env=env)
    try:
        _wait_port("127.0.0.1", HA_PORT, timeout=120)
        _wait_port("127.0.0.1", SSH_PORT, timeout=30)

        # Onboarding. ha_onboard.py waits for HA to
        # respond before attempting the POSTs and prints
        # client_id\taccess_token\trefresh_token on stdout.
        r = subprocess.run(
            ["python3", str(ONBOARD_SCRIPT), "--base-url", HA_BASE_URL],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(
                f"onboarding failed: stdout={r.stdout!r} stderr={r.stderr!r}",
            )
        parts = r.stdout.strip().split("\t")
        if len(parts) != 3 or not all(parts):
            raise RuntimeError(
                f"onboarding returned malformed output: stdout={r.stdout!r} "
                f"stderr={r.stderr!r}"
            )
        client_id, access_token, refresh_token = parts

        yield DockerHA(
            config_dir=config_dir,
            test_dir=test_dir,
            token=access_token,
            refresh_token=refresh_token,
            client_id=client_id,
        )
    finally:
        _compose_down(HARNESS_DIR, env)


_WORKTREE_EXCLUDES = (
    ".venv",
    "tmp",
    "node_modules",
    ".pytest_cache",
    "tests/docker/.cache",
)


def copy_repo_into_container(container_path: str) -> None:
    """Ship the host working tree into the container.

    Tars the current working tree (including .git and any
    uncommitted edits) and extracts inside the container
    at ``container_path``. Uses docker cp via a tar pipe
    rather than a bind mount because Docker Desktop on
    macOS blocks chmod across the user-namespace boundary
    (install.sh's ``chmod -R a+rX`` fails on bind mounts).

    Shipping the working tree rather than cloning from
    .git means tests exercise the developer's live code,
    including uncommitted edits. That matters because the
    point of running the harness is validating changes
    before they're committed.
    """
    subprocess.run(
        [
            "docker",
            "exec",
            CONTAINER_NAME,
            "sh",
            "-c",
            f"rm -rf {container_path} && mkdir -p {container_path}",
        ],
        check=True,
    )
    tar_cmd = ["tar", "-cf", "-"]
    for excl in _WORKTREE_EXCLUDES:
        tar_cmd.extend(["--exclude", f"./{excl}"])
    tar_cmd.extend(["-C", str(REPO_ROOT), "."])
    extract_cmd = [
        "docker",
        "exec",
        "-i",
        CONTAINER_NAME,
        "sh",
        "-c",
        f"cd {container_path} && tar -xf -",
    ]
    with subprocess.Popen(tar_cmd, stdout=subprocess.PIPE) as tar_proc:
        result = subprocess.run(
            extract_cmd,
            stdin=tar_proc.stdout,
            check=False,
        )
        if tar_proc.stdout is not None:
            tar_proc.stdout.close()
        if tar_proc.wait() != 0:
            raise RuntimeError("local tar exited non-zero")
    if result.returncode != 0:
        raise RuntimeError(
            f"container-side tar extract exited {result.returncode}",
        )


@pytest.fixture
def repo_in_config(docker_ha: DockerHA) -> str:
    """Ensure /config/ha-blueprint-toolkit is a fresh repo clone.

    Returns the container-side path. Each test calling
    this gets a clean clone; the fixture re-copies on
    every invocation.
    """
    path = "/config/ha-blueprint-toolkit"
    copy_repo_into_container(path)
    # git 2.35+ refuses cross-uid repos without this.
    docker_ha.exec_shell(
        "git config --global --add safe.directory '*'",
    )
    return path
