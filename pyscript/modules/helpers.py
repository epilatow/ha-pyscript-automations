# This is AI generated code
"""Shared helpers for automation logic modules.

Does not use PyScript-injected globals.

Provides dataclasses, persistent notification support,
timestamp formatting, interval gating, regex matching,
and shared notification-cap logic used by watchdog
automations.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Protocol

    class CappableResult(Protocol):
        """Structural type expected by ``prepare_notifications``.

        Any watchdog result dataclass that exposes:

        - ``has_issue: bool``
        - ``notification_id: str``
        - ``notification_title: str``
        - ``to_notification(suppress: bool = False) -> PersistentNotification``

        is usable as input. ``notification_id`` and
        ``notification_title`` are used by the helper as
        the sort key so each watchdog doesn't need to
        sort results itself: results are ordered by
        ``(notification_title, notification_id)`` before
        the cap is applied, giving a deterministic
        shown/suppressed split when the cap is exceeded.
        """

        has_issue: bool
        notification_id: str
        notification_title: str

        def to_notification(
            self,
            suppress: bool = False,
        ) -> "PersistentNotification":
            """Return a PersistentNotification for this result."""
            ...


@dataclass
class PersistentNotification:
    """A persistent notification to create or dismiss."""

    active: bool
    notification_id: str
    title: str
    message: str


@dataclass
class EntityRegistryInfo:
    """Entity registry fields for drift detection."""

    entity_id: str
    name: str | None
    # Default entity name (HA entry.original_name).
    # Used when entry.name is not set.
    original_name: str | None
    has_entity_name: bool
    device_id: str | None


@dataclass
class DeviceEntry:
    """Device discovered during integration scan."""

    id: str
    url: str

    # Current device name. HA device registry
    # device.name_by_user (if set) or device.name
    # (set by integration).
    name: str

    # Integration default name. HA device registry
    # device.name. Non-deterministic for
    # multi-integration devices.
    default_name: str

    # Map integrations to the entity ids they provide.
    integration_entities: dict[str, set[str]] = field(
        default_factory=dict,
    )


def format_timestamp(template: str, dt: datetime) -> str:
    """Format timestamp tokens in a template string.

    Supported tokens: YYYY, YY, MM, DD, HH, mm, ss.
    """
    if not template:
        return ""
    # Replace longest tokens first so YYYY is consumed
    # before YY can match.  Uses str.replace instead of
    # re.sub + lambda because PyScript's AST evaluator
    # cannot resolve local variables in lambda closures.
    result = template
    result = result.replace("YYYY", f"{dt.year:04d}")
    result = result.replace("YY", f"{dt.year % 100:02d}")
    result = result.replace("MM", f"{dt.month:02d}")
    result = result.replace("DD", f"{dt.day:02d}")
    result = result.replace("HH", f"{dt.hour:02d}")
    result = result.replace("mm", f"{dt.minute:02d}")
    result = result.replace("ss", f"{dt.second:02d}")
    return result


def format_notification(
    text: str,
    prefix: str,
    suffix: str,
    current_time: datetime,
) -> str:
    """Format notification with prefix/suffix and
    timestamp tokens."""
    formatted_prefix = format_timestamp(
        prefix,
        current_time,
    )
    formatted_suffix = format_timestamp(
        suffix,
        current_time,
    )
    return f"{formatted_prefix}{text}{formatted_suffix}"


def on_interval(
    check_interval_minutes: int,
    current_time: datetime,
) -> bool:
    """Return True if this tick should run evaluation.

    Uses modulo arithmetic on the minute-of-epoch to gate
    execution to every N minutes without persistent state.
    """
    if check_interval_minutes <= 0:
        return True
    minutes_since_epoch = int(
        current_time.timestamp() // 60,
    )
    return (minutes_since_epoch % check_interval_minutes) == 0


def matches_pattern(
    text: str,
    pattern: str,
) -> bool:
    """Return True if text matches regex pattern.

    Returns False if pattern is empty or invalid.
    """
    if not pattern:
        return False
    try:
        return bool(
            re.search(pattern, text, re.IGNORECASE),
        )
    except re.error:
        return False


def slugify(text: str) -> str:
    """Return a Home Assistant-compatible slug from ``text``.

    Mirrors ``homeassistant.util.slugify(text, separator="_")``
    for the ASCII-only common case: NFKD decomposition,
    drop non-ASCII characters, lowercase, collapse runs of
    non-alphanumeric characters into a single underscore,
    and strip leading and trailing underscores. Empty input
    returns ``""``; non-empty input that collapses to an
    empty slug (e.g. emoji-only, punctuation-only) returns
    ``"unknown"``, matching HA's fallback.
    """
    if not text:
        return ""
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = (
        normalized.encode(
            "ascii",
            "ignore",
        )
        .decode("ascii")
        .lower()
    )
    slug = re.sub(r"[^a-z0-9]+", "_", ascii_text).strip("_")
    return slug or "unknown"


def prepare_notifications(
    results: "Sequence[CappableResult]",
    max_notifications: int,
    cap_notification_id: str,
    cap_title: str,
    cap_item_label: str,
) -> list[PersistentNotification]:
    """Sort, build, and cap per-result notifications.

    This helper is the "glue" step every watchdog runs
    between `evaluate_*` (which produces result
    dataclasses) and `_process_persistent_notifications`
    (which creates/dismisses them in HA). It is the
    single place where shared notification semantics live.

    Responsibilities, in order:

    1. **Sort** ``results`` deterministically by
       ``(notification_title, notification_id)``. Each
       watchdog used to sort its own results in its
       logic module; now the helper does it once.
       Deterministic ordering is required so the
       shown/suppressed split below is reproducible
       across runs — users should see the same
       "first N" subset when the cap is exceeded,
       regardless of whichever internal-dict ordering
       the logic module happened to iterate in.
    2. **Cap** the issue set. When the number of
       has-issue results exceeds ``max_notifications``
       (and the cap is non-zero), the first
       ``max_notifications`` are shown in full, the
       rest are suppressed (passed as ``suppress=True``
       to ``to_notification``, which dismisses their
       stored ID), and a cap-reached summary
       notification is appended.
    3. **Emit clean-result notifications anyway** when
       the cap is exceeded, so any lingering
       notifications from a prior run where the same
       result was in the issue partition get dismissed.
    4. **Always emit the cap-summary slot** — active
       when the cap is reached, inactive otherwise —
       so a previously-active summary gets dismissed
       when the cap no longer applies.

    This runs as standard Python inside the module
    rather than under PyScript's AST evaluator, so it
    can use ``sorted(key=fn)`` freely. Service wrappers
    that call this function inherit that benefit: they
    no longer need to sort themselves.

    Args:
        results: Watchdog result dataclasses
            implementing the ``CappableResult`` protocol.
        max_notifications: Per-run cap; ``0`` means
            unlimited.
        cap_notification_id: Persistent notification ID
            used for the cap-summary notification. Must
            be unique per watchdog instance.
        cap_title: Title to use when the cap is reached.
        cap_item_label: Human-readable label describing
            what's being counted, e.g.
            ``"devices with issues"`` or
            ``"owners with broken references"``. Inserted
            into the cap summary message.

    Returns:
        A flat list of ``PersistentNotification`` ready
        for ``_process_persistent_notifications`` to
        create or dismiss. Every input result produces
        exactly one notification, plus one cap-summary
        notification.
    """
    # Sort by title then id so the cap-exceeded split is
    # deterministic run-to-run.
    sorted_results = [
        r
        for _, _, r in sorted(
            [
                (
                    (r.notification_title, r.notification_id),
                    i,
                    r,
                )
                for i, r in enumerate(results)
            ]
        )
    ]

    notifications: list[PersistentNotification] = []
    issues = [r for r in sorted_results if r.has_issue]

    if max_notifications > 0 and len(issues) > max_notifications:
        shown = issues[:max_notifications]
        suppressed = issues[max_notifications:]
        for r in shown:
            notifications.append(r.to_notification())
        for r in suppressed:
            notifications.append(
                r.to_notification(suppress=True),
            )
        # Emit notifications for clean results so any
        # lingering notifications from a prior run get
        # dismissed.
        for r in sorted_results:
            if not r.has_issue:
                notifications.append(r.to_notification())
        notifications.append(
            PersistentNotification(
                active=True,
                notification_id=cap_notification_id,
                title=cap_title,
                message=(
                    "Showing "
                    + str(max_notifications)
                    + " of "
                    + str(len(issues))
                    + " "
                    + cap_item_label
                    + ". "
                    + str(len(suppressed))
                    + " additional notifications were"
                    " suppressed. Increase the"
                    " notification cap or fix existing"
                    " issues to see more."
                ),
            ),
        )
    else:
        for r in sorted_results:
            notifications.append(r.to_notification())
        # Cap slot always emitted so a previously-active
        # summary gets dismissed when the cap no longer
        # applies.
        notifications.append(
            PersistentNotification(
                active=False,
                notification_id=cap_notification_id,
                title="",
                message="",
            ),
        )
    return notifications
