"""Pytest configuration - runs before test collection."""
# This is AI generated code

import atexit
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

# Repository root
_REPO_ROOT = Path(__file__).parent.parent

# Make ``custom_components.blueprint_toolkit.*`` importable
sys.path.insert(0, str(_REPO_ROOT))


# Create a temporary directory for __pycache__ and redirect
# all bytecode there
_pycache_tmpdir = tempfile.mkdtemp(prefix="pytest_pycache_")
sys.pycache_prefix = _pycache_tmpdir

# Also prevent bytecode writing for subsequent imports
# (belt and suspenders)
sys.dont_write_bytecode = True


def _cleanup_all_caches() -> None:
    """Remove temp dirs and any cache dirs in the repo."""
    # Clean up the temp pycache directory
    shutil.rmtree(_pycache_tmpdir, ignore_errors=True)
    # Clean up any __pycache__ created before
    # sys.pycache_prefix was set (e.g., conftest.py's
    # own bytecode)
    for pycache in _REPO_ROOT.rglob("__pycache__"):
        if pycache.is_dir():
            shutil.rmtree(pycache, ignore_errors=True)
    # Clean up any .mypy_cache directories
    for mypy_cache in _REPO_ROOT.rglob(".mypy_cache"):
        if mypy_cache.is_dir():
            shutil.rmtree(mypy_cache, ignore_errors=True)


# Register cleanup for when Python exits
atexit.register(_cleanup_all_caches)


def pytest_sessionfinish(
    session,  # type: ignore[no-untyped-def]
    exitstatus,  # type: ignore[no-untyped-def]
) -> None:
    """Clean up pycache directories after test session."""
    _cleanup_all_caches()


def run_tests(
    test_file: str,
    script_path: Path,
    repo_root: Path,
) -> None:
    """Entry point for running a test file directly.

    Handles ``--verbose`` and ``--coverage`` flags, then
    invokes ``pytest.main()``.  Called from each test
    file's ``__main__`` block.

    Args:
        test_file: The test file's ``__file__`` path.
        script_path: Path to the script under test
            (used to derive the coverage module name).
        repo_root: Repository root directory.
    """
    import argparse

    import pytest  # type: ignore[import-not-found]

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose test output",
    )
    parser.add_argument(
        "--coverage",
        action="store_true",
        help="Run with coverage report",
    )
    args = parser.parse_args()

    if args.coverage:
        # Run as subprocess so coverage tracks from the
        # start, including import-time lines (class defs,
        # function signatures, etc.) that are missed when
        # using pytest.main() in-process.
        import subprocess

        module = script_path.stem.replace("-", "_")
        cov_dir = Path(tempfile.gettempdir())
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            test_file,
            "-p",
            "no:cacheprovider",
            f"--cov={module}",
            "--cov-report=term-missing",
            f"--cov-report=html:{cov_dir / (module + '_htmlcov')}",
        ]
        if args.verbose:
            cmd.append("-v")
        env = os.environ.copy()
        env["PYTHONPATH"] = str(script_path.parent)
        env["COVERAGE_FILE"] = str(cov_dir / f"{module}.coverage")
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run(cmd)
        # Clean up .coverage file left by subprocess
        cov_file = Path.cwd() / ".coverage"
        if cov_file.exists():
            cov_file.unlink()
        raise SystemExit(result.returncode)

    pytest_args = [test_file, "-p", "no:cacheprovider"]
    if args.verbose:
        pytest_args.append("-v")
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    raise SystemExit(pytest.main(pytest_args))


class CodeQualityBase:
    """Base for per-file code quality checks.

    Subclass as ``TestCodeQuality`` in each test file
    and set ``ruff_targets`` and ``mypy_targets`` to
    the files that test file covers.
    """

    ruff_targets: list[str] = []
    mypy_targets: list[str] = []

    def test_ruff_lint(self) -> None:
        """Ruff linter passes on all target files."""
        for target in self.ruff_targets:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "ruff",
                    "check",
                    target,
                ],
                cwd=_REPO_ROOT,
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, (
                f"ruff lint failed on {target}."
                ' Run "uvx ruff check --fix ."'
                " to auto-fix.\n\n"
                f"{result.stdout}{result.stderr}"
            )

    def test_ruff_format(self) -> None:
        """Ruff formatting passes on all target files."""
        for target in self.ruff_targets:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "ruff",
                    "format",
                    "--check",
                    target,
                ],
                cwd=_REPO_ROOT,
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, (
                f"ruff format failed on {target}."
                ' Run "uvx ruff format ."'
                " to auto-fix.\n\n"
                f"{result.stdout}{result.stderr}"
            )

    def test_mypy_strict(self) -> None:
        """Mypy strict passes on all target files."""
        import pytest  # type: ignore[import-not-found]

        if not self.mypy_targets:
            pytest.skip("no mypy targets")
        for target in self.mypy_targets:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "mypy",
                    target,
                    "--strict",
                ],
                cwd=_REPO_ROOT,
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, (
                f"mypy failed on {target}.\n\n{result.stdout}{result.stderr}"
            )


# --------------------------------------------------------
# BlueprintSchemaDriftBase: shared per-handler test base
# --------------------------------------------------------


_BUNDLED_BLUEPRINTS_DIR = (
    _REPO_ROOT
    / "custom_components"
    / "blueprint_toolkit"
    / "bundled"
    / "blueprints"
    / "automation"
    / "blueprint_toolkit"
)


class BlueprintSchemaDriftBase:
    """Base for per-handler blueprint-vs-schema drift checks.

    Subclass per handler test file:

        class TestBlueprintSchemaDrift(BlueprintSchemaDriftBase):
            handler = my_handler_module
            blueprint_filename = "my_handler.yaml"

    Inherited tests verify:

    - The first action's ``data:`` keys match the handler's
      ``_SCHEMA`` ``vol.Required`` keys.
    - The first action's ``action:`` targets the handler's
      registered service (``blueprint_toolkit.<service>``).

    Originally duplicated as a per-handler ``TestBlueprintSchemaDrift``
    class in each test file. Centralised here so the drift
    contract evolves in one place; per-handler files just
    declare the two class vars and inherit the two tests.
    """

    handler: Any = None  # subclass override
    blueprint_filename: str = ""  # subclass override

    def _load_blueprint(self) -> dict[str, Any]:
        import voluptuous as vol  # noqa: F401, PLC0415
        import yaml  # noqa: PLC0415

        class _PermissiveLoader(yaml.SafeLoader):
            """SafeLoader that ignores blueprint-only ``!`` tags.

            Blueprint YAML uses ``!input <name>`` to interpolate
            inputs at load time; SafeLoader otherwise raises on
            the unrecognised tag. This test only inspects keys
            and the ``action:`` string, so tag values just get
            passed through.
            """

        def _passthrough(_loader: Any, _suffix: str, node: Any) -> Any:
            if hasattr(node, "value") and isinstance(node.value, str):
                return node.value
            return None

        _PermissiveLoader.add_multi_constructor("!", _passthrough)

        path = _BUNDLED_BLUEPRINTS_DIR / self.blueprint_filename
        loaded: dict[str, Any] = yaml.load(  # noqa: S506
            path.read_text(),
            Loader=_PermissiveLoader,
        )
        assert isinstance(loaded, dict)
        return loaded

    @staticmethod
    def _required_keys(schema: Any) -> set[str]:
        import voluptuous as vol  # noqa: PLC0415

        return {
            str(k.schema) for k in schema.schema if isinstance(k, vol.Required)
        }

    def _first_action(self, bp: dict[str, Any]) -> dict[str, Any]:
        # Blueprints accept either ``actions:`` (list, current
        # convention) or singular ``action:`` (mapping). Handle
        # both so the base doesn't trip on an older blueprint.
        actions = bp.get("actions") or bp.get("action") or []
        first: dict[str, Any] = (
            actions[0] if isinstance(actions, list) else actions
        )
        assert isinstance(first, dict)
        return first

    def test_yaml_data_keys_match_schema_required_keys(self) -> None:
        bp = self._load_blueprint()
        action = self._first_action(bp)
        yaml_keys = set((action.get("data") or {}).keys())
        schema_keys = self._required_keys(self.handler._SCHEMA)
        assert yaml_keys == schema_keys, (
            f"blueprint {self.blueprint_filename} 'data:' keys do "
            f"not match handler._SCHEMA's vol.Required keys.\n"
            f"  only in YAML:   {sorted(yaml_keys - schema_keys)}\n"
            f"  only in schema: {sorted(schema_keys - yaml_keys)}"
        )

    def test_blueprint_action_targets_registered_service(self) -> None:
        bp = self._load_blueprint()
        action = self._first_action(bp)
        action_name = action.get("action", "")
        expected = f"blueprint_toolkit.{self.handler._SERVICE}"
        assert action_name == expected, (
            f"blueprint {self.blueprint_filename} action: "
            f"{action_name!r} does not match the registered "
            f"service {expected!r}"
        )


class BlueprintDefaultsRoundTripBase(BlueprintSchemaDriftBase):
    """Base for per-handler blueprint-defaults-vs-schema round-trip checks.

    Subclass per handler test file::

        class TestBlueprintDefaultsRoundTrip(BlueprintDefaultsRoundTripBase):
            handler = my_handler_module
            blueprint_filename = "my_handler.yaml"
            template_defaults = {
                "instance_id": "automation.test",
                "trigger_id": "manual",
            }

    Without this guard a blueprint edit that bumps an input
    default out of the schema's range silently lands every
    newly-created automation in ``unavailable`` until the user
    notices and edits the value.

    Reads the first action's ``data:`` block; for each schema-
    required key whose ``data:`` value is a ``!input <name>``
    reference (recorded by the permissive loader as just the
    bare input name), looks up the input's ``default:`` in
    the blueprint's ``input:`` block and feeds that into a
    payload. Templated values (``"{{ ... }}"`` strings, e.g.
    ``instance_id: "{{ this.entity_id }}"``) come from
    ``template_defaults`` -- subclasses must supply a
    test-time stand-in for each templated key. Anything else
    in ``data:`` is passed through verbatim.

    The constructed payload is then run through
    ``handler._SCHEMA``; any ``vol.Invalid`` raised fails
    the test.
    """

    template_defaults: dict[str, Any] = {}  # subclass override

    def test_blueprint_defaults_pass_schema(self) -> None:
        bp = self._load_blueprint()
        action = self._first_action(bp)
        data: dict[str, Any] = dict(action.get("data") or {})
        inputs: dict[str, Any] = (
            (bp.get("blueprint") or {}).get("input") or {}
        )
        schema_keys = self._required_keys(self.handler._SCHEMA)

        payload: dict[str, Any] = {}
        missing_overrides: list[str] = []
        for key in sorted(schema_keys):
            # ``template_defaults`` always wins -- subclasses
            # use it both for templated ``data:`` keys (e.g.
            # ``instance_id: "{{ this.entity_id }}"``) and for
            # blueprint inputs that intentionally lack a
            # ``default:`` (the user-must-supply case, e.g.
            # TEC's ``controlled_entities``).
            if key in self.template_defaults:
                payload[key] = self.template_defaults[key]
                continue
            value = data.get(key)
            if isinstance(value, str) and value in inputs:
                # ``!input <name>`` reference -- look up the
                # input's default.
                input_def = inputs[value]
                if isinstance(input_def, dict) and "default" in input_def:
                    payload[key] = input_def["default"]
                else:
                    missing_overrides.append(
                        f"{key} (input {value!r} has no default; supply via"
                        " ``template_defaults``)"
                    )
            elif isinstance(value, str) and "{{" in value:
                missing_overrides.append(
                    f"{key} (templated; supply via ``template_defaults``)"
                )
            else:
                payload[key] = value

        assert not missing_overrides, (
            "blueprint defaults are missing for these schema-required keys:\n"
            + "\n".join(f"  - {entry}" for entry in missing_overrides)
        )

        # Should not raise -- if the blueprint defaults are
        # outside the schema's accepted range, this is the
        # canary.
        self.handler._SCHEMA(payload)


class RecoveryEventsIntegrationBase:
    """Shared restart-recovery + reload-recovery integration tests.

    Subclass per handler integration test file::

        class TestRecoveryEvents(RecoveryEventsIntegrationBase):
            service_tag = "DW"
            setup_integration = staticmethod(_setup_integration)

    Inherited tests verify that ``register_blueprint_handler``'s
    discovery-and-kick wiring fires both at integration setup
    (the ``hass.is_running`` immediate-call branch -- pytest-HACC
    has HA up by the time the integration loads) and on every
    ``EVENT_AUTOMATION_RELOADED``. The deferred
    ``EVENT_HOMEASSISTANT_STARTED`` branch is covered at the unit
    level by ``test_helpers_lifecycle.py``, which can simulate
    ``CoreState.starting`` cleanly.
    """

    service_tag: str = ""  # subclass override (e.g. "DW")
    # Subclass override: ``staticmethod(_setup_integration)``
    # bound to the per-handler integration test file's
    # module-level ``_setup_integration`` async function.
    setup_integration: Any = None

    async def test_setup_emits_recovery_log(
        self,
        hass: Any,
        caplog: Any,
    ) -> None:
        import logging  # noqa: PLC0415

        with caplog.at_level(
            logging.INFO,
            logger="custom_components.blueprint_toolkit.helpers",
        ):
            await self.setup_integration(hass)

        tag = f"[{self.service_tag}]"
        assert any(
            tag in r.getMessage()
            and "discovered at startup" in r.getMessage()
            for r in caplog.records
        ), (
            f"expected {tag} startup-recovery log line; "
            f"saw: {[r.getMessage() for r in caplog.records]}"
        )

    async def test_automation_reloaded_event_kicks_discovery(
        self,
        hass: Any,
        caplog: Any,
    ) -> None:
        import logging  # noqa: PLC0415

        from homeassistant.components.automation import (  # noqa: PLC0415
            EVENT_AUTOMATION_RELOADED,
        )

        await self.setup_integration(hass)
        caplog.clear()
        with caplog.at_level(
            logging.INFO,
            logger="custom_components.blueprint_toolkit.helpers",
        ):
            hass.bus.async_fire(EVENT_AUTOMATION_RELOADED, {})
            await hass.async_block_till_done()

        tag = f"[{self.service_tag}]"
        assert any(
            tag in r.getMessage() and "discovered" in r.getMessage()
            for r in caplog.records
        ), (
            f"expected {tag} recovery log line after"
            " EVENT_AUTOMATION_RELOADED; "
            f"saw: {[r.getMessage() for r in caplog.records]}"
        )
