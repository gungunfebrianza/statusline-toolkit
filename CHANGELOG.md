# Changelog

All notable changes to this project are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/).

## [1.0.0] - 2026-07-07

First release published to [PyPI](https://pypi.org/project/statusline-toolkit/).

### Added
- Multi-currency conversion (`--currency CODE`, any ISO 4217 code), replacing
  the original IDR-only conversion. `--idr` is kept as a shorthand for
  `--currency IDR` for backward compatibility.
- Smooth RGB gradient coloring for usage percentages (green → amber → red),
  with automatic 24-bit truecolor detection and a basic-ANSI fallback.
- Git-style colored lines-added/removed segment (green `+`, red `-`).
- Session duration and a derived burn-rate (`$/hr`) segment.
- Project directory name segment, for telling sessions apart at a glance.
- Adaptive layout: segments drop automatically (least essential first) to
  fit the terminal width (`--width`, `--no-adapt`).
- Opt-in local usage tracking (`--track`) with a text cost report
  (`--stats`, `--by-project`, `--by-model`).
- Self-contained HTML usage dashboard (`--dashboard`, `--dashboard-file`,
  `--open`) — summary cards plus cost-by-day/project/model bar charts.
- Personal defaults file (`~/.claude/statusline-toolkit.json`, `--config`)
  so common flags don't need retyping every run.
- Plugin architecture: custom summary-line segments via
  `~/.claude/statusline-plugins/` (`--plugins-dir`, `--no-plugins`),
  isolated so a broken plugin can never crash the statusline.
- `--version` flag.
- Full `unittest` test suite.
- `.gitignore` for runtime-generated files (`usage_history.json`,
  `usage_dashboard.html`, `__pycache__/`, packaging build artifacts).
- `LICENSE` (MIT).
- CI (`.github/workflows/test.yml`): runs the test suite on Linux/macOS/Windows
  for every push and pull request.
- `pyproject.toml`: installable via `pip`/`pipx` with a `statusline-toolkit`
  console script, version sourced from `__version__`.

### Changed
- `exchange_rate.json` schema generalized from single-currency
  (`usd_to_idr`) to multi-currency (`rates: {...}`); existing single-currency
  files still load unchanged.
- Stale-rate warning generalized to whichever currency is in use.
- README rewritten for clarity, then substantially trimmed for readability.

### Fixed
- Some currency symbols (₩, ₹, ₫, ฿, ₱) could crash the script outright
  under Windows' legacy `cp1252` console encoding. Output is now forced to
  UTF-8, and those currencies fall back to their plain ISO code instead of
  a symbol.
- `exchange_rate.json` containing valid JSON that wasn't an object (e.g. a
  bare array) crashed the statusline instead of being treated as "no rate
  configured."
- `--setup` crashed instead of prompting if an existing `settings.json` had
  a malformed (non-object) `statusLine` value.
- The personal defaults file crashed the whole run if `rate_file`,
  `history_file`, or `plugins_dir` was set to a non-string value, instead
  of falling back to the default.
- `usage_history.json` writes are now atomic (temp file + rename), so a
  reader can never see a partially-written or corrupted file.
- `pyproject.toml` used the deprecated `license = {file = "LICENSE"}` table
  form; switched to the SPDX `license = "MIT"` + `license-files` form
  before setuptools drops support for the old one.

## [0.1.0] - 2026-07-06

Initial commit (never published as a package). Read a Claude Code
statusline JSON payload from stdin or a file, print a one-line summary
(model, context-window bar, cost), with a fixed-rate USD → IDR conversion
(`--idr`) and a `--setup` installer for `~/.claude/settings.json`.
