"""Notification hook (no matcher): desktop notification and optional Slack message.

Best effort and silent: the exit code is always 0 and nothing is ever written to stdout or stderr.
- Desktop: Windows (PowerShell balloon tip), macOS (osascript), Linux (notify-send). A missing
  mechanism is skipped; the child process is started detached and never waited on.
  INCIDENT_NOTIFY_DESKTOP=0 switches this part off (tests, CI).
- Slack: only when SLACK_WEBHOOK_URL is set. JSON POST with a 3 second timeout. The URL is a
  secret: it is never printed, logged or audited, and no error text is ever produced.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from typing import Any

import _common

MAX_TEXT = 300
SLACK_TIMEOUT = 3

WINDOWS_SCRIPT = (
    "Add-Type -AssemblyName System.Windows.Forms; "
    "Add-Type -AssemblyName System.Drawing; "
    "$n = New-Object System.Windows.Forms.NotifyIcon; "
    "$n.Icon = [System.Drawing.SystemIcons]::Information; "
    "$n.Visible = $true; "
    "$n.ShowBalloonTip(5000, $env:INCIDENT_NOTIFY_TITLE, $env:INCIDENT_NOTIFY_BODY, 'Info'); "
    "Start-Sleep -Seconds 6; "
    "$n.Dispose()"
)
MAC_SCRIPT = (
    'display notification (system attribute "INCIDENT_NOTIFY_BODY") '
    'with title (system attribute "INCIDENT_NOTIFY_TITLE")'
)


def clip(text: str, limit: int = MAX_TEXT) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def build_text(payload: dict[str, Any]) -> str:
    message = payload.get("message")
    message = (
        message if isinstance(message, str) and message else "Claude Code needs your attention"
    )
    return clip(f"[{_common.project_dir().name}] {message}")


def desktop_command() -> list[str] | None:
    if sys.platform == "win32":
        exe = shutil.which("powershell") or shutil.which("pwsh")
        if exe:
            return [exe, "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command"]
        return None
    if sys.platform == "darwin":
        exe = shutil.which("osascript")
        return [exe, "-e", MAC_SCRIPT] if exe else None
    exe = shutil.which("notify-send")
    return [exe, "--"] if exe else None


def notify_desktop(title: str, body: str) -> None:
    if os.environ.get("INCIDENT_NOTIFY_DESKTOP") == "0":
        return
    command = desktop_command()
    if command is None:
        return
    env = dict(os.environ)
    env["INCIDENT_NOTIFY_TITLE"] = title
    env["INCIDENT_NOTIFY_BODY"] = body
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": env,
    }
    if sys.platform == "win32":
        command.append(WINDOWS_SCRIPT)
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    elif sys.platform == "darwin":
        kwargs["start_new_session"] = True
    else:
        command.extend([title, body])
        kwargs["start_new_session"] = True
    subprocess.Popen(command, **kwargs)  # noqa: S603 - fire and forget, never waited on


def notify_slack(text: str) -> None:
    url = os.environ.get("SLACK_WEBHOOK_URL", "").strip()
    if not url.lower().startswith(("https://", "http://")):
        return
    data = json.dumps({"text": text}).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=SLACK_TIMEOUT) as response:  # noqa: S310
        response.read(1024)


def main() -> None:
    try:
        payload = _common.read_payload()
    except Exception:
        payload = {}
    try:
        text = build_text(payload)
        title = payload.get("title")
        title = clip(title, 100) if isinstance(title, str) and title else "Claude Code"
    except Exception:
        return
    try:
        notify_desktop(title, text)
    except Exception:
        pass
    try:
        notify_slack(text)
    except Exception:
        # Deliberately silent: error texts of urllib can contain the webhook URL.
        pass


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        pass
    sys.exit(0)
