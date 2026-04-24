# This is AI generated code
"""ha-pyscript-automations integration entry points.

Wraps the pure-function reconciler and sync installer
modules in the HA async lifecycle: ``async_setup_entry``
plans + applies on every startup, ``async_remove_entry``
removes everything previously installed when the user
uninstalls the integration.

Module-level imports stay HA-free so the reconciler /
installer modules remain importable from non-HA test
environments. HA-specific imports happen inside the entry
point functions, and type annotations live behind
``TYPE_CHECKING`` so they evaluate lazily under
``from __future__ import annotations``.
"""

from __future__ import annotations

import functools
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from . import installer, reconciler
from .const import (
    OPTION_CLI_SYMLINK_DIR,
    STORAGE_KEY,
    STORAGE_VERSION,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Files under bundled/ ship with the integration; HA reads
# manifest.json to discover them. We resolve our own
# location rather than hass.config.path so that running
# under unusual config-dir setups still works.
_BUNDLED_ROOT = Path(__file__).parent / "bundled"


def _coerce_cli_symlink_dir(raw: object) -> Path | None:
    """Return a Path for the option, or None when unset/empty."""
    if not raw:
        return None
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not raw:
        return None
    return Path(raw)


async def _load_prior_manifest(hass: HomeAssistant) -> frozenset[Path]:
    from homeassistant.helpers.storage import Store

    store: Store[dict[str, object]] = Store(
        hass,
        STORAGE_VERSION,
        STORAGE_KEY,
    )
    raw = await store.async_load() or {}
    paths = raw.get("destinations", []) or []
    return frozenset(Path(p) for p in paths if isinstance(p, str))


async def _save_manifest(
    hass: HomeAssistant,
    destinations: frozenset[Path],
) -> None:
    from homeassistant.helpers.storage import Store

    store: Store[dict[str, object]] = Store(
        hass,
        STORAGE_VERSION,
        STORAGE_KEY,
    )
    await store.async_save(
        {"destinations": sorted(str(p) for p in destinations)},
    )


async def _fire_reload_services(
    hass: HomeAssistant,
    *,
    pyscript: bool,
    automation: bool,
) -> None:
    if pyscript and hass.services.has_service("pyscript", "reload"):
        await hass.services.async_call(
            "pyscript",
            "reload",
            blocking=True,
        )
    if automation and hass.services.has_service(
        "automation",
        "reload",
    ):
        await hass.services.async_call(
            "automation",
            "reload",
            blocking=True,
        )


def _register_docs_static_route(hass: HomeAssistant) -> None:
    """Serve rendered docs at /local/ha_pyscript_automations/docs/.

    HA's default ``/local/`` handler refuses to follow
    symlinks whose targets escape ``/config/www/``, and is
    only wired up at startup if ``/config/www/`` already
    exists. We sidestep both by registering our own
    aiohttp static route, pointing directly at the bundled
    docs directory inside the integration. Doc links work
    for HACS-installed users; dev-install users (who don't
    load this integration) see broken /local/ doc links --
    a documented dev-install limitation.
    """
    docs_dir = _BUNDLED_ROOT / "www" / "ha_pyscript_automations" / "docs"
    if not docs_dir.is_dir():
        _LOGGER.warning(
            "docs directory missing under bundled payload: %s",
            docs_dir,
        )
        return
    hass.http.app.router.add_static(
        prefix="/local/ha_pyscript_automations/docs",
        path=str(docs_dir),
        show_index=False,
    )


async def _async_options_updated(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Re-run setup when the options flow changes anything.

    Without this listener HA quietly persists the new
    options and the reconciler is not re-run until next HA
    restart. With it, changing ``cli_symlink_dir`` (or any
    future option) takes effect immediately.
    """
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Plan + apply the bundled payload's symlinks."""
    config_root = Path(hass.config.config_dir)
    cli_symlink_dir = _coerce_cli_symlink_dir(
        entry.options.get(OPTION_CLI_SYMLINK_DIR),
    )
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    prior = await _load_prior_manifest(hass)

    plan = await hass.async_add_executor_job(
        functools.partial(
            reconciler.plan,
            bundled_root=_BUNDLED_ROOT,
            config_root=config_root,
            prior_manifest=prior,
            mode=reconciler.Mode.HACS,
            cli_symlink_dir=cli_symlink_dir,
        ),
    )

    result = await hass.async_add_executor_job(installer.apply, plan)

    if result.errors:
        for err in result.errors:
            _LOGGER.error("install error: %s", err)
    if result.conflicts:
        for c in result.conflicts:
            _LOGGER.warning(
                "install conflict at %s: %s %s",
                c.destination,
                c.kind,
                c.details,
            )

    await _save_manifest(hass, plan.new_manifest)

    if result.changed:
        await _fire_reload_services(hass, pyscript=True, automation=True)

    _register_docs_static_route(hass)

    # Conflicts surface to the user via Repairs rather
    # than by failing the setup. Real install errors raise
    # an OSError inside the executor job which propagates
    # up and HA marks the integration as setup-failed.
    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Unload the config entry. No filesystem side effects."""
    return True


async def async_remove_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Remove the config entry, wiping everything we installed."""
    prior = await _load_prior_manifest(hass)
    if not prior:
        return

    actions = tuple(
        reconciler.Action(
            kind=reconciler.ActionKind.REMOVE,
            destination=dest,
            target=None,
        )
        for dest in sorted(prior)
    )
    plan = reconciler.ReconcilePlan(
        actions=actions,
        new_manifest=frozenset(),
        conflicts=(),
    )
    await hass.async_add_executor_job(installer.apply, plan)
    await _save_manifest(hass, frozenset())
    await _fire_reload_services(hass, pyscript=True, automation=True)
