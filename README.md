# statusline-toolkit

A small, dependency-free Python CLI for inspecting the JSON payload [Claude
Code](https://claude.com/product/claude-code) sends to a `statusLine` command
(see the [statusline docs](https://code.claude.com/docs/en/statusline)).

It can:

- Show the entire payload, or just the fields you pick.
- List every field path available in a given payload.
- Estimate `cost.total_cost_usd` in Indonesian Rupiah (IDR), using a fixed,
  manually-updated exchange rate — **not** a live currency API call.
- Surface 5-hour / 7-day rate limit usage (`rate_limits.*.used_percentage`)
  in the summary line, when present in the payload.
- Configure itself as your Claude Code statusLine with a single command —
  detects Windows, macOS, or Linux automatically.

## Requirements

- Python 3.9+ (uses only the standard library — no `pip install` needed).

## Usage

```bash
# Live: pipe whatever JSON Claude Code (or anything else) sends on stdin
some_producer_of_json | python statusline_toolkit.py

# Offline: inspect a saved payload instead of stdin
python statusline_toolkit.py --input sample_statusline_data.json --all

# Discover which fields exist in a payload, without printing values
python statusline_toolkit.py --input sample_statusline_data.json --list

# Print only specific fields (repeatable)
python statusline_toolkit.py --input sample_statusline_data.json \
    --field model.display_name --field cost.total_cost_usd

# Add a Rupiah estimate next to the USD cost
python statusline_toolkit.py --input sample_statusline_data.json --idr

# Override the exchange rate for a single run
python statusline_toolkit.py --input sample_statusline_data.json --idr --rate 15800
```

With no flags, it prints a one-line summary (model, context-usage bar, cost,
and rate limit usage when available):

```
[Claude Sonnet 5] [####------] 42% | $0.1234 (~Rp 2.011) | 5h: 24% 7d: 41%
```

### Setting it up as your Claude Code status line

Run the built-in installer once:

```bash
python statusline_toolkit.py --setup
```

This detects your OS (Windows, macOS, or Linux) and writes a `statusLine` entry
into `~/.claude/settings.json` that runs this script with `--idr` using the
same Python interpreter you ran the installer with (`sys.executable`), so
there's no dependency on `python` vs `python3` being on your `PATH`. It:

- Backs up your existing `settings.json` to `settings.json.bak` before making
  any changes.
- Prompts for confirmation if a different `statusLine` is already configured
  (skip the prompt with `-y`/`--yes`).
- Does nothing if it's already configured correctly (safe to run more than once).

Options:

```bash
# Skip the overwrite confirmation prompt
python statusline_toolkit.py --setup --yes

# Point at a different settings.json (mainly useful for testing)
python statusline_toolkit.py --setup --settings-file /path/to/settings.json
```

Restart Claude Code (or start a new session) afterwards to see it take effect.

To configure it by hand instead, the generated entry looks like this:

```json
{
  "statusLine": {
    "type": "command",
    "command": "\"C:/path/to/python.exe\" \"C:/path/to/statusline-toolkit/statusline_toolkit.py\" --idr"
  }
}
```

## Currency conversion — why it's not realtime

`exchange_rate.json` holds a manually maintained USD → IDR rate:

```json
{
  "usd_to_idr": 16300,
  "updated_at": "2026-07-06",
  "note": "Manually maintained, not fetched live. ..."
}
```

The script reads this file for the default rate. To refresh it, edit the
`usd_to_idr` value and `updated_at` date yourself (e.g. by checking
[xe.com](https://www.xe.com) or Google), or pass `--rate <value>` to override
it for a single run without touching the file. This is intentional: no
network calls, no API keys, no rate limits — just a number you control.

## Files

| File | Purpose |
|---|---|
| `statusline_toolkit.py` | The CLI itself, including the `--setup` installer |
| `exchange_rate.json` | Manually maintained USD→IDR rate used by `--idr` |
| `sample_statusline_data.json` | Example payload for trying the tool without a live Claude Code session |

`--setup` also creates `settings.json.bak` next to your real `settings.json`
the first time it changes something — not part of this repo, just mentioned
so you know where your previous config went.
