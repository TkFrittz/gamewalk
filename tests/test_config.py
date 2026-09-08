"""Config validation.

The app's settings screen is a phone being poked at arm's length. Every rule
here is reachable by dragging a slider, so a rejection must be total: the old
config stays live and nothing is half-applied.
"""

from __future__ import annotations

import json

import pytest

from pc import config as cfgmod
from pc.config import ConfigError

GOOD = {
    "version": 3,
    "profiles": {
        "default": {
            "tiers": [
                {"name": "walk", "keys": ["w"], "enter_spm": 0, "exit_spm": 0},
                {"name": "run", "keys": ["shift", "w"],
                 "enter_spm": 145, "exit_spm": 130},
            ],
        }
    },
}


def variant(**profile_changes):
    raw = json.loads(json.dumps(GOOD))
    raw["profiles"]["default"].update(profile_changes)
    return raw


def test_accepts_the_shipped_default():
    with cfgmod.DEFAULT_PATH.open(encoding="utf-8") as fh:
        cfg = cfgmod.validate(json.load(fh))
    assert cfg.profile.tiers[0].keys == ["w"]


def test_roundtrips_through_json():
    cfg = cfgmod.validate(GOOD)
    assert cfgmod.to_dict(cfgmod.validate(cfgmod.to_dict(cfg))) == cfgmod.to_dict(cfg)


def test_rejects_equal_thresholds():
    """exit == enter removes the hysteresis gap and the tier chatters."""
    raw = variant(tiers=[
        {"name": "walk", "keys": ["w"], "enter_spm": 0, "exit_spm": 0},
        {"name": "run", "keys": ["shift", "w"], "enter_spm": 140, "exit_spm": 140},
    ])
    with pytest.raises(ConfigError, match="flicker"):
        cfgmod.validate(raw)


def test_rejects_inverted_thresholds():
    raw = variant(tiers=[
        {"name": "walk", "keys": ["w"], "enter_spm": 0, "exit_spm": 0},
        {"name": "run", "keys": ["shift", "w"], "enter_spm": 120, "exit_spm": 150},
    ])
    with pytest.raises(ConfigError):
        cfgmod.validate(raw)


def test_rejects_out_of_order_tiers():
    raw = variant(tiers=[
        {"name": "walk", "keys": ["w"], "enter_spm": 0, "exit_spm": 0},
        {"name": "sprint", "keys": ["shift", "w"], "enter_spm": 180, "exit_spm": 170},
        {"name": "jog", "keys": ["ctrl", "w"], "enter_spm": 120, "exit_spm": 110},
    ])
    with pytest.raises(ConfigError, match="above the tier below"):
        cfgmod.validate(raw)


def test_rejects_unknown_key():
    """A typo must be caught here, not at 3am as a silently dead keybind."""
    raw = variant(tiers=[
        {"name": "walk", "keys": ["forwards"], "enter_spm": 0, "exit_spm": 0},
    ])
    with pytest.raises(ConfigError, match="unknown key"):
        cfgmod.validate(raw)


def test_rejects_a_partial_tier():
    """The silent-corruption case: because lists replace wholesale, a patch
    carrying only the changed field would have name and thresholds filled in
    from defaults -- renaming the tier and zeroing its thresholds while
    reporting success."""
    raw = variant(tiers=[
        {"name": "walk", "keys": ["w"], "enter_spm": 0, "exit_spm": 0},
        {"enter_spm": 130},
    ])
    with pytest.raises(ConfigError, match="send the whole tier"):
        cfgmod.validate(raw)


def test_rejects_tier_missing_only_its_name():
    raw = variant(tiers=[{"keys": ["w"], "enter_spm": 0, "exit_spm": 0}])
    with pytest.raises(ConfigError, match="missing name"):
        cfgmod.validate(raw)


def test_rejects_empty_key_list():
    raw = variant(tiers=[{"name": "walk", "keys": [], "enter_spm": 0, "exit_spm": 0}])
    with pytest.raises(ConfigError):
        cfgmod.validate(raw)


def test_rejects_no_tiers():
    with pytest.raises(ConfigError):
        cfgmod.validate(variant(tiers=[]))


def test_rejects_missing_active_profile():
    raw = json.loads(json.dumps(GOOD))
    raw["active_profile"] = "nonexistent"
    with pytest.raises(ConfigError, match="no profile named"):
        cfgmod.validate(raw)


def test_rejects_out_of_range_numbers():
    raw = json.loads(json.dumps(GOOD))
    raw["deadman_ms"] = 50
    with pytest.raises(ConfigError, match="out of range"):
        cfgmod.validate(raw)


def test_rejects_hold_min_above_max():
    raw = variant(hold={"mode": "adaptive", "multiplier": 1.8,
                        "min_ms": 900, "max_ms": 600, "fixed_ms": 650})
    with pytest.raises(ConfigError, match="exceed"):
        cfgmod.validate(raw)


def test_rejects_bad_panic_hotkey():
    raw = json.loads(json.dumps(GOOD))
    raw["panic_hotkey"] = "banana"
    with pytest.raises(ConfigError, match="panic_hotkey"):
        cfgmod.validate(raw)


def test_key_names_are_normalized():
    raw = variant(tiers=[
        {"name": "walk", "keys": ["W", " Control "], "enter_spm": 0, "exit_spm": 0},
    ])
    assert cfgmod.validate(raw).profile.tiers[0].keys == ["w", "ctrl"]


def test_booleans_are_not_numbers():
    """`True` is an int in Python; a JSON true must not pass a range check."""
    raw = json.loads(json.dumps(GOOD))
    raw["deadman_ms"] = True
    with pytest.raises(ConfigError, match="expected a number"):
        cfgmod.validate(raw)


# --- patching --------------------------------------------------------------

def test_deep_merge_preserves_siblings():
    base = {"a": {"x": 1, "y": 2}, "b": 3}
    assert cfgmod.deep_merge(base, {"a": {"y": 9}}) == {"a": {"x": 1, "y": 9}, "b": 3}


def test_deep_merge_replaces_lists_wholesale():
    """A partial list is ambiguous -- is index 1 an edit or an insert? -- and
    guessing would corrupt someone's keybinds."""
    base = {"tiers": [{"name": "walk"}, {"name": "run"}]}
    assert cfgmod.deep_merge(base, {"tiers": [{"name": "jog"}]})["tiers"] == [
        {"name": "jog"}]


def test_patch_rejection_leaves_config_untouched():
    cfg = cfgmod.validate(GOOD)
    before = cfgmod.to_dict(cfg)
    merged = cfgmod.deep_merge(before, {"deadman_ms": 999999})
    with pytest.raises(ConfigError):
        cfgmod.validate(merged)
    assert cfgmod.to_dict(cfg) == before


# --- wire format -----------------------------------------------------------

def test_wire_dict_carries_render_metadata():
    """The app renders settings from these descriptors, so adding a config
    field must not require releasing a new APK."""
    wire = cfgmod.wire_dict(cfgmod.validate(GOOD))
    assert "meta" in wire and "keys" in wire
    assert "hold.multiplier" in wire["meta"]
    assert wire["meta"]["hold.multiplier"]["type"] == "float"


def test_wire_dict_does_not_echo_the_token():
    cfg = cfgmod.validate(GOOD)
    cfg.token = "s3cret"
    assert "token" not in cfgmod.wire_dict(cfg)


def test_every_meta_entry_points_at_a_real_field():
    """Guards against a descriptor outliving the setting it describes, which
    would render a control on the phone that silently does nothing."""
    wire = cfgmod.to_dict(cfgmod.validate(GOOD))
    profile = wire["profiles"]["default"]
    for path in cfgmod.META:
        if path.startswith("tiers[]."):
            assert path.split(".", 1)[1] in profile["tiers"][0]
            continue
        node = profile if path.split(".")[0] in profile else wire
        for part in path.split("."):
            assert part in node, f"meta describes missing field {path!r}"
            node = node[part]


# --- disk ------------------------------------------------------------------

def test_creates_config_from_default_on_first_run(tmp_path):
    path = tmp_path / "config.json"
    cfg = cfgmod.load(path)
    assert path.exists() and cfg.port == 5599


def test_save_is_atomic(tmp_path):
    """A half-written config that fails to parse is a miserable way to lose
    your keybinds."""
    path = tmp_path / "config.json"
    cfg = cfgmod.validate(GOOD)
    cfgmod.save(cfg, path)
    cfgmod.save(cfg, path)
    assert not list(tmp_path.glob("*.tmp"))
    assert cfgmod.load(path).version == 3
