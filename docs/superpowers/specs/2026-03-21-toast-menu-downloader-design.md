# Toast Menu Downloader — Design Spec

## Purpose

A Python script that downloads Lowertown restaurant menus from the Toast POS API on a schedule (4x/day via macOS launchd), storing them as timestamped JSON snapshots and always-current consumer-ready files (JSON + markdown).

This is a discrete, single-responsibility process. Downstream projects (website auto-updater, menu screen builder, etc.) consume the output files — this script does not know or care about them.

## Constraints

- Single restaurant location
- Standard API access (self-created credentials via Toast Web)
- Runs locally on macOS via launchd
- Credentials stored in `.env` file
- No webhook support needed at 4x/day polling frequency

## Project Structure

```
Lowertown Menu Download/
├── .env                        # TOAST_HOSTNAME, TOAST_CLIENT_ID, TOAST_CLIENT_SECRET, TOAST_RESTAURANT_GUID
├── .env.example                # Template showing required vars
├── .gitignore
├── requirements.txt            # Pin requests, python-dotenv
├── download_menus.py           # Main script
├── data/
│   ├── raw/                    # Timestamped raw JSON snapshots
│   │   └── menus_2026-03-21T080000.json
│   ├── current/
│   │   ├── menus.json          # Latest processed menu (always overwritten)
│   │   └── menus.md            # Latest human-readable menu (always overwritten)
│   └── log.json                # Fetch log: timestamp, status, errors
└── com.lowertown.menu-download.plist  # launchd schedule config
```

## Script Flow (download_menus.py)

1. **Load config** — Read `.env` for credentials and restaurant GUID
2. **Authenticate** — POST `/authentication/v1/authentication/login` with clientId, clientSecret, userAccessType=TOAST_MACHINE_CLIENT
3. **Check staleness** — GET `/menus/v2/metadata`, compare `lastModified` against `toast_last_modified` stored in `data/current/menus.json` (read from the file if it exists). If unchanged, log "no changes" and exit early
4. **Fetch full menus** — GET `/menus/v2/menus` with `Toast-Restaurant-External-ID` header
5. **Write raw snapshot** — Save full API response to `data/raw/menus_YYYY-MM-DDTHHMMSS.json`
6. **Process for consumers** — Transform into clean structure, write to `data/current/menus.json`
7. **Generate markdown** — Render human-readable menu to `data/current/menus.md`
8. **Log the run** — Append entry to `data/log.json`

Error handling: On failure (auth, network, API error), log the error and exit without modifying `data/current/` files. Downstream consumers always see the last successful fetch. No retries — the next scheduled run handles transient failures.

Atomic writes: `data/current/` files are written to a temp file first, then renamed into place, so consumers never see a partially-written file.

### Discovery Mode

`python download_menus.py --discover` — Authenticates and calls GET `/restaurants/v1/restaurants/{restaurantGuid}` (or iterates known endpoints) to display available restaurant info. Run once during setup, paste GUID into `.env`.

Note: With Standard API access, discovery may be limited. If the endpoint isn't available, the user can find their restaurant GUID in Toast Web (Administration > API Access) and enter it manually.

## Consumer JSON Format (data/current/menus.json)

```json
{
  "restaurant_guid": "abc-123",
  "fetched_at": "2026-03-21T08:00:00",
  "toast_last_modified": "2026-03-21T07:45:00",
  "menus": [
    {
      "name": "Lunch",
      "guid": "...",
      "description": "...",
      "groups": [
        {
          "name": "Appetizers",
          "items": [
            {
              "name": "Caesar Salad",
              "guid": "...",
              "description": "...",
              "price": 895,
              "price_display": "$8.95",
              "calories": 450,
              "image": {"url": "..."},
              "modifiers": [
                {
                  "name": "Dressing",
                  "required": true,
                  "options": [
                    {"name": "Ranch", "price": 0, "price_display": "$0.00"}
                  ]
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}
```

Both raw cents (`price`) and formatted strings (`price_display`) included. GUIDs preserved for downstream consumers that need Toast entity references.

## Log Format (data/log.json)

A JSON array, most recent entry last. Each entry:

```json
{
  "timestamp": "2026-03-21T08:00:05",
  "status": "changed",
  "toast_last_modified": "2026-03-21T07:45:00",
  "error": null
}
```

Status values: `"changed"` (new data fetched), `"unchanged"` (skipped, menu not modified), `"error"` (failure, details in `error` field).

Log is kept to the last 500 entries (trimmed on each write).

## Markdown Format (data/current/menus.md)

Example output:

```markdown
# Lowertown Menu
*Last updated: March 21, 2026 at 8:00 AM*

## Lunch

### Appetizers

- **Caesar Salad** — $8.95
  Fresh romaine, parmesan, croutons, house dressing
  - *Dressing (required):* Ranch, Vinaigrette, Blue Cheese (+$0.50)

- **House Salad** — $7.95
  Mixed greens, tomato, cucumber

### Entrees

- **Grilled Salmon** — $18.95
  Pan-seared Atlantic salmon with seasonal vegetables
```

Shows: menu name as H2, group name as H3, items as bold with price, description on next line, modifiers indented below. Zero-price modifiers shown without price; non-zero shown with (+$X.XX). Calories shown if available.

## Scheduling

launchd plist lives in the project directory and is symlinked to `~/Library/LaunchAgents/com.lowertown.menu-download.plist`. Runs every 6 hours (6am, 12pm, 6pm, midnight). Logs stdout/stderr to `data/launchd.log`. Edit the plist in the project, then `launchctl unload/load` to apply changes.

## Authentication

- Toast Standard API access
- Credentials: clientId + clientSecret from Toast Web
- `TOAST_HOSTNAME`: The API hostname provided by Toast (e.g., `ws-api.toasttab.com` for production, sandbox hostname for testing)
- Token cached in memory per run (no persistence needed — each run is short-lived)
- userAccessType: TOAST_MACHINE_CLIENT
- Required scope: menus:read

## Rate Limits

- `/menus/v2/metadata`: 20 req/s (generous, called first each run)
- `/menus/v2/menus`: 1 req/s per location (only called when menu has changed)
- At 4 runs/day for 1 location, rate limits are a non-issue

## Dependencies

- Python 3 (system or brew)
- `requests` library
- `python-dotenv` library
