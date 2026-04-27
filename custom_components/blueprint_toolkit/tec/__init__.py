# This is AI generated code
"""Native Trigger Entity Controller for blueprint_toolkit.

Public surface: ``async_register(hass, entry)`` and
``async_unregister(hass)``, called from the integration's
``async_setup_entry`` / ``async_unload_entry`` to wire up
the native ``blueprint_toolkit.trigger_entity_controller``
service plus the discovery / scheduling / restart-recovery
plumbing.

Module layout:

- ``logic`` -- pure-function decision tree, lifted from
  ``pyscript/modules/trigger_entity_controller.py``.
  Identical API and behaviour; the existing TEC tests
  (``tests/test_trigger_entity_controller.py``) cover
  both copies.
- ``helpers`` -- subset of ``pyscript/modules/helpers.py``
  needed by ``logic`` (token-aware notification
  formatting only).
- ``handler`` -- native HA wiring: vol.Schema-driven
  argparse with persistent-notification config-error
  surfacing, three-layer dispatch (entrypoint /
  argparse / service), per-instance state on
  ``hass.data``, ``async_call_later`` auto-off
  scheduling, ``automation.trigger`` re-fire so
  logbook attributes downstream actions to the right
  automation, blueprint discovery via
  ``hass.data[DATA_COMPONENT].entities``, live
  add/remove via ``EVENT_AUTOMATION_RELOADED`` plus
  ``entity_registry_updated``.
"""

from __future__ import annotations

# ``handler`` imports voluptuous + homeassistant at
# module scope, so it's lazy-imported by callers rather
# than re-exported here -- otherwise ``import
# custom_components.blueprint_toolkit.tec.logic`` from
# pure-Python test environments would pull HA in
# transitively. See
# ``custom_components/blueprint_toolkit/__init__.py`` for
# the call site.

__all__: list[str] = []
