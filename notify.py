"""Webhook-first alerting, with iMessage kept as a fallback code path.

Mirrors the shape of display-scheduler/lib/notify.js and
ops-watchdog/src/notify.js (see lowertown/CLAUDE.md's share-nothing
convention: copy the pattern, don't import across projects). Never raises —
falls back to a printed log line so an alert failure can't interrupt the
primary fetch pipeline.
"""

import subprocess

import requests

# TCC-free primary transport (LOW-565/LOW-569): Messages.app's AppleScript
# scripting bridge is wedged fleet-wide (osascript hangs indefinitely on
# `get id of every service`/`send ... to buddy`, even though Messages itself
# is healthy and Automation/TCC consent is intact). Webhook -> n8n -> Gmail
# has no TCC/AppleEvent dependency — see ops-watchdog/src/notify.js, the
# reference implementation this mirrors. Same URL as
# ops-watchdog/config.json's alerts.webhookUrl; not secret.
WEBHOOK_URL = "http://127.0.0.1:5678/webhook/lowertown-power-back"

# 'webhook' is the active default (LOW-565/LOW-569). 'imessage' is kept below
# for manual/future use — do not switch back without first confirming
# `get id of every service` returns promptly under osascript.
METHOD = "webhook"


def _esc_applescript(s):
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def _send_webhook(subject, body):
    resp = requests.post(
        WEBHOOK_URL,
        json={"subject": subject, "message": body},
        timeout=30,
    )
    resp.raise_for_status()


def _send_imessage(recipient, subject, body):
    message = f"{subject}\n\n{body}"
    script = (
        'tell application "Messages"\n'
        "  set targetService to 1st service whose service type = iMessage\n"
        f'  set targetBuddy to buddy "{_esc_applescript(recipient)}" of targetService\n'
        f'  send "{_esc_applescript(message)}" to targetBuddy\n'
        "end tell"
    )
    # timeout kills the subprocess (SIGKILL) if osascript wedges in an
    # AppleEvent call to an unresponsive Messages.app.
    subprocess.run(
        ["osascript", "-e", script],
        timeout=15,
        check=True,
        capture_output=True,
    )


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
