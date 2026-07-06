#!/usr/bin/env python3
"""
statusline-toolkit
===================

A small CLI for inspecting the JSON payload Claude Code sends to a
`statusLine` command (see https://code.claude.com/docs/en/statusline),
with an optional (non-realtime) USD -> IDR cost conversion.

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

    # Add an IDR estimate next to the USD cost
    python statusline_toolkit.py --input sample_statusline_data.json --idr

    # Override the exchange rate for one run
    python statusline_toolkit.py --input sample_statusline_data.json --idr --rate 15800

    # One-time setup: wire this script up as your Claude Code statusLine
    # (works on Windows, macOS, and Linux)
    python statusline_toolkit.py --setup
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

DEFAULT_RATE_FILE = Path(__file__).parent / "exchange_rate.json"
FALLBACK_USD_TO_IDR = 16300.0  # used only if exchange_rate.json is missing/unreadable


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


def load_usd_to_idr_rate(rate_arg: float | None, rate_file: Path) -> tuple[float, str]:
    """
    Resolve the USD->IDR rate to use, in priority order:
    1. --rate given on the command line
    2. usd_to_idr value in exchange_rate.json
    3. built-in fallback constant

    Returns (rate, source_description).
    """
    if rate_arg is not None:
        return rate_arg, "command line (--rate)"

    if rate_file.exists():
        try:
            config = json.loads(rate_file.read_text(encoding="utf-8"))
            rate = float(config["usd_to_idr"])
            updated_at = config.get("updated_at", "unknown date")
            return rate, f"{rate_file.name} (manually set on {updated_at})"
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            pass

    return FALLBACK_USD_TO_IDR, "built-in fallback (no exchange_rate.json found)"


def format_usd(amount: float) -> str:
    return f"${amount:,.4f}"


def format_idr(amount: float) -> str:
    # Indonesian convention: '.' as the thousands separator, no decimals for whole rupiah.
    return f"Rp {amount:,.0f}".replace(",", ".")


def build_bar(percentage: float, width: int = 10) -> str:
    percentage = max(0, min(100, percentage))
    filled = round(percentage * width / 100)
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def detect_os_label() -> str:
    """Human-readable OS name, used only for the setup confirmation message."""
    return {"Windows": "Windows", "Darwin": "macOS", "Linux": "Linux"}.get(platform.system(), platform.system())


def default_settings_path() -> Path:
    """~/.claude/settings.json, resolved per-OS via Path.home()."""
    return Path.home() / ".claude" / "settings.json"


def build_statusline_command(script_path: Path) -> str:
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
    return f'"{python_path}" "{script}" --idr'


def install_statusline(settings_path: Path, assume_yes: bool) -> None:
    """One-time setup: write this script into settings.json's statusLine config."""
    os_label = detect_os_label()
    command = build_statusline_command(Path(__file__))

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
    if existing is not None and existing.get("command") == command:
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


def print_fields(data: dict[str, Any], fields: list[str], idr: bool, rate: float, rate_source: str) -> None:
    for path in fields:
        value = get_field(data, path)
        print(f"{path}: {value!r}")
        if idr and path == "cost.total_cost_usd" and isinstance(value, (int, float)):
            print(f"  -> {format_idr(value * rate)}  (rate: {rate:,.0f} IDR/USD, source: {rate_source})")


def print_summary(data: dict[str, Any], idr: bool, rate: float, rate_source: str) -> None:
    model = get_field(data, "model.display_name") or "unknown model"
    used_pct = get_field(data, "context_window.used_percentage")
    cost = get_field(data, "cost.total_cost_usd")

    five_hour = get_field(data, "rate_limits.five_hour.used_percentage")
    seven_day = get_field(data, "rate_limits.seven_day.used_percentage")

    line = f"[{model}]"
    if isinstance(used_pct, (int, float)):
        line += f" {build_bar(used_pct)} {used_pct:.0f}%"
    if isinstance(cost, (int, float)):
        line += f" | {format_usd(cost)}"
        if idr:
            line += f" (~{format_idr(cost * rate)})"
    limit_parts = []
    if isinstance(five_hour, (int, float)):
        limit_parts.append(f"5h: {five_hour:.0f}%")
    if isinstance(seven_day, (int, float)):
        limit_parts.append(f"7d: {seven_day:.0f}%")
    if limit_parts:
        line += " | " + " ".join(limit_parts)
    print(line)

    if idr and not isinstance(cost, (int, float)):
        print("(no cost.total_cost_usd in this payload yet, so no IDR estimate to show)")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect Claude Code statusline JSON data, with an optional USD->IDR cost estimate.",
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
        "--idr", action="store_true",
        help="Also show cost.total_cost_usd converted to IDR, using a fixed (non-realtime) rate.",
    )
    parser.add_argument(
        "--rate", type=float, default=None,
        help="USD to IDR rate to use for this run, overriding exchange_rate.json.",
    )
    parser.add_argument(
        "--rate-file", default=str(DEFAULT_RATE_FILE),
        help=f"Path to the exchange rate config file (default: {DEFAULT_RATE_FILE.name}).",
    )
    parser.add_argument(
        "--setup", action="store_true",
        help="One-time setup: detect your OS and wire this script into ~/.claude/settings.json "
             "as your statusLine command, then exit.",
    )
    parser.add_argument(
        "-y", "--yes", action="store_true",
        help="With --setup, overwrite an existing statusLine config without asking for confirmation.",
    )
    parser.add_argument(
        "--settings-file", default=None,
        help="With --setup, path to settings.json to update (default: ~/.claude/settings.json).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv if argv is not None else sys.argv[1:])

    if args.setup:
        settings_path = Path(args.settings_file) if args.settings_file else default_settings_path()
        install_statusline(settings_path, args.yes)
        return

    data = read_input(args.input)
    rate, rate_source = load_usd_to_idr_rate(args.rate, Path(args.rate_file))

    if args.all:
        print_all(data)
    elif args.list:
        print_list(data)
    elif args.field:
        print_fields(data, args.field, args.idr, rate, rate_source)
    else:
        print_summary(data, args.idr, rate, rate_source)


if __name__ == "__main__":
    main()
