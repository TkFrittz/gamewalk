"""Mode B detector, driven by synthetic gait.

Real traces are better and `replay.py` exists for them, but synthetic signals
pin down the properties that must hold regardless of whose legs produced them.
"""

from __future__ import annotations

import math

from pc.config import Detector as DetectorCfg
from pc.detector import StepDetector

HZ = 50.0
DT = 1000.0 / HZ
G = 9.81


#: Footfall pulse width. Real heel-strike spikes are roughly 80-120ms wide;
#: anything much narrower than the 20ms sample interval would fall between
#: samples and test the sample rate rather than the detector.
PULSE_SIGMA_S = 0.045
RING_DELAY_S = 0.11


def gait(seconds: float, spm: float, amplitude: float = 3.0,
         *, gravity: bool = True, orientation=(0.0, 0.0, 1.0),
         noise: float = 0.02, ring: float = 0.0):
    """Accelerometer samples for walking in place.

    Each footfall is a pulse of fixed width in *seconds* (not a fraction of
    the step period, which would make fast walking produce narrower spikes
    than slow walking -- the opposite of how legs work), optionally followed
    by a smaller rebound to mimic the ringing that makes naive thresholding
    over-count.
    """
    period = 60.0 / spm
    n = int(seconds * HZ)
    ox, oy, oz = orientation
    two_sigma_sq = 2 * PULSE_SIGMA_S ** 2

    for i in range(n):
        t = i / HZ
        since = t % period
        # Nearest footfall in either direction, so pulses don't get clipped
        # at the period boundary.
        offsets = (since, since - period)
        pulse = sum(amplitude * math.exp(-(d * d) / two_sigma_sq) for d in offsets)
        if ring:
            pulse += sum(
                ring * amplitude * math.exp(-((d - RING_DELAY_S) ** 2) / two_sigma_sq)
                for d in offsets)
        wobble = 0.15 * math.sin(2 * math.pi * t * 1.3)
        mag = pulse + wobble + (math.sin(i * 7.3) * noise)
        base = G if gravity else 0.0
        yield (i * DT, ox * (base + mag), oy * (base + mag), oz * (base + mag))


def count(samples, cfg=None) -> int:
    det = StepDetector(cfg=cfg or DetectorCfg())
    return sum(1 for (t, x, y, z) in samples if det.feed(t, x, y, z))


def test_counts_steps_at_the_right_rate():
    steps = count(gait(20.0, spm=120))
    assert 36 <= steps <= 42, f"expected ~40 steps in 20s at 120spm, got {steps}"


def test_standing_still_produces_nothing():
    """The noise floor's whole job: an adaptive threshold with no floor
    adapts down into sensor noise and fires forever."""
    det = StepDetector(cfg=DetectorCfg())
    steps = 0
    for i in range(int(30 * HZ)):
        jitter = 0.05 * math.sin(i * 2.1) + 0.03 * math.sin(i * 5.7)
        steps += det.feed(i * DT, 0.0, 0.0, G + jitter)
    assert steps == 0, f"invented {steps} steps while standing still"


def test_orientation_does_not_matter():
    """The phone rotates in a pocket, so detection runs on magnitude."""
    upright = count(gait(20.0, spm=120, orientation=(0.0, 0.0, 1.0)))
    sideways = count(gait(20.0, spm=120, orientation=(1.0, 0.0, 0.0)))
    tilted = count(gait(20.0, spm=120, orientation=(0.577, 0.577, 0.577)))
    assert upright == sideways == tilted


def test_ringing_is_not_double_counted():
    """One footfall is one step, even with a rebound after it."""
    clean = count(gait(20.0, spm=110))
    rung = count(gait(20.0, spm=110, ring=0.75))
    assert rung <= clean + 2, f"rebound over-counted: {clean} -> {rung}"


def test_works_with_gravity_already_removed():
    """Mode B must accept linear-acceleration input unchanged."""
    with_g = count(gait(20.0, spm=120, gravity=True))
    without_g = count(gait(20.0, spm=120, gravity=False))
    assert abs(with_g - without_g) <= 2


def test_adapts_to_amplitude():
    """A gentle walk in a loose pocket and a hard jog differ several-fold.

    Both amplitudes sit above `threshold_floor`; below it, not detecting is
    the floor doing its job rather than a failure.
    """
    gentle = count(gait(20.0, spm=120, amplitude=3.0))
    hard = count(gait(20.0, spm=120, amplitude=12.0))
    assert abs(gentle - hard) <= 3, f"gentle={gentle} hard={hard}"


def test_refractory_rejects_a_second_spike_too_soon():
    """Two spikes inside the lockout are one footfall's ringing, not two steps.

    Tested directly rather than by generating impossible 400spm gait, where
    the pulses would overlap into a smear and the test would be measuring the
    signal generator instead of the detector.
    """
    det = StepDetector(cfg=DetectorCfg(refractory_ms=180))
    for i in range(int(4 * HZ)):          # settle the filters
        det.feed(i * DT, 0.0, 0.0, G)

    # A continuous 50Hz stream, because re-arming requires the signal to fall
    # back below the threshold and that can only be observed in samples.
    spikes = (0.0, 100.0, 400.0)
    start = 4000.0
    fired = []
    for i in range(int(0.8 * HZ)):
        offset = i * DT
        bump = sum(6.0 * math.exp(-((offset - s) ** 2) / (2 * 30.0 ** 2))
                   for s in spikes)
        if det.feed(start + offset, 0.0, 0.0, G + bump):
            fired.append(offset)

    assert fired, "no step detected at all"
    assert fired[0] < 60, f"first spike missed, fired at {fired}"
    assert not any(100 <= f < 180 for f in fired), (
        f"spike inside the refractory window was counted: {fired}")
    assert any(f >= 380 for f in fired), (
        f"spike after the window should register again: {fired}")


def test_no_phantom_burst_on_connect():
    """Filters seed from the first sample; starting at zero would make the DC
    estimate ramp up through a second of apparent signal."""
    det = StepDetector(cfg=DetectorCfg())
    first_second = sum(
        det.feed(i * DT, 0.0, 0.0, G) for i in range(int(HZ)))
    assert first_second == 0


def test_reset_clears_state():
    det = StepDetector(cfg=DetectorCfg())
    for (t, x, y, z) in gait(5.0, spm=120):
        det.feed(t, x, y, z)
    det.reset()
    assert not det.primed and det.threshold == 0.0


def test_survives_a_stall_without_firing():
    """A Wi-Fi hiccup leaves a large dt; the filters must not blow open."""
    det = StepDetector(cfg=DetectorCfg())
    for (t, x, y, z) in gait(5.0, spm=120):
        det.feed(t, x, y, z)
    assert det.feed(5000.0 + 30_000.0, 0.0, 0.0, G) is False


def test_out_of_order_samples_are_ignored():
    det = StepDetector(cfg=DetectorCfg())
    for (t, x, y, z) in gait(3.0, spm=120):
        det.feed(t, x, y, z)
    assert det.feed(10.0, 0.0, 0.0, 40.0) is False


def test_lower_factor_is_more_sensitive():
    """The direction of the tuning knob must be the one documented in meta."""
    weak = list(gait(20.0, spm=120, amplitude=1.0, noise=0.25))
    sensitive = count(weak, DetectorCfg(threshold_factor=1.0, threshold_floor=0.3))
    strict = count(weak, DetectorCfg(threshold_factor=2.5, threshold_floor=0.3))
    assert sensitive >= strict
