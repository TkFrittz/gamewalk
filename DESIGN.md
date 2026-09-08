# StepLink — Design Document

**Goal:** Walking in place with my phone in my pocket, **screen off**, makes my PC think I'm
holding `W`. Walk faster and it becomes `Shift+W`. VSteps-style, but built from parts I
control.

Status: **awaiting approval — nothing built yet.**

> **Revision history.** R1 was a Kotlin app over UDP; it deferred to a browser because its
> own §6 priced the Android SDK at ~8 GB and an hour. R2 explored that browser path and
> died on one constraint: a backgrounded tab stops receiving sensor events, so the phone
> would have to sit in my pocket with the screen awake. **Screen-off is a hard requirement,
> so R3 returns to the native app** — with R1's toolchain estimate corrected (§7: ~1 GB, no
> IDE) and R2's detector kept as a fallback (§4b). R1 is preserved at
> [`docs/DESIGN-R1-kotlin-udp.md`](docs/DESIGN-R1-kotlin-udp.md).
>
> **Amended by [BUILD_PLAN.md](BUILD_PLAN.md).** Two later requirements — configure
> everything from the app with live updating, and install by downloading an APK — change §1
> (config ownership), §6 (schema), §7 (CI builds the APK; nothing installed locally) and §5
> (the protocol gains a control channel). BUILD_PLAN.md §11 lists every delta; where the two
> documents disagree, the build plan wins.

---

## 1. What this actually is

```
 ANDROID PHONE (screen off, pocket)      WI-FI LAN            WINDOWS PC
┌────────────────────────────────┐                    ┌─────────────────────────┐
│ Foreground service             │                    │ UDP listener            │
│  ├ wake-up step sensor         │   one small UDP    │        ↓                │
│  ├ partial wake lock           │   datagram per     │ Cadence estimator (SPM) │
│  └ Wi-Fi lock (HIGH_PERF)      │   step, plus a     │        ↓                │
│            ↓                   │   4 Hz heartbeat   │ State machine           │
│    fire-and-forget send        │ ─────────────────► │ idle / walk / run       │
└────────────────────────────────┘                    │        ↓                │
                                                      │ SendInput (scancodes)   │
                                                      │ holds W  /  Shift+W     │
                                                      └─────────────────────────┘
```

The phone is dumb on purpose. It says "step happened" and nothing else. **All the
intelligence — cadence, thresholds, which keys, hysteresis — lives on the PC**, in a config
file I can edit and reload without touching the phone. That is the whole reason this stays
simple to tune: **I never rebuild an APK to change a keybind.** Given that building the APK
is the expensive part of this project, keeping it a thin, stable sensor pipe is the single
most important structural decision here.

## 2. Running with the screen off

This is the requirement that killed R2 and it deserves to be designed for explicitly rather
than assumed. Five separate things on modern Android will stop a naive app the moment the
screen goes dark, and each needs its own countermeasure:

| What breaks | Why | Fix |
|---|---|---|
| Process is killed | Background apps are fair game | **Foreground service** + persistent notification |
| Sensor events stop | The CPU suspends; non-wake sensors just stop delivering | **Wake-up sensor variant** + `PARTIAL_WAKE_LOCK` |
| Wi-Fi radio sleeps | Power save drops the NIC between beacons | **`WifiLock(WIFI_MODE_FULL_HIGH_PERF)`** |
| Doze defers network | Device idle | Foreground services are exempt; plus a one-time **battery-optimization exemption** prompt |
| OEM killers | Samsung/Xiaomi/OnePlus kill aggressively regardless of the above | Documented per-vendor "unrestricted battery" toggle |

**The wake-up sensor is the key one.** `getDefaultSensor(TYPE_STEP_DETECTOR, true)` returns
a variant that wakes the application processor to deliver each event, which is precisely
what "screen off in a pocket" needs. Where a device doesn't expose one, the partial wake
lock keeps the AP awake and the ordinary sensor keeps reporting — more battery, same
behaviour.

**And the non-obvious one: batching must be turned off.** Sensor registration takes a
`maxReportLatencyUs`, and hardware FIFOs will happily buffer step events and deliver them in
a burst seconds later. For a pedometer that is a battery win. For *this* it is fatal — I'd
stand still and then lurch forward. **Register with `maxReportLatencyUs = 0`** so every step
is delivered the moment it happens. Screen-off is exactly the state where the OS most wants
to batch, so this is not a detail I can leave to defaults.

Android 14+ wants a declared `foregroundServiceType`; `health` is the honest fit for a step
sensor and carries no runtime cap (`dataSync` is capped at 6 h/day on Android 15, which
would quietly end long sessions).

## 3. Why these choices

**Step detector, not step counter.** `TYPE_STEP_COUNTER` gives a cumulative total since
boot and is heavily smoothed and batched — it can lag several seconds, which is useless
here. `TYPE_STEP_DETECTOR` fires one event the moment a step is recognised, typically well
under 100 ms. That's the one.

**UDP, not TCP or Bluetooth.** UDP is connectionless: no pairing, no reconnect logic, no
state to get stuck in. A dropped packet costs one step, and the next arrives ~500 ms later
anyway, so loss is self-healing. LAN round trip is 1–5 ms. Bluetooth RFCOMM would work and
stays a fallback for when there's no shared Wi-Fi, but it adds pairing, serial plumbing and
reconnect handling for no latency win.

**`SendInput` with scancodes, not `pyautogui`.** This is the detail that decides whether the
project works at all. Games that read input through DirectInput or RawInput — most of them,
VRChat included — ignore high-level synthetic key events. Keys must be injected as hardware
scancodes via `SendInput`, called through `ctypes` directly rather than trusting a wrapper
to do it right. `SendInput` targets the foreground window, so the game must be focused;
that's expected, since I'm playing it.

**Latency budget:** sensor ~50–80 ms, network ~2 ms, injection ~1 ms. Under 100 ms from
footfall to key — below the point where it feels disconnected.

## 4. The state machine

Every arriving step packet is a timestamp. From the gaps between recent timestamps I get
**SPM** (steps per minute), using the *median of the last 4 intervals* — median rather than
mean so one weird gap doesn't spike the estimate.

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

**Hysteresis is mandatory.** With a single run threshold, a cadence hovering near the line
makes Shift stammer on and off several times a second. Two thresholds — enter run at 145
SPM, fall back to walk only below 130 — mean it takes a real change of pace to switch tiers,
not sensor noise.

**The key is held continuously — it is not re-pressed per step.** Key-down fires once, on
the step that leaves idle. Every later step only pushes the release deadline further out.
Key-up fires once, when I stop. Across a minute of steady walking there is exactly one
keydown event, so there is nothing to stutter. The naive alternative — tap W for 200 ms on
each footfall — is what produces visible hitching, and this design exists to avoid it.

**The one real failure mode: hold time shorter than the step gap.** If the hold expires
before the next step lands, the key releases and re-presses every single step, producing
exactly the stutter I'm trying to avoid. A fixed 650 ms is fine at 120 SPM (500 ms gaps) but
breaks the moment I stroll at 85 SPM (700 ms gaps) — the constant silently becomes wrong at
the low end of my own walking range.

So the hold is **adaptive by default**: 1.8× the measured median step interval, clamped to a
floor and ceiling. At 85 SPM that's a 1270 ms hold; at 160 SPM, 675 ms. It stays correctly
ahead of my cadence without re-tuning a number every time I change pace, and the 1.8× margin
absorbs one irregular step without releasing. The cost is honest: a larger multiplier
tolerates more jitter but coasts longer after I stop. 1.8× is a starting point, not a claim
about what feels best — it's the first thing to tune with `fakestep.py`. A fixed value stays
available in config.

**Tier changes diff the key set, never re-apply it.** walk → run presses Shift and leaves W
untouched. Releasing the whole walk set and pressing the whole run set would blip W off for
a frame and visibly hitch the character at every speed change. So the machine computes
`to_press = new - old` and `to_release = old - new`; keys common to both tiers are never
disturbed.

## 4a. Heartbeat and dead-man switch

A stuck `W` walks me into a wall until I alt-tab and mash the key. In a step-only protocol,
silence is ambiguous — it means either "standing still" or "phone is dead", and the PC
cannot tell which. So the phone sends a **heartbeat 4× a second whenever the service is
armed**, whether or not I'm moving. Silence now means exactly one thing.

**No packet of any kind for 1 s → release every key and drop to idle.** Keys are also
released on explicit stop, on service shutdown, and via a **global panic hotkey** on the PC.
This costs one tiny datagram every 250 ms and removes the entire class of "phone fell off
Wi-Fi and now I'm running into a wall" failures.

## 4b. Fallback: raw accelerometer mode  *(salvaged from R2)*

**The biggest unknown in this project is whether the hardware step detector fires for
walking *in place*.** It's tuned for real gait with forward translation. It very likely
works — the vertical acceleration signature is nearly the same — but "very likely" is not
something I want to discover after installing a toolchain.

So the app ships **two modes**, switchable in its one settings screen:

- **Mode A — hardware step detector** (default). One packet per step. Near-zero battery
  cost, screen-off native, best latency.
- **Mode B — raw accelerometer**, 50 Hz, with detection done on the PC.

Mode B reuses R2's detector unchanged. It works on the magnitude `‖a‖`, which is
rotation-invariant, so pocket orientation doesn't matter:

```
 ‖a‖ ─► remove DC ─► smooth ─► adaptive ─► rising edge ─► STEP
        EMA τ≈1 s    EMA τ≈60ms  threshold   + refractory
```

The threshold is adaptive — `max(1.1, 1.4 × rms)` over a ~3 s window — because a gentle walk
in a loose pocket and a hard jog in tight jeans differ several-fold in amplitude; tracking
the signal's own energy self-calibrates to me and today's trousers. The `1.1 m/s²` floor
stops it from adapting down into sensor noise and firing forever while I stand still.
Triggering on the rising edge rather than the confirmed peak saves 20–30 ms, and a 180 ms
refractory plus a re-arm below `0.6 × thr` stops one footfall's ringing from counting as
four steps.

Mode B costs battery and pays ~30 ms more latency. It exists so that a failure of the
hardware detector is a settings toggle rather than a redesign — and because the PC-side code
is written anyway, it's cheap insurance.

## 5. Wire protocol

One line of ASCII per datagram — debuggable with a packet capture or a two-line listener.

| Packet | Meaning |
|---|---|
| `SL1 <token> STEP <seq> <t_ms>` | Mode A: a step happened |
| `SL1 <token> ACC <seq> <t_ms> <x> <y> <z>` | Mode B: one accelerometer sample |
| `SL1 <token> HB <seq>` | heartbeat, 4 Hz while armed |
| `SL1 <token> STOP <seq>` | user stopped the service |

The PC infers the mode from the packet type — nothing to configure on both ends. `<seq>`
makes packet loss visible in logs instead of invisible.

`token` is a shared secret checked on every packet. Not security theatre: without it, any
stray broadcast on the network could make my character walk into a wall.

## 6. Config

One JSON file next to the script, reloaded on save — no restart:

```json
{
  "port": 5599,
  "token": "change-me",
  "deadman_ms": 1000,
  "panic_hotkey": "f8",
  "hold": {
    "mode": "adaptive",
    "multiplier": 1.8,
    "min_ms": 350,
    "max_ms": 1400,
    "fixed_ms": 650
  },
  "tiers": [
    { "name": "walk", "keys": ["w"],          "enter_spm": 0,   "exit_spm": 0   },
    { "name": "run",  "keys": ["shift", "w"], "enter_spm": 145, "exit_spm": 130 }
  ],
  "detector": {
    "dc_tau_ms": 1000, "smooth_tau_ms": 60,
    "threshold_factor": 1.4, "threshold_floor": 1.1,
    "rearm_ratio": 0.6, "refractory_ms": 180
  }
}
```

`tiers` is an ordered list, and the state machine walks it generically — a third speed tier
is one more entry, not a code change. Swapping `w` for `up`, or adding `ctrl` to crouch-walk,
is a text edit. `detector` only applies in Mode B.

*(This replaces R1's flat `states` map plus `run_enter_spm`/`run_exit_spm`, which couldn't
actually express the generic tier list R1 said it wanted.)*

## 7. Build plan

**Phase 1 — the entire PC half, with no phone and no toolchain.** Python 3.10 (installed),
**stdlib only** — UDP listener, cadence estimator, state machine, `ctypes` SendInput, config
loader, panic hotkey, dead-man, plus the Mode B detector.

It ships with `fakestep.py`, which fires synthetic step packets at a chosen SPM and ramps up
and down, and `replay.py`, which plays back recorded raw traces through the Mode B detector.
**This means the whole PC half gets built, tested and tuned before I install a single byte of
Android tooling** — I can watch it enter and exit run, confirm hysteresis doesn't flicker,
and verify keys actually register inside a game, all from the keyboard. If `SendInput`
doesn't work in the game I care about, I find out today, for free.

**Phase 2 — toolchain, and it is smaller than R1 claimed.** R1 priced Android Studio at
~8 GB and used that to justify avoiding the whole path. But the IDE isn't required to build
an APK:

| | |
|---|---|
| Temurin JDK 17 (`winget`) | ~200 MB |
| Android `cmdline-tools` | ~150 MB |
| `platform-tools` (adb), `platforms;android-34`, `build-tools;34.0.0` | ~160 MB |
| Gradle, fetched by the wrapper | ~150 MB |
| **Total** | **~700 MB, scripted, no IDE** |

A `setup-android.ps1` does the whole thing unattended. The bare `aapt2`/`d8`/`apksigner`
route would shave the Gradle download but means hand-rolling resource compilation, which is
fragile for no real gain.

**Phase 3 — the app.** Kotlin, one screen: PC IP, port, token, Mode A/B, Start/Stop, and a
button for the battery-optimization exemption. A foreground service holds the sensor
registration, wake lock and Wi-Fi lock. Permissions: `ACTIVITY_RECOGNITION`, `INTERNET`,
`FOREGROUND_SERVICE`, `FOREGROUND_SERVICE_HEALTH`, `WAKE_LOCK`,
`REQUEST_IGNORE_BATTERY_OPTIMIZATIONS`. Installed with `adb install` over USB.

**Phase 4 — comfort.** PC broadcasts a discovery beacon so the app finds it instead of me
typing an IP; tray icon with live SPM.

## 8. Things worth knowing before I say yes

**~700 MB and one unattended script stand between here and an APK.** That's the real cost,
it lands *after* Phase 1 has already proven the concept, and it's a one-time cost.

**The hardware step detector might not fire for walking in place.** The single largest
technical unknown. §4b is the answer, and the first thing to test once the app installs.

**OEM battery managers are the most likely cause of a mysterious mid-session stop.** Samsung
and Xiaomi in particular will kill a foreground service that holds a wake lock, no matter
how correctly it's written. The exemption prompt handles most of it; the rest is a per-vendor
settings toggle I'll document rather than pretend doesn't exist.

**Anticheat.** Synthetic scancode input is indistinguishable from a macro tool to
kernel-level anticheat. Fine for VRChat, single-player, and anything without invasive
protection. I should not point this at competitive multiplayer.

**Both devices on the same network,** and some routers isolate wireless clients from each
other by default. If packets vanish with everything else correct, AP isolation is the first
suspect.

**A phone with the screen off in a pocket still needs USB debugging enabled once** to install
the APK — or I sideload the file manually and allow installs from unknown sources.

## 9. Done looks like

Phone in pocket, screen off, service running. Walking in place moves me forward in-game
within a step or two. Picking up the pace switches to running without flickering. Standing
still stops me within about half a second. Locking the phone changes nothing. Changing a
keybind is editing one line of JSON.
