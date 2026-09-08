"""Steps in, held keys out.

Everything that makes this feel smooth rather than stuttery lives here:

  * The key is held *continuously*, not re-pressed per step. One keydown per
    walk, not one per footfall.
  * The hold outlasts the gap between steps, adaptively, so a slow stroll does
    not release the key between footfalls.
  * Tier changes diff the key set, so W is never dropped for a frame when
    Shift goes on.

Pure and clock-injected: every method takes `now_ms`, so the whole thing is
testable without sleeping.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from .config import Config
from .keys import Held

#: Intervals kept for the cadence estimate. Median of 4 rejects one bad gap
#: without lagging behind a genuine change of pace.
_WINDOW = 4

#: Ignore gaps longer than this when estimating cadence -- they mean "started
#: again after a pause", not "walking very slowly".
_MAX_INTERVAL_MS = 2000.0

#: And ignore gaps shorter than this. 150ms is 400 steps per minute, which no
#: one walks; a gap that short means two events for one footfall, or two
#: datagrams that were queued somewhere and arrived together. Believing them
#: is actively harmful: the median interval collapses, the adaptive hold
#: clamps to its floor, and the key releases before the next real step lands.
_MIN_INTERVAL_MS = 150.0

#: The phone sends a millisecond counter reduced mod 1e8, which wraps roughly
#: every 27 hours.
_CLOCK_WRAP_MS = 100_000_000


@dataclass
class Status:
    armed: bool = False
    moving: bool = False
    tier: str = "idle"
    tier_index: int = -1
    spm: float = 0.0
    held: tuple[str, ...] = ()
    hold_ms: float = 0.0
    last_step_ms: float = 0.0
    last_packet_ms: float = 0.0
    steps: int = 0
    #: Mode B only, for the app's live tuning view.
    signal: float = 0.0
    threshold: float = 0.0


@dataclass
class Machine:
    cfg: Config
    held: Held
    status: Status = field(default_factory=Status)

    _intervals: list[float] = field(default_factory=list)
    _step_times: list[float] = field(default_factory=list)
    _release_at: float = 0.0
    _tier_index: int = -1
    #: None rather than 0.0 -- a timestamp of zero is a legitimate first step,
    #: and treating it as "no previous step" silently discards the interval
    #: that the very first hold duration depends on.
    _prev_step: float | None = None
    #: The phone's own clock for the previous step, used for cadence.
    _prev_event: float | None = None

    # --- input --------------------------------------------------------------

    def on_step(self, now_ms: float, event_ms: float | None = None) -> None:
        """A footfall happened.

        `now_ms` is when the datagram arrived here; `event_ms` is when the
        phone's sensor says the step actually occurred.

        Cadence is measured from `event_ms` whenever it is available, because
        arrival times carry every delay between the foot and this line: Wi-Fi
        power-save queuing, and sensor batching that hands over several events
        at once. Both make steps appear to arrive milliseconds apart, and a
        cadence built from that reads in the thousands while the hold time
        collapses to its floor.

        Everything else -- hold expiry, the dead-man switch -- deliberately
        stays on arrival time, since those measure liveness rather than gait,
        and extending the hold from the moment we *learned* of a step is the
        conservative choice when a packet was late.
        """
        if not self.status.armed:
            return

        self.status.steps += 1
        prev, prev_event = self._prev_step, self._prev_event
        self._prev_step = now_ms
        self._prev_event = event_ms
        self.status.last_step_ms = now_ms

        gap = None
        if event_ms is not None and prev_event is not None:
            gap = event_ms - prev_event
            if gap < 0:                       # the phone's counter wrapped
                gap += _CLOCK_WRAP_MS
        elif prev is not None:
            gap = now_ms - prev

        if gap is not None and _MIN_INTERVAL_MS <= gap <= _MAX_INTERVAL_MS:
            self._intervals.append(gap)
            del self._intervals[:-_WINDOW]
        elif gap is not None and gap < _MIN_INTERVAL_MS:
            # Too fast to be a real step. Keep the cadence we already have
            # rather than poisoning the median with it.
            pass
        else:
            # A long gap means this is a fresh start, not a slow step. Keeping
            # the stale intervals would produce a wrong cadence for the first
            # few steps of every restart.
            self._intervals.clear()

        self._step_times.append(now_ms)
        del self._step_times[:-_WINDOW]

        if not self.status.moving:
            # Require a few steps before moving, so one jolt in a pocket does
            # not walk the character off a ledge.
            recent = [t for t in self._step_times if now_ms - t <= _MAX_INTERVAL_MS]
            if len(recent) < self.cfg.start_steps:
                return
            self.status.moving = True

        self.status.spm = self._estimate_spm()
        self._release_at = now_ms + self._hold_ms()
        self._apply_tier(now_ms)

    def on_packet(self, now_ms: float) -> None:
        """Any datagram at all. Feeds the dead-man switch."""
        self.status.last_packet_ms = now_ms

    def arm(self, now_ms: float) -> None:
        self.status.armed = True
        self.status.last_packet_ms = now_ms

    def disarm(self) -> None:
        self.status.armed = False
        self._stop()

    # --- time ---------------------------------------------------------------

    def tick(self, now_ms: float) -> None:
        """Call frequently. Handles hold expiry and the dead-man switch."""
        if not self.status.armed:
            return

        # Dead-man: silence is unambiguous because the phone heartbeats while
        # armed, so no packets means the phone is gone, not that we are still.
        if self.status.last_packet_ms and (
            now_ms - self.status.last_packet_ms > self.cfg.deadman_ms
        ):
            self._stop()
            self.status.spm = 0.0
            return

        if self.status.moving and now_ms >= self._release_at:
            self._stop()

    # --- internals ----------------------------------------------------------

    def _estimate_spm(self) -> float:
        if not self._intervals:
            return 0.0
        median = statistics.median(self._intervals)
        return 60000.0 / median if median > 0 else 0.0

    def _hold_ms(self) -> float:
        interval = statistics.median(self._intervals) if self._intervals else None
        ms = self.cfg.profile.hold.duration_ms(interval)
        self.status.hold_ms = ms
        return ms

    def _select_tier(self) -> int:
        """Walk the tier list with hysteresis.

        Rising uses enter_spm, falling uses exit_spm, and the gap between them
        is what stops Shift stammering when cadence sits on the boundary.
        """
        tiers = self.cfg.profile.tiers
        i = max(0, self._tier_index)
        spm = self.status.spm

        while i + 1 < len(tiers) and spm >= tiers[i + 1].enter_spm:
            i += 1
        while i > 0 and spm <= tiers[i].exit_spm:
            i -= 1
        return i

    def _apply_tier(self, now_ms: float) -> None:
        index = self._select_tier()
        tier = self.cfg.profile.tiers[index]
        self._tier_index = index
        self.status.tier_index = index
        self.status.tier = tier.name
        self.held.apply(tier.keys)
        self.status.held = tuple(sorted(self.held.down))

    def _stop(self) -> None:
        self.held.release_all()
        self.status.moving = False
        self.status.tier = "idle"
        self.status.tier_index = -1
        self.status.held = ()
        self._tier_index = -1
        self._prev_step = None
        self._prev_event = None
        # Zero the cadence here rather than only on the dead-man path: a
        # normal stop was leaving the last reading on screen, so the display
        # claimed 130 spm while standing still.
        self.status.spm = 0.0
        self._intervals.clear()
        self._step_times.clear()

    # --- live config --------------------------------------------------------

    def replace_config(self, cfg: Config) -> None:
        """Swap config underneath a running session.

        Clamps the tier index because the new config may have fewer tiers, and
        re-applies immediately so a change is felt on this step rather than the
        next one -- that immediacy is the entire point of live editing.
        """
        self.cfg = cfg
        if self.status.moving:
            self._tier_index = min(self._tier_index, len(cfg.profile.tiers) - 1)
            self._apply_tier(self.status.last_step_ms)
