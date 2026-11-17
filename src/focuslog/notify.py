"""Desktop notifications when a round or break ends.

Uses whatever the operating system already has (no extra packages). This is
best-effort: if anything goes wrong we stay quiet, because a missing
notification should never crash a running timer.
"""

import os
import shutil
import subprocess
import sys

# PowerShell script for a Windows toast. The text comes in through environment
# variables instead of being pasted into the script, so a subject name with
# quotes in it can't break (or inject into) the command.
_WINDOWS_TOAST = """
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$type = [Windows.UI.Notifications.ToastTemplateType]::ToastText02
$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent($type)
$nodes = $xml.GetElementsByTagName('text')
$nodes.Item(0).AppendChild($xml.CreateTextNode($env:FOCUSLOG_TITLE)) | Out-Null
$nodes.Item(1).AppendChild($xml.CreateTextNode($env:FOCUSLOG_MESSAGE)) | Out-Null
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('FocusLog').Show($toast)
"""


def build_command(title: str, message: str, platform: str = sys.platform, which=shutil.which):
    """Return (argv, extra_env) for this platform, or None if we can't notify."""
    if platform == "darwin" and which("osascript"):
        # title and message are passed as arguments, not spliced into the script
        script = [
            "-e", "on run argv",
            "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
            "-e", "end run",
        ]
        return ["osascript", *script, title, message], {}
    if platform.startswith("win") and which("powershell"):
        env = {"FOCUSLOG_TITLE": title, "FOCUSLOG_MESSAGE": message}
        return ["powershell", "-NoProfile", "-Command", _WINDOWS_TOAST], env
    if platform.startswith("linux") and which("notify-send"):
        return ["notify-send", "--app-name=FocusLog", title, message], {}
    return None


def notify(title: str, message: str, bell: bool = True, popen=subprocess.Popen) -> bool:
    """Ring the terminal bell and try to show a desktop notification.

    Returns True if a notification command was started.
    """
    if bell:
        try:
            sys.stdout.write("\a")
            sys.stdout.flush()
        except Exception:
            pass

    command = build_command(title, message)
    if command is None:
        return False
    argv, extra_env = command
    try:
        # Popen (not run) so the timer never waits on the notification
        popen(argv, env={**os.environ, **extra_env},
              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False
