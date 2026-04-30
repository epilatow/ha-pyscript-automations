# This is AI generated code
"""Sensor Threshold Switch Controller subpackage for blueprint_toolkit.

Public surface: ``async_register(hass, entry)`` and
``async_unregister(hass, entry)``, called from the
integration's ``async_setup_entry`` / ``async_unload_entry``
to wire up the
``blueprint_toolkit.sensor_threshold_switch_controller``
service plus periodic-with-jitter scheduling and
restart-recovery plumbing.

Module layout:

- ``logic`` -- pure-function spike-detection / release /
  auto-off / manual-override evaluator. Owns sample
  windowing, threshold logic, notification formatting
  via ``helpers.format_notification``. No HA-side
  imports.
- ``handler`` -- HA wiring: vol.Schema-driven argparse,
  three-layer dispatch (entrypoint / argparse / service),
  per-instance state load/save through the diagnostic
  state entity's attributes, periodic ``timer`` ticks
  via ``helpers.schedule_periodic_with_jitter`` (the
  blueprint's ``time_pattern`` minute trigger is gone),
  switch turn_on / turn_off + best-effort notification
  dispatch via the user-configured notify service.
"""

from __future__ import annotations

# ``handler`` imports voluptuous + homeassistant at module
# scope, so it's lazy-imported by callers rather than
# re-exported here -- otherwise importing this subpackage's
# ``.logic`` from pure-Python test environments would pull
# HA in transitively. See
# ``custom_components/blueprint_toolkit/__init__.py`` for
# the call site.

__all__: list[str] = []
