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
3. **Check staleness** — GET `/menus/v2/metadata`, compare `lastModified` against most recent raw snapshot timestamp. If unchanged, log "no changes" and exit early
4. **Fetch full menus** — GET `/menus/v2/menus` with `Toast-Restaurant-External-ID` header
5. **Write raw snapshot** — Save full API response to `data/raw/menus_YYYY-MM-DDTHHMMSS.json`
6. **Process for consumers** — Transform into clean structure, write to `data/current/menus.json`
7. **Generate markdown** — Render human-readable menu to `data/current/menus.md`
8. **Log the run** — Append entry to `data/log.json`

Error handling: On failure, log the error and exit without modifying `data/current/` files. Downstream consumers always see the last successful fetch.

### Discovery Mode

`python download_menus.py --discover` — Authenticates and looks up available restaurant GUIDs. Run once during setup, paste GUID into `.env`.

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

## Scheduling

launchd plist installed to `~/Library/LaunchAgents/com.lowertown.menu-download.plist`. Runs every 6 hours (6am, 12pm, 6pm, midnight). Logs to `data/launchd.log`.

## Authentication

- Toast Standard API access
- Credentials: clientId + clientSecret from Toast Web
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
