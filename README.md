# Lowertown Menu Download

Automated menu downloader for Lowertown Bar & Cafe using the Toast POS Menus API V2. Polls for changes 4x/day via macOS launchd, writing menu data in three formats:

- **Raw JSON snapshots** (`data/raw/`) — timestamped archives of API responses
- **Consumer-ready JSON** (`data/current/menus.json`) — cleaned, with modifiers resolved inline
- **Human-readable Markdown** (`data/current/menus.md`)

Downstream consumers (website auto-updater, menu screen builder, etc.) read from `data/current/`. This project only handles fetching and storing.

## Setup

Requires Python 3 and a `.env` file with Toast API credentials:

```
TOAST_CLIENT_ID=...
TOAST_CLIENT_SECRET=...
TOAST_RESTAURANT_GUID=...
```

Install dependencies:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

## Usage

```bash
.venv/bin/python3 download_menus.py              # Fetch if menu has changed
.venv/bin/python3 download_menus.py --force       # Always fetch, ignore staleness
.venv/bin/python3 download_menus.py --discover    # Verify credentials / find restaurant GUID
```

## Scheduling

A launchd plist is included for automatic runs on macOS:

```bash
ln -s "$(pwd)/com.lowertown.menu-download.plist" ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.lowertown.menu-download.plist
```

## How It Works

1. Authenticate with Toast API using `.env` credentials
2. Check the metadata endpoint for `lastUpdated` — skip if unchanged
3. Fetch full menus, save raw snapshot to `data/raw/`
4. Resolve Toast's modifier reference system (IDs → inline data), transform into clean consumer format
5. Write `data/current/menus.json` and `data/current/menus.md` (atomic writes)
6. Append entry to `data/log.json` (trimmed to 500 entries)

## Project Structure

```
download_menus.py                    # Main script
requirements.txt                     # Python dependencies
com.lowertown.menu-download.plist    # macOS launchd schedule
.env                                 # Toast API credentials (not committed)
data/
  current/                           # Latest processed output
    menus.json                       # Consumer-ready JSON
    menus.md                         # Human-readable markdown
  raw/                               # Timestamped raw API snapshots
  log.json                           # Run history
```
