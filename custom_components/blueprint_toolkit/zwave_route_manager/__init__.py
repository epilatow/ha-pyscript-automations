# This is AI generated code
"""Z-Wave Route Manager subpackage for blueprint_toolkit.

Public surface: ``async_register(hass, entry)`` and
``async_unregister(hass, entry)``, called from the
integration's ``async_setup_entry`` / ``async_unload_entry``
to wire up the ``blueprint_toolkit.zwave_route_manager``
service plus periodic scheduling and restart-recovery
plumbing.

Module layout:

- ``logic`` -- pure-function decision tree. Owns YAML
  parsing, entity-to-node resolution, the diff/plan state
  machine, and the bridge-timeout circuit breaker. No
  HA-side imports.
- ``bridge`` -- async socket.io client for zwave-js-ui's
  ``ZWAVE_API`` event. Imported by ``handler`` only.
- ``handler`` -- HA wiring: vol.Schema-driven argparse with
  persistent-notification config-error surfacing,
  three-layer dispatch (entrypoint / argparse / service),
  periodic scheduling via ``async_track_time_interval``,
  per-instance state on ``entry.runtime_data``, four
  notification categories (config errors, API
  unavailable, apply failures, pending timeouts).
"""

from __future__ import annotations

# ``handler`` imports voluptuous + homeassistant at module
# scope, so it's lazy-imported by callers rather than
# re-exported here -- otherwise ``import
# custom_components.blueprint_toolkit.zwave_route_manager.logic``
# from pure-Python test environments would pull HA in
# transitively. See
# ``custom_components/blueprint_toolkit/__init__.py`` for
# the call site.

__all__: list[str] = []
