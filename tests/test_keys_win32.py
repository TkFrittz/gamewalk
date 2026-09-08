"""The Win32 INPUT struct layout.

These are the tests that were missing. Every "live" verification of the helper
ran with --dry-run, so the real injection path was never executed once, and a
struct that was 32 bytes instead of 40 shipped happily: SendInput rejected
every call with ERROR_INVALID_PARAMETER and no key was ever pressed.

A wrong layout cannot be caught by any test that mocks the sink, so these
assert the layout itself. They run everywhere -- the structs are pure ctypes
and need no Windows.
"""

from __future__ import annotations

import ctypes

import pytest

from pc import keys

IS_64BIT = ctypes.sizeof(ctypes.c_void_p) == 8


def test_input_struct_is_the_size_windows_expects():
    """40 bytes on x64, 28 on x86. Anything else is rejected with error 87."""
    assert ctypes.sizeof(keys._INPUT) == keys.EXPECTED_INPUT_SIZE


def test_union_is_sized_by_its_largest_member():
    """MOUSEINPUT is the largest, even though only the keyboard member is used.
    Declaring only KEYBDINPUT is what made the struct too small."""
    assert ctypes.sizeof(keys._INPUTUNION) == ctypes.sizeof(keys._MOUSEINPUT)
    assert ctypes.sizeof(keys._INPUTUNION) >= ctypes.sizeof(keys._KEYBDINPUT)


def test_extra_info_is_pointer_sized():
    """ULONG_PTR, not DWORD. Undersizing it shifts nothing on x86 but
    truncates the struct on x64."""
    assert ctypes.sizeof(keys._ULONG_PTR) == ctypes.sizeof(ctypes.c_void_p)


def test_keybdinput_field_offsets():
    """Explicit offsets, so a field reordering can't silently pass the size
    check while writing the scancode into the wrong place."""
    off = {name: getattr(keys._KEYBDINPUT, name).offset
           for name, _ in keys._KEYBDINPUT._fields_}
    assert off["wVk"] == 0
    assert off["wScan"] == 2
    assert off["dwFlags"] == 4
    assert off["time"] == 8
    assert off["dwExtraInfo"] == (16 if IS_64BIT else 12)


def test_union_starts_after_the_type_field():
    """The union is pointer-aligned, so on x64 there are four padding bytes
    after `type` that must not be assumed away."""
    assert keys._INPUT.type.offset == 0
    assert keys._INPUT.u.offset == (8 if IS_64BIT else 4)


def test_scancode_flag_values():
    """Scancodes, not virtual keys -- this is what makes games see the input."""
    assert keys.KEYEVENTF_SCANCODE == 0x0008
    assert keys.KEYEVENTF_KEYUP == 0x0002
    assert keys.KEYEVENTF_EXTENDEDKEY == 0x0001
    assert keys.INPUT_KEYBOARD == 1


def test_arrow_keys_are_marked_extended():
    """Arrows share scancodes with the numpad; without the extended flag,
    binding "up" would press numpad-8 instead."""
    for name in ("up", "down", "left", "right"):
        assert keys._SCAN[name][1] is True, f"{name} must be extended"
    assert keys._SCAN["w"][1] is False


@pytest.mark.skipif(not keys.IS_WINDOWS, reason="needs Win32")
def test_real_sendinput_is_accepted():
    """Actually call SendInput, which no test did before.

    Sends a key-*up* for a key that isn't held: the API call is exercised in
    full, and a release of an unpressed key is a no-op, so running the suite
    can't type into whatever window happens to be focused.
    """
    sink = keys.WindowsSink()
    sink.release("w")  # raises OSError if SendInput rejects the struct
