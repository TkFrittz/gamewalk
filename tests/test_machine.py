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


# --- reported from real use: bursty arrival --------------------------------

def test_burst_arrival_does_not_wreck_cadence():
    """The bug behind "one step in game, then it stops".

    Wi-Fi power-save queuing and sensor batching deliver several steps at once.
    Measured from arrival, the gaps look like milliseconds, the median interval
    collapses, the adaptive hold clamps to its floor, and the key is released
    before the next real step lands. Cadence must come from the phone's own
    event clock instead.
    """
    m, sink = build()
    event = 0.0
    arrival = 0.0
    for i in range(20):
        event += 500.0                       # a steady 120 spm on the phone
        # ...but delivered in pairs, so every other packet arrives ~15ms later
        arrival = event + (0.0 if i % 2 else 480.0)
        m.on_packet(arrival)
        m.on_step(arrival, event)
        m.tick(arrival)

    assert 100 <= m.status.spm <= 140, f"cadence read as {m.status.spm:.0f} spm"
    assert [e for e in sink.events if e[0] == "release"] == [], (
        "key was released between steps -- this is the in-game stutter")


def test_impossibly_fast_steps_are_ignored():
    """Two events for one footfall must not poison the median."""
    m, _ = build()
    walk(m, spm=110, seconds=6)
    steady = m.status.spm

    t = 6000.0
    for offset in (0.0, 12.0, 25.0):          # a burst of three, 12ms apart
        m.on_packet(t + offset)
        m.on_step(t + offset, t + offset)
    assert m.status.spm == pytest.approx(steady, rel=0.2)


def test_hold_survives_a_late_packet():
    """A step delivered 300ms late must not shorten the hold below the gap."""
    m, sink = build()
    event = 0.0
    for i in range(12):
        event += 520.0
        late = 300.0 if i % 3 == 0 else 0.0
        arrival = event + late
        m.on_packet(arrival)
        m.on_step(arrival, event)
        m.tick(arrival)
    assert [e for e in sink.events if e[0] == "release"] == []


def test_spm_returns_to_zero_when_walking_stops():
    """The display was keeping the last reading after you stood still."""
    m, _ = build()
    t = walk(m, spm=120, seconds=5)
    assert m.status.spm > 0

    for dt in range(0, 3000, 50):             # heartbeats continue, steps don't
        m.on_packet(t + dt)
        m.tick(t + dt)

    assert not m.status.moving
    assert m.status.spm == 0.0


def test_phone_clock_wrap_is_not_a_huge_gap():
    """The phone's counter is mod 1e8 and wraps every ~27 hours."""
    m, _ = build()
    walk(m, spm=120, seconds=4)
    before = m.status.spm

    wrap = 100_000_000
    m.on_packet(5000.0)
    m.on_step(5000.0, wrap - 200.0)
    m.on_packet(5500.0)
    m.on_step(5500.0, 300.0)                  # wrapped: real gap is 500ms
    assert m.status.spm == pytest.approx(before, rel=0.3)


def test_falls_back_to_arrival_time_without_an_event_clock():
    """An older app sends no timestamp; cadence must still work."""
    m, _ = build()
    t = 0.0
    for _ in range(10):
        m.on_packet(t)
        m.on_step(t)
        t += 500.0
    assert 110 <= m.status.spm <= 130
