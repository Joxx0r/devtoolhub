"""Win32 window focus and process launch utilities (Windows only)."""

from __future__ import annotations

import subprocess
import sys

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

    SW_RESTORE = 9
    SW_SHOW = 5
    HWND_TOPMOST = -1
    HWND_NOTOPMOST = -2
    SWP_NOMOVE = 0x0002
    SWP_NOSIZE = 0x0001
    SWP_SHOWWINDOW = 0x0040
    KEYEVENTF_KEYUP = 0x0002
    VK_MENU = 0x12  # Alt key

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _find_hwnd(title: str) -> int:
        """Find a window by exact title, then fall back to partial match."""
        # Exact match first
        hwnd = user32.FindWindowW(None, title)
        if hwnd:
            return hwnd

        # Partial match via EnumWindows (handles title changes like "Unreal Scheduler - ...")
        results: list[int] = []
        needle = title.lower()

        def callback(hwnd: int, _lParam: int) -> bool:
            if user32.IsWindowVisible(hwnd):
                length = user32.GetWindowTextLengthW(hwnd)
                if length:
                    buf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, buf, length + 1)
                    if needle in buf.value.lower():
                        results.append(hwnd)
            return True

        user32.EnumWindows(WNDENUMPROC(callback), 0)
        return results[0] if results else 0

    def focus_window(title: str) -> bool:
        """Find a window by title and bring it to the foreground.

        Uses multiple strategies to reliably steal focus on Windows:
        1. Restore if minimized
        2. Alt-key trick to bypass foreground lock
        3. SetWindowPos topmost/non-topmost to force to front
        """
        hwnd = _find_hwnd(title)
        if not hwnd:
            return False

        # Restore if minimized
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        else:
            user32.ShowWindow(hwnd, SW_SHOW)

        # Alt-key trick to unlock SetForegroundWindow
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)

        user32.SetForegroundWindow(hwnd)

        # Topmost then non-topmost: forces window to front without staying on top
        flags = SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, flags)
        user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, flags)

        return True

    CREATE_NEW_PROCESS_GROUP = 0x00000200
    CREATE_NO_WINDOW = 0x08000000

    def launch_process(
        command: str,
        *,
        cwd: str | None = None,
        wsl: bool = False,
    ) -> int | None:
        """Launch a command as a detached process. Returns PID or None."""
        try:
            if wsl:
                # WSL services: run via wsl.exe with CREATE_NO_WINDOW
                proc = subprocess.Popen(
                    ["wsl", "-d", "Ubuntu", "--", "bash", "-c", command],
                    cwd=cwd,
                    creationflags=CREATE_NO_WINDOW,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                )
            else:
                si = subprocess.STARTUPINFO()
                si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                si.wShowWindow = 0  # SW_HIDE
                proc = subprocess.Popen(
                    command,
                    shell=True,
                    cwd=cwd,
                    creationflags=CREATE_NEW_PROCESS_GROUP,
                    startupinfo=si,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                )
            return proc.pid
        except OSError:
            return None

else:

    def _find_hwnd(title: str) -> int:
        return 0

    def focus_window(title: str) -> bool:
        """Stub for non-Windows platforms."""
        return False

    def launch_process(
        command: str,
        *,
        cwd: str | None = None,
        wsl: bool = False,
    ) -> int | None:
        """Launch a command as a background process."""
        try:
            proc = subprocess.Popen(
                command,
                shell=True,
                cwd=cwd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
            return proc.pid
        except OSError:
            return None
