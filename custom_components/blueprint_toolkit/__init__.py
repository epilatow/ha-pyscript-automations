# This is AI generated code
"""blueprint_toolkit integration entry points.

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
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import installer, reconciler
from .const import (
    DOMAIN,
    OPTION_CLI_SYMLINK_DIR,
    STORAGE_KEY,
    STORAGE_VERSION,
)

# Repairs issue IDs are duplicated here (the source-of-
# truth lives in repairs.py) rather than imported, so this
# module's import graph stays HA-free for the unit tests
# that import via the package path.
_ISSUE_INSTALL_CONFLICTS = "install_conflicts"
_ISSUE_INSTALL_FAILURE = "install_failure"

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


@dataclass
class IntegrationData:
    """Per-config-entry runtime state.

    Lives at ``entry.runtime_data``; HA auto-clears the
    attribute on entry unload, but our explicit
    ``async_unload_entry`` still walks ``handlers`` to
    cancel pending wakeups + unsubscribe bus listeners
    before that happens. ``handlers[<service>]`` is the
    per-port bucket the shared lifecycle helpers in
    ``helpers.py`` populate.

    Cross-reload state (the Repairs-flow handoff for
    force-confirmed destinations) lives separately in
    ``hass.data[DOMAIN]`` because it must survive the
    unload between Repairs flow completion and the
    triggered config-entry reload.
    """

    handlers: dict[str, dict[str, Any]] = field(default_factory=dict)


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
    """Serve rendered docs at /local/blueprint_toolkit/docs/.

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
    docs_dir = _BUNDLED_ROOT / "www" / "blueprint_toolkit" / "docs"
    if not docs_dir.is_dir():
        _LOGGER.warning(
            "docs directory missing under bundled payload: %s",
            docs_dir,
        )
        return
    hass.http.app.router.add_static(
        prefix="/local/blueprint_toolkit/docs",
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


def _surface_conflicts(
    hass: HomeAssistant,
    entry: ConfigEntry,
    conflicts: tuple[reconciler.Conflict, ...],
) -> None:
    from homeassistant.helpers import issue_registry as ir

    if not conflicts:
        ir.async_delete_issue(hass, DOMAIN, _ISSUE_INSTALL_CONFLICTS)
        return
    serialised = [
        {
            "destination": str(c.destination),
            "kind": c.kind,
            "details": c.details,
        }
        for c in conflicts
    ]
    ir.async_create_issue(
        hass,
        DOMAIN,
        _ISSUE_INSTALL_CONFLICTS,
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=_ISSUE_INSTALL_CONFLICTS,
        data={
            "entry_id": entry.entry_id,
            "conflicts": serialised,
            "conflict_destinations": [str(c.destination) for c in conflicts],
        },
    )


def _surface_failure(
    hass: HomeAssistant,
    entry: ConfigEntry,
    errors: list[str],
) -> None:
    from homeassistant.helpers import issue_registry as ir

    if not errors:
        ir.async_delete_issue(hass, DOMAIN, _ISSUE_INSTALL_FAILURE)
        return
    ir.async_create_issue(
        hass,
        DOMAIN,
        _ISSUE_INSTALL_FAILURE,
        is_fixable=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key=_ISSUE_INSTALL_FAILURE,
        data={
            "entry_id": entry.entry_id,
            "errors": list(errors),
        },
    )


def _clear_stale_blueprint_service_repairs(hass: HomeAssistant) -> int:
    """Sweep automation ``service_not_found`` repairs for our services.

    The HA boot orders integration setup in parallel with
    automation trigger evaluation: if a blueprint-backed
    automation fires before pyscript's startup scan
    registers our ``*_blueprint_entrypoint`` services, the
    automation integration creates a persistent
    ``service_not_found`` Repairs issue. Subsequent
    successful service calls do not clear that issue, so it
    sits in the UI until the user dismisses it manually.

    This sweep runs after the synchronous ``pyscript.reload``
    above, at which point every blueprint-entrypoint service
    is guaranteed to be registered. Any matching issue in
    the registry is therefore stale, and we delete it.
    Returns the count of issues cleared (for logging).
    """
    from homeassistant.helpers import issue_registry as ir

    registry = ir.async_get(hass)
    targets: list[tuple[str, str]] = []
    for issue in list(registry.issues.values()):
        if (
            issue.domain == "automation"
            and getattr(issue, "translation_key", None) == "service_not_found"
            and "_blueprint_entrypoint" in issue.issue_id
            and "pyscript." in issue.issue_id
        ):
            targets.append((issue.domain, issue.issue_id))
    for domain, issue_id in targets:
        ir.async_delete_issue(hass, domain, issue_id)
    return len(targets)


def _consume_pending_force_destinations(
    hass: HomeAssistant,
) -> frozenset[Path]:
    """Pop and return any force_destinations the Repairs flow stashed.

    The Repairs ``InstallConflictsFlow`` writes the
    user-confirmed destinations into ``hass.data[DOMAIN]``
    and triggers an integration reload; this call (which
    runs inside the next ``async_setup_entry``) consumes
    them so they don't leak into a subsequent reconcile.
    """
    bucket = hass.data.get(DOMAIN, {})
    raw = bucket.pop("pending_force_destinations", None)
    if not raw:
        return frozenset()
    return frozenset(Path(p) for p in raw)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Plan + apply the bundled payload's symlinks."""
    # Initialise per-entry runtime state. Subpackage
    # handler buckets land under ``entry.runtime_data.handlers``
    # via the shared lifecycle helpers in ``helpers.py``.
    entry.runtime_data = IntegrationData()
    config_root = Path(hass.config.config_dir)
    cli_symlink_dir = _coerce_cli_symlink_dir(
        entry.options.get(OPTION_CLI_SYMLINK_DIR),
    )
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    prior = await _load_prior_manifest(hass)
    force_destinations = _consume_pending_force_destinations(hass)

    plan = await hass.async_add_executor_job(
        functools.partial(
            reconciler.plan,
            bundled_root=_BUNDLED_ROOT,
            config_root=config_root,
            prior_manifest=prior,
            mode=reconciler.Mode.HACS,
            cli_symlink_dir=cli_symlink_dir,
            force_destinations=force_destinations,
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

    _surface_conflicts(hass, entry, plan.conflicts)
    _surface_failure(hass, entry, result.errors)

    await _save_manifest(hass, plan.new_manifest)

    # pyscript runs its own startup scan asynchronously, and
    # ``after_dependencies`` only orders us after pyscript's
    # setup, not after its scan -- so a steady-state restart
    # (where the reconciler has nothing to change) can land us
    # before pyscript has registered our blueprint-entrypoint
    # services, and any automation that fires in that window
    # logs a service_not_found error and creates a
    # persistent Repairs issue. Forcing pyscript.reload here
    # is synchronous (blocking=True) and re-imports every
    # pyscript file before we return, closing the race.
    # automation.reload is still gated on result.changed --
    # re-rendering automations is wasted work when nothing
    # bundled actually changed.
    await _fire_reload_services(
        hass,
        pyscript=True,
        automation=result.changed,
    )

    # The pyscript.reload above closes the race for any
    # automation that fires *after* our setup, but boot-time
    # triggers (which are most of them on a restart) fire
    # before our setup runs and have already created stale
    # service_not_found repairs by now. Sweep them up.
    cleared = _clear_stale_blueprint_service_repairs(hass)
    if cleared:
        _LOGGER.info(
            "cleared %d stale service_not_found repair(s) "
            "for blueprint-backed automations",
            cleared,
        )

    _register_docs_static_route(hass)

    # trigger_entity_controller service handler.
    # Lazy-imported because the handler module pulls in
    # ``voluptuous`` and ``homeassistant`` at module scope.
    from .trigger_entity_controller import handler as tec_handler

    await tec_handler.async_register(hass, entry)

    # Conflicts surface to the user via Repairs rather
    # than by failing the setup. Real install errors raise
    # an OSError inside the executor job which propagates
    # up and HA marks the integration as setup-failed.
    return True


async def async_unload_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Unload the config entry. No filesystem side effects.

    Tears down the TEC handler so a reload (e.g.
    after ``_async_options_updated`` fires from an
    options-flow save) doesn't leak service
    registrations, bus listeners, or pending
    ``async_call_later`` wakeups.
    """
    from .trigger_entity_controller import handler as tec_handler

    await tec_handler.async_unregister(hass, entry)
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
