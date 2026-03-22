# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Toast POS menu downloader for Lowertown Bar & Cafe. A single Python script that polls the Toast Menus API V2, detects changes via the metadata endpoint, and writes menu data in three formats: raw JSON snapshots, consumer-ready JSON, and human-readable markdown. Designed to run on a schedule via macOS launchd (4x/day).

Downstream consumers (website auto-updater, menu screen builder, etc.) read from `data/current/` — this project only handles fetching and storing.

## Commands

```bash
# Setup
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Run
.venv/bin/python3 download_menus.py              # Normal fetch (skips if unchanged)
.venv/bin/python3 download_menus.py --force       # Always fetch, ignore staleness
.venv/bin/python3 download_menus.py --discover    # Verify credentials / find restaurant GUID

# Activate schedule (symlink plist, then load)
ln -s "$(pwd)/com.lowertown.menu-download.plist" ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.lowertown.menu-download.plist
```

## Architecture

Single-file script (`download_menus.py`) with this flow:

1. Load `.env` credentials → authenticate with Toast API
2. Check `/menus/v2/metadata` `lastUpdated` against `data/current/menus.json` `toast_last_modified`
3. If changed (or `--force`): fetch full menus from `/menus/v2/menus`
4. Write raw snapshot to `data/raw/menus_TIMESTAMP.json`
5. Resolve modifier references (Toast returns modifiers as reference IDs in top-level dicts, not inline), transform into clean consumer format → `data/current/menus.json`
6. Generate markdown → `data/current/menus.md`
7. Prune raw snapshots older than 30 days
8. Append to `data/log.json`

All writes to `data/current/` are atomic (write-to-temp, then rename).

## Toast API Notes

- The real API response structure differs from Toast's documentation examples:
  - Response is a **dict** with `menus`, `modifierGroupReferences`, `modifierOptionReferences` keys (not a bare array)
  - Menu items are in `menuItems` (not `items`)
  - Prices are **dollars as floats** (e.g., `3.5`), not cents as integers
  - Modifiers use a reference system: items have `modifierGroupReferences` (integer IDs), resolved against top-level lookup dicts
  - Metadata uses `lastUpdated` key (not `lastModified`)
- Rate limit: 1 req/s for `/menus/v2/menus`, 20 req/s for `/menus/v2/metadata`
- Auth: Standard API access, `TOAST_MACHINE_CLIENT` type, scope `menus:read`

## Key Design Decisions

- **No visibility filtering**: All menus/items are stored regardless of channel visibility. Downstream consumers filter as needed.
- **No retries**: Failures log and exit; the next scheduled run handles transient issues.
- **Staleness via metadata endpoint**: Cheap call (generous rate limit) avoids unnecessary full menu fetches.
- **Log trimmed to 500 entries**: Prevents unbounded growth at 4 entries/day.
