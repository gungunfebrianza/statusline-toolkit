#!/usr/bin/env python3
"""
Unit tests for statusline_toolkit.py.

Uses only unittest (standard library) so running the test suite requires
no extra installs, matching the project's zero-dependency goal.

    python -m unittest test_statusline_toolkit.py -v
    # or simply
    python test_statusline_toolkit.py
"""

from __future__ import annotations

import contextlib
import datetime
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import statusline_toolkit as st


class FlattenTests(unittest.TestCase):
    def test_flattens_nested_dict_into_dot_paths(self):
        data = {"a": {"b": {"c": 1}}, "d": 2}
        self.assertEqual(st.flatten(data), {"a.b.c": 1, "d": 2})

    def test_keeps_lists_as_leaf_values(self):
        data = {"a": [1, 2, 3]}
        self.assertEqual(st.flatten(data), {"a": [1, 2, 3]})

    def test_empty_dict_flattens_to_empty_dict(self):
        self.assertEqual(st.flatten({}), {})


class GetFieldTests(unittest.TestCase):
    def setUp(self):
        self.data = {"cost": {"total_cost_usd": 0.5}, "model": {"display_name": "Test Model"}}

    def test_returns_value_at_existing_dot_path(self):
        self.assertEqual(st.get_field(self.data, "cost.total_cost_usd"), 0.5)
        self.assertEqual(st.get_field(self.data, "model.display_name"), "Test Model")

    def test_returns_none_for_missing_path(self):
        self.assertIsNone(st.get_field(self.data, "cost.does_not_exist"))
        self.assertIsNone(st.get_field(self.data, "nope.at.all"))

    def test_returns_none_when_traversing_through_a_non_dict(self):
        self.assertIsNone(st.get_field(self.data, "cost.total_cost_usd.nested"))


class ProjectDirNameTests(unittest.TestCase):
    def test_posix_path(self):
        self.assertEqual(st.project_dir_name("/home/user/my-project"), "my-project")

    def test_windows_path(self):
        self.assertEqual(st.project_dir_name(r"C:\Users\user\my-project"), "my-project")

    def test_strips_trailing_slash(self):
        self.assertEqual(st.project_dir_name("/home/user/cool-project/"), "cool-project")

    def test_single_segment_path(self):
        self.assertEqual(st.project_dir_name("/project"), "project")


class ColorForPercentageTests(unittest.TestCase):
    def test_green_below_50(self):
        self.assertEqual(st.color_for_percentage(0), st.ANSI_GREEN)
        self.assertEqual(st.color_for_percentage(49.9), st.ANSI_GREEN)

    def test_yellow_between_50_and_79(self):
        self.assertEqual(st.color_for_percentage(50), st.ANSI_YELLOW)
        self.assertEqual(st.color_for_percentage(79.9), st.ANSI_YELLOW)

    def test_red_at_80_and_above(self):
        self.assertEqual(st.color_for_percentage(80), st.ANSI_RED)
        self.assertEqual(st.color_for_percentage(100), st.ANSI_RED)


class GradientRgbTests(unittest.TestCase):
    def test_matches_stops_exactly(self):
        self.assertEqual(st.gradient_rgb(0), (46, 204, 64))
        self.assertEqual(st.gradient_rgb(50), (255, 193, 7))
        self.assertEqual(st.gradient_rgb(100), (220, 53, 69))

    def test_interpolates_between_green_and_amber(self):
        self.assertEqual(st.gradient_rgb(25), (150, 198, 36))

    def test_interpolates_between_amber_and_red(self):
        self.assertEqual(st.gradient_rgb(75), (238, 123, 38))

    def test_clamps_out_of_range_percentages(self):
        self.assertEqual(st.gradient_rgb(150), st.gradient_rgb(100))
        self.assertEqual(st.gradient_rgb(-10), st.gradient_rgb(0))


class SupportsTruecolorTests(unittest.TestCase):
    def test_true_when_colorterm_is_truecolor(self):
        with patch.dict("os.environ", {"COLORTERM": "truecolor"}, clear=True):
            self.assertTrue(st.supports_truecolor())

    def test_true_when_windows_terminal_session(self):
        with patch.dict("os.environ", {"WT_SESSION": "some-guid"}, clear=True):
            self.assertTrue(st.supports_truecolor())

    def test_false_on_plain_windows_without_signals(self):
        with patch.dict("os.environ", {}, clear=True), patch("statusline_toolkit.platform.system", return_value="Windows"):
            self.assertFalse(st.supports_truecolor())

    def test_true_on_linux_without_signals(self):
        with patch.dict("os.environ", {}, clear=True), patch("statusline_toolkit.platform.system", return_value="Linux"):
            self.assertTrue(st.supports_truecolor())


class ColorizeTests(unittest.TestCase):
    def test_uses_truecolor_gradient_when_supported(self):
        with patch("statusline_toolkit.supports_truecolor", return_value=True):
            result = st.colorize("42%", 42, use_color=True)
        r, g, b = st.gradient_rgb(42)
        self.assertEqual(result, f"\033[38;2;{r};{g};{b}m42%{st.ANSI_RESET}")

    def test_falls_back_to_basic_ansi_when_truecolor_unsupported(self):
        with patch("statusline_toolkit.supports_truecolor", return_value=False):
            result = st.colorize("42%", 42, use_color=True)
        self.assertEqual(result, f"{st.ANSI_GREEN}42%{st.ANSI_RESET}")

    def test_returns_plain_text_when_color_disabled(self):
        self.assertEqual(st.colorize("42%", 42, use_color=False), "42%")


class ColorizeFixedTests(unittest.TestCase):
    def test_uses_truecolor_when_supported(self):
        with patch("statusline_toolkit.supports_truecolor", return_value=True):
            result = st.colorize_fixed("+156", (46, 204, 64), st.ANSI_GREEN, use_color=True)
        self.assertEqual(result, f"\033[38;2;46;204;64m+156{st.ANSI_RESET}")

    def test_falls_back_to_basic_ansi_when_truecolor_unsupported(self):
        with patch("statusline_toolkit.supports_truecolor", return_value=False):
            result = st.colorize_fixed("-23", (220, 53, 69), st.ANSI_RED, use_color=True)
        self.assertEqual(result, f"{st.ANSI_RED}-23{st.ANSI_RESET}")

    def test_returns_plain_text_when_color_disabled(self):
        self.assertEqual(st.colorize_fixed("+156", (46, 204, 64), st.ANSI_GREEN, use_color=False), "+156")


class ResolveColorTests(unittest.TestCase):
    def test_resolves_known_color_name(self):
        rgb, basic_ansi = st.resolve_color("cyan")
        self.assertEqual(rgb, (0, 188, 212))
        self.assertEqual(basic_ansi, "\033[36m")

    def test_color_name_is_case_and_whitespace_insensitive(self):
        self.assertEqual(st.resolve_color(" Cyan "), st.resolve_color("cyan"))

    def test_resolves_hex_color(self):
        rgb, basic_ansi = st.resolve_color("#00FF00")
        self.assertEqual(rgb, (0, 255, 0))
        self.assertEqual(basic_ansi, "\033[37m")

    def test_returns_none_for_unresolvable_value(self):
        self.assertIsNone(st.resolve_color("not-a-color"))
        self.assertIsNone(st.resolve_color("#GGGGGG"))
        self.assertIsNone(st.resolve_color("#FFF"))


class ColorizeNamedTests(unittest.TestCase):
    def test_uses_truecolor_when_supported(self):
        with patch("statusline_toolkit.supports_truecolor", return_value=True):
            result = st.colorize_named("hi", "cyan", use_color=True)
        self.assertEqual(result, f"\033[38;2;0;188;212mhi{st.ANSI_RESET}")

    def test_falls_back_to_basic_ansi_when_truecolor_unsupported(self):
        with patch("statusline_toolkit.supports_truecolor", return_value=False):
            result = st.colorize_named("hi", "cyan", use_color=True)
        self.assertEqual(result, f"\033[36mhi{st.ANSI_RESET}")

    def test_returns_plain_text_when_color_disabled(self):
        self.assertEqual(st.colorize_named("hi", "cyan", use_color=False), "hi")

    def test_returns_plain_text_for_unresolvable_color(self):
        self.assertEqual(st.colorize_named("hi", "not-a-color", use_color=True), "hi")


class VisibleLengthTests(unittest.TestCase):
    def test_plain_text_length_unchanged(self):
        self.assertEqual(st.visible_length("hello"), 5)

    def test_strips_truecolor_ansi_codes(self):
        text = f"\033[38;2;46;204;64m+156{st.ANSI_RESET}"
        self.assertEqual(st.visible_length(text), 4)

    def test_strips_basic_ansi_codes(self):
        text = f"{st.ANSI_GREEN}42%{st.ANSI_RESET}"
        self.assertEqual(st.visible_length(text), 3)


class BuildBarTests(unittest.TestCase):
    def test_bar_is_fully_filled_at_100_percent(self):
        bar = st.build_bar(100, width=10, use_color=False)
        self.assertEqual(bar, "[##########]")

    def test_bar_is_empty_at_0_percent(self):
        bar = st.build_bar(0, width=10, use_color=False)
        self.assertEqual(bar, "[----------]")

    def test_clamps_out_of_range_percentages(self):
        self.assertEqual(st.build_bar(150, width=10, use_color=False), "[##########]")
        self.assertEqual(st.build_bar(-20, width=10, use_color=False), "[----------]")


class FormatDurationTests(unittest.TestCase):
    def test_seconds_only(self):
        self.assertEqual(st.format_duration(45_000), "45s")

    def test_minutes_and_seconds(self):
        self.assertEqual(st.format_duration(754_000), "12m34s")

    def test_hours_and_minutes(self):
        self.assertEqual(st.format_duration(3_723_000), "1h02m")

    def test_zero_duration(self):
        self.assertEqual(st.format_duration(0), "0s")


class FormatBurnRateTests(unittest.TestCase):
    def test_extrapolates_short_session_to_hourly_rate(self):
        # $0.1234 over 45s -> 0.1234 / (45/3600) hours = $9.872/hr
        self.assertEqual(st.format_burn_rate(0.1234, 45_000), "$9.87/hr")

    def test_extrapolates_longer_session_to_hourly_rate(self):
        # $1.50 over 1h02m03s -> 1.50 / (3723/3600) hours = $1.45/hr
        self.assertEqual(st.format_burn_rate(1.50, 3_723_000), "$1.45/hr")

    def test_zero_cost_gives_zero_rate(self):
        self.assertEqual(st.format_burn_rate(0, 60_000), "$0.00/hr")


class FormatCurrencyTests(unittest.TestCase):
    def test_format_usd(self):
        self.assertEqual(st.format_usd(0.1234), "$0.1234")

    def test_format_idr_uses_dot_thousands_separator(self):
        self.assertEqual(st.format_idr(2_011_190), "Rp 2.011.190")

    def test_format_currency_idr_delegates_to_format_idr(self):
        self.assertEqual(st.format_currency(2_011_190, "idr"), "Rp 2.011.190")

    def test_format_currency_uses_two_decimals_by_default(self):
        self.assertEqual(st.format_currency(113.6, "eur"), "€113.60")

    def test_format_currency_uses_zero_decimals_for_jpy(self):
        self.assertEqual(st.format_currency(18150.4, "JPY"), "¥18,150")

    def test_format_currency_falls_back_to_code_when_no_symbol_known(self):
        self.assertEqual(st.format_currency(1234.5, "SEK"), "SEK 1,234.50")

    def test_format_currency_falls_back_to_code_for_non_cp1252_symbols(self):
        # KRW/INR/VND/THB/PHP intentionally have no symbol mapped (see
        # CURRENCY_SYMBOLS comment) so they can never crash print() under
        # Windows' legacy cp1252 console encoding.
        self.assertEqual(st.format_currency(1300, "KRW"), "KRW 1,300")
        for code in ("KRW", "INR", "VND", "THB", "PHP"):
            with self.subTest(code=code):
                st.format_currency(100, code).encode("cp1252")


class FormatRateTests(unittest.TestCase):
    def test_large_rate_has_no_decimals(self):
        self.assertEqual(st.format_rate(16300, "IDR"), "16,300 IDR/USD")

    def test_small_rate_keeps_four_decimals(self):
        self.assertEqual(st.format_rate(0.92, "EUR"), "0.9200 EUR/USD")


class ReadRateConfigTests(unittest.TestCase):
    def test_none_when_file_missing(self):
        self.assertIsNone(st.read_rate_config(Path("does-not-exist.json")))

    def test_upgrades_legacy_usd_to_idr_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            rate_file = Path(tmp) / "exchange_rate.json"
            rate_file.write_text(json.dumps({"usd_to_idr": 15000, "updated_at": "2026-01-01"}))
            config = st.read_rate_config(rate_file)
            self.assertIsNotNone(config)
            assert config is not None
            self.assertEqual(config["rates"], {"IDR": 15000})

    def test_reads_multi_currency_rates_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            rate_file = Path(tmp) / "exchange_rate.json"
            rate_file.write_text(json.dumps({"rates": {"eur": 0.92, "GBP": 0.79}, "updated_at": "2026-01-01"}))
            config = st.read_rate_config(rate_file)
            self.assertIsNotNone(config)
            assert config is not None
            self.assertEqual(config["rates"], {"EUR": 0.92, "GBP": 0.79})

    def test_returns_none_when_file_is_not_a_json_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            rate_file = Path(tmp) / "exchange_rate.json"
            rate_file.write_text(json.dumps([1, 2, 3]))
            self.assertIsNone(st.read_rate_config(rate_file))


class ReadDefaultsConfigTests(unittest.TestCase):
    def test_returns_empty_dict_when_file_missing(self):
        self.assertEqual(st.read_defaults_config(Path("does-not-exist.json")), {})

    def test_returns_empty_dict_when_file_malformed(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_file = Path(tmp) / "statusline-toolkit.json"
            config_file.write_text("not valid json")
            self.assertEqual(st.read_defaults_config(config_file), {})

    def test_returns_empty_dict_when_file_is_not_a_json_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_file = Path(tmp) / "statusline-toolkit.json"
            config_file.write_text(json.dumps([1, 2, 3]))
            self.assertEqual(st.read_defaults_config(config_file), {})

    def test_reads_known_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_file = Path(tmp) / "statusline-toolkit.json"
            config_file.write_text(json.dumps({"currency": "EUR", "track": True, "width": 100}))
            config = st.read_defaults_config(config_file)
            self.assertEqual(config, {"currency": "EUR", "track": True, "width": 100})

    def test_ignores_unknown_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_file = Path(tmp) / "statusline-toolkit.json"
            config_file.write_text(json.dumps({"currency": "EUR", "totally_made_up_key": "oops"}))
            config = st.read_defaults_config(config_file)
            self.assertEqual(config, {"currency": "EUR"})


class MergedFlagTests(unittest.TestCase):
    def test_cli_true_wins_regardless_of_config(self):
        self.assertTrue(st.merged_flag(True, {"track": False}, "track"))

    def test_config_true_used_when_cli_false(self):
        self.assertTrue(st.merged_flag(False, {"track": True}, "track"))

    def test_false_when_neither_set(self):
        self.assertFalse(st.merged_flag(False, {}, "track"))


class MergedPathTests(unittest.TestCase):
    def test_cli_value_wins(self):
        self.assertEqual(st.merged_path("cli.json", {"rate_file": "config.json"}, "rate_file", Path("default.json")), Path("cli.json"))

    def test_config_value_used_when_no_cli_value(self):
        self.assertEqual(st.merged_path(None, {"rate_file": "config.json"}, "rate_file", Path("default.json")), Path("config.json"))

    def test_falls_back_to_default_when_neither_set(self):
        self.assertEqual(st.merged_path(None, {}, "rate_file", Path("default.json")), Path("default.json"))

    def test_falls_back_to_default_when_config_value_is_not_a_string(self):
        self.assertEqual(st.merged_path(None, {"rate_file": 12345}, "rate_file", Path("default.json")), Path("default.json"))


class MergedColorsTests(unittest.TestCase):
    def test_returns_defaults_when_no_config(self):
        self.assertEqual(st.merged_colors({}), st.DEFAULT_SEGMENT_COLORS)

    def test_valid_override_replaces_default(self):
        colors = st.merged_colors({"colors": {"model": "red"}})
        self.assertEqual(colors["model"], "red")
        self.assertEqual(colors["project"], st.DEFAULT_SEGMENT_COLORS["project"])  # untouched

    def test_hex_override_is_accepted(self):
        colors = st.merged_colors({"colors": {"cost": "#00FF00"}})
        self.assertEqual(colors["cost"], "#00FF00")

    def test_unresolvable_override_keeps_default(self):
        colors = st.merged_colors({"colors": {"duration": "not-a-color"}})
        self.assertEqual(colors["duration"], st.DEFAULT_SEGMENT_COLORS["duration"])

    def test_unknown_segment_key_is_ignored(self):
        colors = st.merged_colors({"colors": {"totally_made_up": "red"}})
        self.assertEqual(colors, st.DEFAULT_SEGMENT_COLORS)

    def test_non_dict_colors_value_is_ignored(self):
        self.assertEqual(st.merged_colors({"colors": "not-a-dict"}), st.DEFAULT_SEGMENT_COLORS)


class LoadRateTests(unittest.TestCase):
    def test_command_line_rate_takes_priority(self):
        rate, source = st.load_rate("IDR", 15800.0, Path("does-not-exist.json"))
        self.assertEqual(rate, 15800.0)
        self.assertIn("command line", source)

    def test_falls_back_to_builtin_idr_rate_when_file_missing(self):
        rate, source = st.load_rate("idr", None, Path("does-not-exist.json"))
        self.assertEqual(rate, st.FALLBACK_RATES["IDR"])
        self.assertIn("fallback", source)

    def test_reads_rate_from_file_when_no_override_given(self):
        with tempfile.TemporaryDirectory() as tmp:
            rate_file = Path(tmp) / "exchange_rate.json"
            rate_file.write_text(json.dumps({"rates": {"EUR": 0.92}, "updated_at": "2026-01-01"}))
            rate, source = st.load_rate("eur", None, rate_file)
            self.assertEqual(rate, 0.92)
            self.assertIn("exchange_rate.json", source)

    def test_reads_legacy_usd_to_idr_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            rate_file = Path(tmp) / "exchange_rate.json"
            rate_file.write_text(json.dumps({"usd_to_idr": 15000, "updated_at": "2026-01-01"}))
            rate, _source = st.load_rate("IDR", None, rate_file)
            self.assertEqual(rate, 15000.0)

    def test_exits_with_helpful_message_when_currency_unconfigured(self):
        with tempfile.TemporaryDirectory() as tmp:
            rate_file = Path(tmp) / "exchange_rate.json"
            rate_file.write_text(json.dumps({"rates": {"IDR": 16300}, "updated_at": "2026-01-01"}))
            with self.assertRaises(SystemExit) as ctx:
                st.load_rate("EUR", None, rate_file)
            self.assertIn("EUR", str(ctx.exception))


class RateStalenessWarningTests(unittest.TestCase):
    def test_none_when_rate_file_missing(self):
        self.assertIsNone(st.rate_staleness_warning(Path("does-not-exist.json")))

    def test_none_when_rate_is_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            rate_file = Path(tmp) / "exchange_rate.json"
            today = datetime.date.today().isoformat()
            rate_file.write_text(json.dumps({"rates": {"IDR": 16300}, "updated_at": today}))
            self.assertIsNone(st.rate_staleness_warning(rate_file))

    def test_warns_when_rate_is_older_than_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            rate_file = Path(tmp) / "exchange_rate.json"
            old_date = datetime.date.today() - datetime.timedelta(days=st.RATE_STALE_DAYS + 1)
            rate_file.write_text(json.dumps({"rates": {"IDR": 16300}, "updated_at": old_date.isoformat()}))
            warning = st.rate_staleness_warning(rate_file)
            self.assertIsNotNone(warning)
            self.assertIn("consider refreshing", warning)

    def test_none_when_rate_file_is_malformed(self):
        with tempfile.TemporaryDirectory() as tmp:
            rate_file = Path(tmp) / "exchange_rate.json"
            rate_file.write_text("not valid json")
            self.assertIsNone(st.rate_staleness_warning(rate_file))

    def test_none_when_updated_at_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            rate_file = Path(tmp) / "exchange_rate.json"
            rate_file.write_text(json.dumps({"rates": {"IDR": 16300}}))
            self.assertIsNone(st.rate_staleness_warning(rate_file))


class BuildStatuslineCommandTests(unittest.TestCase):
    def test_defaults_to_idr_shorthand_when_no_currency_given(self):
        command = st.build_statusline_command(Path("statusline_toolkit.py"))
        self.assertIn("--idr", command)
        self.assertNotIn("--currency", command)

    def test_uses_currency_flag_when_given(self):
        command = st.build_statusline_command(Path("statusline_toolkit.py"), "eur")
        self.assertIn("--currency EUR", command)

    def test_omits_track_flag_by_default(self):
        command = st.build_statusline_command(Path("statusline_toolkit.py"))
        self.assertNotIn("--track", command)

    def test_appends_track_flag_when_requested(self):
        command = st.build_statusline_command(Path("statusline_toolkit.py"), "eur", track=True)
        self.assertIn("--currency EUR --track", command)


class InstallStatuslineTests(unittest.TestCase):
    def _install(self, *args, **kwargs) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            st.install_statusline(*args, **kwargs)

    def test_writes_statusline_command_to_fresh_settings_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            self._install(settings_path, assume_yes=True)
            settings = json.loads(settings_path.read_text())
            self.assertIn("--idr", settings["statusLine"]["command"])

    def test_is_idempotent_when_already_configured_correctly(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            self._install(settings_path, assume_yes=True)
            first_write_time = settings_path.stat().st_mtime_ns
            self._install(settings_path, assume_yes=True)
            self.assertEqual(settings_path.stat().st_mtime_ns, first_write_time)

    def test_does_not_crash_when_existing_statusline_is_not_a_dict(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            settings_path.write_text(json.dumps({"statusLine": "not-an-object"}))
            self._install(settings_path, assume_yes=True)  # must not raise
            settings = json.loads(settings_path.read_text())
            self.assertIn("--idr", settings["statusLine"]["command"])


class ReadWriteHistoryTests(unittest.TestCase):
    def test_read_returns_empty_dict_when_file_missing(self):
        self.assertEqual(st.read_history(Path("does-not-exist.json")), {})

    def test_read_returns_empty_dict_when_file_malformed(self):
        with tempfile.TemporaryDirectory() as tmp:
            history_file = Path(tmp) / "usage_history.json"
            history_file.write_text("not valid json")
            self.assertEqual(st.read_history(history_file), {})

    def test_read_returns_empty_dict_when_file_is_not_a_json_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            history_file = Path(tmp) / "usage_history.json"
            history_file.write_text(json.dumps([1, 2, 3]))
            self.assertEqual(st.read_history(history_file), {})

    def test_write_then_read_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            history_file = Path(tmp) / "usage_history.json"
            history = {"sess-1": {"date": "2026-07-07", "cost_usd": 1.5}}
            st.write_history(history_file, history)
            self.assertEqual(st.read_history(history_file), history)

    def test_write_leaves_no_stray_temp_file_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history_file = tmp_path / "usage_history.json"
            st.write_history(history_file, {"sess-1": {"date": "2026-07-07", "cost_usd": 1.5}})
            self.assertEqual(list(tmp_path.iterdir()), [history_file])


class UpsertSessionRecordTests(unittest.TestCase):
    def test_records_cost_model_and_project_keyed_by_session_id(self):
        history: dict = {}
        payload = {
            "session_id": "abc123",
            "cost": {"total_cost_usd": 0.5},
            "model": {"display_name": "Claude Sonnet 5"},
            "workspace": {"current_dir": "/home/user/my-project"},
        }
        self.assertTrue(st.upsert_session_record(history, payload))
        record = history["abc123"]
        self.assertEqual(record["cost_usd"], 0.5)
        self.assertEqual(record["model"], "Claude Sonnet 5")
        self.assertEqual(record["project"], "my-project")
        self.assertEqual(record["date"], datetime.date.today().isoformat())

    def test_returns_false_and_does_not_modify_history_when_session_id_missing(self):
        history: dict = {}
        self.assertFalse(st.upsert_session_record(history, {"cost": {"total_cost_usd": 0.5}}))
        self.assertEqual(history, {})

    def test_overwrites_existing_record_for_same_session_id(self):
        history = {"abc123": {"date": "2026-01-01", "cost_usd": 0.1, "model": None, "project": None}}
        st.upsert_session_record(history, {"session_id": "abc123", "cost": {"total_cost_usd": 9.9}})
        self.assertEqual(len(history), 1)
        self.assertEqual(history["abc123"]["cost_usd"], 9.9)


class BuildStatsReportTests(unittest.TestCase):
    def test_reports_no_sessions_when_history_empty(self):
        self.assertIn("No tracked sessions yet", st.build_stats_report({}))

    def test_groups_by_date_with_totals_and_counts(self):
        history = {
            "s1": {"date": "2026-07-05", "cost_usd": 1.2},
            "s2": {"date": "2026-07-05", "cost_usd": 0.5},
            "s3": {"date": "2026-07-06", "cost_usd": 2.0},
        }
        report = st.build_stats_report(history)
        self.assertIn("2026-07-05", report)
        self.assertIn("2026-07-06", report)
        self.assertIn("2 sessions", report)
        self.assertIn("3 sessions tracked", report)
        self.assertIn(st.format_usd(3.7), report)  # grand total

    def test_singular_session_wording(self):
        report = st.build_stats_report({"s1": {"date": "2026-07-05", "cost_usd": 1.0}})
        self.assertIn("1 session tracked", report)
        self.assertNotIn("1 sessions", report)

    def test_groups_by_project_when_requested(self):
        history = {
            "s1": {"date": "2026-07-05", "cost_usd": 1.2, "project": "proj-a"},
            "s2": {"date": "2026-07-06", "cost_usd": 2.0, "project": "proj-a"},
            "s3": {"date": "2026-07-06", "cost_usd": 0.5, "project": "proj-b"},
        }
        report = st.build_stats_report(history, group_by="project")
        self.assertIn("by project", report)
        self.assertIn("proj-a", report)
        self.assertIn("proj-b", report)
        self.assertIn(st.format_usd(3.2), report)  # proj-a subtotal

    def test_project_grouping_falls_back_to_unknown_when_missing(self):
        history = {"s1": {"date": "2026-07-05", "cost_usd": 1.0, "project": None}}
        report = st.build_stats_report(history, group_by="project")
        self.assertIn("unknown", report)

    def test_groups_by_model_when_requested(self):
        history = {
            "s1": {"date": "2026-07-05", "cost_usd": 1.2, "model": "Claude Opus 4.8"},
            "s2": {"date": "2026-07-06", "cost_usd": 2.0, "model": "Claude Opus 4.8"},
            "s3": {"date": "2026-07-06", "cost_usd": 0.5, "model": "Claude Sonnet 5"},
        }
        report = st.build_stats_report(history, group_by="model")
        self.assertIn("by model", report)
        self.assertIn("Claude Opus 4.8", report)
        self.assertIn("Claude Sonnet 5", report)
        self.assertIn(st.format_usd(3.2), report)  # Opus subtotal

    def test_model_grouping_falls_back_to_unknown_when_missing(self):
        history = {"s1": {"date": "2026-07-05", "cost_usd": 1.0, "model": None}}
        report = st.build_stats_report(history, group_by="model")
        self.assertIn("unknown", report)

    def test_rejects_invalid_group_by(self):
        with self.assertRaises(ValueError):
            st.build_stats_report({"s1": {"date": "2026-07-05", "cost_usd": 1.0}}, group_by="currency")

    def test_shows_converted_amount_when_currency_given(self):
        history = {"s1": {"date": "2026-07-05", "cost_usd": 1.0}}
        report = st.build_stats_report(history, currency="EUR", rate=0.92)
        self.assertIn(st.format_currency(0.92, "EUR"), report)

    def test_omits_converted_amount_when_no_currency(self):
        history = {"s1": {"date": "2026-07-05", "cost_usd": 1.0}}
        report = st.build_stats_report(history)
        self.assertNotIn("~", report)


class ConvertedSuffixTests(unittest.TestCase):
    def test_empty_string_when_no_currency(self):
        self.assertEqual(st.converted_suffix(1.0, None, None), "")

    def test_formats_converted_amount(self):
        self.assertEqual(st.converted_suffix(1.0, "EUR", 0.92), f" (~{st.format_currency(0.92, 'EUR')})")


class GroupCostsTests(unittest.TestCase):
    def test_sums_cost_and_counts_sessions_per_key(self):
        history = {
            "s1": {"project": "proj-a", "cost_usd": 1.2},
            "s2": {"project": "proj-a", "cost_usd": 2.0},
            "s3": {"project": "proj-b", "cost_usd": 0.5},
        }
        self.assertEqual(st.group_costs(history, "project"), {"proj-a": (3.2, 2), "proj-b": (0.5, 1)})

    def test_missing_key_falls_back_to_unknown(self):
        history = {"s1": {"cost_usd": 1.0, "project": None}}
        self.assertEqual(st.group_costs(history, "project"), {"unknown": (1.0, 1)})

    def test_empty_history_returns_empty_dict(self):
        self.assertEqual(st.group_costs({}, "project"), {})


class DashboardBarRowsTests(unittest.TestCase):
    def test_empty_groups_shows_placeholder(self):
        self.assertIn("No data yet", st._dashboard_bar_rows({}, sort_by_value=True))

    def test_sorts_by_value_descending_when_requested(self):
        html_out = st._dashboard_bar_rows({"b": (1.0, 1), "a": (5.0, 1)}, sort_by_value=True)
        self.assertLess(html_out.index(">a<"), html_out.index(">b<"))

    def test_sorts_alphabetically_when_not_by_value(self):
        html_out = st._dashboard_bar_rows({"2026-07-06": (1.0, 1), "2026-07-05": (5.0, 1)}, sort_by_value=False)
        self.assertLess(html_out.index("2026-07-05"), html_out.index("2026-07-06"))

    def test_largest_value_fills_100_percent(self):
        html_out = st._dashboard_bar_rows({"a": (2.0, 1), "b": (1.0, 1)}, sort_by_value=True)
        self.assertIn("width:100.0%", html_out)

    def test_escapes_unsafe_labels(self):
        html_out = st._dashboard_bar_rows({"<script>alert(1)</script>": (1.0, 1)}, sort_by_value=True)
        self.assertNotIn("<script>alert", html_out)
        self.assertIn("&lt;script&gt;", html_out)

    def test_shows_session_count_next_to_cost(self):
        html_out = st._dashboard_bar_rows({"proj-a": (3.2, 2)}, sort_by_value=True)
        self.assertIn("2 sessions", html_out)

    def test_singular_session_wording(self):
        html_out = st._dashboard_bar_rows({"proj-a": (1.0, 1)}, sort_by_value=True)
        self.assertIn("1 session", html_out)
        self.assertNotIn("1 sessions", html_out)

    def test_highlights_matching_row(self):
        html_out = st._dashboard_bar_rows(
            {"2026-07-06": (1.0, 1), "2026-07-07": (2.0, 1)},
            sort_by_value=False,
            highlights={"2026-07-07": ["Today"]},
        )
        self.assertIn("bar-row-highlight", html_out)
        self.assertIn('highlight-badge">Today</span>', html_out)
        # only the matching row is marked
        self.assertEqual(html_out.count("bar-row-highlight"), 1)

    def test_multiple_badges_on_the_same_row(self):
        html_out = st._dashboard_bar_rows(
            {"2026-07-07": (1.0, 1)}, sort_by_value=False, highlights={"2026-07-07": ["Today", "Priciest"]},
        )
        self.assertIn('highlight-badge">Today</span>', html_out)
        self.assertIn('highlight-badge">Priciest</span>', html_out)

    def test_no_highlight_when_highlights_is_none(self):
        html_out = st._dashboard_bar_rows({"2026-07-07": (1.0, 1)}, sort_by_value=False, highlights=None)
        self.assertNotIn("bar-row-highlight", html_out)
        self.assertNotIn("highlight-badge", html_out)

    def test_no_highlight_when_no_row_matches(self):
        html_out = st._dashboard_bar_rows(
            {"2026-07-06": (1.0, 1)}, sort_by_value=False, highlights={"2026-07-07": ["Today"]},
        )
        self.assertNotIn("bar-row-highlight", html_out)

    def test_converted_currency_shown_when_given(self):
        html_out = st._dashboard_bar_rows({"proj-a": (1.0, 1)}, sort_by_value=True, currency="EUR", rate=0.92)
        self.assertIn(st.format_currency(0.92, "EUR"), html_out)


class ExtremeDayLabelTests(unittest.TestCase):
    def test_returns_none_for_empty_input(self):
        self.assertIsNone(st._extreme_day_label({}, pick_max=True))

    def test_picks_highest_cost_day(self):
        by_date = {"2026-07-05": (1.2, 1), "2026-07-06": (4.87, 1)}
        label = st._extreme_day_label(by_date, pick_max=True)
        self.assertIn("2026-07-06", label)
        self.assertIn(st.format_usd(4.87), label)

    def test_picks_lowest_cost_day(self):
        by_date = {"2026-07-05": (1.2, 1), "2026-07-06": (4.87, 1)}
        label = st._extreme_day_label(by_date, pick_max=False)
        self.assertIn("2026-07-05", label)
        self.assertIn(st.format_usd(1.2), label)

    def test_escapes_unsafe_date_label(self):
        by_date = {"<script>": (1.0, 1)}
        label = st._extreme_day_label(by_date, pick_max=True)
        self.assertNotIn("<script>", label)
        self.assertIn("&lt;script&gt;", label)


class BuildDashboardHtmlTests(unittest.TestCase):
    def test_includes_summary_cards(self):
        history = {
            "s1": {"date": "2026-07-05", "cost_usd": 1.0, "project": "proj-a", "model": "Sonnet"},
            "s2": {"date": "2026-07-06", "cost_usd": 3.0, "project": "proj-b", "model": "Opus"},
        }
        page = st.build_dashboard_html(history)
        self.assertIn("<!doctype html>", page.lower())
        self.assertIn("2 sessions tracked", page)
        self.assertIn(st.format_usd(4.0), page)  # total
        self.assertIn(st.format_usd(2.0), page)  # average
        self.assertIn("2026-07-05 to 2026-07-06", page)
        self.assertIn("proj-a", page)
        self.assertIn("Opus", page)

    def test_handles_empty_history(self):
        page = st.build_dashboard_html({})
        self.assertIn("0 sessions tracked", page)
        self.assertIn("no sessions yet", page)
        self.assertIn("No data yet", page)

    def test_includes_generated_timestamp(self):
        fixed_time = datetime.datetime(2026, 7, 7, 14, 30)
        page = st.build_dashboard_html({}, generated_at=fixed_time)
        self.assertIn("Generated 2026-07-07 14:30", page)

    def test_defaults_generated_timestamp_to_now(self):
        page = st.build_dashboard_html({})
        self.assertIn("Generated", page)
        self.assertIn(str(datetime.date.today().year), page)

    def test_includes_priciest_and_cheapest_day_cards(self):
        history = {
            "s1": {"date": "2026-07-05", "cost_usd": 1.2},
            "s2": {"date": "2026-07-06", "cost_usd": 4.87},
        }
        page = st.build_dashboard_html(history)
        self.assertIn("Priciest day", page)
        self.assertIn("Cheapest day", page)
        self.assertIn("2026-07-06", page)  # priciest
        self.assertIn("2026-07-05", page)  # cheapest

    def test_omits_extreme_day_cards_when_history_empty(self):
        page = st.build_dashboard_html({})
        self.assertNotIn("Priciest day", page)
        self.assertNotIn("Cheapest day", page)

    def test_highlights_todays_bar_in_cost_by_day(self):
        today = datetime.date.today().isoformat()
        # today's cost is deliberately the smallest, so this test isn't conflated with "Priciest"
        history = {
            "s1": {"date": "2026-01-01", "cost_usd": 5.0},
            "s2": {"date": today, "cost_usd": 0.1},
        }
        page = st.build_dashboard_html(history)
        self.assertIn("bar-row-highlight", page)
        self.assertIn('highlight-badge">Today</span>', page)

    def test_highlights_priciest_bar_in_cost_by_day(self):
        history = {
            "s1": {"date": "2026-01-01", "cost_usd": 1.0},
            "s2": {"date": "2026-01-02", "cost_usd": 5.0},
        }
        page = st.build_dashboard_html(history)
        self.assertIn('highlight-badge">Priciest</span>', page)

    def test_converted_currency_in_cards_and_bars(self):
        history = {"s1": {"date": "2026-07-05", "cost_usd": 1.0, "project": "proj-a"}}
        page = st.build_dashboard_html(history, currency="EUR", rate=0.92)
        self.assertIn(st.format_currency(0.92, "EUR"), page)


class RecordSessionTests(unittest.TestCase):
    def test_writes_history_file_with_session_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            history_file = Path(tmp) / "usage_history.json"
            payload = {"session_id": "abc123", "cost": {"total_cost_usd": 0.25}}
            st.record_session(history_file, payload)
            history = st.read_history(history_file)
            self.assertIn("abc123", history)
            self.assertEqual(history["abc123"]["cost_usd"], 0.25)

    def test_does_not_create_file_when_session_id_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            history_file = Path(tmp) / "usage_history.json"
            st.record_session(history_file, {"cost": {"total_cost_usd": 0.25}})
            self.assertFalse(history_file.exists())


class GetTerminalWidthTests(unittest.TestCase):
    def test_uses_shutil_terminal_size(self):
        with patch("statusline_toolkit.shutil.get_terminal_size", return_value=os.terminal_size((123, 24))):
            self.assertEqual(st.get_terminal_width(), 123)

    def test_falls_back_when_no_terminal_attached(self):
        with patch(
            "statusline_toolkit.shutil.get_terminal_size",
            side_effect=lambda fallback: os.terminal_size(fallback),
        ):
            self.assertEqual(st.get_terminal_width(fallback=80), 80)


class AssembleSummaryLineTests(unittest.TestCase):
    def setUp(self):
        self.parts = {
            "model": "[Model]",
            "project": "(proj)",
            "bar": "[####------] 42%",
            "cost": "$0.1234",
            "currency": "(~Rp 2.011)",
            "lines": "+156/-23",
            "duration": "45s",
            "burn_rate": "$9.87/hr",
            "rate_limits": "5h: 24% 7d: 41%",
        }

    def test_full_line_includes_everything_in_order(self):
        line = st._assemble_summary_line(self.parts, frozenset())
        self.assertEqual(
            line,
            "[Model] (proj) [####------] 42% | $0.1234 (~Rp 2.011) | +156/-23 | 45s | $9.87/hr | 5h: 24% 7d: 41%",
        )

    def test_dropping_currency_keeps_raw_cost(self):
        line = st._assemble_summary_line(self.parts, frozenset({"currency"}))
        self.assertIn("$0.1234", line)
        self.assertNotIn("Rp", line)

    def test_dropping_project_removes_it_but_keeps_model_and_bar(self):
        line = st._assemble_summary_line(self.parts, frozenset({"project"}))
        self.assertNotIn("(proj)", line)
        self.assertIn("[Model]", line)
        self.assertIn("[####------] 42%", line)


class FitSummaryLineTests(unittest.TestCase):
    def setUp(self):
        self.parts = {
            "model": "[Model]",
            "project": "(proj)",
            "bar": "[####------] 42%",
            "cost": "$0.1234",
            "currency": "(~Rp 2.011)",
            "lines": "+156/-23",
            "duration": "45s",
            "burn_rate": "$9.87/hr",
            "rate_limits": "5h: 24% 7d: 41%",
        }
        self.full_line = st._assemble_summary_line(self.parts, frozenset())

    def test_none_max_width_returns_full_line_unmodified(self):
        self.assertEqual(st.fit_summary_line(self.parts, None), self.full_line)

    def test_returns_full_line_when_it_already_fits(self):
        self.assertEqual(st.fit_summary_line(self.parts, len(self.full_line)), self.full_line)

    def test_drops_rate_limits_first(self):
        line = st.fit_summary_line(self.parts, len(self.full_line) - 1)
        self.assertNotIn("5h:", line)
        self.assertIn("$9.87/hr", line)  # burn_rate not yet dropped

    def test_never_drops_below_core_model_bar_cost(self):
        line = st.fit_summary_line(self.parts, 1)  # impossibly narrow
        self.assertEqual(line, "[Model] [####------] 42% | $0.1234")


class DiscoverPluginsTests(unittest.TestCase):
    def test_returns_empty_list_when_directory_missing(self):
        self.assertEqual(st.discover_plugins(Path("does-not-exist-dir")), [])

    def test_loads_a_valid_plugin(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin_file = Path(tmp) / "git_branch.py"
            plugin_file.write_text("def segment(data, use_color):\n    return '(main)'\n")
            plugins = st.discover_plugins(Path(tmp))
            self.assertEqual(len(plugins), 1)
            name, priority, segment_fn = plugins[0]
            self.assertEqual(name, "git_branch")
            self.assertEqual(priority, 0)
            self.assertEqual(segment_fn({}, True), "(main)")

    def test_reads_custom_priority(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin_file = Path(tmp) / "weather.py"
            plugin_file.write_text("PRIORITY = 10\ndef segment(data, use_color):\n    return '22C'\n")
            plugins = st.discover_plugins(Path(tmp))
            self.assertEqual(plugins[0][1], 10)

    def test_non_int_priority_falls_back_to_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin_file = Path(tmp) / "bad_priority.py"
            plugin_file.write_text("PRIORITY = 'high'\ndef segment(data, use_color):\n    return 'x'\n")
            plugins = st.discover_plugins(Path(tmp))
            self.assertEqual(plugins[0][1], st.PLUGIN_DEFAULT_PRIORITY)

    def test_skips_plugin_that_raises_on_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin_file = Path(tmp) / "broken.py"
            plugin_file.write_text("raise RuntimeError('boom')\n")
            self.assertEqual(st.discover_plugins(Path(tmp)), [])

    def test_skips_file_with_no_segment_function(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin_file = Path(tmp) / "no_segment.py"
            plugin_file.write_text("X = 1\n")
            self.assertEqual(st.discover_plugins(Path(tmp)), [])

    def test_skips_underscore_prefixed_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin_file = Path(tmp) / "_shared.py"
            plugin_file.write_text("def segment(data, use_color):\n    return 'x'\n")
            self.assertEqual(st.discover_plugins(Path(tmp)), [])

    def test_ignores_non_python_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "readme.txt").write_text("not a plugin")
            self.assertEqual(st.discover_plugins(Path(tmp)), [])


class RunPluginSegmentsTests(unittest.TestCase):
    def test_collects_string_results_namespaced_by_plugin(self):
        plugins = [("git_branch", 0, lambda data, use_color: "(main)")]
        self.assertEqual(st.run_plugin_segments(plugins, {}, True), {"plugin:git_branch": "(main)"})

    def test_skips_plugin_that_raises_at_call_time(self):
        def boom(data, use_color):
            raise ValueError("nope")
        plugins = [("bad", 0, boom)]
        self.assertEqual(st.run_plugin_segments(plugins, {}, True), {})

    def test_skips_none_return(self):
        plugins = [("quiet", 0, lambda data, use_color: None)]
        self.assertEqual(st.run_plugin_segments(plugins, {}, True), {})

    def test_skips_empty_string_return(self):
        plugins = [("empty", 0, lambda data, use_color: "")]
        self.assertEqual(st.run_plugin_segments(plugins, {}, True), {})

    def test_skips_non_string_return(self):
        plugins = [("wrong_type", 0, lambda data, use_color: 42)]
        self.assertEqual(st.run_plugin_segments(plugins, {}, True), {})


class PluginDropOrderTests(unittest.TestCase):
    def test_sorted_by_priority_ascending(self):
        plugins = [("weather", 10, None), ("git_branch", 0, None)]
        plugin_parts = {"plugin:weather": "22C", "plugin:git_branch": "(main)"}
        self.assertEqual(st.plugin_drop_order(plugins, plugin_parts), ("plugin:git_branch", "plugin:weather"))

    def test_excludes_plugins_with_no_rendered_part(self):
        plugins = [("weather", 10, None), ("quiet", 0, None)]
        plugin_parts = {"plugin:weather": "22C"}  # "quiet" returned None and isn't here
        self.assertEqual(st.plugin_drop_order(plugins, plugin_parts), ("plugin:weather",))


class SegmentColorIntegrationTests(unittest.TestCase):
    """Confirms segment_colors actually reach model/project/cost/duration/burn_rate in the real pipeline."""

    def setUp(self):
        self.data = {
            "model": {"display_name": "Test Model"},
            "workspace": {"current_dir": "/home/user/my-project"},
            "cost": {"total_cost_usd": 1.0, "total_duration_ms": 3_600_000},
        }

    def test_default_colors_applied(self):
        with patch("statusline_toolkit.supports_truecolor", return_value=False):
            parts = st._build_summary_parts(self.data, None, None, use_color=True)
        self.assertTrue(parts["model"].startswith(f"{st.NAMED_COLORS['cyan'][1]}["))
        self.assertTrue(parts["project"].startswith(f"{st.NAMED_COLORS['blue'][1]}("))
        self.assertTrue(parts["cost"].startswith(f"{st.NAMED_COLORS['yellow'][1]}$"))
        self.assertTrue(parts["duration"].startswith(st.NAMED_COLORS["gray"][1]))
        self.assertTrue(parts["burn_rate"].startswith(st.NAMED_COLORS["magenta"][1]))

    def test_custom_colors_override_defaults(self):
        custom = {**st.DEFAULT_SEGMENT_COLORS, "model": "red"}
        with patch("statusline_toolkit.supports_truecolor", return_value=False):
            parts = st._build_summary_parts(self.data, None, None, use_color=True, segment_colors=custom)
        self.assertTrue(parts["model"].startswith(st.NAMED_COLORS["red"][1]))

    def test_no_color_leaves_segments_plain(self):
        parts = st._build_summary_parts(self.data, None, None, use_color=False)
        self.assertEqual(parts["model"], "[Test Model]")
        self.assertEqual(parts["project"], "(my-project)")


class PluginIntegrationTests(unittest.TestCase):
    """Confirms plugin segments flow through _assemble_summary_line and fit_summary_line correctly."""

    def test_plugin_segment_renders_after_core_segments(self):
        parts = {"model": "[Model]", "bar": "[####------] 42%", "cost": "$0.1234", "plugin:git_branch": "(main)"}
        line = st._assemble_summary_line(parts, frozenset())
        self.assertTrue(line.endswith("| (main)"))

    def test_plugin_segment_drops_before_any_core_segment(self):
        parts = {
            "model": "[Model]", "bar": "[####------] 42%", "cost": "$0.1234",
            "rate_limits": "5h: 24% 7d: 41%", "plugin:git_branch": "(main)",
        }
        full_line = st._assemble_summary_line(parts, frozenset())
        drop_order = ("plugin:git_branch",) + st.ADAPTIVE_DROP_ORDER
        line = st.fit_summary_line(parts, len(full_line) - 1, drop_order)
        self.assertNotIn("(main)", line)
        self.assertIn("5h:", line)  # core segment survives; plugin was sacrificed first

    def test_print_summary_includes_plugin_segment_with_generous_width(self):
        plugins = [("git_branch", 0, lambda data, use_color: "(main)")]
        data = {"model": {"display_name": "Test Model"}, "cost": {"total_cost_usd": 0.5}}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            st.print_summary(data, None, None, None, use_color=False, max_width=None, plugins=plugins)
        self.assertIn("(main)", out.getvalue())

    def test_print_summary_with_no_plugins_is_unaffected(self):
        data = {"model": {"display_name": "Test Model"}, "cost": {"total_cost_usd": 0.5}}
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            st.print_summary(data, None, None, None, use_color=False, max_width=None, plugins=None)
        self.assertIn("Test Model", out.getvalue())


class VersionFlagTests(unittest.TestCase):
    def test_prints_version_and_exits_zero(self):
        out = io.StringIO()
        with self.assertRaises(SystemExit) as ctx, contextlib.redirect_stdout(out):
            st.parse_args(["--version"])
        self.assertEqual(ctx.exception.code, 0)
        self.assertIn(st.__version__, out.getvalue())


class MainConfigPrecedenceTests(unittest.TestCase):
    """End-to-end checks that main() layers CLI flags over the personal defaults file correctly."""

    def _run(self, argv: list[str]) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            st.main(argv)
        return out.getvalue()

    def test_config_currency_and_track_apply_without_cli_flags(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_file = tmp_path / "config.json"
            history_file = tmp_path / "history.json"
            payload_file = tmp_path / "payload.json"
            config_file.write_text(json.dumps({"currency": "IDR", "track": True, "no_color": True}))
            payload_file.write_text(json.dumps({
                "session_id": "test-session",
                "model": {"display_name": "Test Model"},
                "cost": {"total_cost_usd": 0.5},
            }))

            output = self._run([
                "--input", str(payload_file),
                "--config", str(config_file),
                "--history-file", str(history_file),
            ])

            self.assertIn("Rp", output)  # currency conversion applied from config
            self.assertTrue(history_file.exists())  # track applied from config

    def test_cli_currency_overrides_config_currency(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            config_file = tmp_path / "config.json"
            rate_file = tmp_path / "rates.json"
            payload_file = tmp_path / "payload.json"
            # Config asks for EUR, but rate_file only has IDR configured — if config's
            # EUR incorrectly won over the CLI's --currency IDR, this would raise
            # SystemExit instead of returning output.
            config_file.write_text(json.dumps({"currency": "EUR"}))
            rate_file.write_text(json.dumps({"rates": {"IDR": 16300}, "updated_at": "2026-01-01"}))
            payload_file.write_text(json.dumps({
                "model": {"display_name": "Test Model"},
                "cost": {"total_cost_usd": 0.5},
            }))

            output = self._run([
                "--input", str(payload_file),
                "--config", str(config_file),
                "--rate-file", str(rate_file),
                "--currency", "IDR",
                "--no-color",
            ])
            self.assertIn("Rp", output)

    def test_no_config_file_behaves_as_if_unset(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            payload_file = tmp_path / "payload.json"
            payload_file.write_text(json.dumps({
                "model": {"display_name": "Test Model"},
                "cost": {"total_cost_usd": 0.5},
            }))
            output = self._run([
                "--input", str(payload_file),
                "--config", str(tmp_path / "does-not-exist.json"),
                "--no-color",
            ])
            self.assertNotIn("Rp", output)
            self.assertIn("Test Model", output)


class StatsGroupingCliTests(unittest.TestCase):
    def _run(self, argv: list[str]) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            st.main(argv)
        return out.getvalue()

    def _history_file(self, tmp_path: Path) -> Path:
        history_file = tmp_path / "usage_history.json"
        history_file.write_text(json.dumps({
            "s1": {"date": "2026-07-05", "cost_usd": 1.0, "project": "proj-a", "model": "Opus"},
            "s2": {"date": "2026-07-06", "cost_usd": 2.0, "project": "proj-b", "model": "Sonnet"},
        }))
        return history_file

    def test_by_model_flag_groups_by_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            history_file = self._history_file(Path(tmp))
            output = self._run(["--stats", "--by-model", "--history-file", str(history_file)])
            self.assertIn("by model", output)
            self.assertIn("Opus", output)
            self.assertIn("Sonnet", output)

    def test_by_model_takes_priority_over_by_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            history_file = self._history_file(Path(tmp))
            output = self._run(["--stats", "--by-model", "--by-project", "--history-file", str(history_file)])
            self.assertIn("by model", output)

    def test_no_grouping_flags_defaults_to_by_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            history_file = self._history_file(Path(tmp))
            output = self._run(["--stats", "--history-file", str(history_file)])
            self.assertIn("by day", output)

    def test_currency_flag_shows_converted_amounts(self):
        with tempfile.TemporaryDirectory() as tmp:
            history_file = self._history_file(Path(tmp))
            output = self._run([
                "--stats", "--history-file", str(history_file), "--currency", "EUR", "--rate", "0.92",
            ])
            self.assertIn(st.format_currency(0.92, "EUR"), output)


class DashboardCliTests(unittest.TestCase):
    def _run(self, argv: list[str]) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            st.main(argv)
        return out.getvalue()

    def test_writes_dashboard_to_default_location_next_to_history_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history_file = tmp_path / "usage_history.json"
            history_file.write_text(json.dumps({"s1": {"date": "2026-07-05", "cost_usd": 1.0}}))

            output = self._run(["--dashboard", "--history-file", str(history_file)])

            default_dashboard = tmp_path / "usage_dashboard.html"
            self.assertIn(str(default_dashboard), output)
            self.assertTrue(default_dashboard.exists())
            self.assertIn("<!doctype html>", default_dashboard.read_text(encoding="utf-8").lower())

    def test_dashboard_file_overrides_default_location(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history_file = tmp_path / "usage_history.json"
            history_file.write_text(json.dumps({"s1": {"date": "2026-07-05", "cost_usd": 1.0}}))
            custom_path = tmp_path / "custom_dashboard.html"

            self._run(["--dashboard", "--history-file", str(history_file), "--dashboard-file", str(custom_path)])

            self.assertTrue(custom_path.exists())

    def test_open_flag_launches_browser_with_file_uri(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history_file = tmp_path / "usage_history.json"
            history_file.write_text(json.dumps({"s1": {"date": "2026-07-05", "cost_usd": 1.0}}))
            dashboard_path = tmp_path / "dash.html"

            with patch("statusline_toolkit.webbrowser.open") as mock_open:
                self._run([
                    "--dashboard",
                    "--history-file", str(history_file),
                    "--dashboard-file", str(dashboard_path),
                    "--open",
                ])
            mock_open.assert_called_once()
            (called_uri,), _ = mock_open.call_args
            self.assertTrue(called_uri.startswith("file://"))

    def test_no_open_flag_does_not_launch_browser(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history_file = tmp_path / "usage_history.json"
            history_file.write_text(json.dumps({"s1": {"date": "2026-07-05", "cost_usd": 1.0}}))

            with patch("statusline_toolkit.webbrowser.open") as mock_open:
                self._run(["--dashboard", "--history-file", str(history_file)])
            mock_open.assert_not_called()

    def test_currency_flag_shows_converted_amounts_in_dashboard(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            history_file = tmp_path / "usage_history.json"
            history_file.write_text(json.dumps({"s1": {"date": "2026-07-05", "cost_usd": 1.0}}))
            dashboard_path = tmp_path / "dash.html"

            self._run([
                "--dashboard",
                "--history-file", str(history_file),
                "--dashboard-file", str(dashboard_path),
                "--currency", "EUR",
                "--rate", "0.92",
            ])
            content = dashboard_path.read_text(encoding="utf-8")
            self.assertIn(st.format_currency(0.92, "EUR"), content)


if __name__ == "__main__":
    unittest.main()
