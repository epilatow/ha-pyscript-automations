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

_SCRIPT_PATH = REPO_ROOT / "pyscript" / "modules" / "helpers.py"

sys.path.insert(0, str(_SCRIPT_PATH.parent))

from conftest import CodeQualityBase  # noqa: E402
from helpers import (  # noqa: E402
    format_notification,
    format_timestamp,
    matches_pattern,
    on_interval,
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


class TestShouldRun:
    def test_runs_on_interval_boundary(self) -> None:
        t = datetime(2024, 1, 15, 12, 0, 0)
        assert on_interval(60, t) is True

    def test_skips_off_interval(self) -> None:
        t = datetime(2024, 1, 15, 12, 1, 0)
        assert on_interval(60, t) is False

    def test_interval_one_always_runs(self) -> None:
        t = datetime(2024, 1, 15, 12, 37, 0)
        assert on_interval(1, t) is True

    def test_interval_zero_always_runs(self) -> None:
        t = datetime(2024, 1, 15, 12, 37, 0)
        assert on_interval(0, t) is True

    def test_negative_interval_always_runs(self) -> None:
        t = datetime(2024, 1, 15, 12, 37, 0)
        assert on_interval(-5, t) is True


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


class TestCodeQuality(CodeQualityBase):
    ruff_targets = [
        "pyscript/modules/helpers.py",
        "tests/test_helpers.py",
    ]
    mypy_targets = [
        "pyscript/modules/helpers.py",
    ]


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _SCRIPT_PATH, REPO_ROOT)
