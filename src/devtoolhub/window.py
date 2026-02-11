"""Win32 window focus utilities via ctypes (Windows only)."""

from __future__ import annotations

import sys

if sys.platform == "win32":
    import ctypes

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    SW_RESTORE = 9

    def focus_window(title: str) -> bool:
        """Find a window by exact title and bring it to the foreground.

        Returns True if the window was found and focused.
        """
        hwnd = user32.FindWindowW(None, title)
        if not hwnd:
            return False
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        user32.SetForegroundWindow(hwnd)
        return True

else:

    def focus_window(title: str) -> bool:
        """Stub for non-Windows platforms."""
        return False
