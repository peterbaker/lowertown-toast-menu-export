"""Webhook-first alerting, with iMessage kept as a fallback code path.

Mirrors the shape of display-scheduler/lib/notify.js and
ops-watchdog/src/notify.js (see lowertown/CLAUDE.md's share-nothing
convention: copy the pattern, don't import across projects). Never raises —
falls back to a printed log line so an alert failure can't interrupt the
primary fetch pipeline.
"""

import requests

from messagebridge import send_via_bridge

# TCC-free transport (LOW-565/LOW-569): webhook -> n8n -> Gmail depends on
# nothing the OS can revoke, so it stays the default. The iMessage path was
# repaired 2026-09-04 by routing it through MessageBridge.app — see
# ops-watchdog/src/notify.js, the reference implementation this mirrors. Same
# URL as ops-watchdog/config.json's alerts.webhookUrl; not secret.
WEBHOOK_URL = "http://127.0.0.1:5678/webhook/lowertown-power-back"

# 'webhook' is the active default (LOW-565/LOW-569); 'imessage' is available
# and goes through MessageBridge.app.
METHOD = "webhook"


def _send_webhook(subject, body):
    resp = requests.post(
        WEBHOOK_URL,
        json={"subject": subject, "message": body},
        timeout=30,
    )
    resp.raise_for_status()


def _send_imessage(recipient, subject, body):
    # Never osascript directly: see messagebridge.py for why an ungranted
    # AppleEvent to Messages hangs and wedges the app for every other caller.
    send_via_bridge("imessage", recipient, f"{subject}\n\n{body}", tag="menu-fetch")


def notify(recipient, subject, body):
    """Send an alert per METHOD. Catches everything; never raises into the
    caller."""
    if METHOD == "webhook":
        try:
            _send_webhook(subject, body)
            print(f"[notify] Sent via webhook: {subject}")
        except Exception as e:
            print(f"[notify] webhook send failed: {e}")
            print(f"{subject}\n{body}")
        return

    if not recipient:
        print(f"[notify] {subject}\n{body}")
        return

    try:
        _send_imessage(recipient, subject, body)
        print(f"[notify] Sent via imessage to {recipient}: {subject}")
    except Exception as e:
        print(f"[notify] imessage send failed: {e}")
        print(f"{subject}\n{body}")
