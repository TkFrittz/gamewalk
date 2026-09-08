"""Config patch construction.

This is the reference implementation of "edit one field of one list element",
which the Android settings screen has to mirror. Lists replace wholesale on
the PC, so a client that gets this wrong silently destroys the fields it
didn't mention -- and the patch still validates.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.remote import _coerce, _nest, _resolve_indices  # noqa: E402

CURRENT = {
    "profiles": {
        "default": {
            "tiers": [
                {"name": "walk", "keys": ["w"], "enter_spm": 0, "exit_spm": 0},
                {"name": "run", "keys": ["shift", "w"],
                 "enter_spm": 145, "exit_spm": 130},
            ],
            "hold": {"mode": "adaptive", "multiplier": 1.8},
        }
    },
    "deadman_ms": 1000,
}


def patch_for(path: str, value: str):
    return _resolve_indices(_nest(path, _coerce(value)), CURRENT)


def test_scalar_edit():
    assert patch_for("deadman_ms", "1500") == {"deadman_ms": 1500}


def test_nested_scalar_edit_keeps_siblings_implicit():
    """Only the touched branch is sent; the PC deep-merges the rest."""
    patch = patch_for("profiles.default.hold.multiplier", "2.2")
    assert patch == {"profiles": {"default": {"hold": {"multiplier": 2.2}}}}


def test_editing_one_tier_field_preserves_the_others():
    """The regression: sending {"enter_spm": 130} alone would drop name,
    keys and exit_spm, and the validator would fill them from defaults --
    renaming the tier while reporting success."""
    patch = patch_for("profiles.default.tiers.1.enter_spm", "130")
    tiers = patch["profiles"]["default"]["tiers"]

    assert len(tiers) == 2, "the whole list must be sent, not just one element"
    assert tiers[1] == {"name": "run", "keys": ["shift", "w"],
                        "enter_spm": 130, "exit_spm": 130}
    assert tiers[0] == CURRENT["profiles"]["default"]["tiers"][0]


def test_editing_a_tier_key_list_preserves_the_rest_of_the_tier():
    patch = patch_for("profiles.default.tiers.0.keys", '["up"]')
    tier = patch["profiles"]["default"]["tiers"][0]
    assert tier == {"name": "walk", "keys": ["up"], "enter_spm": 0, "exit_spm": 0}


def test_does_not_mutate_the_source_config():
    patch_for("profiles.default.tiers.0.keys", '["up"]')
    assert CURRENT["profiles"]["default"]["tiers"][0]["keys"] == ["w"]


def test_out_of_range_index_is_refused():
    with pytest.raises(SystemExit):
        patch_for("profiles.default.tiers.9.enter_spm", "100")


def test_value_coercion():
    assert _coerce("130") == 130
    assert _coerce("2.5") == 2.5
    assert _coerce("true") is True
    assert _coerce('["shift","w"]') == ["shift", "w"]
    assert _coerce("adaptive") == "adaptive", "bare words stay strings"
