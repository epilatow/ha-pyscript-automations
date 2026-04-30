# This is AI generated code
"""Entity Defaults Watchdog subpackage for blueprint_toolkit.

Public surface: ``async_register(hass, entry)`` and
``async_unregister(hass, entry)``, called from the
integration's ``async_setup_entry`` / ``async_unload_entry``
to wire up the ``blueprint_toolkit.entity_defaults_watchdog``
service plus periodic-with-jitter scheduling and
restart-recovery plumbing.

Module layout:

- ``logic`` -- entity-drift evaluator. Owns per-device
  drift classification, deviceless-entity collision-suffix
  handling, notification body assembly, and the
  cap-and-sort glue against
  ``helpers.prepare_notifications``. No HA dependencies;
  safe to import + call outside the HA process.
- ``handler`` -- HA wiring: vol.Schema-driven argparse,
  three-layer dispatch (entrypoint / argparse / service),
  periodic scheduling via
  ``helpers.schedule_periodic_with_jitter``, registry-walk
  truth-set assembly + scan offloaded to
  ``hass.async_add_executor_job``, sweep-dispatched
  per-device + deviceless aggregate notifications.
"""

from __future__ import annotations

# ``handler`` imports voluptuous + homeassistant at module
# scope, so it's lazy-imported by callers rather than
# re-exported here -- otherwise importing
# ``custom_components.blueprint_toolkit.entity_defaults_watchdog.logic``
# from pure-Python test environments would pull HA in
# transitively. See
# ``custom_components/blueprint_toolkit/__init__.py`` for
# the call site.

__all__: list[str] = []
