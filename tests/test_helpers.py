#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest", "pytest-cov", "ruff", "mypy"]
# ///
# This is AI generated code
"""Tests for helpers module."""

import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

_SCRIPT_PATH = (
    REPO_ROOT / "custom_components" / "blueprint_toolkit" / "helpers.py"
)

sys.path.insert(0, str(REPO_ROOT))

from conftest import CodeQualityBase  # noqa: E402

from custom_components.blueprint_toolkit.helpers import (  # noqa: E402
    PersistentNotification,
    format_notification,
    format_timestamp,
    matches_pattern,
    notification_prefix,
    prepare_notifications,
    resolve_target_integrations,
    slugify,
)

T0 = datetime(2024, 1, 15, 12, 0, 0)


class TestFormatTimestamp:
    def test_full_format(self) -> None:
        dt = datetime(2024, 3, 5, 14, 7, 9)
        result = format_timestamp(
            "YYYY-MM-DD HH:mm:ss",
            dt,
        )
        assert result == "2024-03-05 14:07:09"

    def test_short_year(self) -> None:
        dt = datetime(2024, 1, 1, 0, 0, 0)
        assert format_timestamp("YY", dt) == "24"

    def test_empty_template(self) -> None:
        assert format_timestamp("", T0) == ""

    def test_no_tokens(self) -> None:
        assert format_timestamp("no tokens here", T0) == "no tokens here"

    def test_prefix_with_tokens(self) -> None:
        dt = datetime(2024, 6, 15, 8, 30, 0)
        result = format_timestamp("Log at HH:mm - ", dt)
        assert result == "Log at 08:30 - "


class TestFormatNotification:
    def test_prefix_and_suffix(self) -> None:
        dt = datetime(2024, 6, 15, 8, 30, 0)
        result = format_notification(
            "Fan on.",
            "PRE: ",
            " at HH:mm",
            dt,
        )
        assert result == "PRE: Fan on. at 08:30"

    def test_empty_prefix_suffix(self) -> None:
        result = format_notification("hello", "", "", T0)
        assert result == "hello"

    def test_timestamp_tokens_in_both(self) -> None:
        dt = datetime(2024, 1, 2, 3, 4, 5)
        result = format_notification(
            "msg",
            "YYYY-MM-DD ",
            " HH:mm:ss",
            dt,
        )
        assert result == "2024-01-02 msg 03:04:05"


class TestMatchesPattern:
    def test_empty_pattern_no_match(self) -> None:
        assert matches_pattern("anything", "") is False

    def test_simple_match(self) -> None:
        assert matches_pattern("sensor.temp", "temp")

    def test_case_insensitive(self) -> None:
        assert matches_pattern("Sensor.Temp", "sensor")

    def test_regex_pattern(self) -> None:
        assert matches_pattern(
            "sensor.outdoor_temp",
            r"outdoor.*temp",
        )

    def test_no_match(self) -> None:
        assert not matches_pattern("sensor.temp", "humid")

    def test_invalid_regex_no_crash(self) -> None:
        assert not matches_pattern("test", "[invalid")


class TestNotificationPrefix:
    def test_format(self) -> None:
        assert (
            notification_prefix("device_watchdog", "automation.dw_main")
            == "blueprint_toolkit_device_watchdog__automation.dw_main__"
        )


class TestResolveTargetIntegrations:
    def test_empty_include_means_all(self) -> None:
        assert resolve_target_integrations(
            ["zwave_js", "shelly", "rachio"], [], []
        ) == {"zwave_js", "shelly", "rachio"}

    def test_include_narrows(self) -> None:
        assert resolve_target_integrations(
            ["zwave_js", "shelly", "rachio"], ["zwave_js"], []
        ) == {"zwave_js"}

    def test_exclude_subtracts(self) -> None:
        assert resolve_target_integrations(
            ["zwave_js", "shelly", "rachio"], [], ["shelly"]
        ) == {"zwave_js", "rachio"}

    def test_exclude_overrides_include(self) -> None:
        # Belt-and-suspenders: a value in both lists is
        # excluded.
        assert resolve_target_integrations(
            ["zwave_js", "shelly"], ["zwave_js", "shelly"], ["shelly"]
        ) == {"zwave_js"}

    def test_exclude_unknown_no_crash(self) -> None:
        # Excluding something not in the all-set is a
        # no-op (set.discard, not set.remove).
        assert resolve_target_integrations(
            ["zwave_js"], [], ["never_installed"]
        ) == {"zwave_js"}


class TestSlugify:
    def test_basic_ascii(self) -> None:
        assert slugify("Foo Bar") == "foo_bar"

    def test_already_slug(self) -> None:
        assert slugify("foo_bar") == "foo_bar"

    def test_empty(self) -> None:
        assert slugify("") == ""

    def test_only_separators(self) -> None:
        # Non-empty input that collapses to an empty slug
        # falls back to "unknown", matching HA.
        assert slugify("   ") == "unknown"
        assert slugify("---") == "unknown"

    def test_non_ascii_only_fallback(self) -> None:
        # Emoji-only / non-decomposable-only input collapses
        # to empty after NFKD + ASCII drop, so the HA
        # "unknown" fallback kicks in.
        assert slugify("🌟") == "unknown"

    def test_collapses_runs(self) -> None:
        assert slugify("foo   bar") == "foo_bar"
        assert slugify("foo---bar") == "foo_bar"
        assert slugify("foo.!@#bar") == "foo_bar"

    def test_strips_leading_trailing(self) -> None:
        assert slugify("  foo bar  ") == "foo_bar"
        assert slugify("!foo_bar!") == "foo_bar"

    def test_diacritics(self) -> None:
        assert slugify("Café") == "cafe"
        assert slugify("Zürich") == "zurich"
        assert slugify("naïve") == "naive"

    def test_apostrophes(self) -> None:
        assert slugify("Bob's House") == "bob_s_house"

    def test_colons_and_punctuation(self) -> None:
        assert (
            slugify(
                "Auto-On: Sunset Lights: Sunset to 8pm",
            )
            == "auto_on_sunset_lights_sunset_to_8pm"
        )

    def test_digits_preserved(self) -> None:
        assert slugify("Indoor PM2.5") == "indoor_pm2_5"
        assert slugify("10am") == "10am"

    def test_non_ascii_dropped(self) -> None:
        # Emoji and non-decomposable non-ASCII get dropped
        # entirely, not replaced with separators.
        assert slugify("Living Room 🌟") == "living_room"


class _FakeResult:
    """Minimal CappableResult implementation for cap tests.

    Implements the structural attributes the helper's
    ``CappableResult`` protocol requires:
    ``has_issue``, ``notification_id``,
    ``notification_title``, and ``to_notification``.
    """

    def __init__(
        self,
        notification_id: str,
        has_issue: bool,
        notification_title: str = "",
        message: str = "msg",
    ) -> None:
        self.notification_id = notification_id
        self.has_issue = has_issue
        self.notification_title = notification_title
        self.message = message

    def to_notification(
        self,
        suppress: bool = False,
    ) -> PersistentNotification:
        return PersistentNotification(
            active=self.has_issue and not suppress,
            notification_id=self.notification_id,
            title=self.notification_title,
            message=self.message,
        )


def _find_by_id(
    notifs: list[PersistentNotification],
    nid: str,
) -> PersistentNotification:
    for n in notifs:
        if n.notification_id == nid:
            return n
    raise KeyError(nid)


class TestPrepareNotifications:
    """Cover the shared helper used by every watchdog."""

    def test_no_results_emits_only_inactive_summary(self) -> None:
        notifs = prepare_notifications(
            [],
            max_notifications=0,
            cap_notification_id="wd_cap",
            cap_title="cap",
            cap_item_label="items",
        )
        assert len(notifs) == 1
        assert notifs[0].notification_id == "wd_cap"
        assert notifs[0].active is False

    def test_unlimited_cap_emits_all_and_inactive_summary(
        self,
    ) -> None:
        results = [
            _FakeResult("r1", has_issue=True, notification_title="A"),
            _FakeResult("r2", has_issue=True, notification_title="B"),
            _FakeResult("r3", has_issue=False),
        ]
        notifs = prepare_notifications(
            results,
            max_notifications=0,
            cap_notification_id="wd_cap",
            cap_title="cap",
            cap_item_label="items",
        )
        assert len(notifs) == 4
        assert _find_by_id(notifs, "r1").active is True
        assert _find_by_id(notifs, "r2").active is True
        assert _find_by_id(notifs, "r3").active is False
        assert _find_by_id(notifs, "wd_cap").active is False

    def test_cap_not_exceeded_emits_all_and_inactive_summary(
        self,
    ) -> None:
        results = [
            _FakeResult("r1", has_issue=True, notification_title="A"),
            _FakeResult("r2", has_issue=True, notification_title="B"),
        ]
        notifs = prepare_notifications(
            results,
            max_notifications=5,
            cap_notification_id="wd_cap",
            cap_title="cap",
            cap_item_label="items",
        )
        assert len(notifs) == 3
        assert _find_by_id(notifs, "r1").active is True
        assert _find_by_id(notifs, "r2").active is True
        assert _find_by_id(notifs, "wd_cap").active is False

    def test_cap_exceeded_suppresses_overflow_and_emits_summary(
        self,
    ) -> None:
        # Titles "A" through "D" so the sort-before-cap
        # behavior is deterministic: the first two alpha
        # by title are r_a/r_b, suppressed are r_c/r_d.
        results = [
            _FakeResult(
                "r_a",
                has_issue=True,
                notification_title="A",
            ),
            _FakeResult(
                "r_b",
                has_issue=True,
                notification_title="B",
            ),
            _FakeResult(
                "r_c",
                has_issue=True,
                notification_title="C",
            ),
            _FakeResult(
                "r_d",
                has_issue=True,
                notification_title="D",
            ),
            _FakeResult("r_clean", has_issue=False),
        ]
        notifs = prepare_notifications(
            results,
            max_notifications=2,
            cap_notification_id="wd_cap",
            cap_title="Watchdog: cap reached",
            cap_item_label="devices with issues",
        )
        # 5 results + 1 cap summary = 6 notifications total
        assert len(notifs) == 6
        # First two issues (by sorted title) shown
        assert _find_by_id(notifs, "r_a").active is True
        assert _find_by_id(notifs, "r_b").active is True
        # Remaining two issues suppressed (inactive,
        # dismissed by ID on next process)
        assert _find_by_id(notifs, "r_c").active is False
        assert _find_by_id(notifs, "r_d").active is False
        # Clean result still emits an inactive
        # notification so any prior "active" instance
        # is dismissed
        assert _find_by_id(notifs, "r_clean").active is False
        # Cap summary is active and contains counts +
        # the supplied label + remediation hint
        summary = _find_by_id(notifs, "wd_cap")
        assert summary.active is True
        assert summary.title == "Watchdog: cap reached"
        assert "Showing 2 of 4 devices with issues" in summary.message
        assert "2 additional notifications were suppressed" in summary.message
        assert "Increase the notification cap" in summary.message

    def test_cap_exactly_at_threshold_does_not_suppress(
        self,
    ) -> None:
        results = [
            _FakeResult("r1", has_issue=True, notification_title="A"),
            _FakeResult("r2", has_issue=True, notification_title="B"),
        ]
        notifs = prepare_notifications(
            results,
            max_notifications=2,
            cap_notification_id="wd_cap",
            cap_title="cap",
            cap_item_label="items",
        )
        assert _find_by_id(notifs, "r1").active is True
        assert _find_by_id(notifs, "r2").active is True
        assert _find_by_id(notifs, "wd_cap").active is False

    def test_clean_results_never_count_against_cap(self) -> None:
        # Many clean results, one issue: cap=1 should not
        # trigger the "cap exceeded" branch.
        results = [
            _FakeResult("clean1", has_issue=False),
            _FakeResult("clean2", has_issue=False),
            _FakeResult("clean3", has_issue=False),
            _FakeResult(
                "issue1",
                has_issue=True,
                notification_title="I",
            ),
        ]
        notifs = prepare_notifications(
            results,
            max_notifications=1,
            cap_notification_id="wd_cap",
            cap_title="cap",
            cap_item_label="items",
        )
        assert _find_by_id(notifs, "issue1").active is True
        assert _find_by_id(notifs, "wd_cap").active is False

    def test_sort_determines_cap_shown_subset(self) -> None:
        # Unsorted input. The helper must sort by
        # (notification_title, notification_id) before
        # applying the cap so the shown subset is
        # deterministic.
        results = [
            _FakeResult(
                "id_zebra",
                has_issue=True,
                notification_title="Zebra",
            ),
            _FakeResult(
                "id_alpha",
                has_issue=True,
                notification_title="Alpha",
            ),
            _FakeResult(
                "id_mango",
                has_issue=True,
                notification_title="Mango",
            ),
        ]
        notifs = prepare_notifications(
            results,
            max_notifications=1,
            cap_notification_id="wd_cap",
            cap_title="cap",
            cap_item_label="items",
        )
        # "Alpha" wins the cap race (sorted first).
        assert _find_by_id(notifs, "id_alpha").active is True
        assert _find_by_id(notifs, "id_mango").active is False
        assert _find_by_id(notifs, "id_zebra").active is False

    def test_title_tiebreaks_on_notification_id(self) -> None:
        # Two results with identical titles -- the
        # secondary sort key (notification_id) decides
        # which is kept when the cap is exceeded.
        results = [
            _FakeResult(
                "id_b",
                has_issue=True,
                notification_title="Same",
            ),
            _FakeResult(
                "id_a",
                has_issue=True,
                notification_title="Same",
            ),
        ]
        notifs = prepare_notifications(
            results,
            max_notifications=1,
            cap_notification_id="wd_cap",
            cap_title="cap",
            cap_item_label="items",
        )
        # id_a sorts before id_b, so it's shown.
        assert _find_by_id(notifs, "id_a").active is True
        assert _find_by_id(notifs, "id_b").active is False

    def test_sort_does_not_mutate_caller_sequence(self) -> None:
        # Ensure the helper doesn't sort its input
        # argument in place -- downstream code sometimes
        # iterates results again after the helper runs.
        results = [
            _FakeResult(
                "id_b",
                has_issue=True,
                notification_title="B",
            ),
            _FakeResult(
                "id_a",
                has_issue=True,
                notification_title="A",
            ),
        ]
        original_order = [r.notification_id for r in results]
        prepare_notifications(
            results,
            max_notifications=0,
            cap_notification_id="wd_cap",
            cap_title="cap",
            cap_item_label="items",
        )
        assert [r.notification_id for r in results] == original_order


class TestCodeQuality(CodeQualityBase):
    ruff_targets = [
        "custom_components/blueprint_toolkit/helpers.py",
        "tests/test_helpers.py",
    ]
    mypy_targets = [
        "custom_components/blueprint_toolkit/helpers.py",
    ]


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _SCRIPT_PATH, REPO_ROOT)
