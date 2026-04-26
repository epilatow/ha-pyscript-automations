# This is AI generated code
"""Repairs UI integration for ha-blueprint-toolkit.

Two issue types surfaced from ``async_setup_entry``:

- ``install_conflicts`` -- the reconciler refused to
  overwrite something at one or more of our destinations.
  Listed verbatim. The fix flow's Submit removes each
  symlink or file and re-installs.
- ``install_failure`` -- the installer raised an
  ``OSError`` while applying actions (unwritable parent,
  cross-mount, directory where we want a file, ...).
  Surfaces the captured error text. Submit re-runs the
  full setup against the current bundle, which is the
  same path a restart would take.

HA discovers this module via the ``repairs`` platform
convention -- the file name and the
``async_create_fix_flow`` factory function name are
contractual.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.data_entry_flow import FlowResult

ISSUE_INSTALL_CONFLICTS = "install_conflicts"
ISSUE_INSTALL_FAILURE = "install_failure"


class InstallConflictsFlow(RepairsFlow):
    """Confirm-and-overwrite flow for ``install_conflicts``."""

    def __init__(self, data: dict[str, Any] | None) -> None:
        self._data = data or {}

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,  # noqa: ARG002
    ) -> FlowResult:
        return await self.async_step_confirm()

    async def async_step_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        if user_input is not None:
            await _retry_setup(
                self.hass,
                self._data.get("entry_id"),
                force_destinations=frozenset(
                    self._data.get("conflict_destinations", []) or [],
                ),
            )
            return self.async_create_entry(data={})

        # vol.Schema({}) renders as a single Submit
        # button; the conflict listing comes through the
        # description placeholder rendered by the
        # frontend with the strings.json template.
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders={
                "conflicts": _format_conflict_list(
                    self._data.get("conflicts", []) or [],
                ),
            },
        )


class InstallFailureFlow(RepairsFlow):
    """Retry-after-fix flow for ``install_failure``."""

    def __init__(self, data: dict[str, Any] | None) -> None:
        self._data = data or {}

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,  # noqa: ARG002
    ) -> FlowResult:
        return await self.async_step_confirm()

    async def async_step_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        if user_input is not None:
            await _retry_setup(self.hass, self._data.get("entry_id"))
            return self.async_create_entry(data={})

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders={
                "errors": "\n".join(
                    self._data.get("errors", []) or ["(no error text)"],
                ),
            },
        )


async def async_create_fix_flow(
    hass: HomeAssistant,  # noqa: ARG001
    issue_id: str,
    data: dict[str, Any] | None,
) -> RepairsFlow:
    """Factory HA calls to instantiate a flow per issue."""
    if issue_id.startswith(ISSUE_INSTALL_CONFLICTS):
        return InstallConflictsFlow(data)
    if issue_id.startswith(ISSUE_INSTALL_FAILURE):
        return InstallFailureFlow(data)
    msg = f"unknown issue_id: {issue_id!r}"
    raise ValueError(msg)


def _format_conflict_list(conflicts: list[dict[str, str]]) -> str:
    """Render conflict dicts back into the user-visible string format.

    Mirrors the format used in the dev-install CLI's
    plan-print output and the integration's WARNING log
    line so the user sees the same wording across
    surfaces.
    """
    if not conflicts:
        return "(no conflicts)"
    lines: list[str] = []
    for c in conflicts:
        kind = c.get("kind", "?")
        dest = c.get("destination", "?")
        details = c.get("details", "")
        if kind == "unknown_symlink":
            lines.append(f"unexpected symlink: {dest} ({details})")
        elif kind == "regular_file":
            lines.append(f"unexpected file: {dest}")
        elif kind == "regular_dir":
            # Directories will not be removed by Overwrite
            # (the installer's unlink raises on dirs); we
            # still list them so the user knows what to
            # move aside manually.
            lines.append(f"unexpected directory: {dest}")
        else:
            lines.append(f"unexpected ({kind}): {dest} {details}")
    return "\n".join(lines)


async def _retry_setup(
    hass: HomeAssistant,
    entry_id: str | None,
    *,
    force_destinations: frozenset[str] = frozenset(),
) -> None:
    """Re-run the integration's setup, optionally with force_destinations.

    Stash the force list under the entry's runtime_data so
    ``async_setup_entry`` (which the reload triggers) can
    pick it up; clear after consumption. The reload itself
    does an unload + setup, which is what runs the
    reconciler again.
    """
    if not entry_id:
        return
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None:
        return

    # Use hass.data scoped by domain for one-shot signal
    # to the next async_setup_entry call. runtime_data is
    # cleared on unload, so we use hass.data instead.
    if force_destinations:
        bucket = hass.data.setdefault(DOMAIN, {})
        bucket["pending_force_destinations"] = force_destinations

    await hass.config_entries.async_reload(entry_id)
