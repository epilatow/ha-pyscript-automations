#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["pytest", "pytest-cov", "ruff", "mypy"]
# ///
# This is AI generated code
"""Tests for the standalone zwave_network_info script.

The script bootstraps its own venv for socketio / aiohttp, but
all the pure logic (formatting, parsing, sorting, row assembly)
runs in stdlib Python and is what we test here. Network-facing
fetchers are not unit-tested.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).parent.parent
_SCRIPT_PATH = REPO_ROOT / "scripts" / "zwave_network_info.py"

sys.path.insert(0, str(_SCRIPT_PATH.parent))

import zwave_network_info as zni  # noqa: E402
from conftest import CodeQualityBase  # noqa: E402

# --- _parse_numeric ---------------------------------------------


class TestParseNumeric:
    def test_int(self) -> None:
        assert zni._parse_numeric("42") == 42

    def test_float_non_integer(self) -> None:
        assert zni._parse_numeric("3.5") == 3.5

    def test_float_integer_value_collapses_to_int(self) -> None:
        # "100.0" comes back as 100 (int), not 100.0 (float), so
        # renderers don't show ".0" for whole numbers.
        v = zni._parse_numeric("100.0")
        assert v == 100
        assert isinstance(v, int)

    def test_none_and_sentinels(self) -> None:
        for raw in (None, "unavailable", "unknown", ""):
            assert zni._parse_numeric(raw) is None

    def test_garbage(self) -> None:
        assert zni._parse_numeric("not a number") is None


# --- _drop_rate / _fmt_drop_rate --------------------------------


class TestDropRate:
    def test_both_none(self) -> None:
        assert zni._drop_rate(None, None) is None

    def test_no_activity_is_none(self) -> None:
        # 0 success + 0 drops is a meaningless rate, not 0%.
        assert zni._drop_rate(0, 0) is None

    def test_all_success(self) -> None:
        assert zni._drop_rate(100, 0) == 0.0

    def test_all_drops(self) -> None:
        assert zni._drop_rate(0, 100) == 1.0

    def test_mixed(self) -> None:
        assert zni._drop_rate(90, 10) == pytest.approx(0.1)

    def test_drops_only_counter_present(self) -> None:
        # When only the drops counter came back, treat the missing
        # success counter as 0 rather than None -- the rate is
        # still computable.
        assert zni._drop_rate(None, 5) == 1.0

    def test_success_only_counter_present(self) -> None:
        assert zni._drop_rate(5, None) == 0.0

    def test_non_numeric_rejected(self) -> None:
        # Strings shouldn't creep into the arithmetic; treated
        # like None.
        assert zni._drop_rate("5", "3") is None

    def test_explicit_zero_counter_not_confused_with_none(self) -> None:
        # This is the bug the refactor fixed: 0 must not get
        # coerced to None by a truthiness check. We verify by
        # driving a rate computation that requires distinguishing
        # between "success=0, drops=5" (1.0) and "no data" (None).
        assert zni._drop_rate(0, 5) == 1.0
        assert zni._drop_rate(5, 0) == 0.0


class TestFmtDropRate:
    def test_none(self) -> None:
        assert zni._fmt_drop_rate(None, use_color=False) == zni.DASH

    def test_zero(self) -> None:
        assert zni._fmt_drop_rate(0.0, use_color=False) == "0"

    def test_sub_one_percent(self) -> None:
        # Non-zero rate that would round to 0 shows as "<1".
        assert zni._fmt_drop_rate(0.001, use_color=False) == "<1"

    def test_rounded_int_percent(self) -> None:
        assert zni._fmt_drop_rate(0.03, use_color=False) == "3"
        assert zni._fmt_drop_rate(0.10, use_color=False) == "10"

    def test_color_thresholds(self) -> None:
        # green < 1%, yellow 1-5%, red > 5%.
        assert zni.ANSI_GREEN in zni._fmt_drop_rate(0.005, use_color=True)
        assert zni.ANSI_YELLOW in zni._fmt_drop_rate(0.03, use_color=True)
        assert zni.ANSI_RED in zni._fmt_drop_rate(0.20, use_color=True)


# --- _ss_quality / _ss_color ------------------------------------


class TestSsQuality:
    def test_mesh_buckets(self) -> None:
        assert zni._ss_quality(-60, "Mesh") == "good"
        assert zni._ss_quality(-75, "Mesh") == "fair"
        assert zni._ss_quality(-90, "Mesh") == "poor"

    def test_lr_buckets(self) -> None:
        # LR thresholds are ~15 dB weaker than Mesh. A value
        # that's "poor" on Mesh is often still "fair" on LR.
        assert zni._ss_quality(-90, "LR") == "fair"
        assert zni._ss_quality(-70, "LR") == "good"
        assert zni._ss_quality(-105, "LR") == "poor"

    def test_none_input(self) -> None:
        assert zni._ss_quality(None, "Mesh") is None

    def test_unknown_protocol_falls_back_to_mesh(self) -> None:
        assert zni._ss_quality(-60, None) == "good"
        assert zni._ss_quality(-90, "unknown") == "poor"

    def test_boundary_values(self) -> None:
        # Boundaries: "> good_above" is good; "> fair_above" is
        # fair; otherwise poor. Exactly at the boundary lands in
        # the weaker bucket.
        assert zni._ss_quality(-70, "Mesh") == "fair"
        assert zni._ss_quality(-85, "Mesh") == "poor"


class TestSsColor:
    def test_color_per_bucket(self) -> None:
        assert zni._ss_color(-60, "Mesh") == zni.ANSI_GREEN
        assert zni._ss_color(-75, "Mesh") == zni.ANSI_YELLOW
        assert zni._ss_color(-90, "Mesh") == zni.ANSI_RED

    def test_none_input(self) -> None:
        assert zni._ss_color(None, "Mesh") == ""


# --- _fmt_last_seen ---------------------------------------------


class TestFmtLastSeen:
    def test_none(self) -> None:
        assert zni._fmt_last_seen(None) == zni.DASH

    def test_unparseable(self) -> None:
        assert zni._fmt_last_seen("this is not a timestamp") == zni.DASH

    def test_naive_timestamp_assumed_utc(self) -> None:
        # Naive (no tz) input should be treated as UTC, not as
        # local time (which would give variable results per host).
        t = datetime.now(UTC) - timedelta(minutes=5)
        assert zni._fmt_last_seen(t.replace(tzinfo=None).isoformat()) == "5m"

    def test_zulu_suffix(self) -> None:
        # Python 3.11+ parses "Z" natively; the script's old
        # .replace("Z", "+00:00") hack was dead code, but we
        # want to make sure the native path still works.
        t = datetime.now(UTC) - timedelta(hours=2)
        stamp = t.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
        assert zni._fmt_last_seen(stamp) == "2h"

    def test_tier_transitions(self) -> None:
        now = datetime.now(UTC)
        cases = [
            (timedelta(seconds=0), "now"),
            (timedelta(seconds=30), "30s"),
            (timedelta(minutes=5), "5m"),
            (timedelta(hours=3), "3h"),
            (timedelta(days=2), "2d"),
            (timedelta(days=120), "4mo"),
        ]
        for delta, expected in cases:
            stamp = (now - delta).isoformat()
            assert zni._fmt_last_seen(stamp) == expected, expected

    def test_future_clamped_to_now(self) -> None:
        # Clock skew / HA recording a state slightly in the
        # future shouldn't surface as a negative age.
        future = (datetime.now(UTC) + timedelta(seconds=30)).isoformat()
        assert zni._fmt_last_seen(future) == "now"


# --- _fmt_update ------------------------------------------------


class TestFmtUpdate:
    def test_no_updates(self) -> None:
        assert zni._fmt_update(None) == "no"
        assert zni._fmt_update([]) == "no"

    def test_with_version(self) -> None:
        assert zni._fmt_update([{"version": "7.21.0"}]) == "yes (v7.21.0)"

    def test_without_version(self) -> None:
        assert zni._fmt_update([{"other_field": "x"}]) == "yes"

    def test_non_list(self) -> None:
        assert zni._fmt_update("garbage") == "no"


# --- _fmt_num / _fmt_battery / _fmt_version / _fmt_neighbors ---


class TestFmtNum:
    def test_none(self) -> None:
        assert zni._fmt_num(None) == zni.DASH

    def test_int(self) -> None:
        assert zni._fmt_num(42) == "42"

    def test_float_integer_value(self) -> None:
        assert zni._fmt_num(42.0) == "42"

    def test_float_fractional(self) -> None:
        assert zni._fmt_num(3.15) == "3.1"


class TestFmtBattery:
    def test_none(self) -> None:
        assert zni._fmt_battery(None) == zni.DASH

    def test_int(self) -> None:
        assert zni._fmt_battery(75) == "75"

    def test_float_truncates(self) -> None:
        assert zni._fmt_battery(75.9) == "75"


class TestFmtVersion:
    def test_none(self) -> None:
        assert zni._fmt_version(None) == zni.DASH

    def test_empty(self) -> None:
        assert zni._fmt_version("") == zni.DASH

    def test_value(self) -> None:
        assert zni._fmt_version("1.2.3") == "v1.2.3"


class TestFmtNeighbors:
    def test_none(self) -> None:
        assert zni._fmt_neighbors(None) == zni.DASH

    def test_empty(self) -> None:
        assert zni._fmt_neighbors([]) == "none"

    def test_list(self) -> None:
        assert zni._fmt_neighbors([1, 17, 50]) == "1 17 50"


class TestFmtRole:
    def test_none(self) -> None:
        assert zni._fmt_role(None) == zni.DASH

    def test_known(self) -> None:
        assert zni._fmt_role(5) == "AlwaysOn"

    def test_unknown(self) -> None:
        assert zni._fmt_role(99) == "?99"


class TestFmtRouteSpeed:
    def test_none(self) -> None:
        assert zni._fmt_route_speed(None) == zni.DASH

    def test_known(self) -> None:
        assert zni._fmt_route_speed(3) == "100k"
        assert zni._fmt_route_speed(4) == "LR"

    def test_unknown(self) -> None:
        assert zni._fmt_route_speed(99) == "?99"


class TestFmtMaxSpeed:
    def test_none(self) -> None:
        assert zni._fmt_max_speed(None) == zni.DASH

    def test_known(self) -> None:
        assert zni._fmt_max_speed(100000) == "100k"

    def test_unknown(self) -> None:
        assert zni._fmt_max_speed(42000) == "?42000"


# --- _visible_len / _pad ----------------------------------------


class TestVisibleLen:
    def test_plain(self) -> None:
        assert zni._visible_len("hello") == 5

    def test_with_ansi(self) -> None:
        s = f"{zni.ANSI_GREEN}hello{zni.ANSI_RESET}"
        assert zni._visible_len(s) == 5

    def test_truncated_ansi_terminates(self) -> None:
        # Malformed/truncated escape shouldn't scan past the end
        # of string or hang; the exact fallback count isn't
        # important (alignment code only cares that it returns).
        result = zni._visible_len("\033[3")
        assert isinstance(result, int)
        assert result >= 0


class TestPad:
    def test_left_align(self) -> None:
        assert zni._pad("hi", 5, right_align=False) == "hi   "

    def test_right_align(self) -> None:
        assert zni._pad("hi", 5, right_align=True) == "   hi"

    def test_overflow(self) -> None:
        assert zni._pad("abcdef", 3, right_align=False) == "abcdef"

    def test_visible_len_used_for_padding(self) -> None:
        # A colored 5-char string should pad to width 5, not
        # width-including-escape-codes.
        colored = f"{zni.ANSI_GREEN}hello{zni.ANSI_RESET}"
        padded = zni._pad(colored, 7, right_align=False)
        assert padded.endswith("  ")
        assert zni._visible_len(padded) == 7


# --- _parse_columns / _parse_sort -------------------------------


class TestParseColumns:
    def test_empty_returns_defaults(self) -> None:
        assert zni._parse_columns("") == zni._COLUMN_ALIASES["defaults"]

    def test_all(self) -> None:
        assert zni._parse_columns("all") == zni.ALL_COLUMNS

    def test_alias_expansion(self) -> None:
        assert zni._parse_columns("firmware") == zni._COLUMN_ALIASES["firmware"]

    def test_mixed_alias_and_columns(self) -> None:
        # Aliases expand in place; duplicates are dropped.
        out = zni._parse_columns("node,firmware,device")
        assert out == ["node", "firmware-dev", "firmware-sdk", "device"]

    def test_unknown_column_raises(self) -> None:
        with pytest.raises(SystemExit):
            zni._parse_columns("not-a-real-column")

    def test_whitespace_and_case(self) -> None:
        assert zni._parse_columns("  Node , Device ") == ["node", "device"]

    def test_singular_default_alias_works(self) -> None:
        # 'default' is a back-compat singular synonym of 'defaults'.
        assert zni._parse_columns("default") == zni._COLUMN_ALIASES["defaults"]


class TestParseSort:
    def test_default(self) -> None:
        assert zni._parse_sort("node") == ["node"]

    def test_multi(self) -> None:
        assert zni._parse_sort("location,device") == ["location", "device"]

    def test_dedup(self) -> None:
        assert zni._parse_sort("location,location") == ["location"]

    def test_unknown_raises(self) -> None:
        with pytest.raises(SystemExit):
            zni._parse_sort("not-a-real-column")

    def test_alias_rejected(self) -> None:
        # --sort accepts real column names only, not aliases.
        with pytest.raises(SystemExit):
            zni._parse_sort("firmware")

    def test_empty_falls_back_to_node(self) -> None:
        assert zni._parse_sort("") == ["node"]
        assert zni._parse_sort(" , ") == ["node"]


# --- _route_label -----------------------------------------------


class TestRouteLabel:
    def test_none(self) -> None:
        assert zni._route_label(None, {}) == zni.DASH

    def test_no_repeaters(self) -> None:
        assert zni._route_label({"repeaters": []}, {}) == zni.DASH

    def test_repeater_resolves_to_device_name(self) -> None:
        node_to_ha = {
            50: {"device_name": "Hallway Switch", "stat_entities": {}},
        }
        assert (
            zni._route_label({"repeaters": [50]}, node_to_ha)
            == "Hallway Switch"
        )

    def test_repeater_falls_back_to_node_label(self) -> None:
        # Unknown node id -> "node N".
        assert zni._route_label({"repeaters": [99]}, {}) == "node 99"

    def test_multiple_hops_shows_count(self) -> None:
        node_to_ha = {
            50: {"device_name": "Repeater A", "stat_entities": {}},
        }
        assert (
            zni._route_label({"repeaters": [50, 17, 13]}, node_to_ha)
            == "Repeater A (+2)"
        )


# --- _state_at_or_before ----------------------------------------


def _hist(times: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """Build a minimal HA history payload.

    Each ``(timestamp, state)`` pair becomes a row. Sorted
    chronologically as HA returns them.
    """
    return [{"last_changed": t, "state": s} for t, s in times]


class TestStateAtOrBefore:
    def test_empty_history(self) -> None:
        assert zni._state_at_or_before([], datetime.now(UTC)) is None

    def test_returns_last_state_before_target(self) -> None:
        hist = _hist(
            [
                ("2026-04-01T00:00:00+00:00", "-70"),
                ("2026-04-05T00:00:00+00:00", "-80"),
                ("2026-04-10T00:00:00+00:00", "-90"),
            ],
        )
        target = datetime(2026, 4, 7, tzinfo=UTC)
        assert zni._state_at_or_before(hist, target) == "-80"

    def test_target_before_any_reading(self) -> None:
        hist = _hist([("2026-04-10T00:00:00+00:00", "-70")])
        target = datetime(2026, 4, 1, tzinfo=UTC)
        assert zni._state_at_or_before(hist, target) is None

    def test_skips_unavailable_states(self) -> None:
        hist = _hist(
            [
                ("2026-04-01T00:00:00+00:00", "-70"),
                ("2026-04-02T00:00:00+00:00", "unavailable"),
                ("2026-04-03T00:00:00+00:00", "unknown"),
            ],
        )
        target = datetime(2026, 4, 5, tzinfo=UTC)
        assert zni._state_at_or_before(hist, target) == "-70"

    def test_zulu_timestamp_parses(self) -> None:
        hist = _hist([("2026-04-01T00:00:00Z", "-70")])
        target = datetime(2026, 4, 5, tzinfo=UTC)
        assert zni._state_at_or_before(hist, target) == "-70"

    def test_malformed_timestamp_skipped(self) -> None:
        hist = [
            {"last_changed": "not-a-timestamp", "state": "-70"},
            {"last_changed": "2026-04-01T00:00:00+00:00", "state": "-75"},
        ]
        target = datetime(2026, 4, 5, tzinfo=UTC)
        assert zni._state_at_or_before(hist, target) == "-75"

    def test_last_updated_fallback(self) -> None:
        hist = [
            {"last_updated": "2026-04-01T00:00:00+00:00", "state": "-70"},
        ]
        target = datetime(2026, 4, 5, tzinfo=UTC)
        assert zni._state_at_or_before(hist, target) == "-70"


# --- _read_recorder_keep_days ----------------------------------


class TestReadRecorderKeepDays:
    def test_missing_file(self, tmp_path: Path) -> None:
        with patch.object(
            zni,
            "HA_CONFIG_YAML",
            tmp_path / "does-not-exist.yaml",
        ):
            assert zni._read_recorder_keep_days() == 10

    def test_inline_block(self, tmp_path: Path) -> None:
        cfg = tmp_path / "configuration.yaml"
        cfg.write_text(
            "default_config:\n"
            "\n"
            "recorder:\n"
            "  purge_keep_days: 30\n"
            "  commit_interval: 5\n",
        )
        with patch.object(zni, "HA_CONFIG_YAML", cfg):
            assert zni._read_recorder_keep_days() == 30

    def test_include_file(self, tmp_path: Path) -> None:
        cfg = tmp_path / "configuration.yaml"
        included = tmp_path / "recorder.yaml"
        cfg.write_text("recorder: !include recorder.yaml\n")
        included.write_text("purge_keep_days: 45\ncommit_interval: 5\n")
        with patch.object(zni, "HA_CONFIG_YAML", cfg):
            assert zni._read_recorder_keep_days() == 45

    def test_include_dir_merge_named_falls_back_to_default(
        self,
        tmp_path: Path,
    ) -> None:
        # Documented limitation: !include_dir_* isn't parsed, so
        # retention silently falls back to the built-in default.
        # That's the safe direction (under-estimates rather than
        # over-estimates retention).
        cfg = tmp_path / "configuration.yaml"
        cfg.write_text("recorder: !include_dir_merge_named recorder_d\n")
        with patch.object(zni, "HA_CONFIG_YAML", cfg):
            assert zni._read_recorder_keep_days() == 10

    def test_no_recorder_key(self, tmp_path: Path) -> None:
        cfg = tmp_path / "configuration.yaml"
        cfg.write_text("default_config:\n")
        with patch.object(zni, "HA_CONFIG_YAML", cfg):
            assert zni._read_recorder_keep_days() == 10

    def test_recorder_without_purge_keep_days(self, tmp_path: Path) -> None:
        cfg = tmp_path / "configuration.yaml"
        cfg.write_text("recorder:\n  commit_interval: 5\n")
        with patch.object(zni, "HA_CONFIG_YAML", cfg):
            assert zni._read_recorder_keep_days() == 10


# --- bootstrap_venv --------------------------------------------


def _fake_venv_creator(venv_dir: Path) -> Any:
    """Build a fake ``subprocess.run`` that simulates
    ``python -m venv`` by materialising ``bin/python``.
    """

    def fake_run(*popenargs: Any, **kwargs: Any) -> None:
        args = popenargs[0] if popenargs else kwargs.get("args")
        if isinstance(args, list) and "venv" in args:
            (venv_dir / "bin").mkdir(parents=True, exist_ok=True)
            (venv_dir / "bin" / "python").touch()

    return fake_run


class TestBootstrapVenv:
    """Verify the stamp-file gate.

    The stamp distinguishes a complete install from a partial
    one (venv created but pip install failed mid-way). Without
    it, a partial venv is mistaken for a working one and the
    script later crashes importing ``socketio`` inside it.
    """

    def test_fresh_install_creates_venv_and_writes_stamp(
        self,
        tmp_path: Path,
    ) -> None:
        venv_dir = tmp_path / ".venv"
        stamp = venv_dir / ".installed"
        with (
            patch.object(zni, "VENV_DIR", venv_dir),
            patch.object(zni, "VENV_STAMP", stamp),
            patch.object(
                zni.subprocess,
                "run",
                autospec=True,
                side_effect=_fake_venv_creator(venv_dir),
            ) as mock_run,
        ):
            zni.bootstrap_venv()
        # venv create + pip install.
        assert mock_run.call_count == 2
        assert stamp.exists()

    def test_partial_venv_reinstalls_and_stamps(
        self,
        tmp_path: Path,
    ) -> None:
        # vpy exists but no stamp -> pip install must re-run.
        # This is the bug the stamp was added to fix: without it,
        # a half-built venv gets treated as ready and the script
        # crashes on the first ``import socketio``.
        venv_dir = tmp_path / ".venv"
        (venv_dir / "bin").mkdir(parents=True)
        (venv_dir / "bin" / "python").touch()
        stamp = venv_dir / ".installed"
        with (
            patch.object(zni, "VENV_DIR", venv_dir),
            patch.object(zni, "VENV_STAMP", stamp),
            patch.object(zni.subprocess, "run", autospec=True) as mock_run,
        ):
            zni.bootstrap_venv()
        # Only pip install (no venv re-create).
        assert mock_run.call_count == 1
        cmd = mock_run.call_args[0][0]
        assert cmd[0].endswith("/pip")
        assert stamp.exists()

    def test_stamp_present_is_noop(self, tmp_path: Path) -> None:
        venv_dir = tmp_path / ".venv"
        (venv_dir / "bin").mkdir(parents=True)
        (venv_dir / "bin" / "python").touch()
        stamp = venv_dir / ".installed"
        stamp.touch()
        with (
            patch.object(zni, "VENV_DIR", venv_dir),
            patch.object(zni, "VENV_STAMP", stamp),
            patch.object(zni.subprocess, "run", autospec=True) as mock_run,
        ):
            zni.bootstrap_venv()
        mock_run.assert_not_called()


# --- Row / ALL_COLUMNS consistency -----------------------------


class TestRowSchema:
    def test_row_fields_match_all_columns(self) -> None:
        # The import-time sanity check in zwave_network_info.py
        # already enforces this, but a test gives a clearer
        # failure message if someone adds a column without a
        # matching Row field.
        from dataclasses import fields

        row_fields = {f.name for f in fields(zni.Row)}
        expected = {zni._row_attr(c) for c in zni.ALL_COLUMNS}
        assert row_fields == expected

    def test_row_attr_maps_dashes(self) -> None:
        assert zni._row_attr("rx-drop-rate") == "rx_drop_rate"
        assert zni._row_attr("node") == "node"


# --- build_node_to_ha -------------------------------------------


class TestBuildNodeToHa:
    def test_maps_zwave_device_to_node_id(self) -> None:
        devices = {
            "dev_A": {
                "id": "dev_A",
                "name": "Hallway Lock",
                "name_by_user": None,
                "area_id": "area_hallway",
                "identifiers": [["zwave_js", "1234-18-0"]],
            },
        }
        areas = {"area_hallway": {"area_id": "area_hallway", "name": "Hall"}}
        entities = [
            {
                "entity_id": "sensor.hallway_lock_signal_strength",
                "device_id": "dev_A",
                "platform": "zwave_js",
            },
            {
                "entity_id": "sensor.hallway_lock_battery_level",
                "device_id": "dev_A",
                "platform": "zwave_js",
            },
            # Non-zwave_js platform should be ignored.
            {
                "entity_id": "sensor.hallway_lock_humidity",
                "device_id": "dev_A",
                "platform": "other",
            },
        ]
        out = zni.build_node_to_ha(devices, areas, entities)
        assert 18 in out
        info = out[18]
        assert info["device_name"] == "Hallway Lock"
        assert info["area_name"] == "Hall"
        assert info["stat_entities"]["ss"] == (
            "sensor.hallway_lock_signal_strength"
        )
        assert info["stat_entities"]["battery"] == (
            "sensor.hallway_lock_battery_level"
        )

    def test_prefers_name_by_user(self) -> None:
        devices = {
            "dev_A": {
                "id": "dev_A",
                "name": "Raw Device Name",
                "name_by_user": "User Friendly",
                "identifiers": [["zwave_js", "1234-7-0"]],
            },
        }
        out = zni.build_node_to_ha(devices, {}, [])
        assert out[7]["device_name"] == "User Friendly"

    def test_skips_non_zwave_identifiers(self) -> None:
        devices = {
            "dev_A": {
                "id": "dev_A",
                "name": "Something",
                "identifiers": [["matter", "foo"]],
            },
        }
        out = zni.build_node_to_ha(devices, {}, [])
        assert out == {}

    def test_handles_unparseable_identifier(self) -> None:
        devices = {
            "dev_A": {
                "id": "dev_A",
                "name": "Something",
                "identifiers": [["zwave_js", "malformed"]],
            },
        }
        out = zni.build_node_to_ha(devices, {}, [])
        assert out == {}


# --- build_rows -------------------------------------------------


def _make_stat_entities(
    node_name: str,
    cols: list[str],
) -> dict[str, str]:
    return {col: f"sensor.{node_name}_{zni.ENTITY_SUFFIX[col]}" for col in cols}


class TestBuildRows:
    def test_skips_controller(self) -> None:
        zwave_nodes = {
            1: {"id": 1, "protocol": 0, "isListening": True},
            18: {
                "id": 18,
                "protocol": 0,
                "isListening": True,
                "statistics": {},
            },
        }
        rows = zni.build_rows(
            zwave_nodes,
            {},
            {},
            {},
            day_offsets=[],
        )
        assert [r.node for r in rows] == [18]

    def test_basic_row_population(self) -> None:
        zwave_nodes = {
            18: {
                "id": 18,
                "protocol": 1,  # LR
                "isListening": True,
                "manufacturer": "Kwikset",
                "productDescription": "HC620",
                "productLabel": "HC620",
                "security": "S2_AccessControl",
                "supportsBeaming": True,
                "firmwareVersion": "7.20.0",
                "sdkVersion": "7.13.8",
                "zwavePlusVersion": 2,
                "interviewStage": "Complete",
                "zwavePlusRoleType": 5,
                "maxDataRate": 100000,
                "statistics": {"lwr": {"protocolDataRate": 3}},
                "availableFirmwareUpdates": [],
            },
        }
        node_to_ha = {
            18: {
                "device_name": "Front Door Lock",
                "area_name": "Entry",
                "stat_entities": _make_stat_entities(
                    "front_door_lock",
                    [
                        "ss",
                        "ss-quality",  # backs onto the ss sensor
                        "rtt",
                        "rx",
                        "tx",
                        "rx-drop",
                        "tx-drop",
                        "timeouts",
                        "battery",
                        "status",
                        "last-seen",
                    ],
                ),
            },
        }
        current_states = {
            "sensor.front_door_lock_signal_strength": "-75",
            "sensor.front_door_lock_round_trip_time": "12.5",
            "sensor.front_door_lock_successful_commands_rx": "100",
            "sensor.front_door_lock_successful_commands_tx": "95",
            "sensor.front_door_lock_commands_dropped_rx": "0",
            "sensor.front_door_lock_commands_dropped_tx": "5",
            "sensor.front_door_lock_timed_out_responses": "2",
            "sensor.front_door_lock_battery_level": "88",
            "sensor.front_door_lock_node_status": "alive",
        }
        rows = zni.build_rows(
            zwave_nodes,
            node_to_ha,
            current_states,
            {},
            day_offsets=[],
        )
        assert len(rows) == 1
        row = rows[0]
        assert row.node == 18
        assert row.device == "Front Door Lock"
        assert row.location == "Entry"
        assert row.protocol == "LR"
        assert row.power == "Mains"
        assert row.manufacturer == "Kwikset"
        assert row.ss == -75
        # -75 on LR is "good" (LR's good_above is -80).
        assert row.ss_quality == "good"
        assert row.rtt == 12.5
        assert row.rx == 100
        assert row.tx == 95
        assert row.rx_drop == 0
        assert row.tx_drop == 5
        # tx_drop_rate = 5 / (5 + 95) = 0.05 = 5%
        assert row.tx_drop_rate == pytest.approx(0.05)
        # rx_drop_rate = 0 / (0 + 100) = 0.0
        assert row.rx_drop_rate == 0.0
        assert row.timeouts == 2
        assert row.battery == 88
        assert row.status == "alive"
        assert row.plus == "v2"
        assert row.role == 5
        assert row.firmware_dev == "7.20.0"
        assert row.firmware_sdk == "7.13.8"
        assert row.max_speed == 100000
        assert row.route_speed == 3
        assert row.update == "no"

    def test_battery_node_shows_battery_power(self) -> None:
        zwave_nodes = {
            18: {
                "id": 18,
                "protocol": 0,
                "isListening": False,
                "statistics": {},
            },
        }
        rows = zni.build_rows(
            zwave_nodes,
            {},
            {},
            {},
            day_offsets=[],
        )
        assert rows[0].power == "Battery"
        assert rows[0].protocol == "Mesh"

    def test_historical_list_shape(self) -> None:
        # --days 2 -> list of 3 values [latest, -1d, -2d].
        zwave_nodes = {
            18: {
                "id": 18,
                "protocol": 0,
                "isListening": True,
                "statistics": {},
            },
        }
        node_to_ha = {
            18: {
                "device_name": "Node 18",
                "area_name": "",
                "stat_entities": {
                    "ss": "sensor.n18_signal_strength",
                    "ss-quality": "sensor.n18_signal_strength",
                },
            },
        }
        history = {
            # -68 is "good" on Mesh (> -70); -80 is "fair" (in
            # the -70..-85 band).
            "sensor.n18_signal_strength": {1: "-68", 2: "-80"},
        }
        current_states = {"sensor.n18_signal_strength": "-65"}
        rows = zni.build_rows(
            zwave_nodes,
            node_to_ha,
            current_states,
            history,
            day_offsets=[1, 2],
        )
        assert rows[0].ss == [-65, -68, -80]
        assert rows[0].ss_quality == ["good", "good", "fair"]

    def test_routes_rendered_via_node_to_ha(self) -> None:
        zwave_nodes = {
            18: {
                "id": 18,
                "protocol": 0,
                "isListening": True,
                "statistics": {},
                "applicationRoute": {"repeaters": [50]},
                "prioritySUCReturnRoute": {"repeaters": [50, 13]},
            },
            50: {
                "id": 50,
                "protocol": 0,
                "isListening": True,
                "statistics": {},
            },
        }
        node_to_ha = {
            50: {
                "device_name": "Hallway",
                "area_name": "",
                "stat_entities": {},
            },
        }
        rows_by_node = {
            r.node: r
            for r in zni.build_rows(
                zwave_nodes,
                node_to_ha,
                {},
                {},
                day_offsets=[],
            )
        }
        assert rows_by_node[18].priority_route == "Hallway"
        assert rows_by_node[18].suc_route == "Hallway (+1)"


# --- _sort_rows -------------------------------------------------


def _mk_row(**overrides: Any) -> zni.Row:
    """Construct a Row with sane defaults, override any field."""
    defaults: dict[str, Any] = {
        "node": 0,
        "device": "",
        "location": "",
        "protocol": "Mesh",
        "priority_route": "-",
        "suc_route": "-",
        "power": "Mains",
        "manufacturer": "",
        "product": "",
        "product_code": "",
        "security": "",
        "beaming": "no",
        "firmware_dev": None,
        "firmware_sdk": None,
        "plus": "no",
        "interview": "",
        "route_speed": None,
        "max_speed": None,
        "role": None,
        "update": "no",
        "neighbors": None,
        "ss": None,
        "ss_quality": None,
        "rtt": None,
        "rx": None,
        "tx": None,
        "rx_drop": None,
        "tx_drop": None,
        "rx_drop_rate": None,
        "tx_drop_rate": None,
        "timeouts": None,
        "battery": None,
        "status": None,
        "last_seen": None,
    }
    defaults.update(overrides)
    return zni.Row(**defaults)


class TestSortRows:
    def test_sort_by_node_ascending(self) -> None:
        rows = [_mk_row(node=n) for n in [18, 2, 50]]
        sorted_rows = zni._sort_rows(rows, ["node"], reverse=False)
        assert [r.node for r in sorted_rows] == [2, 18, 50]

    def test_sort_by_device_case_insensitive(self) -> None:
        rows = [
            _mk_row(node=1, device="Zigbee"),
            _mk_row(node=2, device="apple"),
            _mk_row(node=3, device="Banana"),
        ]
        sorted_rows = zni._sort_rows(rows, ["device"], reverse=False)
        assert [r.device for r in sorted_rows] == ["apple", "Banana", "Zigbee"]

    def test_null_primary_always_last(self) -> None:
        rows = [
            _mk_row(node=1, ss=-70),
            _mk_row(node=2, ss=None),
            _mk_row(node=3, ss=-85),
        ]
        # Ascending: -85, -70, then null last.
        asc = zni._sort_rows(rows, ["ss"], reverse=False)
        assert [r.node for r in asc] == [3, 1, 2]
        # Reverse: -70, -85, null still last (documented
        # invariant).
        desc = zni._sort_rows(rows, ["ss"], reverse=True)
        assert [r.node for r in desc] == [1, 3, 2]

    def test_ss_quality_severity_sort(self) -> None:
        # "--sort ss-quality" should surface poor nodes first.
        rows = [
            _mk_row(node=1, ss_quality="good"),
            _mk_row(node=2, ss_quality="poor"),
            _mk_row(node=3, ss_quality="fair"),
        ]
        out = zni._sort_rows(rows, ["ss-quality"], reverse=False)
        assert [r.node for r in out] == [2, 3, 1]

    def test_historical_list_sort_on_latest(self) -> None:
        rows = [
            _mk_row(node=1, ss=[-70, -75]),
            _mk_row(node=2, ss=[-90, -85]),
            _mk_row(node=3, ss=[-60, -65]),
        ]
        out = zni._sort_rows(rows, ["ss"], reverse=False)
        # Latest values: -60, -70, -90. Ascending sort puts
        # -90 first.
        assert [r.node for r in out] == [2, 1, 3]

    def test_neighbors_sorts_by_count(self) -> None:
        rows = [
            _mk_row(node=1, neighbors=[10, 20]),
            _mk_row(node=2, neighbors=[]),
            _mk_row(node=3, neighbors=[10, 20, 30]),
        ]
        out = zni._sort_rows(rows, ["neighbors"], reverse=False)
        assert [r.node for r in out] == [2, 1, 3]

    def test_secondary_key_tiebreaks(self) -> None:
        rows = [
            _mk_row(node=1, location="Office", device="B"),
            _mk_row(node=2, location="Office", device="A"),
            _mk_row(node=3, location="Hall", device="C"),
        ]
        out = zni._sort_rows(
            rows,
            ["location", "device"],
            reverse=False,
        )
        assert [r.node for r in out] == [3, 2, 1]


# --- _fmt_history_cell -----------------------------------------


class TestFmtHistoryCell:
    def test_scalar_latest_only(self) -> None:
        assert (
            zni._fmt_history_cell(
                -75,
                "ss",
                use_color=False,
                protocol="Mesh",
            )
            == "-75"
        )

    def test_scalar_rate(self) -> None:
        # Scalar rate cell (days=0) shows as percent int.
        out = zni._fmt_history_cell(
            0.03,
            "rx-drop-rate",
            use_color=False,
            protocol="Mesh",
        )
        assert out == "3"

    def test_list_joins_with_spaces(self) -> None:
        out = zni._fmt_history_cell(
            [-70, -75, None],
            "ss",
            use_color=False,
            protocol="Mesh",
        )
        assert out == "-70 -75 -"

    def test_list_position_widths_align(self) -> None:
        # Widths cover each sub-position; the formatter should
        # right-pad each sub-value to match.
        widths = [3, 3, 3]
        out = zni._fmt_history_cell(
            [None, -80, -85],
            "ss",
            use_color=False,
            position_widths=widths,
            protocol="Mesh",
        )
        # Each sub-value becomes 3 chars wide.
        # "  -" "-80" "-85" joined by single spaces.
        assert out == "  - -80 -85"

    def test_ss_quality_list_strings(self) -> None:
        out = zni._fmt_history_cell(
            ["poor", "fair", "good"],
            "ss-quality",
            use_color=False,
            protocol="Mesh",
        )
        assert out == "poor fair good"


# --- render_table -----------------------------------------------


class TestRenderTable:
    def test_minimal_table_has_header_and_divider(self) -> None:
        rows = [_mk_row(node=18, device="Hall Lock")]
        out = zni.render_table(
            rows,
            ["node", "device"],
            use_color=False,
        )
        lines = out.split("\n")
        assert lines[0].startswith("Node")
        assert "Device" in lines[0]
        # Divider row is just dashes and separators.
        assert all(c in "- " for c in lines[1])
        assert "18" in lines[2]
        assert "Hall Lock" in lines[2]

    def test_no_header_option(self) -> None:
        rows = [_mk_row(node=18, device="X")]
        out = zni.render_table(
            rows,
            ["node", "device"],
            use_color=False,
            show_header=False,
        )
        assert "Node" not in out
        assert "Device" not in out

    def test_empty_table_still_prints_header(self) -> None:
        out = zni.render_table(
            [],
            ["node", "device"],
            use_color=False,
        )
        lines = out.split("\n")
        assert lines[0].strip().startswith("Node")


# --- Code quality ----------------------------------------------


class TestCodeQuality(CodeQualityBase):
    ruff_targets = [
        "scripts/zwave_network_info.py",
        "tests/test_zwave_network_info.py",
    ]
    mypy_targets = [
        "scripts/zwave_network_info.py",
    ]


if __name__ == "__main__":
    from conftest import run_tests

    run_tests(__file__, _SCRIPT_PATH, REPO_ROOT)
