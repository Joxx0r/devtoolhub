"""Win32 window focus and process launch utilities (Windows only)."""

from __future__ import annotations

import subprocess
import sys

if sys.platform == "win32":
    import ctypes

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    SW_RESTORE = 9
    KEYEVENTF_KEYUP = 0x0002
    VK_MENU = 0x12  # Alt key

    def focus_window(title: str) -> bool:
        """Find a window by exact title and bring it to the foreground.

        Uses the Alt-key workaround to bypass Windows foreground lock
        restrictions that prevent background processes from stealing focus.
        """
        hwnd = user32.FindWindowW(None, title)
        if not hwnd:
            return False

        # Restore if minimized
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)

        # Simulate Alt key press — this allows SetForegroundWindow to succeed
        # even when called from a background process (Windows restriction workaround)
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)

        user32.SetForegroundWindow(hwnd)
        return True

    CREATE_NEW_PROCESS_GROUP = 0x00000200
    DETACHED_PROCESS = 0x00000008

    def launch_process(command: str) -> int | None:
        """Launch a command as a detached process. Returns PID or None."""
        try:
            proc = subprocess.Popen(
                command,
                shell=True,
                creationflags=CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
            return proc.pid
        except OSError:
            return None

else:

    def focus_window(title: str) -> bool:
        """Stub for non-Windows platforms."""
        return False

    def launch_process(command: str) -> int | None:
        """Launch a command as a background process."""
        try:
            proc = subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            return proc.pid
        except OSError:
            return None
