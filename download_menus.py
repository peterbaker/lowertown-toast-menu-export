#!/usr/bin/env python3
"""
Toast Menu Downloader for Lowertown.

Downloads menus from the Toast POS API and stores them as:
  - Timestamped raw JSON snapshots in data/raw/
  - Consumer-ready JSON in data/current/menus.json
  - Human-readable markdown in data/current/menus.md

Usage:
  python download_menus.py             # Normal fetch
  python download_menus.py --discover  # Look up restaurant GUID
  python download_menus.py --force     # Skip staleness check, always fetch
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import requests
from dotenv import load_dotenv

from menu_diff import diff_menu, record_changes

# ── Paths ────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
CURRENT_DIR = DATA_DIR / "current"
LOG_FILE = DATA_DIR / "log.json"
MAX_LOG_ENTRIES = 500
RAW_RETENTION_DAYS = 30


# ── Toast API ────────────────────────────────────────────────────────

def authenticate(hostname, client_id, client_secret):
    """Authenticate with Toast and return a bearer token."""
    resp = requests.post(
        f"https://{hostname}/authentication/v1/authentication/login",
        json={
            "clientId": client_id,
            "clientSecret": client_secret,
            "userAccessType": "TOAST_MACHINE_CLIENT",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["token"]["accessToken"]


def fetch_metadata(hostname, token, restaurant_guid):
    """Fetch menu metadata (lastModified timestamp)."""
    resp = requests.get(
        f"https://{hostname}/menus/v2/metadata",
        headers={
            "Authorization": f"Bearer {token}",
            "Toast-Restaurant-External-ID": restaurant_guid,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_menus(hostname, token, restaurant_guid):
    """Fetch full menu data from Toast Menus API V2."""
    resp = requests.get(
        f"https://{hostname}/menus/v2/menus",
        headers={
            "Authorization": f"Bearer {token}",
            "Toast-Restaurant-External-ID": restaurant_guid,
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_stock(hostname, token, restaurant_guid):
    """Fetch inventory/stock status. Returns set of out-of-stock item GUIDs."""
    resp = requests.get(
        f"https://{hostname}/stock/v1/inventory",
        headers={
            "Authorization": f"Bearer {token}",
            "Toast-Restaurant-External-ID": restaurant_guid,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return {
        item["guid"]
        for item in resp.json()
        if item.get("status") == "OUT_OF_STOCK"
    }


def discover_restaurant(hostname, token, restaurant_guid):
    """Try to fetch restaurant info for discovery/verification."""
    resp = requests.get(
        f"https://{hostname}/restaurants/v1/restaurants/{restaurant_guid}",
        headers={
            "Authorization": f"Bearer {token}",
            "Toast-Restaurant-External-ID": restaurant_guid,
        },
        timeout=30,
    )
    if resp.status_code == 200:
        return resp.json()
    return None


# ── Staleness Check ──────────────────────────────────────────────────

def get_last_modified_from_current():
    """Read toast_last_modified from data/current/menus.json if it exists."""
    current_file = CURRENT_DIR / "menus.json"
    if not current_file.exists():
        return None
    try:
        with open(current_file) as f:
            data = json.load(f)
        return data.get("toast_last_modified")
    except (json.JSONDecodeError, OSError):
        return None


# ── Processing ───────────────────────────────────────────────────────

def format_price(dollars):
    """Convert dollar amount (float) to display string."""
    if dollars is None:
        return None
    return f"${dollars:.2f}"


def resolve_modifiers(item, mod_group_refs, mod_option_refs):
    """Resolve modifier references for a menu item into full objects."""
    modifiers = []
    for ref_id in item.get("modifierGroupReferences", []):
        ref_key = str(ref_id)
        mg = mod_group_refs.get(ref_key)
        if not mg:
            continue
        mod_group = {
            "name": mg.get("name", ""),
            "guid": mg.get("guid", ""),
            "required": mg.get("minSelections", 0) > 0,
            "min_selections": mg.get("minSelections", 0),
            "max_selections": mg.get("maxSelections"),
            "options": [],
        }
        for opt_ref_id in mg.get("modifierOptionReferences", []):
            opt_key = str(opt_ref_id)
            opt = mod_option_refs.get(opt_key)
            if not opt:
                continue
            mod_group["options"].append({
                "name": opt.get("name", ""),
                "guid": opt.get("guid", ""),
                "price": opt.get("price", 0),
                "price_display": format_price(opt.get("price", 0)),
            })
        if mod_group["options"]:
            modifiers.append(mod_group)
    return modifiers


def process_menus(raw_response, restaurant_guid, fetched_at, toast_last_modified):
    """Transform raw Toast API response into the consumer JSON format.

    The Toast API returns a dict with 'menus', 'modifierGroupReferences',
    and 'modifierOptionReferences' keys. Items reference modifiers by ID.
    """
    raw_menus = raw_response.get("menus", [])
    mod_group_refs = raw_response.get("modifierGroupReferences", {})
    mod_option_refs = raw_response.get("modifierOptionReferences", {})

    menus = []
    for menu in raw_menus:
        processed_menu = {
            "name": menu.get("name", ""),
            "guid": menu.get("guid", ""),
            "description": menu.get("description", ""),
            "visibility": menu.get("visibility", []),
            "availability": menu.get("availability"),
            "groups": [],
        }

        for group in menu.get("menuGroups", []):
            processed_group = {
                "name": group.get("name", ""),
                "guid": group.get("guid", ""),
                "description": group.get("description", ""),
                "items": [],
            }

            for item in group.get("menuItems", []):
                processed_item = {
                    "name": item.get("name", ""),
                    "guid": item.get("guid", ""),
                    "description": item.get("description", ""),
                    "price": item.get("price"),
                    "price_display": format_price(item.get("price")),
                    "calories": item.get("calories"),
                    "image": item.get("image"),
                    "visibility": item.get("visibility", []),
                    "modifiers": resolve_modifiers(
                        item, mod_group_refs, mod_option_refs
                    ),
                }
                processed_group["items"].append(processed_item)

            if processed_group["items"]:
                processed_menu["groups"].append(processed_group)

        if processed_menu["groups"]:
            menus.append(processed_menu)

    return {
        "restaurant_guid": restaurant_guid,
        "fetched_at": fetched_at,
        "toast_last_modified": toast_last_modified,
        "menus": menus,
    }


def generate_markdown(consumer_data):
    """Render consumer data as human-readable markdown."""
    lines = []
    fetched_dt = datetime.fromisoformat(consumer_data["fetched_at"])
    lines.append("# Lowertown Menu")
    lines.append(f"*Last updated: {fetched_dt.strftime('%B %d, %Y at %-I:%M %p')}*")
    lines.append("")

    for menu in consumer_data["menus"]:
        lines.append(f"## {menu['name']}")
        if menu.get("description"):
            lines.append(f"*{menu['description']}*")
        lines.append("")

        for group in menu["groups"]:
            lines.append(f"### {group['name']}")
            if group.get("description"):
                lines.append(f"*{group['description']}*")
            lines.append("")

            for item in group["items"]:
                # Item name and price
                price_str = item["price_display"] or "Market Price"
                line = f"- **{item['name']}** — {price_str}"
                if item.get("calories"):
                    line += f" ({item['calories']} cal)"
                lines.append(line)

                # Description
                if item.get("description"):
                    lines.append(f"  {item['description']}")

                # Modifiers
                for mod in item.get("modifiers", []):
                    req = " (required)" if mod["required"] else ""
                    options = []
                    for opt in mod["options"]:
                        if opt["price"] and opt["price"] > 0:
                            options.append(f"{opt['name']} (+{opt['price_display']})")
                        else:
                            options.append(opt["name"])
                    lines.append(f"  - *{mod['name']}{req}:* {', '.join(options)}")

                lines.append("")

    return "\n".join(lines)


# ── Post-Processors ──────────────────────────────────────────────────

SKIP_GROUPS = {"***Don't Make***", "Coffee/Tea"}

BAR_MENU_SOURCES = {
    "qr": "QR Code Menu",
    "happy_hour": "Happy Hour",
    "late_night": "Late Night Happy Hour",
}


def build_bar_menu(consumer_data, out_of_stock_guids=None):
    """Extract bar menu from consumer data: QR Code Menu groups + happy hour sections.

    Items whose GUID is in out_of_stock_guids are excluded.
    """
    if out_of_stock_guids is None:
        out_of_stock_guids = set()
    menus_by_name = {m["name"]: m for m in consumer_data["menus"]}
    bar_menu = {
        "fetched_at": consumer_data["fetched_at"],
        "toast_last_modified": consumer_data["toast_last_modified"],
        "sections": [],
    }

    def slim_item(item):
        return {
            "name": item["name"],
            "guid": item["guid"],
            "description": item.get("description", ""),
            "price": item["price"],
            "price_display": item["price_display"],
        }

    def is_available(item):
        return item["guid"] not in out_of_stock_guids

    # Main sections from QR Code Menu
    qr_menu = menus_by_name.get(BAR_MENU_SOURCES["qr"])
    if qr_menu:
        for group in qr_menu["groups"]:
            if group["name"] in SKIP_GROUPS:
                continue
            items = [slim_item(i) for i in group["items"] if is_available(i)]
            if items:
                bar_menu["sections"].append({"name": group["name"], "items": items})

    # Happy hour sections
    for key in ("happy_hour", "late_night"):
        hh_menu = menus_by_name.get(BAR_MENU_SOURCES[key])
        if not hh_menu:
            continue
        for group in hh_menu["groups"]:
            items = [slim_item(i) for i in group["items"] if is_available(i)]
            if items:
                bar_menu["sections"].append({
                    "name": f"{hh_menu['name']} — {group['name']}",
                    "items": items,
                })

    return bar_menu


CAFE_MENU_GROUPS = {"Coffee", "Tea", "Non-Alcoholic/Kombucha", "Beverages", "Coffee Beans"}


def build_cafe_menu(consumer_data, out_of_stock_guids=None):
    """Extract cafe/coffee menu from Cafe POS Menu.

    Includes Coffee, Tea, Non-Alcoholic/Kombucha, and Beverages groups.
    Items whose GUID is in out_of_stock_guids are excluded.
    """
    if out_of_stock_guids is None:
        out_of_stock_guids = set()
    menus_by_name = {m["name"]: m for m in consumer_data["menus"]}
    cafe_menu = {
        "fetched_at": consumer_data["fetched_at"],
        "toast_last_modified": consumer_data["toast_last_modified"],
        "sections": [],
    }

    def slim_item(item):
        return {
            "name": item["name"],
            "guid": item["guid"],
            "description": item.get("description", ""),
            "price": item["price"],
            "price_display": item["price_display"],
        }

    def is_available(item):
        return item["guid"] not in out_of_stock_guids

    pos_menu = menus_by_name.get("Cafe POS Menu")
    if pos_menu:
        for group in pos_menu["groups"]:
            if group["name"] not in CAFE_MENU_GROUPS:
                continue
            items = [slim_item(i) for i in group["items"] if is_available(i)]
            if items:
                cafe_menu["sections"].append({"name": group["name"], "items": items})

    return cafe_menu


def generate_sectioned_markdown(title, data):
    """Render a sectioned menu (bar, cafe, etc.) as markdown."""
    lines = []
    fetched_dt = datetime.fromisoformat(data["fetched_at"])
    lines.append(f"# {title}")
    lines.append(f"*Last updated: {fetched_dt.strftime('%B %d, %Y at %-I:%M %p')}*")
    lines.append("")

    for section in data["sections"]:
        lines.append(f"## {section['name']}")
        lines.append("")
        for item in section["items"]:
            price_str = item["price_display"] or "Market Price"
            lines.append(f"- **{item['name']}** — {price_str}")
            if item.get("description"):
                lines.append(f"  {item['description']}")
            lines.append("")

    return "\n".join(lines)


# ── File I/O ─────────────────────────────────────────────────────────

def atomic_write(path, content, binary=False):
    """Write content to a temp file then rename into place."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "wb" if binary else "w"
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, mode) as f:
            f.write(content)
        os.replace(tmp_path, path)
    except BaseException:
        os.unlink(tmp_path)
        raise


def _load_json_safe(path):
    """Read a JSON file; return None if missing or unreadable."""
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _summarize_event(event):
    """One-line summary of a change-tracking event (for stdout)."""
    parts = []
    for profile, sections in (event or {}).get("profiles", {}).items():
        if sections.get("initial") or not sections:
            continue
        added = removed = priced = 0
        for diff in sections.values():
            if not isinstance(diff, dict):
                continue
            added   += len(diff.get("added", []))
            removed += len(diff.get("removed", []))
            priced  += len(diff.get("price_changes", []))
        if added or removed or priced:
            bits = []
            if added:   bits.append(f"+{added}")
            if removed: bits.append(f"-{removed}")
            if priced:  bits.append(f"${priced}")
            parts.append(f"{profile} ({' '.join(bits)})")
    return ", ".join(parts)


def prune_raw_snapshots():
    """Delete raw snapshots older than RAW_RETENTION_DAYS."""
    if not RAW_DIR.exists():
        return
    cutoff = datetime.now() - timedelta(days=RAW_RETENTION_DAYS)
    pruned = 0
    for f in RAW_DIR.glob("menus_*.json"):
        if f.stat().st_mtime < cutoff.timestamp():
            f.unlink()
            pruned += 1
    if pruned:
        print(f"Pruned {pruned} raw snapshot(s) older than {RAW_RETENTION_DAYS} days.")


def append_log(status, toast_last_modified=None, error=None):
    """Append an entry to data/log.json, trimming to MAX_LOG_ENTRIES."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    entries = []
    if LOG_FILE.exists():
        try:
            with open(LOG_FILE) as f:
                entries = json.load(f)
        except (json.JSONDecodeError, OSError):
            entries = []

    entries.append({
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "status": status,
        "toast_last_modified": toast_last_modified,
        "error": error,
    })

    # Trim to last N entries
    entries = entries[-MAX_LOG_ENTRIES:]
    atomic_write(LOG_FILE, json.dumps(entries, indent=2))


# ── Commands ─────────────────────────────────────────────────────────

def cmd_discover(hostname, client_id, client_secret, restaurant_guid):
    """Discovery mode: verify credentials and look up restaurant info."""
    print("Authenticating with Toast...")
    try:
        token = authenticate(hostname, client_id, client_secret)
    except requests.HTTPError as e:
        print(f"Authentication failed: {e}")
        print("Check your TOAST_CLIENT_ID and TOAST_CLIENT_SECRET in .env")
        sys.exit(1)
    print("Authentication successful.\n")

    if restaurant_guid:
        print(f"Looking up restaurant GUID: {restaurant_guid}")
        info = discover_restaurant(hostname, token, restaurant_guid)
        if info:
            print(f"  Name: {info.get('restaurantName', 'N/A')}")
            print(f"  GUID: {info.get('guid', restaurant_guid)}")
            print(f"  Location: {info.get('location', {}).get('address1', 'N/A')}")
            print("\nRestaurant GUID is valid. You're all set.")
        else:
            print("  Could not fetch restaurant info (endpoint may not be available")
            print("  with Standard API access). But the GUID may still be valid.")
            print("\n  Try running: python download_menus.py --force")
            print("  If menus download successfully, your GUID is correct.")
    else:
        print("No TOAST_RESTAURANT_GUID set in .env.")
        print("\nTo find your restaurant GUID:")
        print("  1. Log into Toast Web (https://www.toasttab.com)")
        print("  2. Go to your restaurant admin")
        print("  3. The GUID is in the URL or under Administration > API Access")
        print("  4. Add it to your .env file as TOAST_RESTAURANT_GUID=<guid>")


def cmd_fetch(hostname, client_id, client_secret, restaurant_guid, force=False):
    """Main fetch: authenticate, check staleness, download, process, save."""
    now = datetime.now()
    fetched_at = now.isoformat(timespec="seconds")

    # Authenticate
    try:
        token = authenticate(hostname, client_id, client_secret)
    except requests.HTTPError as e:
        append_log("error", error=f"Authentication failed: {e}")
        print(f"Authentication failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Check staleness
    try:
        metadata = fetch_metadata(hostname, token, restaurant_guid)
        toast_last_modified = metadata.get("lastUpdated")
    except requests.HTTPError as e:
        append_log("error", error=f"Metadata fetch failed: {e}")
        print(f"Metadata fetch failed: {e}", file=sys.stderr)
        sys.exit(1)

    if not force:
        cached_last_modified = get_last_modified_from_current()
        if cached_last_modified and toast_last_modified == cached_last_modified:
            append_log("unchanged", toast_last_modified=toast_last_modified)
            print(f"Menu unchanged (last modified: {toast_last_modified}). Skipping.")
            return

    # Fetch full menus
    try:
        raw_menus = fetch_menus(hostname, token, restaurant_guid)
    except requests.HTTPError as e:
        append_log("error", toast_last_modified=toast_last_modified,
                   error=f"Menu fetch failed: {e}")
        print(f"Menu fetch failed: {e}", file=sys.stderr)
        sys.exit(1)

    # Write raw snapshot
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    timestamp_str = now.strftime("%Y-%m-%dT%H%M%S")
    raw_path = RAW_DIR / f"menus_{timestamp_str}.json"
    atomic_write(raw_path, json.dumps(raw_menus, indent=2))

    # Process for consumers
    consumer_data = process_menus(raw_menus, restaurant_guid, fetched_at,
                                  toast_last_modified)
    CURRENT_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write(CURRENT_DIR / "menus.json", json.dumps(consumer_data, indent=2))

    # Generate markdown
    markdown = generate_markdown(consumer_data)
    atomic_write(CURRENT_DIR / "menus.md", markdown)

    # Fetch stock for post-processors
    try:
        out_of_stock = fetch_stock(hostname, token, restaurant_guid)
    except requests.HTTPError:
        out_of_stock = set()  # Degrade gracefully — show all items if stock API fails

    # Post-processors — read the old versions FIRST, so we can diff before overwriting
    old_bar  = _load_json_safe(CURRENT_DIR / "bar_menu.json")
    old_cafe = _load_json_safe(CURRENT_DIR / "cafe_menu.json")

    bar_menu = build_bar_menu(consumer_data, out_of_stock)
    atomic_write(CURRENT_DIR / "bar_menu.json", json.dumps(bar_menu, indent=2))
    atomic_write(CURRENT_DIR / "bar_menu.md",
                 generate_sectioned_markdown("Lowertown Bar Menu", bar_menu))

    cafe_menu = build_cafe_menu(consumer_data, out_of_stock)
    atomic_write(CURRENT_DIR / "cafe_menu.json", json.dumps(cafe_menu, indent=2))
    atomic_write(CURRENT_DIR / "cafe_menu.md",
                 generate_sectioned_markdown("Lowertown Cafe Menu", cafe_menu))

    # Change tracking — diff new vs previous consumer menus, append to changes log
    try:
        event = record_changes(
            DATA_DIR,
            timestamp=fetched_at,
            toast_last_modified=toast_last_modified,
            per_profile={
                "bar":  diff_menu(old_bar,  bar_menu),
                "cafe": diff_menu(old_cafe, cafe_menu),
            },
        )
        if event:
            summary = _summarize_event(event)
            if summary:
                print(f"  Changes: {summary}")
    except Exception as e:
        print(f"  (change tracking failed: {e})", file=sys.stderr)

    # Prune old raw snapshots
    prune_raw_snapshots()

    # Log success
    menu_count = len(consumer_data["menus"])
    item_count = sum(
        len(item)
        for m in consumer_data["menus"]
        for g in m["groups"]
        for item in [g["items"]]
    )
    append_log("changed", toast_last_modified=toast_last_modified)
    print(f"Menus downloaded: {menu_count} menus, {item_count} items.")
    print(f"  Raw snapshot: {raw_path}")
    print(f"  Consumer JSON: {CURRENT_DIR / 'menus.json'}")
    print(f"  Markdown: {CURRENT_DIR / 'menus.md'}")


# ── Main ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Download Lowertown menus from Toast")
    parser.add_argument("--discover", action="store_true",
                        help="Look up restaurant GUID and verify credentials")
    parser.add_argument("--force", action="store_true",
                        help="Skip staleness check, always fetch")
    args = parser.parse_args()

    # Load .env
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        print(f"No .env file found at {env_path}")
        print(f"Copy .env.example to .env and fill in your credentials.")
        sys.exit(1)
    load_dotenv(env_path)

    hostname = os.getenv("TOAST_HOSTNAME")
    client_id = os.getenv("TOAST_CLIENT_ID")
    client_secret = os.getenv("TOAST_CLIENT_SECRET")
    restaurant_guid = os.getenv("TOAST_RESTAURANT_GUID")

    if not all([hostname, client_id, client_secret]):
        print("Missing required .env variables: TOAST_HOSTNAME, TOAST_CLIENT_ID, TOAST_CLIENT_SECRET")
        sys.exit(1)

    if args.discover:
        cmd_discover(hostname, client_id, client_secret, restaurant_guid)
    else:
        if not restaurant_guid:
            print("TOAST_RESTAURANT_GUID not set in .env.")
            print("Run: python download_menus.py --discover")
            sys.exit(1)
        cmd_fetch(hostname, client_id, client_secret, restaurant_guid, force=args.force)


if __name__ == "__main__":
    main()
