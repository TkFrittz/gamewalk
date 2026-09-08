# GameWalk

Walk in place with your phone in your pocket, **screen off**, and your PC thinks you're
holding `W`. Jog and it becomes `Shift+W`. A VSteps-style walking-in-place controller built
from parts you own.

> **Status: M1 done — the whole PC half works and is tested. The Android app is next.**
> **[BUILD_PLAN.md](BUILD_PLAN.md)** is the plan of record — milestones, protocol, config
> schema, and how the APK gets built and delivered.
> **[DESIGN.md](DESIGN.md)** is the reasoning behind the architecture; the build plan amends
> it in a few places and lists every delta.

## Try it now, without a phone

The PC half is complete and testable from the keyboard — that's deliberate, so that
scancode injection is proven in your game before any Android work exists. See
**[pc/README.md](pc/README.md)**.

```bash
python -m pc --dry-run --verbose        # terminal 1: helper, keys logged not pressed
python tools/fakestep.py --ramp 90:190  # terminal 2: pretend to walk, then jog
```

A 30-step ramp should log exactly **four** key events — `W` down, `Shift` added entering
run, `Shift` dropped leaving run, `W` up at the end. One keydown per walk, not one per
footfall, is the whole smoothness design. Drop `--dry-run` to drive a real game.

## Install (once M4 lands)

1. **Phone** — open Releases, tap the APK, install. No toolchain, no cable, no build.
2. **PC** — run `run.bat`. Python 3.10+, standard library only.
3. Open the app, tap the PC it finds on your network, type the PIN it shows. Done.

Everything after that — keybinds, speed tiers, sensitivity, hold timing — is configured from
the phone, and every change applies live without restarting anything.

*(The design doc calls the project **StepLink**, from before this repo existed. Same thing —
happy to unify the naming either way.)*

## How it works

```
 ANDROID PHONE (screen off)          WI-FI              WINDOWS PC
 hardware step sensor  ──── UDP: one datagram/step ────►  cadence → state machine
 foreground service         + 4 Hz heartbeat              → SendInput holds W / Shift+W
```

The phone is deliberately dumb: it reports "a step happened" and nothing else. **All the
logic — cadence, speed tiers, hysteresis, keybinds — lives on the PC in a JSON config**, so
changing a keybind never means rebuilding an APK.

## Repo layout

| Path | What | Milestone |
|---|---|---|
| [BUILD_PLAN.md](BUILD_PLAN.md) | Milestones, protocol, config schema, APK pipeline | — |
| [DESIGN.md](DESIGN.md) | Architecture and the reasoning behind it | — |
| [docs/DESIGN-R1-kotlin-udp.md](docs/DESIGN-R1-kotlin-udp.md) | Superseded R1 draft, kept for the record | — |
| `pc/` | Python helper: UDP listener, cadence, state machine, key injection | M1 |
| `tools/` | `fakestep.py`, `replay.py`, `remote.py` | M1–M2 |
| `.github/workflows/` | CI: tests, and the APK build + release | M0, M4 |
| `app/` | Kotlin app + foreground service | M4–M7 |

## Build order

Detailed in [BUILD_PLAN.md §3](BUILD_PLAN.md). Two gates matter:

- **M1 is go/no-go.** The entire PC half comes first and is testable with `fakestep.py` — no
  phone, no Android tooling. If synthetic keystrokes don't register in your game, that's a
  today problem, found for free.
- **M4 is the delivery gate.** CI builds and signs a *hello-world* APK and publishes it to
  Releases before the app does anything real, so signing and install friction get debugged in
  isolation. After M4, every milestone ends with an APK you can install from your phone.

Then: M5 screen-off survival → M6 live config UI → M7 zero-config first run → M8 comfort.

## Known risks

Spelled out in [DESIGN.md §8](DESIGN.md). The short version:

- **Does the hardware step detector fire for walking *in place*?** It's tuned for real gait
  with forward translation. Biggest unknown; mitigated by a raw-accelerometer fallback mode
  (§4b) that moves detection to the PC.
- **OEM battery managers** (Samsung, Xiaomi) kill foreground services regardless of how
  correctly they're written.
- **Anticheat** treats synthetic scancode input like a macro tool. Fine for single-player and
  VRChat; don't point it at competitive multiplayer.

## Setup

Nothing to install yet. `pc/config.default.json` will be the template — copy it to
`pc/config.json` (gitignored, holds your LAN token and tuning) once Phase 1 lands.
