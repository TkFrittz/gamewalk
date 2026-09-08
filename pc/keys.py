"""Synthetic keyboard input via Win32 SendInput, using hardware scancodes.

The scancode part is the whole point. Games that read input through DirectInput
or RawInput ignore synthetic *virtual-key* events, which is why high-level
automation libraries appear to work everywhere except in the games you actually
want to use them in. Sending KEYEVENTF_SCANCODE makes the event indistinguish-
able from a real key at the level most games read.

This module must import cleanly on non-Windows so the test suite can run on CI
Linux runners; it fails only when you actually try to inject.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Iterable, Protocol

IS_WINDOWS = sys.platform == "win32"

# --- Scancode table (set 1). Extended keys carry the 0xE0 prefix flag. ------
_SCAN: dict[str, tuple[int, bool]] = {
    "esc": (0x01, False), "1": (0x02, False), "2": (0x03, False),
    "3": (0x04, False), "4": (0x05, False), "5": (0x06, False),
    "6": (0x07, False), "7": (0x08, False), "8": (0x09, False),
    "9": (0x0A, False), "0": (0x0B, False), "minus": (0x0C, False),
    "equals": (0x0D, False), "backspace": (0x0E, False), "tab": (0x0F, False),
    "q": (0x10, False), "w": (0x11, False), "e": (0x12, False),
    "r": (0x13, False), "t": (0x14, False), "y": (0x15, False),
    "u": (0x16, False), "i": (0x17, False), "o": (0x18, False),
    "p": (0x19, False), "enter": (0x1C, False), "ctrl": (0x1D, False),
    "lctrl": (0x1D, False), "a": (0x1E, False), "s": (0x1F, False),
    "d": (0x20, False), "f": (0x21, False), "g": (0x22, False),
    "h": (0x23, False), "j": (0x24, False), "k": (0x25, False),
    "l": (0x26, False), "shift": (0x2A, False), "lshift": (0x2A, False),
    "z": (0x2C, False), "x": (0x2D, False), "c": (0x2E, False),
    "v": (0x2F, False), "b": (0x30, False), "n": (0x31, False),
    "m": (0x32, False), "comma": (0x33, False), "period": (0x34, False),
    "slash": (0x35, False), "rshift": (0x36, False), "alt": (0x38, False),
    "lalt": (0x38, False), "space": (0x39, False), "capslock": (0x3A, False),
    "f1": (0x3B, False), "f2": (0x3C, False), "f3": (0x3D, False),
    "f4": (0x3E, False), "f5": (0x3F, False), "f6": (0x40, False),
    "f7": (0x41, False), "f8": (0x42, False), "f9": (0x43, False),
    "f10": (0x44, False), "f11": (0x57, False), "f12": (0x58, False),
    # Extended (0xE0-prefixed)
    "rctrl": (0x1D, True), "ralt": (0x38, True),
    "up": (0x48, True), "left": (0x4B, True),
    "right": (0x4D, True), "down": (0x50, True),
    "insert": (0x52, True), "delete": (0x53, True),
    "home": (0x47, True), "end": (0x4F, True),
    "pageup": (0x49, True), "pagedown": (0x51, True),
}

_ALIASES = {
    "control": "ctrl", "return": "enter", "escape": "esc",
    "pgup": "pageup", "pgdn": "pagedown", "del": "delete", "ins": "insert",
    "spacebar": "space",
}


def normalize(name: str) -> str:
    n = name.strip().lower()
    return _ALIASES.get(n, n)


def is_valid(name: str) -> bool:
    return normalize(name) in _SCAN


def valid_names() -> list[str]:
    return sorted(_SCAN)


# --- Win32 plumbing --------------------------------------------------------

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", _KEYBDINPUT), ("_pad", ctypes.c_byte * 24)]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


class Sink(Protocol):
    """Where key events go. Swappable so tests and --dry-run share one path."""

    def press(self, name: str) -> None: ...
    def release(self, name: str) -> None: ...


class WindowsSink:
    def __init__(self) -> None:
        if not IS_WINDOWS:
            raise RuntimeError("WindowsSink requires Windows; use DryRunSink")
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)

    def _send(self, name: str, up: bool) -> None:
        key = normalize(name)
        try:
            scan, extended = _SCAN[key]
        except KeyError:
            raise KeyError(f"unknown key {name!r}") from None

        flags = KEYEVENTF_SCANCODE
        if extended:
            flags |= KEYEVENTF_EXTENDEDKEY
        if up:
            flags |= KEYEVENTF_KEYUP

        inp = _INPUT(type=INPUT_KEYBOARD)
        inp.ki = _KEYBDINPUT(0, scan, flags, 0, None)
        sent = self._user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))
        if sent != 1:
            raise OSError(
                f"SendInput failed for {key!r}: {ctypes.get_last_error()}"
            )

    def press(self, name: str) -> None:
        self._send(name, up=False)

    def release(self, name: str) -> None:
        self._send(name, up=True)


class DryRunSink:
    """Records instead of injecting.

    The default while developing: a helper that really holds W types into
    whatever editor is focused, which is this project's most annoying possible
    self-inflicted wound.
    """

    def __init__(self, echo: bool = True) -> None:
        self.echo = echo
        self.events: list[tuple[str, str]] = []

    def press(self, name: str) -> None:
        self._record("press", name)

    def release(self, name: str) -> None:
        self._record("release", name)

    def _record(self, action: str, name: str) -> None:
        key = normalize(name)
        if key not in _SCAN:
            raise KeyError(f"unknown key {name!r}")
        self.events.append((action, key))
        if self.echo:
            arrow = "v" if action == "press" else "^"
            print(f"  [dry-run] {arrow} {key}", flush=True)


def make_sink(dry_run: bool, echo: bool = True) -> Sink:
    if dry_run or not IS_WINDOWS:
        return DryRunSink(echo=echo)
    return WindowsSink()


class Held:
    """Tracks which keys are currently down and applies set-differences.

    Never re-applies a key that is already held. Going walk -> run must press
    Shift and leave W strictly untouched: releasing the whole walk set and
    pressing the whole run set would drop W for a frame and visibly hitch the
    character at every speed change.
    """

    def __init__(self, sink: Sink) -> None:
        self.sink = sink
        self.down: set[str] = set()

    def apply(self, wanted: Iterable[str]) -> tuple[set[str], set[str]]:
        target = {normalize(k) for k in wanted}
        to_release = self.down - target
        to_press = target - self.down

        # Release first: a tier swap that reuses a modifier should not briefly
        # hold the union of both tiers.
        for key in sorted(to_release):
            self.sink.release(key)
        for key in sorted(to_press):
            self.sink.press(key)

        self.down = target
        return to_press, to_release

    def release_all(self) -> set[str]:
        released = set(self.down)
        for key in sorted(released):
            self.sink.release(key)
        self.down = set()
        return released
