# This is AI generated code
"""Config and options flows for ha-blueprint-toolkit.

The user-facing flow is intentionally minimal:

- The setup flow asks for nothing. Confirming the form
  creates the single config entry; HA's
  ``async_setup_entry`` then runs the reconciler.
- The options flow exposes one optional input,
  ``cli_symlink_dir``, controlling whether the bundled
  CLI scripts are symlinked into a user-visible
  directory. Empty (the default) means CLI scripts are
  not installed; the bundled file remains accessible at
  its original path inside the integration.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

from .const import DOMAIN, OPTION_CLI_SYMLINK_DIR

if TYPE_CHECKING:
    from homeassistant.data_entry_flow import FlowResult


class BlueprintToolkitConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Single-step config flow with no required inputs."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        # The integration is single-instance: a second
        # entry would re-reconcile the same destinations
        # and confuse manifest tracking.
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            return self.async_create_entry(
                title="Blueprint Toolkit",
                data={},
            )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({}),
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> BlueprintToolkitOptionsFlow:
        return BlueprintToolkitOptionsFlow()


class BlueprintToolkitOptionsFlow(config_entries.OptionsFlow):
    """One-field options flow for the optional CLI dir.

    HA 2026 made ``config_entry`` a read-only property on
    ``OptionsFlow``; the framework supplies the binding via
    ``_config_entry_id``. We just inherit and don't try to
    set the attribute ourselves.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options.get(OPTION_CLI_SYMLINK_DIR, "")
        schema = vol.Schema(
            {
                vol.Optional(OPTION_CLI_SYMLINK_DIR, default=current): str,
            },
        )
        return self.async_show_form(step_id="init", data_schema=schema)
