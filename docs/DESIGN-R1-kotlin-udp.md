# StepLink — Design Document

**Goal:** Walking in place with my phone in my pocket makes my PC think I'm holding `W`.
Walk faster and it becomes `Shift+W`. VSteps-style, but built from parts I control.

Status: **awaiting approval — nothing built yet.**

---

## 1. What this actually is

Three pieces:

```
 ANDROID PHONE                    WI-FI LAN                  WINDOWS PC
┌──────────────────────┐                              ┌──────────────────────────┐
│ Hardware step sensor │                              │ UDP listener             │
│ TYPE_STEP_DETECTOR   │                              │        ↓                 │
│        ↓             │   UDP packet, one per step   │ Cadence estimator (SPM)  │
│ Foreground service   │ ───────────────────────────► │        ↓                 │
│        ↓             │       "STEP <seq> <token>"   │ State machine            │
│ Fire-and-forget send │                              │ idle / walk / run        │
└──────────────────────┘                              │        ↓                 │
                                                      │ SendInput (scancodes)    │
                                                      │ holds W  /  Shift+W      │
                                                      └──────────────────────────┘
```

The phone is dumb on purpose. It says "step happened" and nothing else. **All the
intelligence — cadence, thresholds, which keys, hysteresis — lives on the PC**, in a
config file I can edit and reload without touching the phone app. That's the whole
reason this stays simple to tune: I never rebuild an APK to change a keybind.

## 2. Why these choices

**Step detector, not step counter.** Android exposes two sensors. `TYPE_STEP_COUNTER`
gives a cumulative total since boot and is heavily smoothed and batched — it can lag
several seconds, which is useless here. `TYPE_STEP_DETECTOR` fires one event the moment a
step is recognised, typically well under 100 ms. That's the one.

**UDP over Wi-Fi, not Bluetooth.** UDP is connectionless: no pairing, no reconnect logic,
no state to get stuck in. A dropped packet costs one step, and the next one arrives
~500 ms later anyway, so loss is self-healing. LAN round trip is 1–5 ms. Bluetooth RFCOMM
would work and is listed as a fallback for when there's no shared Wi-Fi, but it adds
pairing, serial-port plumbing, and reconnect handling for no latency win.

**`SendInput` with scancodes, not `pyautogui`.** This is the detail that decides whether
the project works at all. Games that read input through DirectInput or RawInput — which is
most of them, VRChat included — ignore high-level synthetic key events. Keys must be
injected as hardware scancodes via `SendInput`. I'll call it through `ctypes` directly
rather than trusting a wrapper library to do it right.

**Latency budget:** sensor ~50–80 ms, network ~2 ms, key injection ~1 ms. Call it under
100 ms from footfall to key — below the threshold where it feels disconnected.

## 3. The state machine

Every arriving packet is a timestamp. From the gaps between recent timestamps I get
**SPM** (steps per minute), using the *median of the last 4 intervals* — median rather
than mean so one weird gap doesn't spike the estimate.

```
                 ≥2 steps within max hold
      ┌──────┐  ─────────────────────►  ┌──────┐  SPM ≥ run_enter  ┌──────┐
      │ IDLE │                          │ WALK │ ────────────────► │ RUN  │
      │      │                          │  W   │                   │ Sh+W │
      │ none │  ◄─────────────────────  │ held │ ◄──────────────── │ held │
      └──────┘  no step for hold expiry └──────┘  SPM ≤ run_exit    └──────┘
                          ▲                                            │
                          └────────────────────────────────────────────┘
                                 no step for hold expiry
```

**Hysteresis is mandatory.** With a single run threshold, cadence hovering near the line
makes Shift stammer on and off several times a second. Two thresholds — enter run at 145
SPM, fall back to walk only below 130 — means it takes a real change in pace to switch
tiers, not sensor noise.

## 3a. Why this is smooth, and the one way it could fail

**The key is held continuously — it is not re-pressed per step.** Key-down fires once, on
the step that moves me out of idle. Each later step only pushes the release deadline
further out. Key-up fires once, when I stop. Across a minute of steady walking there is
exactly one keydown event, so there is nothing to stutter.

The naive alternative — tap W for 200 ms on every footfall — is what produces visible
hitching, and this design exists specifically to avoid it.

**The one real failure mode: hold time shorter than the step gap.** If the hold expires
before the next step lands, the key releases and re-presses every single step, and I get
exactly the stutter I'm trying to avoid. A fixed 650 ms is fine at 120 SPM (500 ms gaps)
but breaks the moment I stroll at 85 SPM (700 ms gaps) — the constant silently becomes
wrong at the low end of my own walking range.

So the hold is **adaptive by default**: it tracks 1.8× the measured median step interval,
clamped to a floor and ceiling. At 85 SPM that's a 1270 ms hold; at 160 SPM, 675 ms. It
stays correctly ahead of my cadence without me re-tuning a number every time I change
pace, and the 1.8× margin absorbs an irregular step without releasing. A fixed millisecond
value is still available in config for anyone who wants to pin it.

The cost is honest and worth stating: a larger multiplier tolerates more sensor jitter but
means coasting longer after I stop. 1.8× is the starting point, not a claim about what
feels best — that's the first thing to tune with `fakestep.py`.

**Tier changes must diff the key set, never re-apply it.** Going walk → run should press
Shift and leave W untouched. If the implementation instead releases the whole walk set and
presses the whole run set, W blips off for a frame and the character visibly hitches at
every speed change. The state machine computes `to_press = new - old` and
`to_release = old - new`, so held keys common to both tiers are never disturbed.

## 4. Config

One JSON file next to the script, reloaded on save — no restart:

```json
{
  "port": 5599,
  "token": "change-me",
  "hold": {
    "mode": "adaptive",
    "multiplier": 1.8,
    "min_ms": 350,
    "max_ms": 1400,
    "fixed_ms": 650
  },
  "run_enter_spm": 145,
  "run_exit_spm": 130,
  "states": {
    "walk": ["w"],
    "run":  ["shift", "w"]
  }
}
```

`states` is a plain map of tier → keys held. Swapping `w` for `up`, or adding `ctrl` to
crouch-walk, is a text edit. Adding a third speed tier later means one more entry plus one
more threshold pair — the state machine is written generically over the tier list rather
than hardcoding walk and run.

`hold.mode` is `"adaptive"` (hold = `multiplier` × median step interval, clamped between
`min_ms` and `max_ms`) or `"fixed"` (hold = `fixed_ms` flat, ignoring cadence). Both are
adjustable live; `fakestep.py` makes it quick to feel the difference.

`token` is a shared secret checked on every packet. Not security theatre: without it, any
stray broadcast on the network could make my character walk into a wall.

## 5. Build plan

**Phase 1 — PC side (nothing phone-related).** Python 3.10, already installed, stdlib
only. UDP listener, cadence estimator, state machine, `ctypes` SendInput, config loader.
Ships with `fakestep.py`, a script that fires synthetic step packets at a chosen SPM and
ramps up and down. **This means the entire PC half gets tested and tuned before the phone
exists** — I can watch it enter and exit run, verify hysteresis, and confirm keys actually
register in a game, all from the keyboard.

**Phase 2 — Android app.** Kotlin, one screen: PC IP, port, token, Start/Stop. A
foreground service holds the sensor registration and a Wi-Fi lock so Doze doesn't kill it
mid-session. Permissions: `ACTIVITY_RECOGNITION`, `INTERNET`, `FOREGROUND_SERVICE`.

**Phase 3 — comfort.** PC broadcasts a discovery beacon so the app finds it instead of me
typing an IP; global hotkey to arm and disarm; tray icon with live SPM readout.

## 6. Things worth knowing before I say yes

**Phase 2 needs an Android toolchain** — JDK plus the Android SDK, roughly 8 GB and an
hour of setup. This machine has neither right now. It's the single largest cost in the
project, and it lands *after* Phase 1 has already proven the concept.

There is a zero-toolchain fallback: **Termux + Termux:API** from F-Droid, where
`termux-sensor` streams sensor readings to a ten-line script that sends the same UDP
packets. It skips the SDK entirely, but only the polled step *counter* is exposed that
way, so latency is worse and it's flakier in the background. Worth keeping in the back
pocket if the SDK install turns painful; not the plan.

**Anticheat.** Synthetic scancode input is indistinguishable from a macro tool to
kernel-level anticheat. Fine for VRChat, single-player, and anything without invasive
protection. I should not point this at competitive multiplayer.

**Both devices must be on the same network,** and some routers isolate wireless clients
from each other by default. If packets vanish with everything else correct, AP isolation
is the first thing to check.

## 7. Done looks like

Phone in pocket, app started, walking in place moves me forward in-game within a step or
two. Picking up the pace switches to running without flickering. Standing still stops me
within about half a second. Changing a keybind is editing one line of JSON.
