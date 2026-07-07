#!/usr/bin/env python3
"""
statusline-toolkit
===================

A small CLI for inspecting the JSON payload Claude Code sends to a
`statusLine` command (see https://code.claude.com/docs/en/statusline),
with an optional (non-realtime) USD -> any-currency cost conversion.

Usage examples
--------------
    # Read live data piped from Claude Code (or any JSON) on stdin
    claude_statusline_payload | python statusline_toolkit.py

    # Inspect a saved/sample payload instead of stdin
    python statusline_toolkit.py --input sample_statusline_data.json --all

    # Discover which fields are present, without printing values
    python statusline_toolkit.py --input sample_statusline_data.json --list

    # Print only specific fields
    python statusline_toolkit.py --input sample_statusline_data.json \\
        --field model.display_name --field cost.total_cost_usd

    # Add a converted-currency estimate next to the USD cost
    python statusline_toolkit.py --input sample_statusline_data.json --currency EUR
    python statusline_toolkit.py --input sample_statusline_data.json --currency JPY

    # --idr is a backward-compatible shorthand for --currency IDR
    python statusline_toolkit.py --input sample_statusline_data.json --idr

    # Override the exchange rate for one run
    python statusline_toolkit.py --input sample_statusline_data.json --currency EUR --rate 0.92

    # One-time setup: wire this script up as your Claude Code statusLine
    # (works on Windows, macOS, and Linux)
    python statusline_toolkit.py --setup
    python statusline_toolkit.py --setup --currency EUR

    # Record this session's cost into a local history file, and later report on it
    python statusline_toolkit.py --input sample_statusline_data.json --track
    python statusline_toolkit.py --stats
"""

from __future__ import annotations

import argparse
import datetime
import html
import importlib.util
import json
import os
import platform
import re
import shutil
import sys
import webbrowser
from pathlib import Path
from typing import Any

__version__ = "1.0.0"

DEFAULT_RATE_FILE = Path(__file__).parent / "exchange_rate.json"
DEFAULT_HISTORY_FILE = Path(__file__).parent / "usage_history.json"
DEFAULT_CONFIG_FILE = Path.home() / ".claude" / "statusline-toolkit.json"
DEFAULT_PLUGINS_DIR = Path.home() / ".claude" / "statusline-plugins"
RATE_STALE_DAYS = 30  # warn if exchange_rate.json's updated_at is older than this
PLUGIN_DEFAULT_PRIORITY = 0  # lower drops sooner under width pressure; see discover_plugins

# Keys recognized in the personal defaults file (DEFAULT_CONFIG_FILE). Anything
# else in that file is ignored rather than rejected, so it stays forward-compatible.
CONFIG_KEYS = (
    "currency", "no_color", "track", "by_project", "by_model", "no_adapt", "width",
    "rate_file", "history_file", "plugins_dir", "no_plugins", "colors",
)

# Segments with a fixed (non-gradient) customizable color. Override any of these via the
# "colors" object in your personal defaults file, e.g. {"colors": {"model": "#00AFFF"}}.
DEFAULT_SEGMENT_COLORS = {
    "model": "cyan",
    "project": "blue",
    "cost": "yellow",
    "burn_rate": "magenta",
    "duration": "gray",
}

# Only IDR ships with a bundled fallback (this toolkit's original currency);
# every other currency must come from exchange_rate.json or --rate.
FALLBACK_RATES = {"IDR": 16300.0}

# Currencies conventionally shown with no decimal places (e.g. amounts too
# large for cents to matter). ISO 4217 minor-unit exceptions, plus IDR.
ZERO_DECIMAL_CURRENCIES = {
    "BIF", "CLP", "DJF", "GNF", "IDR", "ISK", "JPY", "KMF", "KRW",
    "PYG", "RWF", "UGX", "VND", "VUV", "XAF", "XOF", "XPF",
}

# A few common symbols for nicer output; unlisted currencies fall back to
# printing their ISO 4217 code instead (e.g. "SEK 1,234.00"). Deliberately
# limited to symbols that survive Windows' legacy cp1252 console encoding
# (₩, ₹, ₫, ฿, ₱ etc. don't, and would crash `print()` there) — see
# format_currency's fallback and the stdout reconfiguration in main().
CURRENCY_SYMBOLS = {
    "USD": "$", "EUR": "€", "GBP": "£", "JPY": "¥", "CNY": "¥", "IDR": "Rp",
}

# Thresholds (inclusive lower bound) for coloring a usage percentage. Used as
# a fallback on terminals that don't understand 24-bit truecolor.
ANSI_RESET = "\033[0m"
ANSI_GREEN = "\033[32m"
ANSI_YELLOW = "\033[33m"
ANSI_RED = "\033[31m"

# RGB stops the gradient is interpolated between: green at 0%, amber at 50%,
# red at 100%. Percentages between stops blend smoothly rather than jumping.
GRADIENT_STOPS: tuple[tuple[float, tuple[int, int, int]], ...] = (
    (0, (46, 204, 64)),
    (50, (255, 193, 7)),
    (100, (220, 53, 69)),
)


def color_for_percentage(percentage: float) -> str:
    """Green under 50%, yellow 50-79%, red 80%+ — the basic-ANSI fallback signal."""
    if percentage >= 80:
        return ANSI_RED
    if percentage >= 50:
        return ANSI_YELLOW
    return ANSI_GREEN


def gradient_rgb(percentage: float) -> tuple[int, int, int]:
    """Interpolate GRADIENT_STOPS at `percentage`, blending smoothly between the two nearest stops."""
    percentage = max(0.0, min(100.0, percentage))
    for (lo_pct, lo_rgb), (hi_pct, hi_rgb) in zip(GRADIENT_STOPS, GRADIENT_STOPS[1:]):
        if lo_pct <= percentage <= hi_pct:
            t = (percentage - lo_pct) / (hi_pct - lo_pct)
            return tuple(round(lo + (hi - lo) * t) for lo, hi in zip(lo_rgb, hi_rgb))
    return GRADIENT_STOPS[-1][1]


def supports_truecolor() -> bool:
    """
    Best-effort detection of 24-bit ANSI color support.

    Explicit signals (COLORTERM, Windows Terminal's WT_SESSION) are trusted first.
    Otherwise, assume yes on Linux/macOS (nearly universal today) and no on plain
    Windows consoles, which historically only reliably support the basic 16 colors.
    """
    if os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit"):
        return True
    if os.environ.get("WT_SESSION"):
        return True
    return platform.system() != "Windows"


def colorize(text: str, percentage: float, use_color: bool) -> str:
    if not use_color:
        return text
    if supports_truecolor():
        r, g, b = gradient_rgb(percentage)
        color = f"\033[38;2;{r};{g};{b}m"
    else:
        color = color_for_percentage(percentage)
    return f"{color}{text}{ANSI_RESET}"


def colorize_fixed(text: str, rgb: tuple[int, int, int], basic_ansi: str, use_color: bool) -> str:
    """Like colorize, but for a fixed semantic color (e.g. git-style +/-) rather than a percentage gradient."""
    if not use_color:
        return text
    if supports_truecolor():
        r, g, b = rgb
        color = f"\033[38;2;{r};{g};{b}m"
    else:
        color = basic_ansi
    return f"{color}{text}{ANSI_RESET}"


# User-customizable segment colors: a name from this table, or a "#RRGGBB" hex string (only
# usable with truecolor; falls back to plain white on basic-ANSI terminals). Names cover the
# basic 8-color ANSI palette plus a couple of friendly extras (orange/purple) that fall back
# to their nearest basic-ANSI equivalent.
NAMED_COLORS: dict[str, tuple[tuple[int, int, int], str]] = {
    "black": ((60, 60, 60), "\033[30m"),
    "red": ((220, 53, 69), "\033[31m"),
    "green": ((46, 204, 64), "\033[32m"),
    "yellow": ((255, 193, 7), "\033[33m"),
    "blue": ((66, 135, 245), "\033[34m"),
    "magenta": ((191, 90, 242), "\033[35m"),
    "cyan": ((0, 188, 212), "\033[36m"),
    "white": ((230, 230, 230), "\033[37m"),
    "gray": ((140, 140, 140), "\033[90m"),
    "grey": ((140, 140, 140), "\033[90m"),
    "orange": ((255, 140, 0), "\033[33m"),
    "purple": ((160, 90, 210), "\033[35m"),
}

HEX_COLOR_RE = re.compile(r"^#([0-9a-fA-F]{6})$")


def resolve_color(value: str) -> tuple[tuple[int, int, int], str] | None:
    """
    Resolve a color name (see NAMED_COLORS) or a "#RRGGBB" hex string to (rgb, basic_ansi_fallback).
    Returns None if `value` is neither — callers should treat that as "leave it uncolored."
    """
    named = NAMED_COLORS.get(value.strip().lower())
    if named is not None:
        return named
    match = HEX_COLOR_RE.match(value.strip())
    if match:
        hex_digits = match.group(1)
        rgb = (int(hex_digits[0:2], 16), int(hex_digits[2:4], 16), int(hex_digits[4:6], 16))
        return rgb, "\033[37m"  # arbitrary hex has no natural basic-ANSI equivalent; fall back to white
    return None


def colorize_named(text: str, color_value: str, use_color: bool) -> str:
    """Colorize `text` with a user-configured color name/hex (see resolve_color); invalid values render plain."""
    if not use_color:
        return text
    resolved = resolve_color(color_value)
    if resolved is None:
        return text
    rgb, basic_ansi = resolved
    if supports_truecolor():
        r, g, b = rgb
        color = f"\033[38;2;{r};{g};{b}m"
    else:
        color = basic_ansi
    return f"{color}{text}{ANSI_RESET}"


ANSI_ESCAPE_RE = re.compile(r"\033\[[0-9;]*m")


def visible_length(text: str) -> int:
    """Length of `text` as it would appear on screen, ignoring ANSI color escape codes."""
    return len(ANSI_ESCAPE_RE.sub("", text))


def get_terminal_width(fallback: int = 80) -> int:
    """
    Detect the terminal width in columns (honors the COLUMNS env var, via shutil).

    Falls back to `fallback` when there's no attached terminal to query — which is the
    common case when Claude Code runs this as a statusLine subprocess, so --width lets
    that be overridden explicitly if Claude Code's actual render width is known.
    """
    return shutil.get_terminal_size(fallback=(fallback, 24)).columns


def read_input(path: str | None) -> dict[str, Any]:
    """Load the statusline JSON payload from a file, or from stdin if no path is given."""
    text = Path(path).read_text(encoding="utf-8") if path else sys.stdin.read()
    if not text.strip():
        raise SystemExit("No input JSON received (empty file or empty stdin).")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Input is not valid JSON: {exc}") from exc


def flatten(data: Any, prefix: str = "") -> dict[str, Any]:
    """Flatten a nested dict into {'a.b.c': value} pairs. Lists are kept as leaf values."""
    flat: dict[str, Any] = {}
    if isinstance(data, dict):
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else key
            flat.update(flatten(value, path))
    else:
        flat[prefix] = data
    return flat


def get_field(data: dict[str, Any], dot_path: str) -> Any:
    """Look up a dot-path like 'cost.total_cost_usd' in a nested dict. Returns None if absent."""
    node: Any = data
    for part in dot_path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def project_dir_name(path: str) -> str:
    """Last path segment, e.g. 'my-project' from '/home/user/my-project' or 'C:\\work\\my-project'."""
    cleaned = path.rstrip("/\\")
    if not cleaned:
        return path
    return cleaned.replace("\\", "/").rsplit("/", 1)[-1]


def read_rate_config(rate_file: Path) -> dict[str, Any] | None:
    """Load exchange_rate.json, transparently upgrading the legacy {"usd_to_idr": ...} schema."""
    if not rate_file.exists():
        return None
    try:
        config = json.loads(rate_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(config, dict):
        return None

    rates = config.get("rates")
    if rates is None and "usd_to_idr" in config:
        rates = {"IDR": config["usd_to_idr"]}  # legacy single-currency schema
    config["rates"] = {str(code).upper(): value for code, value in (rates or {}).items()}
    return config


def load_rate(currency: str, rate_arg: float | None, rate_file: Path) -> tuple[float, str]:
    """
    Resolve the USD->`currency` rate to use, in priority order:
    1. --rate given on the command line
    2. the "rates" entry for `currency` in exchange_rate.json (or its legacy usd_to_idr key)
    3. a built-in fallback (IDR only)

    Returns (rate, source_description). Exits with a helpful message if no rate can be found.
    """
    currency = currency.upper()

    if rate_arg is not None:
        return rate_arg, "command line (--rate)"

    config = read_rate_config(rate_file)
    if config is not None:
        try:
            rate = float(config["rates"][currency])
            updated_at = config.get("updated_at", "unknown date")
            return rate, f"{rate_file.name} (manually set on {updated_at})"
        except (KeyError, TypeError, ValueError):
            pass

    if currency in FALLBACK_RATES:
        return FALLBACK_RATES[currency], "built-in fallback (no exchange_rate.json found)"

    raise SystemExit(
        f"No exchange rate found for {currency}. Add \"{currency}\": <rate> to the "
        f'"rates" object in {rate_file}, or pass --rate <value> for a one-off conversion.'
    )


def rate_staleness_warning(rate_file: Path) -> str | None:
    """Return a one-line warning if exchange_rate.json's updated_at is more than RATE_STALE_DAYS old."""
    config = read_rate_config(rate_file)
    if config is None:
        return None
    try:
        updated_at = datetime.date.fromisoformat(config["updated_at"])
    except (KeyError, TypeError, ValueError):
        return None

    age_days = (datetime.date.today() - updated_at).days
    if age_days > RATE_STALE_DAYS:
        return (
            f"({rate_file.name} is {age_days} days old, last updated {updated_at.isoformat()} "
            "- consider refreshing its rates)"
        )
    return None


def plural_suffix(count: int) -> str:
    return "" if count == 1 else "s"


def format_usd(amount: float) -> str:
    return f"${amount:,.4f}"


def format_idr(amount: float) -> str:
    # Indonesian convention: '.' as the thousands separator, no decimals for whole rupiah.
    return f"Rp {amount:,.0f}".replace(",", ".")


def format_currency(amount: float, currency_code: str) -> str:
    """Format `amount` in `currency_code`, using Indonesian-style grouping only for IDR."""
    currency_code = currency_code.upper()
    if currency_code == "IDR":
        return format_idr(amount)

    decimals = 0 if currency_code in ZERO_DECIMAL_CURRENCIES else 2
    formatted = f"{amount:,.{decimals}f}"
    symbol = CURRENCY_SYMBOLS.get(currency_code)
    return f"{symbol}{formatted}" if symbol else f"{currency_code} {formatted}"


def converted_suffix(amount_usd: float, currency: str | None, rate: float | None) -> str:
    """' (~CONVERTED)' if a currency/rate is given, else '' — shared by --stats and --dashboard."""
    if not currency:
        return ""
    return f" (~{format_currency(amount_usd * rate, currency)})"


def format_rate(rate: float, currency_code: str) -> str:
    """Format a USD->currency rate for display, e.g. '16,300' or '0.9200'."""
    decimals = 0 if rate >= 100 else 4
    return f"{rate:,.{decimals}f} {currency_code.upper()}/USD"


def format_duration(total_ms: float) -> str:
    """Format milliseconds as a compact human-readable duration, e.g. 12m34s or 1h02m."""
    total_seconds = int(total_ms // 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{seconds:02d}s"
    return f"{seconds}s"


def format_burn_rate(cost_usd: float, duration_ms: float) -> str:
    """Extrapolate cost/duration to a $/hr rate, e.g. '$9.87/hr' — how fast this session is spending."""
    hours = duration_ms / 3_600_000
    return f"${cost_usd / hours:,.2f}/hr"


def read_history(history_file: Path) -> dict[str, Any]:
    """Load the local usage-tracking file written by --track. Missing/malformed files just mean no history yet."""
    if not history_file.exists():
        return {}
    try:
        history = json.loads(history_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return history if isinstance(history, dict) else {}


def write_history(history_file: Path, history: dict[str, Any]) -> None:
    """
    Write atomically (temp file + os.replace) so a reader never sees a partially-written or
    corrupted file, even if two statusline renders happen to write around the same time.
    A per-process temp filename avoids concurrent writers colliding with each other's temp file.
    """
    tmp_file = history_file.with_name(f".{history_file.name}.{os.getpid()}.tmp")
    tmp_file.write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp_file, history_file)


def upsert_session_record(history: dict[str, Any], payload: dict[str, Any]) -> bool:
    """
    Update `history` in place with today's latest snapshot for this session (keyed by session_id,
    so repeated statusLine renders within the same session overwrite rather than pile up).

    Returns False (no-op) if the payload has no session_id to key on.
    """
    session_id = get_field(payload, "session_id")
    if not isinstance(session_id, str) or not session_id:
        return False

    cost = get_field(payload, "cost.total_cost_usd")
    project_dir = get_field(payload, "workspace.current_dir") or get_field(payload, "cwd")
    history[session_id] = {
        "date": datetime.date.today().isoformat(),
        "cost_usd": float(cost) if isinstance(cost, (int, float)) else 0.0,
        "model": get_field(payload, "model.display_name"),
        "project": project_dir_name(project_dir) if isinstance(project_dir, str) and project_dir else None,
    }
    return True


def record_session(history_file: Path, payload: dict[str, Any]) -> None:
    """Read, update, and rewrite the history file with this run's session snapshot (if it has a session_id)."""
    history = read_history(history_file)
    if upsert_session_record(history, payload):
        write_history(history_file, history)


GROUP_BY_LABELS = {"date": "day", "project": "project", "model": "model"}


def group_costs(history: dict[str, Any], key: str) -> dict[str, tuple[float, int]]:
    """
    Sum cost_usd and count sessions per `key` field ('date', 'project', or 'model') across
    tracked history records; missing values bucket as 'unknown'. Shared by build_stats_report
    (the text report) and build_dashboard_html (the HTML bar charts).

    Returns {label: (total_cost, session_count)}.
    """
    groups: dict[str, tuple[float, int]] = {}
    for record in history.values():
        group = record.get(key) or "unknown"
        cost, count = groups.get(group, (0.0, 0))
        groups[group] = (cost + record.get("cost_usd", 0.0), count + 1)
    return groups


def build_stats_report(
    history: dict[str, Any], group_by: str = "date", currency: str | None = None, rate: float | None = None
) -> str:
    """
    Render a cost/session-count breakdown plus a grand total, from --track's recorded sessions.

    `group_by` is "date" (the default — a per-day breakdown), "project", or "model" — whichever
    field of the tracked session record to attribute cost to (same values shown in the summary line).
    Pass `currency`/`rate` (e.g. from --currency/--idr) to also show a converted estimate.
    """
    if group_by not in GROUP_BY_LABELS:
        raise ValueError(f"group_by must be one of {list(GROUP_BY_LABELS)}, got {group_by!r}")
    if not history:
        return "No tracked sessions yet. Run with --track to start recording."

    groups = group_costs(history, group_by)
    key_width = max(len(key) for key in groups)
    lines = []
    grand_total = 0.0
    grand_sessions = 0
    for key in sorted(groups):
        subtotal, count = groups[key]
        grand_total += subtotal
        grand_sessions += count
        converted = converted_suffix(subtotal, currency, rate)
        lines.append(f"  {key.ljust(key_width)}    {format_usd(subtotal)}{converted}   ({count} session{plural_suffix(count)})")

    avg = grand_total / grand_sessions if grand_sessions else 0.0
    grand_converted = converted_suffix(grand_total, currency, rate)
    header = f"Usage history by {GROUP_BY_LABELS[group_by]} ({grand_sessions} session{plural_suffix(grand_sessions)} tracked)"
    footer = (
        f"  {'-' * (key_width + 30)}\n"
        f"  {'Total'.ljust(key_width)}    {format_usd(grand_total)}{grand_converted}   "
        f"({grand_sessions} session{plural_suffix(grand_sessions)}, avg {format_usd(avg)}/session)"
    )
    return "\n".join([header, *lines, footer])


DASHBOARD_CSS = """
:root { color-scheme: light dark; }
body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    margin: 2rem auto; max-width: 720px; padding: 0 1rem;
    background: #ffffff; color: #1a1a1a;
}
@media (prefers-color-scheme: dark) {
    body { background: #111318; color: #e8e8e8; }
    .card { background: #1c1f26; }
    .bar-track { background: #2a2e37; }
}
h1 { font-size: 1.5rem; margin-bottom: 0.25rem; }
.subtitle { opacity: 0.7; margin-top: 0; margin-bottom: 2rem; }
.cards { display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 2.5rem; }
.card {
    background: #f2f2f5; border-radius: 10px; padding: 0.9rem 1.4rem; min-width: 140px;
}
.card .label { font-size: 0.8rem; opacity: 0.7; }
.card .value { font-size: 1.6rem; font-weight: 600; }
.card .sub { font-size: 0.75rem; opacity: 0.6; margin-top: 0.2rem; }
section { margin-bottom: 2.5rem; }
h2 { font-size: 1.05rem; margin-bottom: 0.75rem; }
.bar-row {
    display: flex; align-items: center; gap: 0.75rem; margin: 0.4rem 0;
    border-radius: 6px; padding: 0.15rem 0.3rem;
}
.bar-row-highlight { background: rgba(99, 99, 241, 0.12); }
@media (prefers-color-scheme: dark) {
    .bar-row-highlight { background: rgba(129, 140, 248, 0.2); }
}
.bar-label {
    width: 140px; flex-shrink: 0; font-size: 0.85rem; text-align: right;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.bar-track { flex: 1; background: #ececef; border-radius: 4px; height: 18px; overflow: hidden; }
.bar-fill { background: #6366f1; height: 100%; border-radius: 4px; }
.bar-value { width: 190px; flex-shrink: 0; font-size: 0.85rem; opacity: 0.85; }
.highlight-badge {
    font-size: 0.7rem; font-weight: 600; background: #6366f1; color: #fff;
    padding: 0.05rem 0.4rem; border-radius: 3px; margin-left: 0.5rem;
}
.empty { opacity: 0.6; font-style: italic; }
.generated { opacity: 0.5; font-size: 0.8rem; margin-top: 2rem; }
""".strip()


def _dashboard_bar_rows(
    groups: dict[str, tuple[float, int]],
    sort_by_value: bool,
    highlights: dict[str, list[str]] | None = None,
    currency: str | None = None,
    rate: float | None = None,
) -> str:
    """
    Render one HTML bar-chart row per (label, (cost, count)) — the session count sits next to
    the dollar amount so a tall bar's meaning is clear (one expensive session vs. many cheap ones).
    sort_by_value=False sorts alphabetically (for dates); True sorts by cost, highest first.

    `highlights` maps a label to the badge text(s) to show on that row (e.g. {"2026-07-07":
    ["Today", "Priciest"]} if the same day is both) — a label with no entry gets no badge.
    `currency`/`rate`, if given, add a converted estimate next to each dollar amount.
    """
    if not groups:
        return '<p class="empty">No data yet.</p>'

    highlights = highlights or {}
    max_cost = max(cost for cost, _ in groups.values()) or 1.0
    items = sorted(groups.items(), key=lambda kv: kv[1][0], reverse=True) if sort_by_value else sorted(groups.items())

    rows = []
    for label, (cost, count) in items:
        pct = (cost / max_cost) * 100
        safe_label = html.escape(str(label))
        badges = highlights.get(label, [])
        row_class = "bar-row bar-row-highlight" if badges else "bar-row"
        badge_html = "".join(f'<span class="highlight-badge">{html.escape(b)}</span>' for b in badges)
        converted = html.escape(converted_suffix(cost, currency, rate))
        rows.append(
            f'<div class="{row_class}">'
            f'<div class="bar-label" title="{safe_label}">{safe_label}</div>'
            f'<div class="bar-track"><div class="bar-fill" style="width:{pct:.1f}%"></div></div>'
            f'<div class="bar-value">{html.escape(format_usd(cost))}{converted} &middot; '
            f'{count} session{plural_suffix(count)}{badge_html}</div>'
            "</div>"
        )
    return "\n".join(rows)


def _extreme_day_label(
    by_date: dict[str, tuple[float, int]], pick_max: bool, currency: str | None = None, rate: float | None = None
) -> str | None:
    """'DATE ($X.XXXX)' for the highest- or lowest-cost day in by_date, or None if there's no data."""
    if not by_date:
        return None
    selector = max if pick_max else min
    date, (cost, _count) = selector(by_date.items(), key=lambda kv: kv[1][0])
    converted = html.escape(converted_suffix(cost, currency, rate))
    return f"{html.escape(date)} ({html.escape(format_usd(cost))}{converted})"


def build_dashboard_html(
    history: dict[str, Any],
    generated_at: datetime.datetime | None = None,
    currency: str | None = None,
    rate: float | None = None,
) -> str:
    """
    Render a self-contained HTML usage dashboard (summary cards + per-day/project/model bar charts).

    `generated_at` defaults to now; it's a parameter mainly so tests can pin a fixed timestamp.
    `currency`/`rate` (e.g. from --currency/--idr) add a converted estimate next to every amount.
    """
    generated_at = generated_at or datetime.datetime.now()
    session_count = len(history)
    total_cost = sum(record.get("cost_usd", 0.0) for record in history.values())
    avg_cost = total_cost / session_count if session_count else 0.0
    dates = sorted(record["date"] for record in history.values() if record.get("date"))
    date_range = f"{dates[0]} to {dates[-1]}" if dates else "no sessions yet"
    total_converted = html.escape(converted_suffix(total_cost, currency, rate))
    avg_converted = html.escape(converted_suffix(avg_cost, currency, rate))

    by_date = group_costs(history, "date")
    today = datetime.date.today().isoformat()
    priciest_day = _extreme_day_label(by_date, pick_max=True, currency=currency, rate=rate)
    cheapest_day = _extreme_day_label(by_date, pick_max=False, currency=currency, rate=rate)
    extreme_day_cards = ""
    if priciest_day is not None and cheapest_day is not None:
        extreme_day_cards = f"""
  <div class="card"><div class="label">Priciest day</div><div class="value">{priciest_day}</div></div>
  <div class="card"><div class="label">Cheapest day</div><div class="value">{cheapest_day}</div></div>"""

    highlights: dict[str, list[str]] = {}
    if today in by_date:
        highlights.setdefault(today, []).append("Today")
    if by_date:
        priciest_date = max(by_date.items(), key=lambda kv: kv[1][0])[0]
        highlights.setdefault(priciest_date, []).append("Priciest")

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>statusline-toolkit usage dashboard</title>
<style>
{DASHBOARD_CSS}
</style>
</head>
<body>
<h1>Usage dashboard</h1>
<p class="subtitle">{session_count} session{plural_suffix(session_count)} tracked &middot; {html.escape(date_range)}</p>
<div class="cards">
  <div class="card"><div class="label">Total spent</div><div class="value">{html.escape(format_usd(total_cost))}{total_converted}</div></div>
  <div class="card"><div class="label">Sessions</div><div class="value">{session_count}</div></div>
  <div class="card"><div class="label">Avg / session</div><div class="value">{html.escape(format_usd(avg_cost))}{avg_converted}</div></div>{extreme_day_cards}
</div>
<section>
  <h2>Cost by day</h2>
  {_dashboard_bar_rows(by_date, sort_by_value=False, highlights=highlights, currency=currency, rate=rate)}
</section>
<section>
  <h2>Cost by project</h2>
  {_dashboard_bar_rows(group_costs(history, "project"), sort_by_value=True, currency=currency, rate=rate)}
</section>
<section>
  <h2>Cost by model</h2>
  {_dashboard_bar_rows(group_costs(history, "model"), sort_by_value=True, currency=currency, rate=rate)}
</section>
<p class="generated">Generated {html.escape(generated_at.strftime("%Y-%m-%d %H:%M"))}</p>
</body>
</html>
"""


def build_bar(percentage: float, width: int = 10, use_color: bool = True) -> str:
    percentage = max(0, min(100, percentage))
    filled = round(percentage * width / 100)
    bar = "[" + "#" * filled + "-" * (width - filled) + "]"
    return colorize(bar, percentage, use_color)


def detect_os_label() -> str:
    """Human-readable OS name, used only for the setup confirmation message."""
    return {"Windows": "Windows", "Darwin": "macOS", "Linux": "Linux"}.get(platform.system(), platform.system())


def default_settings_path() -> Path:
    """~/.claude/settings.json, resolved per-OS via Path.home()."""
    return Path.home() / ".claude" / "settings.json"


def read_defaults_config(config_file: Path) -> dict[str, Any]:
    """
    Load personal CLI defaults from `config_file` (see DEFAULT_CONFIG_FILE).

    Missing/malformed files, or files that aren't a JSON object, just mean no personal
    defaults — every setting here is optional and CLI flags always take priority.
    """
    if not config_file.exists():
        return {}
    try:
        config = json.loads(config_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return {key: config[key] for key in CONFIG_KEYS if key in config} if isinstance(config, dict) else {}


def merged_flag(cli_value: bool, config: dict[str, Any], key: str) -> bool:
    """CLI flag OR'd with its personal-defaults value — there's no --no-X to force one back off."""
    return cli_value or bool(config.get(key))


def merged_path(cli_value: str | None, config: dict[str, Any], key: str, default: Path) -> Path:
    """CLI value if given, else the personal-defaults value (if it's a string), else `default`."""
    if cli_value:
        return Path(cli_value)
    config_value = config.get(key)
    return Path(config_value) if isinstance(config_value, str) else Path(default)


def merged_colors(config: dict[str, Any]) -> dict[str, str]:
    """
    DEFAULT_SEGMENT_COLORS, with any valid overrides from the personal defaults file's "colors"
    object layered on top. An override is ignored (keeping the default) if its key isn't a
    known segment or its value isn't a resolvable color name/hex — a typo should never mean
    "render this segment with no color at all."
    """
    colors = dict(DEFAULT_SEGMENT_COLORS)
    overrides = config.get("colors")
    if isinstance(overrides, dict):
        for key, value in overrides.items():
            if key in colors and isinstance(value, str) and resolve_color(value) is not None:
                colors[key] = value
    return colors


def build_statusline_command(script_path: Path, currency: str | None = None, track: bool = False) -> str:
    """
    Build the settings.json "command" string that runs this script as a statusLine.

    Uses sys.executable (the interpreter currently running this script) rather than
    a bare "python"/"python3", so it works regardless of which one is on PATH. Forward
    slashes are used for both paths because Claude Code on Windows runs statusLine
    commands through Git Bash by default, which treats unescaped backslashes in paths
    as escape characters; forward slashes work correctly on Windows, macOS, and Linux.
    """
    python_path = Path(sys.executable).as_posix()
    script = script_path.resolve().as_posix()
    flag = f"--currency {currency.upper()}" if currency else "--idr"
    if track:
        flag += " --track"
    return f'"{python_path}" "{script}" {flag}'


def install_statusline(
    settings_path: Path, assume_yes: bool, currency: str | None = None, track: bool = False
) -> None:
    """One-time setup: write this script into settings.json's statusLine config."""
    os_label = detect_os_label()
    command = build_statusline_command(Path(__file__), currency, track)

    settings_path.parent.mkdir(parents=True, exist_ok=True)

    if settings_path.exists():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SystemExit(
                f"{settings_path} exists but is not valid JSON ({exc}). "
                "Fix or remove it by hand before running --setup."
            ) from exc
    else:
        settings = {}

    existing = settings.get("statusLine")
    existing_command = existing.get("command") if isinstance(existing, dict) else None
    if existing is not None and existing_command == command:
        print(f"Detected OS: {os_label}")
        print(f"{settings_path} already points statusLine at statusline-toolkit. Nothing to do.")
        return

    if existing is not None and not assume_yes:
        print(f"{settings_path} already has a statusLine configured:")
        print(json.dumps(existing, indent=2))
        answer = input("Overwrite it with statusline-toolkit? [y/N] ").strip().lower()
        if answer != "y":
            print("Setup cancelled. No changes made.")
            return

    if settings_path.exists():
        backup_path = settings_path.parent / (settings_path.name + ".bak")
        shutil.copy2(settings_path, backup_path)
        print(f"Backed up existing settings to {backup_path}")

    settings["statusLine"] = {"type": "command", "command": command}
    settings_path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")

    print(f"Detected OS: {os_label}")
    print(f"Updated {settings_path} with:")
    print(json.dumps({"statusLine": settings["statusLine"]}, indent=2))
    print("Restart Claude Code (or start a new session) to see it take effect.")


def print_all(data: dict[str, Any]) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def print_list(data: dict[str, Any]) -> None:
    for path in sorted(flatten(data)):
        print(path)


def print_fields(
    data: dict[str, Any], fields: list[str], currency: str | None, rate: float | None, rate_source: str | None
) -> None:
    for path in fields:
        value = get_field(data, path)
        print(f"{path}: {value!r}")
        if currency and path == "cost.total_cost_usd" and isinstance(value, (int, float)):
            print(f"  -> {format_currency(value * rate, currency)}  (rate: {format_rate(rate, currency)}, source: {rate_source})")


# Optional segments, dropped in this order (first = least essential) when the
# assembled line would otherwise overflow the terminal width. The model name,
# context-usage bar/%, and raw USD cost are the core and are never dropped.
# Plugin segments are always considered even less essential than any of these
# (see plugin_drop_order) — they drop before any built-in segment does.
ADAPTIVE_DROP_ORDER: tuple[str, ...] = ("rate_limits", "burn_rate", "duration", "lines", "project", "currency")

# Keys _assemble_summary_line renders explicitly, in order. Any other key present in
# `parts` (i.e. a plugin segment) renders after these, in whatever order it appears.
_CORE_SEGMENT_KEYS = frozenset({"model", "project", "bar", "cost", "currency", "lines", "duration", "burn_rate", "rate_limits"})


def discover_plugins(plugins_dir: Path) -> list[tuple[str, int, Any]]:
    """
    Load every top-level *.py file in `plugins_dir` as a statusline plugin.

    Each plugin must define `segment(data: dict, use_color: bool) -> str | None` and may set
    a module-level `PRIORITY: int` (default 0 — see plugin_drop_order for what it controls).
    Files starting with "_" are skipped (e.g. a "_shared.py" helper other plugins import).

    A plugin that fails to import, or doesn't define a callable `segment`, is silently
    skipped: a broken plugin file must never crash the statusline. Returns a list of
    (name, priority, segment_fn), where `name` is the filename without ".py".
    """
    if not plugins_dir.is_dir():
        return []

    plugins: list[tuple[str, int, Any]] = []
    for path in sorted(plugins_dir.glob("*.py")):
        if path.stem.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"statusline_plugin_{path.stem}", path)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception:
            continue

        segment_fn = getattr(module, "segment", None)
        if not callable(segment_fn):
            continue
        priority = getattr(module, "PRIORITY", PLUGIN_DEFAULT_PRIORITY)
        if not isinstance(priority, int):
            priority = PLUGIN_DEFAULT_PRIORITY
        plugins.append((path.stem, priority, segment_fn))
    return plugins


def run_plugin_segments(plugins: list[tuple[str, int, Any]], data: dict[str, Any], use_color: bool) -> dict[str, str]:
    """
    Call each plugin's segment(data, use_color) and collect the non-empty string results,
    keyed "plugin:<name>" (namespaced so a plugin can never collide with a built-in segment).

    A plugin that raises, or returns anything other than a non-empty string, is silently
    skipped for this render — same "never crash the statusline" rule as discover_plugins.
    """
    parts: dict[str, str] = {}
    for name, _priority, segment_fn in plugins:
        try:
            result = segment_fn(data, use_color)
        except Exception:
            continue
        if isinstance(result, str) and result:
            parts[f"plugin:{name}"] = result
    return parts


def plugin_drop_order(plugins: list[tuple[str, int, Any]], plugin_parts: dict[str, str]) -> tuple[str, ...]:
    """Plugin segment keys ("plugin:<name>"), lowest declared PRIORITY first — dropped before any built-in segment."""
    ordered = sorted(plugins, key=lambda p: p[1])
    return tuple(f"plugin:{name}" for name, _priority, _fn in ordered if f"plugin:{name}" in plugin_parts)


def _build_summary_parts(
    data: dict[str, Any],
    currency: str | None,
    rate: float | None,
    use_color: bool,
    segment_colors: dict[str, str] = DEFAULT_SEGMENT_COLORS,
) -> dict[str, str]:
    """Build each summary-line segment (already colored/formatted), keyed by name for _assemble_summary_line."""
    model = get_field(data, "model.display_name") or "unknown model"
    used_pct = get_field(data, "context_window.used_percentage")
    cost = get_field(data, "cost.total_cost_usd")
    project_dir = get_field(data, "workspace.current_dir") or get_field(data, "cwd")
    five_hour = get_field(data, "rate_limits.five_hour.used_percentage")
    seven_day = get_field(data, "rate_limits.seven_day.used_percentage")
    lines_added = get_field(data, "cost.total_lines_added")
    lines_removed = get_field(data, "cost.total_lines_removed")
    duration_ms = get_field(data, "cost.total_duration_ms")

    parts: dict[str, str] = {"model": colorize_named(f"[{model}]", segment_colors["model"], use_color)}

    if isinstance(project_dir, str) and project_dir:
        project_text = f"({project_dir_name(project_dir)})"
        parts["project"] = colorize_named(project_text, segment_colors["project"], use_color)

    if isinstance(used_pct, (int, float)):
        parts["bar"] = f"{build_bar(used_pct, use_color=use_color)} {colorize(f'{used_pct:.0f}%', used_pct, use_color)}"

    if isinstance(cost, (int, float)):
        parts["cost"] = colorize_named(format_usd(cost), segment_colors["cost"], use_color)
        if currency:
            converted_text = f"(~{format_currency(cost * rate, currency)})"
            parts["currency"] = colorize_named(converted_text, segment_colors["cost"], use_color)

    if isinstance(lines_added, (int, float)) or isinstance(lines_removed, (int, float)):
        added = int(lines_added) if isinstance(lines_added, (int, float)) else 0
        removed = int(lines_removed) if isinstance(lines_removed, (int, float)) else 0
        added_text = colorize_fixed(f"+{added}", GRADIENT_STOPS[0][1], ANSI_GREEN, use_color)
        removed_text = colorize_fixed(f"-{removed}", GRADIENT_STOPS[-1][1], ANSI_RED, use_color)
        parts["lines"] = f"{added_text}/{removed_text}"

    if isinstance(duration_ms, (int, float)):
        parts["duration"] = colorize_named(format_duration(duration_ms), segment_colors["duration"], use_color)
        if isinstance(cost, (int, float)) and duration_ms > 0:
            burn_text = format_burn_rate(cost, duration_ms)
            parts["burn_rate"] = colorize_named(burn_text, segment_colors["burn_rate"], use_color)

    limit_parts = []
    if isinstance(five_hour, (int, float)):
        limit_parts.append(f"5h: {colorize(f'{five_hour:.0f}%', five_hour, use_color)}")
    if isinstance(seven_day, (int, float)):
        limit_parts.append(f"7d: {colorize(f'{seven_day:.0f}%', seven_day, use_color)}")
    if limit_parts:
        parts["rate_limits"] = " ".join(limit_parts)

    return parts


def _assemble_summary_line(parts: dict[str, str], dropped: frozenset[str]) -> str:
    segments = [parts["model"]]
    if "project" not in dropped and "project" in parts:
        segments.append(parts["project"])
    if "bar" in parts:
        segments.append(parts["bar"])
    line = " ".join(segments)

    if "cost" in parts:
        cost_segment = parts["cost"]
        if "currency" not in dropped and "currency" in parts:
            cost_segment += f" {parts['currency']}"
        line += f" | {cost_segment}"

    for key in ("lines", "duration", "burn_rate", "rate_limits"):
        if key not in dropped and key in parts:
            line += f" | {parts[key]}"

    # Anything else (plugin segments) renders last, in whatever order it appears in `parts`.
    for key, value in parts.items():
        if key not in _CORE_SEGMENT_KEYS and key not in dropped:
            line += f" | {value}"

    return line


def fit_summary_line(parts: dict[str, str], max_width: int | None, drop_order: tuple[str, ...] = ADAPTIVE_DROP_ORDER) -> str:
    """
    Assemble the full summary line, then drop segments (per `drop_order`) until it fits within
    max_width. max_width=None disables fitting entirely (always the full line). Callers with
    plugin segments should pass plugin_drop_order(...) + ADAPTIVE_DROP_ORDER so plugins drop first.
    """
    dropped: set[str] = set()
    line = _assemble_summary_line(parts, frozenset(dropped))
    if max_width is None:
        return line

    for key in drop_order:
        if visible_length(line) <= max_width:
            break
        if key in parts:
            dropped.add(key)
            line = _assemble_summary_line(parts, frozenset(dropped))
    return line


def print_summary(
    data: dict[str, Any],
    currency: str | None,
    rate: float | None,
    rate_source: str | None,
    use_color: bool = True,
    max_width: int | None = None,
    plugins: list[tuple[str, int, Any]] | None = None,
    segment_colors: dict[str, str] = DEFAULT_SEGMENT_COLORS,
) -> None:
    parts = _build_summary_parts(data, currency, rate, use_color, segment_colors)
    drop_order = ADAPTIVE_DROP_ORDER

    if plugins:
        plugin_parts = run_plugin_segments(plugins, data, use_color)
        if plugin_parts:
            parts.update(plugin_parts)
            drop_order = plugin_drop_order(plugins, plugin_parts) + ADAPTIVE_DROP_ORDER

    print(fit_summary_line(parts, max_width, drop_order))

    cost = get_field(data, "cost.total_cost_usd")
    if currency and not isinstance(cost, (int, float)):
        print(f"(no cost.total_cost_usd in this payload yet, so no {currency.upper()} estimate to show)")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect Claude Code statusline JSON data, with an optional USD->any-currency cost estimate.",
    )
    parser.add_argument(
        "--version", action="version", version=f"statusline-toolkit {__version__}",
    )
    parser.add_argument(
        "-i", "--input",
        help="Path to a JSON file with statusline data. Defaults to reading from stdin.",
    )
    parser.add_argument(
        "-a", "--all", action="store_true",
        help="Print the entire payload as formatted JSON.",
    )
    parser.add_argument(
        "-l", "--list", action="store_true",
        help="List every available field path, without values.",
    )
    parser.add_argument(
        "-f", "--field", action="append", default=[], metavar="DOT.PATH",
        help="Print only this field (repeatable), e.g. -f cost.total_cost_usd",
    )
    parser.add_argument(
        "--currency", metavar="CODE", default=None,
        help="Also show cost.total_cost_usd converted to this ISO 4217 currency code "
             "(e.g. EUR, GBP, JPY, IDR), using a fixed (non-realtime) rate.",
    )
    parser.add_argument(
        "--idr", action="store_true",
        help="Shorthand for --currency IDR (kept for backward compatibility).",
    )
    parser.add_argument(
        "--no-color", action="store_true",
        help="Disable ANSI color coding of usage percentages (also respected via the NO_COLOR env var).",
    )
    parser.add_argument(
        "--width", type=int, default=None,
        help="Override the detected terminal width (columns) used to adaptively fit the summary line. "
             "Useful since Claude Code may run this as a subprocess with no attached terminal to detect.",
    )
    parser.add_argument(
        "--no-adapt", action="store_true",
        help="Always print the full summary line; don't drop segments to fit the terminal width.",
    )
    parser.add_argument(
        "--rate", type=float, default=None,
        help="USD to --currency rate to use for this run, overriding exchange_rate.json.",
    )
    parser.add_argument(
        "--rate-file", default=None,
        help=f"Path to the exchange rate config file (default: {DEFAULT_RATE_FILE.name}, "
             "or your personal defaults file's \"rate_file\" if set).",
    )
    parser.add_argument(
        "--setup", action="store_true",
        help="One-time setup: detect your OS and wire this script into ~/.claude/settings.json "
             "as your statusLine command, then exit. Combine with --currency to pick a currency "
             "other than the default IDR.",
    )
    parser.add_argument(
        "-y", "--yes", action="store_true",
        help="With --setup, overwrite an existing statusLine config without asking for confirmation.",
    )
    parser.add_argument(
        "--settings-file", default=None,
        help="With --setup, path to settings.json to update (default: ~/.claude/settings.json).",
    )
    parser.add_argument(
        "--track", action="store_true",
        help="Record this run's session cost into a local history file, for later --stats reporting. "
             "Combined with --setup, also adds --track to the installed statusLine command.",
    )
    parser.add_argument(
        "--stats", action="store_true",
        help="Print an aggregated cost report from history recorded via --track, then exit. "
             "Grouped by day by default; add --by-project or --by-model to group differently.",
    )
    parser.add_argument(
        "--by-project", action="store_true",
        help="With --stats, group the report by project instead of by day.",
    )
    parser.add_argument(
        "--by-model", action="store_true",
        help="With --stats, group the report by model instead of by day (takes priority over --by-project).",
    )
    parser.add_argument(
        "--history-file", default=None,
        help=f"Path to the usage history file used by --track/--stats (default: {DEFAULT_HISTORY_FILE.name}, "
             "or your personal defaults file's \"history_file\" if set).",
    )
    parser.add_argument(
        "--dashboard", action="store_true",
        help="Render an HTML usage dashboard (cost by day/project/model) from tracked history, then exit.",
    )
    parser.add_argument(
        "--dashboard-file", default=None,
        help="Path to write the dashboard HTML to (default: usage_dashboard.html next to the history file).",
    )
    parser.add_argument(
        "--open", action="store_true",
        help="With --dashboard, open the generated HTML file in your default browser.",
    )
    parser.add_argument(
        "--config", default=None,
        help=f"Path to your personal CLI defaults file (default: {DEFAULT_CONFIG_FILE}). "
             "See the README for the settings it supports.",
    )
    parser.add_argument(
        "--plugins-dir", default=None,
        help=f"Directory of custom segment plugins to load (default: {DEFAULT_PLUGINS_DIR}). "
             "See the README for the plugin contract.",
    )
    parser.add_argument(
        "--no-plugins", action="store_true",
        help="Don't load any plugins for this run.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    # Some currency symbols (and the note above) aren't representable in legacy
    # console encodings like Windows' cp1252; force UTF-8 so printing never
    # crashes the statusline, regardless of which currency or OS is in play.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    args = parse_args(argv if argv is not None else sys.argv[1:])

    # CLI flags always win; unset ones fall back to the personal defaults file, then
    # to built-in defaults. See merged_flag/merged_path for the precedence rules.
    config = read_defaults_config(Path(args.config) if args.config else DEFAULT_CONFIG_FILE)
    currency = args.currency or ("IDR" if args.idr else None) or config.get("currency")
    track_enabled = merged_flag(args.track, config, "track")
    by_project = merged_flag(args.by_project, config, "by_project")
    by_model = merged_flag(args.by_model, config, "by_model")
    no_color = merged_flag(args.no_color, config, "no_color")
    no_adapt = merged_flag(args.no_adapt, config, "no_adapt")
    no_plugins = merged_flag(args.no_plugins, config, "no_plugins")
    width = args.width if args.width is not None else config.get("width")
    rate_file = merged_path(args.rate_file, config, "rate_file", DEFAULT_RATE_FILE)
    history_file = merged_path(args.history_file, config, "history_file", DEFAULT_HISTORY_FILE)
    plugins_dir = merged_path(args.plugins_dir, config, "plugins_dir", DEFAULT_PLUGINS_DIR)
    segment_colors = merged_colors(config)
    rate, rate_source = load_rate(currency, args.rate, rate_file) if currency else (None, None)

    if args.setup:
        settings_path = Path(args.settings_file) if args.settings_file else default_settings_path()
        install_statusline(settings_path, args.yes, currency, track_enabled)
        return

    if args.stats:
        group_by = "model" if by_model else ("project" if by_project else "date")
        print(build_stats_report(read_history(history_file), group_by, currency, rate))
        return

    if args.dashboard:
        dashboard_path = Path(args.dashboard_file) if args.dashboard_file else history_file.parent / "usage_dashboard.html"
        dashboard_html = build_dashboard_html(read_history(history_file), currency=currency, rate=rate)
        dashboard_path.write_text(dashboard_html, encoding="utf-8")
        print(f"Wrote {dashboard_path}")
        if args.open:
            webbrowser.open(dashboard_path.resolve().as_uri())
        return

    data = read_input(args.input)
    use_color = not no_color and not os.environ.get("NO_COLOR")
    max_width = None if no_adapt else (width if width is not None else get_terminal_width())

    if track_enabled:
        record_session(history_file, data)

    if args.all:
        print_all(data)
    elif args.list:
        print_list(data)
    elif args.field:
        print_fields(data, args.field, currency, rate, rate_source)
    else:
        plugins = [] if no_plugins else discover_plugins(plugins_dir)
        print_summary(data, currency, rate, rate_source, use_color, max_width, plugins, segment_colors)

    if currency and args.rate is None:
        warning = rate_staleness_warning(rate_file)
        if warning:
            print(warning)


if __name__ == "__main__":
    main()
