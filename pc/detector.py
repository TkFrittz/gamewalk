"""Mode B: find footfalls in a raw accelerometer stream.

Only used when the phone's hardware step detector is unavailable or ignores
walking in place. Runs on the magnitude of the acceleration vector, which is
rotation-invariant -- the phone is in a pocket at an orientation that changes
as you move, so nothing per-axis is usable.

    ||a|| -> remove DC -> smooth -> adaptive threshold -> rising edge -> STEP

Timestamps come from the phone. Only the *differences* matter, so a clock
offset between phone and PC is harmless.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .config import Detector as DetectorCfg


def _alpha(dt_ms: float, tau_ms: float) -> float:
    """EMA coefficient for a given sample gap, so jittery delivery doesn't
    change the filter's actual time constant."""
    if tau_ms <= 0:
        return 1.0
    return 1.0 - math.exp(-dt_ms / tau_ms)


@dataclass
class StepDetector:
    cfg: DetectorCfg

    dc: float = 0.0
    smoothed: float = 0.0
    rms: float = 0.0
    threshold: float = 0.0
    armed: bool = True
    primed: bool = False
    last_t: float = 0.0
    last_step_t: float = 0.0
    n: int = 0

    #: Energy average window. Long enough to span several steps so a single
    #: footfall doesn't drag the threshold up above the next one.
    rms_tau_ms: float = 3000.0

    def reset(self) -> None:
        self.primed = False
        self.armed = True
        self.n = 0
        self.dc = self.smoothed = self.rms = self.threshold = 0.0
        self.last_t = self.last_step_t = 0.0

    def _bootstrap(self, alpha: float) -> float:
        """Blend an EMA with a running mean for the first samples.

        A plain EMA seeded from one arbitrary sample is at the mercy of what
        that sample happened to catch. Seeding on a footfall spike biases the
        gravity estimate high, and the high-passed signal then sits below the
        real one for several time constants -- measured at 2.5 seconds of
        standing still after every connect before the first step registers.

        1/n converges immediately and decays into the configured EMA once
        enough samples exist for the time constant to mean anything.
        """
        return max(alpha, 1.0 / (self.n + 1))

    def feed(self, t_ms: float, x: float, y: float, z: float) -> bool:
        """One sample in, True if this sample is a step."""
        mag = math.sqrt(x * x + y * y + z * z)

        if not self.primed:
            # Seed the filters from the first sample. Starting at zero would
            # make the DC estimate ramp up through a second of "signal" and
            # fire a burst of phantom steps on every connect.
            self.primed = True
            self.n = 0
            self.dc = mag
            self.smoothed = 0.0
            self.rms = 0.0
            self.last_t = t_ms
            return False

        dt = t_ms - self.last_t
        if dt <= 0 or dt > 1000:
            # Out-of-order or a long stall: re-seed rather than let a huge dt
            # blow the filters wide open.
            self.last_t = t_ms
            self.dc = mag
            return False
        self.last_t = t_ms
        self.n += 1

        # Remove gravity. Harmless when the phone already sends linear accel.
        self.dc += self._bootstrap(_alpha(dt, self.cfg.dc_tau_ms)) * (mag - self.dc)
        hp = mag - self.dc

        self.smoothed += _alpha(dt, self.cfg.smooth_tau_ms) * (hp - self.smoothed)
        self.rms += self._bootstrap(_alpha(dt, self.rms_tau_ms)) * (
            abs(self.smoothed) - self.rms)

        # Adaptive: a gentle walk in a loose pocket and a hard jog in tight
        # jeans differ several-fold in amplitude, so track the signal's own
        # energy. The floor is what stops it adapting down into sensor noise
        # and firing forever while standing still.
        self.threshold = max(
            self.cfg.threshold_floor, self.cfg.threshold_factor * self.rms
        )

        step = False
        if self.armed and self.smoothed > self.threshold:
            if t_ms - self.last_step_t >= self.cfg.refractory_ms:
                step = True
                self.last_step_t = t_ms
            # Disarm either way: below the refractory this is the same footfall
            # ringing, and re-triggering on it would double-count.
            self.armed = False
        elif self.smoothed < self.threshold * self.cfg.rearm_ratio:
            self.armed = True

        return step
