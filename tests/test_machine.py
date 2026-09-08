"""State machine: the properties that make it feel smooth rather than stuttery."""

from __future__ import annotations

import pytest

from pc import config as cfgmod
from pc.keys import DryRunSink, Held
from pc.machine import Machine


def build(**overrides):
    raw = {
        "version": 1,
        "profiles": {
            "default": {
                "tiers": [
                    {"name": "walk", "keys": ["w"], "enter_spm": 0, "exit_spm": 0},
                    {"name": "run", "keys": ["shift", "w"],
                     "enter_spm": 145, "exit_spm": 130},
                ],
                "hold": {"mode": "adaptive", "multiplier": 1.8,
                         "min_ms": 350, "max_ms": 1400, "fixed_ms": 650},
            }
        },
    }
    raw.update(overrides)
    cfg = cfgmod.validate(raw)
    sink = DryRunSink(echo=False)
    m = Machine(cfg=cfg, held=Held(sink))
    m.arm(0.0)
    return m, sink


def walk(m, spm, seconds, t0=0.0):
    """Feed steady steps, ticking between them like the real loop does."""
    interval = 60000.0 / spm
    t = t0
    end = t0 + seconds * 1000.0
    while t < end:
        m.on_packet(t)
        m.on_step(t)
        t += interval
        m.on_packet(t - 1)
        m.tick(t - 1)
    return t


def test_starts_only_after_enough_steps():
    """One jolt in a pocket must not walk the character off a ledge."""
    m, sink = build()
    m.on_step(0.0)
    assert not m.status.moving
    assert sink.events == []

    m.on_step(500.0)
    assert m.status.moving
    assert sink.events == [("press", "w")]


def test_key_is_held_once_across_a_long_walk():
    """The core smoothness property: one keydown per walk, not one per step."""
    m, sink = build()
    walk(m, spm=120, seconds=30)

    presses = [e for e in sink.events if e[0] == "press"]
    releases = [e for e in sink.events if e[0] == "release"]
    assert presses == [("press", "w")], f"W was re-pressed: {presses}"
    assert releases == []


def test_slow_walk_does_not_stutter():
    """The regression the adaptive hold exists for.

    A fixed 650ms hold expires between 85 spm steps (706ms gaps), releasing and
    re-pressing every single step. Adaptive must not.
    """
    m, sink = build()
    walk(m, spm=85, seconds=20)
    assert [e for e in sink.events if e[0] == "release"] == []


def test_fixed_hold_stutters_at_low_cadence():
    """Proves the above test is actually measuring something."""
    m, sink = build()
    m.cfg.profile.hold.mode = "fixed"
    m.cfg.profile.hold.fixed_ms = 650
    walk(m, spm=85, seconds=20)
    assert [e for e in sink.events if e[0] == "release"], (
        "expected a fixed hold to release between slow steps")


def test_tier_change_does_not_drop_the_shared_key():
    """walk -> run presses Shift and leaves W strictly untouched."""
    m, sink = build()
    walk(m, spm=120, seconds=6)
    sink.events.clear()

    walk(m, spm=170, seconds=6, t0=6000.0)
    assert m.status.tier == "run"
    assert ("release", "w") not in sink.events, "W blipped off during a tier change"
    assert ("press", "shift") in sink.events


def test_hysteresis_prevents_flicker_at_the_boundary():
    """Cadence hovering on the line must not stammer Shift on and off."""
    m, sink = build()
    walk(m, spm=170, seconds=5)
    assert m.status.tier == "run"
    sink.events.clear()

    # Sit between exit (130) and enter (145): neither threshold is crossed.
    walk(m, spm=138, seconds=10, t0=5000.0)
    assert m.status.tier == "run", "dropped out of run inside the hysteresis gap"
    assert not [e for e in sink.events if e[1] == "shift"]


def test_falls_back_to_walk_below_exit_threshold():
    m, _ = build()
    walk(m, spm=170, seconds=5)
    assert m.status.tier == "run"
    walk(m, spm=110, seconds=6, t0=5000.0)
    assert m.status.tier == "walk"


def test_release_after_stopping():
    m, sink = build()
    t = walk(m, spm=120, seconds=5)
    sink.events.clear()

    # Keep packets flowing (the heartbeat would), but stop stepping.
    for dt in range(0, 3000, 50):
        m.on_packet(t + dt)
        m.tick(t + dt)

    assert not m.status.moving
    assert ("release", "w") in sink.events


def test_deadman_releases_when_the_phone_goes_silent():
    """No heartbeat means the phone is gone, not that you are standing still."""
    m, sink = build()
    t = walk(m, spm=120, seconds=5)
    sink.events.clear()

    m.tick(t + 1500.0)  # silence past deadman_ms
    assert not m.status.moving
    assert ("release", "w") in sink.events
    assert m.status.spm == 0.0


def test_disarm_releases_everything():
    m, sink = build()
    walk(m, spm=120, seconds=3)
    m.disarm()
    assert sink.events[-1] == ("release", "w")
    assert m.held.down == set()


def test_steps_ignored_while_disarmed():
    m, sink = build()
    m.disarm()
    sink.events.clear()
    for t in range(0, 3000, 500):
        m.on_step(float(t))
    assert sink.events == []


def test_pause_then_resume_does_not_inherit_stale_cadence():
    """A long gap means 'started again', not 'walked very slowly'."""
    m, _ = build()
    walk(m, spm=170, seconds=5)
    m.tick(20000.0)          # stop, release
    walk(m, spm=100, seconds=5, t0=20000.0)
    assert m.status.tier == "walk"
    assert m.status.spm == pytest.approx(100, abs=15)


def test_cadence_survives_one_bad_interval():
    """Median of 4 rejects a single dropped packet without lagging."""
    m, _ = build()
    walk(m, spm=120, seconds=4)
    steady = m.status.spm

    t = 4000.0
    m.on_step(t)
    m.on_step(t + 1000.0)   # one doubled gap, as if a packet vanished
    m.on_step(t + 1500.0)
    m.on_step(t + 2000.0)
    assert m.status.spm == pytest.approx(steady, rel=0.35)


def test_live_config_change_applies_without_restart():
    """Requirement B: a slider drag is felt on the next step."""
    m, sink = build()
    walk(m, spm=150, seconds=5)
    assert m.status.tier == "run"
    sink.events.clear()

    raw = cfgmod.to_dict(m.cfg)
    raw["profiles"]["default"]["tiers"][1]["keys"] = ["ctrl", "w"]
    m.replace_config(cfgmod.validate(raw))

    assert ("press", "ctrl") in sink.events
    assert ("release", "shift") in sink.events
    assert ("release", "w") not in sink.events, "W dropped during a live edit"


def test_live_config_shrinking_tiers_does_not_crash():
    m, _ = build()
    walk(m, spm=170, seconds=5)
    assert m.status.tier_index == 1

    raw = cfgmod.to_dict(m.cfg)
    raw["profiles"]["default"]["tiers"] = raw["profiles"]["default"]["tiers"][:1]
    m.replace_config(cfgmod.validate(raw))
    assert m.status.tier == "walk"


def test_three_tiers_work_without_code_changes():
    """The tier list is generic; a sprint tier is config, not a patch."""
    m, _ = build(profiles={
        "default": {
            "tiers": [
                {"name": "walk", "keys": ["w"], "enter_spm": 0, "exit_spm": 0},
                {"name": "jog", "keys": ["shift", "w"],
                 "enter_spm": 120, "exit_spm": 110},
                {"name": "sprint", "keys": ["shift", "ctrl", "w"],
                 "enter_spm": 175, "exit_spm": 160},
            ],
            "hold": {"mode": "adaptive", "multiplier": 1.8,
                     "min_ms": 350, "max_ms": 1400, "fixed_ms": 650},
        }
    })
    walk(m, spm=190, seconds=6)
    assert m.status.tier == "sprint"
    assert m.status.held == ("ctrl", "shift", "w")
