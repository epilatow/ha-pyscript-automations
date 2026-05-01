# This is AI generated code
"""Shared mock-hass + argparse fixtures for handler tests.

Each per-handler test file under ``tests/test_<service>_handler.py``
needs the same minimal stand-ins for the HA surface that handler-
side unit tests touch (no boot, no event loop above asyncio.run):

- A mock ``services`` collaborator that records every
  ``async_call`` for assertion.
- A mock ``config_entries`` collaborator with a single
  ``async_entries(DOMAIN)`` accessor that returns the
  populated entries list.
- A mock ``ConfigEntry`` whose ``runtime_data.handlers``
  bucket layout matches what ``helpers.spec_bucket`` builds.
- A bare-minimum ``ServiceCall`` stand-in that exposes
  ``data`` and ``context`` -- enough for the argparse layer.
- An async-callable capture that stands in for the per-
  handler ``_async_service_layer`` so argparse-layer tests
  can assert what kwargs the layer would have received
  without booting the service layer itself.

The frozen ``FROZEN_NOW`` value matches the value passed to
``install_homeassistant_stubs(frozen_now=...)`` for a
deterministic ``dt_util.utcnow()`` across handler tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class FrozenNow:
    """Wall-clock the handler-test stubs pin ``dt_util.utcnow()`` to."""

    value = datetime(2026, 4, 28, 23, 0, 0)


@dataclass
class MockServices:
    """Records every ``hass.services.async_call`` for assertion.

    ``calls`` carries ``(domain, name, data)`` tuples;
    ``kwargs`` carries the keyword args (``context=``,
    ``blocking=``) for the matching index.
    """

    calls: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    kwargs: list[dict[str, Any]] = field(default_factory=list)

    async def async_call(
        self,
        domain: str,
        name: str,
        data: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self.calls.append((domain, name, dict(data or {})))
        self.kwargs.append(dict(kwargs))


@dataclass
class MockRuntimeData:
    """Stand-in for ``ConfigEntry.runtime_data``."""

    handlers: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class MockEntry:
    """Stand-in for ``ConfigEntry`` -- only ``runtime_data`` is read."""

    runtime_data: MockRuntimeData = field(default_factory=MockRuntimeData)


@dataclass
class MockConfigEntries:
    """Stand-in for ``hass.config_entries`` with a single accessor."""

    entries: list[MockEntry] = field(default_factory=list)

    def async_entries(self, _domain: str) -> list[MockEntry]:
        return list(self.entries)


@dataclass
class MockHass:
    """Bare-minimum HA stand-in with services + config_entries."""

    services: MockServices = field(default_factory=MockServices)
    config_entries: MockConfigEntries = field(
        default_factory=MockConfigEntries
    )


class FakeServiceCall:
    """Bare-minimum ``ServiceCall`` shape ``_async_argparse`` reads."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.context = None


@dataclass
class ArgparseCapture:
    """Records the kwargs passed into ``_async_service_layer``.

    Drop-in async callable that argparse-layer tests can
    swap in for the real service layer to assert what the
    layer would have received.
    """

    calls: list[dict[str, Any]] = field(default_factory=list)

    async def __call__(self, _hass: Any, _call: Any, **kwargs: Any) -> None:
        self.calls.append(kwargs)
