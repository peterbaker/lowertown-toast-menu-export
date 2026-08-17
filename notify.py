"""iMessage alerting via osascript.

Mirrors the shape of display-scheduler/lib/notify.js and
ops-watchdog/src/notify.js (see lowertown/CLAUDE.md's share-nothing
convention: copy the pattern, don't import across projects). Never raises —
falls back to a printed log line so an alert failure can't interrupt the
primary fetch pipeline.
"""

import subprocess


def _esc_applescript(s):
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def notify(recipient, subject, body):
    """Send a single iMessage. Catches everything; never throws into the caller."""
    if not recipient:
        print(f"[notify] {subject}\n{body}")
        return

    message = f"{subject}\n\n{body}"
    script = (
        'tell application "Messages"\n'
        "  set targetService to 1st service whose service type = iMessage\n"
        f'  set targetBuddy to buddy "{_esc_applescript(recipient)}" of targetService\n'
        f'  send "{_esc_applescript(message)}" to targetBuddy\n'
        "end tell"
    )
    try:
        # timeout kills the subprocess (SIGKILL) if osascript wedges in an
        # AppleEvent call to an unresponsive Messages.app.
        subprocess.run(
            ["osascript", "-e", script],
            timeout=15,
            check=True,
            capture_output=True,
        )
        print(f"[notify] Sent via imessage to {recipient}: {subject}")
    except Exception as e:
        print(f"[notify] imessage send failed: {e}")
        print(f"{subject}\n{body}")
