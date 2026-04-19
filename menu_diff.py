"""
Menu change tracking.

After each successful Toast fetch, diffs the new consumer menus (bar, cafe)
against the versions previously on disk and records the changes in:

    data/changes.jsonl  — append-only, one JSON object per change event
    data/changes.md     — rolling human-readable log of the most recent events

A change event captures per-profile, per-section: items added, removed, and
price changes. The first time a menu file is written there's no "previous" —
that event is marked `initial` and skipped from the markdown log.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


MAX_MD_EVENTS = 50  # keep the readable log focused on recent changes


def diff_menu(old: dict | None, new: dict) -> dict:
    """Compare two consumer-menu dicts and return a per-section diff.

    Input shape:  { "sections": [{ "name", "items": [{ "name", "price", ... }] }] }
    Output shape: { section_name: { added: [...], removed: [...], price_changes: [...] } }
    Returns {"initial": True} if there was no previous version.
    """
    if not old:
        return {"initial": True}

    old_sections = {s["name"]: s for s in old.get("sections", [])}
    new_sections = {s["name"]: s for s in new.get("sections", [])}
    out: dict[str, Any] = {}

    for name in sorted(set(old_sections) | set(new_sections)):
        old_s = old_sections.get(name, {"items": []})
        new_s = new_sections.get(name, {"items": []})
        old_items = {i["name"]: i for i in old_s.get("items", [])}
        new_items = {i["name"]: i for i in new_s.get("items", [])}

        added_names   = [n for n in new_items if n not in old_items]
        removed_names = [n for n in old_items if n not in new_items]
        price_changes = []
        for n in new_items:
            if n in old_items and _price_of(old_items[n]) != _price_of(new_items[n]):
                price_changes.append({
                    "name": n,
                    "from": _price_of(old_items[n]),
                    "to":   _price_of(new_items[n]),
                })

        section_added   = name not in old_sections
        section_removed = name not in new_sections

        if not (added_names or removed_names or price_changes or section_added or section_removed):
            continue

        entry: dict[str, Any] = {}
        if section_added:   entry["section_added"]   = True
        if section_removed: entry["section_removed"] = True
        if added_names:
            entry["added"] = [{"name": n, "price": _price_of(new_items[n])} for n in added_names]
        if removed_names:
            entry["removed"] = [{"name": n, "price": _price_of(old_items[n])} for n in removed_names]
        if price_changes:
            entry["price_changes"] = price_changes
        out[name] = entry

    return out


def _price_of(item: dict) -> float | None:
    if "price" in item and item["price"] is not None:
        return item["price"]
    return None


def record_changes(
    data_dir: Path,
    *,
    timestamp: str,
    toast_last_modified: str | None,
    per_profile: dict[str, dict],
) -> dict | None:
    """Append a change event to changes.jsonl and refresh changes.md.

    `per_profile` is a mapping of profile name (e.g. "bar", "cafe") to the diff
    returned by diff_menu(). Profiles that have no changes should be omitted.

    Returns the event that was recorded (or None if nothing meaningful changed).
    """
    meaningful = {k: v for k, v in per_profile.items() if v and not v.get("initial")}
    if not meaningful and not any(v.get("initial") for v in per_profile.values()):
        return None

    event = {
        "timestamp": timestamp,
        "toast_last_modified": toast_last_modified,
        "profiles": per_profile,
    }

    data_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = data_dir / "changes.jsonl"
    with jsonl_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")

    _write_markdown_log(data_dir)
    return event


def _write_markdown_log(data_dir: Path) -> None:
    """Rewrite changes.md with the last MAX_MD_EVENTS events, newest first."""
    jsonl_path = data_dir / "changes.jsonl"
    md_path    = data_dir / "changes.md"
    if not jsonl_path.exists():
        return

    events = []
    with jsonl_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    # Skip initial-snapshot events from the human log (they're noise)
    events = [e for e in events if not all(v.get("initial") for v in e.get("profiles", {}).values())]
    recent = events[-MAX_MD_EVENTS:]

    lines = ["# Lowertown Menu Change Log", ""]
    lines.append(f"*Last {len(recent)} change events, newest first. Full history in `changes.jsonl`.*")
    lines.append("")

    for event in reversed(recent):
        lines.extend(_format_event(event))
        lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")


def _format_event(event: dict) -> list[str]:
    ts = event.get("timestamp", "?")
    try:
        ts_human = datetime.fromisoformat(ts).strftime("%b %d, %Y — %I:%M %p").lstrip("0")
    except ValueError:
        ts_human = ts

    out = [f"## {ts_human}"]
    if event.get("toast_last_modified"):
        out.append(f"*Toast last modified: {event['toast_last_modified']}*")
        out.append("")

    for profile, sections in event.get("profiles", {}).items():
        if sections.get("initial") or not sections:
            continue
        out.append(f"### {profile} menu")
        for section, diff in sections.items():
            if not isinstance(diff, dict):
                continue
            bullets = []
            if diff.get("section_added"):
                bullets.append("_new section_")
            if diff.get("section_removed"):
                bullets.append("_section removed_")
            for item in diff.get("added", []):
                bullets.append(f"➕ **{item['name']}** — {_fmt_price(item.get('price'))}")
            for item in diff.get("removed", []):
                bullets.append(f"➖ **{item['name']}** — was {_fmt_price(item.get('price'))}")
            for change in diff.get("price_changes", []):
                bullets.append(
                    f"💲 **{change['name']}** — {_fmt_price(change['from'])} → {_fmt_price(change['to'])}"
                )
            if bullets:
                out.append(f"**{section}**")
                out.extend(f"- {b}" for b in bullets)
                out.append("")
    return out


def _fmt_price(p):
    if p is None:
        return "—"
    if float(p).is_integer():
        return f"${int(p)}"
    return f"${p:.2f}"
