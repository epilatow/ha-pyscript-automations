#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = [
#     "pytest",
#     "pytest-cov",
#     "ruff",
#     "mypy",
#     "pytest-homeassistant-custom-component==0.13.324",
# ]
# ///
# This is AI generated code
"""Integration-side tests for blueprint_toolkit.

Exercises the HA-async wiring around the reconciler and
installer:

- Config flow creates a single entry; second attempt aborts
  with single_instance_allowed.
- Options flow persists ``cli_symlink_dir``.
- ``async_setup_entry`` installs symlinks at the expected
  destinations and writes the manifest store.
- ``async_remove_entry`` removes everything we installed.

Reconciler logic (no HA dependencies; safe to import +
call outside the HA process) is covered separately by
``tests/test_reconciler.py``; installer logic by
integration with the dev CLI in
``tests/test_dev_install.py``. This file's job is the
plumbing in ``custom_components/blueprint_toolkit/__init__.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make custom_components/ importable as a top-level
# package; the uv-script env doesn't add the repo root to
# sys.path the way ``python -m pytest`` would.
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest  # noqa: E402
from conftest import CodeQualityBase  # noqa: E402

# pytest-HACC's plugins (in particular patch_recorder)
# refuse to load if any homeassistant.components.* module
# is already in sys.modules. Importing anything from
# pytest_homeassistant_custom_component.common at module
# scope pulls those transitively, so we defer all
# pytest-HACC + integration-package imports until inside
# the test functions themselves.
DOMAIN = "blueprint_toolkit"
OPTION_CLI_SYMLINK_DIR = "cli_symlink_dir"
STORAGE_KEY = f"{DOMAIN}.installed"


@pytest.fixture(autouse=True)
def install_our_integration(hass, enable_custom_integrations):  # noqa: ANN001
    """Make our integration discoverable to HA in every test.

    pytest-HACC's hass.config.config_dir is its own
    testing_config dir, not the repo root. We symlink our
    custom_components/blueprint_toolkit/ into there
    so HA's loader finds it. enable_custom_integrations
    clears HA's loader cache so the symlink takes effect.
    """
    import shutil

    src = (
        Path(__file__).parent.parent / "custom_components" / "blueprint_toolkit"
    )
    cc = Path(hass.config.config_dir) / "custom_components"
    cc.mkdir(exist_ok=True)
    dst = cc / "blueprint_toolkit"
    if dst.is_symlink() or dst.exists():
        if dst.is_dir() and not dst.is_symlink():
            shutil.rmtree(dst)
        else:
            dst.unlink()
    dst.symlink_to(src)
    # The fixture-ordering already had enable_custom_integrations
    # clear DATA_CUSTOM_COMPONENTS; re-clear in case symlink
    # was added after.
    from homeassistant.loader import DATA_CUSTOM_COMPONENTS

    hass.data.pop(DATA_CUSTOM_COMPONENTS, None)
    yield
    # Cleanup so subsequent tests don't pile symlinks on
    # symlinks if pytest-HACC ever changes config_dir
    # behavior between tests.
    if dst.is_symlink():
        dst.unlink()


def _mock_config_entry(**kwargs):  # noqa: ANN001, ANN201
    """Lazy-import wrapper for MockConfigEntry.

    See the module docstring on import deferral.
    """
    from pytest_homeassistant_custom_component.common import (
        MockConfigEntry,
    )

    return MockConfigEntry(**kwargs)


def _expected_destinations(config_dir: Path) -> set[Path]:
    """The destinations the reconciler will install to.

    Mirrors the destination mapping in
    ``reconciler._destination_mapping``; used by tests as
    ground truth without re-running the reconciler logic
    itself. Note that ``bundled/www/`` is intentionally
    NOT installed -- the integration registers an aiohttp
    static route directly at the bundled docs dir
    instead.
    """
    bundled = (
        Path(__file__).parent.parent
        / "custom_components"
        / "blueprint_toolkit"
        / "bundled"
    )
    out: set[Path] = set()
    for src in (bundled / "blueprints").rglob("*.yaml"):
        rel = src.relative_to(bundled / "blueprints")
        out.add(config_dir / "blueprints" / rel)
    return out


class TestConfigFlow:
    async def test_user_step_creates_entry(self, hass) -> None:  # noqa: ANN001
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "user"},
        )
        assert result["type"] == "form"
        assert result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={},
        )
        assert result["type"] == "create_entry"
        assert result["title"] == "Blueprint Toolkit"

    async def test_second_entry_aborts(self, hass) -> None:  # noqa: ANN001
        existing = _mock_config_entry(domain=DOMAIN, data={})
        existing.add_to_hass(hass)
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "user"},
        )
        assert result["type"] == "abort"
        assert result["reason"] == "single_instance_allowed"


class TestOptionsFlow:
    async def test_persists_cli_symlink_dir(self, hass) -> None:  # noqa: ANN001
        entry = _mock_config_entry(domain=DOMAIN, data={}, options={})
        entry.add_to_hass(hass)

        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] == "form"
        assert result["step_id"] == "init"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={OPTION_CLI_SYMLINK_DIR: "/tmp/cli-target"},
        )
        assert result["type"] == "create_entry"
        assert entry.options[OPTION_CLI_SYMLINK_DIR] == "/tmp/cli-target"

    async def test_changing_options_triggers_reconcile(
        self,
        hass,  # noqa: ANN001
        tmp_path,  # noqa: ANN001
    ) -> None:
        # Without an update listener, options changes are
        # silently saved and the reconciler doesn't re-run
        # until next HA restart. The integration registers
        # one in async_setup_entry; this test verifies it.
        entry = _mock_config_entry(domain=DOMAIN, data={}, options={})
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Set cli_symlink_dir via the options flow.
        cli_dir = tmp_path / "cli-target"
        cli_dir.mkdir()
        flow = await hass.config_entries.options.async_init(entry.entry_id)
        await hass.config_entries.options.async_configure(
            flow["flow_id"],
            user_input={OPTION_CLI_SYMLINK_DIR: str(cli_dir)},
        )
        await hass.async_block_till_done()

        # The update listener triggers async_reload, which
        # re-runs async_setup_entry with the new option,
        # which makes the reconciler install the CLI
        # symlink at the new destination.
        cli_dest = cli_dir / "zwave_network_info.py"
        assert cli_dest.is_symlink(), (
            "expected CLI symlink to land after options change; "
            "is the update listener wired up in __init__.py?"
        )


class TestSetupEntry:
    async def test_setup_installs_symlinks(self, hass) -> None:  # noqa: ANN001
        entry = _mock_config_entry(domain=DOMAIN, data={})
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        config_dir = Path(hass.config.config_dir)
        for dest in _expected_destinations(config_dir):
            assert dest.is_symlink(), f"missing expected symlink: {dest}"

    async def test_setup_writes_manifest_store(self, hass) -> None:  # noqa: ANN001
        entry = _mock_config_entry(domain=DOMAIN, data={})
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # pytest-HACC intercepts .storage writes -- the
        # file does not land on disk. Read back via the
        # Store API instead, which goes through the same
        # mock and returns the data the integration saved.
        from homeassistant.helpers.storage import Store

        store = Store(hass, 1, STORAGE_KEY)
        body = await store.async_load()
        assert body is not None, "manifest store not written"
        destinations = set(body["destinations"])
        expected = {
            str(p)
            for p in _expected_destinations(
                Path(hass.config.config_dir),
            )
        }
        assert destinations == expected

    async def test_setup_registers_services(self, hass) -> None:  # noqa: ANN001
        # Asserts every handler registers its service on
        # ``async_setup_entry``. Catches a service that's
        # silently dropped from ``__init__.py``'s setup hook
        # (or whose handler module fails to expose a callable
        # ``async_register``) without each handler suite
        # having to assert it independently.
        entry = _mock_config_entry(domain=DOMAIN, data={})
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        expected = {
            "trigger_entity_controller",
            "zwave_route_manager",
            "reference_watchdog",
            "entity_defaults_watchdog",
            "device_watchdog",
            "sensor_threshold_switch_controller",
        }
        registered = set(hass.services.async_services().get(DOMAIN, {}))
        missing = expected - registered
        assert not missing, (
            f"handler services not registered after setup: {sorted(missing)}"
        )


class TestDocsStaticRoute:
    async def test_setup_serves_rendered_docs_at_local_url(
        self,
        hass,  # noqa: ANN001
        hass_client,  # noqa: ANN001
    ) -> None:
        # async_setup_entry registers an aiohttp static
        # route at /local/blueprint_toolkit/docs/
        # pointing directly at the bundled docs subtree
        # (HA's default /local/ handler can't follow our
        # symlinks; see the long comment in __init__.py
        # _register_docs_static_route).
        entry = _mock_config_entry(domain=DOMAIN, data={})
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        client = await hass_client()
        # device_watchdog.html is one of the six rendered
        # docs the renderer produces. Its rendered title
        # is "Device Watchdog" -- a stable substring that
        # surviving content changes.
        resp = await client.get(
            "/local/blueprint_toolkit/docs/device_watchdog.html",
        )
        assert resp.status == 200, (
            f"static route returned {resp.status} -- "
            "the integration may not have registered it"
        )
        body = await resp.text()
        assert "Device Watchdog" in body, (
            "rendered doc body did not contain the expected title"
        )


class TestRemoveEntry:
    async def test_remove_clears_everything(self, hass) -> None:  # noqa: ANN001
        entry = _mock_config_entry(domain=DOMAIN, data={})
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        config_dir = Path(hass.config.config_dir)
        # Confirm we installed something to begin with.
        installed_before = [
            dest
            for dest in _expected_destinations(config_dir)
            if dest.is_symlink()
        ]
        assert installed_before, "expected setup to install symlinks"

        # Remove the entry.
        assert await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()

        # All symlinks we installed should be gone.
        for dest in _expected_destinations(config_dir):
            assert not dest.is_symlink(), f"symlink survived removal: {dest}"


class TestCodeQuality(CodeQualityBase):
    ruff_targets = [
        "tests/test_integration.py",
        "custom_components/blueprint_toolkit/__init__.py",
        "custom_components/blueprint_toolkit/config_flow.py",
        "custom_components/blueprint_toolkit/const.py",
    ]
    # mypy strict on HA-importing modules requires HA type
    # stubs which the package does not ship; we rely on
    # the runtime tests for confidence.
    mypy_targets: list[str] = []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", *sys.argv[1:]]))
