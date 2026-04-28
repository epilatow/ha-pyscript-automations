# This is AI generated code
"""Shared ``homeassistant.*`` stubs for handler unit tests.

Handler modules (``<service>/handler.py``) import directly
from ``homeassistant.*``. Unit tests don't run inside HA,
so we install lightweight stubs into ``sys.modules`` BEFORE
importing the handler. This module bundles the common core
stubs every handler needs; per-port test files extend with
port-specific captures (``async_call_later`` /
``async_track_time_interval`` capture lists, etc).

Usage:

    # tests/test_<service>_handler.py
    from datetime import datetime
    from _handler_stubs import install_homeassistant_stubs

    stubs = install_homeassistant_stubs(
        frozen_now=datetime(2026, 4, 27, 12, 0, 0),
    )

    # Port-specific captures attach to the stub modules:
    _ATI_CALLS: list[...] = []
    def _track(...): ...
    stubs.event.async_track_time_interval = _track

    # Now safe to import the handler under test:
    from custom_components.blueprint_toolkit.<service> import (
        handler,
    )

Multi-file collection caveat: each call to
``install_homeassistant_stubs`` creates fresh module
objects and overwrites ``sys.modules['homeassistant.*']``.
That's harmless in practice -- by the time a later test
file calls it, earlier handlers have already run their
``from homeassistant.X import Y`` imports and captured
function references locally. Re-stubbing afterwards
doesn't reach into already-imported handler modules.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class HomeAssistantStubs:
    """Handles to the freshly installed stub modules.

    Per-port test files poke port-specific functions onto
    these (e.g. ``stubs.event.async_call_later =
    _capture_fn``) before importing the handler module
    under test.
    """

    components: types.ModuleType
    components_automation: types.ModuleType
    config_entries: types.ModuleType
    const: types.ModuleType
    core: types.ModuleType
    helpers: types.ModuleType
    helpers_cv: types.ModuleType
    helpers_dr: types.ModuleType
    helpers_er: types.ModuleType
    event: types.ModuleType
    util: types.ModuleType
    util_dt: types.ModuleType


def _noop_decorator(f: Any) -> Any:
    return f


def install_homeassistant_stubs(
    *,
    frozen_now: datetime,
) -> HomeAssistantStubs:
    """Install lightweight ``homeassistant.*`` stubs into ``sys.modules``.

    Args:
        frozen_now: Value ``homeassistant.util.dt.now()`` and
            ``.utcnow()`` will return. Tests typically pick a
            fixed datetime so timestamp-dependent assertions
            are stable.

    Returns:
        ``HomeAssistantStubs`` view of the installed modules.
        Per-port test files attach their captures to the
        relevant attributes (e.g. ``stubs.event.async_call_later
        = ...``) before importing the handler module under
        test.
    """
    ha = types.ModuleType("homeassistant")
    components = types.ModuleType("homeassistant.components")
    components_automation = types.ModuleType(
        "homeassistant.components.automation",
    )
    components_automation.EVENT_AUTOMATION_RELOADED = (  # type: ignore[attr-defined]
        "automation_reloaded"
    )
    components_automation.DATA_COMPONENT = (  # type: ignore[attr-defined]
        "automation_data_component"
    )

    config_entries = types.ModuleType("homeassistant.config_entries")
    config_entries.ConfigEntry = type(  # type: ignore[attr-defined]
        "ConfigEntry", (), {}
    )

    const = types.ModuleType("homeassistant.const")
    const.EVENT_HOMEASSISTANT_STARTED = (  # type: ignore[attr-defined]
        "homeassistant_started"
    )

    core = types.ModuleType("homeassistant.core")
    core.callback = _noop_decorator  # type: ignore[attr-defined]
    core.HomeAssistant = type(  # type: ignore[attr-defined]
        "HomeAssistant", (), {}
    )
    core.ServiceCall = type("ServiceCall", (), {})  # type: ignore[attr-defined]
    core.Context = type("Context", (), {})  # type: ignore[attr-defined]
    core.Event = type("Event", (), {})  # type: ignore[attr-defined]

    helpers = types.ModuleType("homeassistant.helpers")
    helpers_cv = types.ModuleType(
        "homeassistant.helpers.config_validation",
    )
    helpers_cv.entity_id = lambda v: str(v)  # type: ignore[attr-defined]
    helpers_cv.boolean = lambda v: bool(v)  # type: ignore[attr-defined]
    helpers_cv.ensure_list = lambda v: (  # type: ignore[attr-defined]
        list(v) if v else []
    )

    helpers_dr = types.ModuleType("homeassistant.helpers.device_registry")
    helpers_dr.async_get = lambda _hass: None  # type: ignore[attr-defined]

    helpers_er = types.ModuleType(
        "homeassistant.helpers.entity_registry",
    )
    helpers_er.EVENT_ENTITY_REGISTRY_UPDATED = (  # type: ignore[attr-defined]
        "entity_registry_updated"
    )
    helpers_er.async_get = lambda _hass: None  # type: ignore[attr-defined]

    # Default no-op stubs for the two HA helpers a handler
    # might subscribe to. Per-port test files swap these for
    # capture-list variants that record invocations.
    event = types.ModuleType("homeassistant.helpers.event")
    event.async_call_later = (  # type: ignore[attr-defined]
        lambda _h, _d, _c: lambda: None
    )
    event.async_track_time_interval = (  # type: ignore[attr-defined]
        lambda _h, _c, _i: lambda: None
    )

    util = types.ModuleType("homeassistant.util")
    util_dt = types.ModuleType("homeassistant.util.dt")
    util_dt.now = lambda: frozen_now  # type: ignore[attr-defined]
    util_dt.utcnow = lambda: frozen_now  # type: ignore[attr-defined]

    sys.modules["homeassistant"] = ha
    sys.modules["homeassistant.components"] = components
    sys.modules["homeassistant.components.automation"] = components_automation
    sys.modules["homeassistant.config_entries"] = config_entries
    sys.modules["homeassistant.const"] = const
    sys.modules["homeassistant.core"] = core
    sys.modules["homeassistant.helpers"] = helpers
    sys.modules["homeassistant.helpers.config_validation"] = helpers_cv
    sys.modules["homeassistant.helpers.device_registry"] = helpers_dr
    sys.modules["homeassistant.helpers.entity_registry"] = helpers_er
    sys.modules["homeassistant.helpers.event"] = event
    sys.modules["homeassistant.util"] = util
    sys.modules["homeassistant.util.dt"] = util_dt

    return HomeAssistantStubs(
        components=components,
        components_automation=components_automation,
        config_entries=config_entries,
        const=const,
        core=core,
        helpers=helpers,
        helpers_cv=helpers_cv,
        helpers_dr=helpers_dr,
        helpers_er=helpers_er,
        event=event,
        util=util,
        util_dt=util_dt,
    )
