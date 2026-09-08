"""Global panic hotkey.

A stuck key walks your character into a wall while you are not holding the
keyboard. Every other safety net here depends on the phone or the network
behaving; this one is the fallback that works when they don't.

Runs its own thread with a Win32 message loop, because RegisterHotKey delivers
WM_HOTKEY to the thread that registered it. No-ops off Windows so the rest of
the helper stays importable on CI.
"""

from __future__ import annotations

import ctypes
import sys
import threading
from ctypes import wintypes
from typing import Callable

from .keys import IS_WINDOWS, normalize

WM_HOTKEY = 0x0312
_HOTKEY_ID = 0xB00B

# Virtual-key codes, needed here because RegisterHotKey speaks VK, not
# scancodes -- unlike SendInput, which needs scancodes to reach games.
_VK = {
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
    "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
    "esc": 0x1B, "space": 0x20, "pause": 0x13, "scrolllock": 0x91,
    "home": 0x24, "end": 0x23, "insert": 0x2D, "delete": 0x2E,
    "pageup": 0x21, "pagedown": 0x22,
}


class PanicHotkey:
    def __init__(self, key: str, on_press: Callable[[], None]) -> None:
        self.key = normalize(key)
        self.on_press = on_press
        self.ok = False
        self.error: str | None = None
        self._thread: threading.Thread | None = None
        self._tid = 0

    def start(self) -> None:
        if not IS_WINDOWS:
            self.error = "not Windows"
            return
        if self.key not in _VK:
            self.error = (
                f"{self.key!r} can't be a global hotkey; "
                f"try one of: {', '.join(sorted(_VK))}"
            )
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="panic")
        self._thread.start()

    def _run(self) -> None:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._tid = kernel32.GetCurrentThreadId()

        if not user32.RegisterHotKey(None, _HOTKEY_ID, 0, _VK[self.key]):
            # Almost always another app already owns the combination.
            self.error = (
                f"{self.key.upper()} is already taken by another program "
                f"(error {ctypes.get_last_error()}); pick a different panic key"
            )
            return
        self.ok = True

        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    try:
                        self.on_press()
                    except Exception as exc:  # never let a callback kill the loop
                        print(f"panic hotkey handler failed: {exc}", file=sys.stderr)
        finally:
            user32.UnregisterHotKey(None, _HOTKEY_ID)
            self.ok = False

    def stop(self) -> None:
        if IS_WINDOWS and self._tid:
            # WM_QUIT unblocks GetMessageW so the thread can unregister.
            ctypes.WinDLL("user32").PostThreadMessageW(self._tid, 0x0012, 0, 0)
