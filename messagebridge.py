"""MessageBridge client (Python). Verbatim port of client/messagebridge.js.

Never call osascript against Messages/Mail directly: AppleEvents consent is
keyed to the calling binary, Homebrew's python is ad-hoc signed, and an
ungranted call HANGS rather than failing — wedging the target app for every
other caller until it is relaunched. MessageBridge.app has a stable bundle id.
"""
import os
import subprocess
import time
import uuid
from pathlib import Path


def bridge_paths():
    home = Path.home()
    return {
        "app": os.environ.get("MESSAGEBRIDGE_APP", str(home / "Applications" / "MessageBridge.app")),
        "spool": Path(os.environ.get("MESSAGEBRIDGE_SPOOL", home / "Library" / "Application Support" / "MessageBridge" / "outbox")),
        "log": Path(os.environ.get("MESSAGEBRIDGE_LOG", home / "Library" / "Logs" / "messagebridge.log")),
        "open": os.environ.get("MESSAGEBRIDGE_OPEN", "/usr/bin/open"),
        "confirm_ms": int(os.environ.get("MESSAGEBRIDGE_CONFIRM_MS", "15000")),
    }


def send_via_bridge(via, to, body, subject="", tag="job"):
    """Send through MessageBridge.app. Raises if the bridge does not confirm."""
    p = bridge_paths()
    job_name = f"{tag}-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}.txt"

    headers = [f"via: {via}", f"to: {to}"]
    if subject:
        headers.append(f"subject: {subject}")
    p["spool"].mkdir(parents=True, exist_ok=True)
    (p["spool"] / job_name).write_text("\n".join(headers) + "\n\n" + body + "\n", encoding="utf-8")

    subprocess.run([p["open"], "-a", p["app"]], timeout=15, check=True)

    deadline = time.time() + p["confirm_ms"] / 1000
    while time.time() < deadline:
        try:
            for line in reversed(p["log"].read_text(encoding="utf-8").splitlines()):
                if job_name in line:
                    if "ERROR" in line:
                        raise RuntimeError(f"bridge reported: {line.strip()}")
                    return line.strip()
        except FileNotFoundError:
            pass
        time.sleep(0.2)
    raise TimeoutError(f"bridge did not confirm job {job_name} within {p['confirm_ms']}ms")
