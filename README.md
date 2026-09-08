# GameWalk

Walk in place with your phone in your pocket, **screen off**, and your PC thinks you're
holding `W`. Jog and it becomes `Shift+W`. A VSteps-style walking-in-place controller built
from parts you own.

> **Status: both halves built. Untested on real hardware — see [Known unknowns](#known-unknowns).**

## Install

### 1. Phone

**[⬇ Download gamewalk.apk](https://github.com/TkFrittz/gamewalk/releases/download/latest/gamewalk.apk)**
— open that link *on your phone*, tap the file, install.

- Android will ask whether to allow installs from your browser. Say yes.
- Play Protect will warn that the developer is unknown. That's what it always says about a
  sideloaded app; tap **Install anyway**.

### 2. PC

Double-click **`run.bat`**. You need Python 3.10+; nothing else to install. It prints
something like:

```
GameWalk helper
  listening   192.168.0.240:5599  (discovery 5598)
  profile     default  (walk, run)
  panic key   F8
  pairing     PIN 481920  (valid 120s)
```

### 3. Pair

Open the app → **Find my PC** → tap the one it finds → type that PIN. Once, ever.

Grant the three permissions it asks for. They all fail the same way if missing — steps just
silently stop arriving — so it asks up front rather than letting you discover it later:

| Permission | Without it |
|---|---|
| Physical activity | The step sensor returns nothing at all |
| Notifications | No service notification, and Android may kill the service |
| Unrestricted battery | The system throttles it once the phone is idle |

### 4. Walk

Tap **Start walking**, pocket the phone, **screen off**. Focus your game. Walk in place.

## Using it

- **Everything is configured from the phone**, live — keybinds, speed tiers, sensitivity,
  hold timing. Changes apply on the next step with nothing restarted.
- **F8 on the PC is the panic key.** Releases every key and disarms. Press again to re-arm.
- **Stop moving and it stops** within about half a second. If the phone drops off Wi-Fi, the
  PC notices the silence within a second and releases everything.

## If it doesn't work

| Symptom | Likely cause |
|---|---|
| "Nothing answered" when finding the PC | Phone on mobile data, not Wi-Fi. Or the router isolates wireless clients from each other |
| Connected, but walking does nothing | Your phone's step detector may not fire for walking *in place*. Switch to **Mode B** in the app — that moves detection to the PC |
| Character moves in bursts, or stutters | Raise **Hold multiplier** in the app |
| Works, then stops after a few minutes | OEM battery manager. Set the app to "unrestricted" in Android's battery settings |
| Game ignores the keys | Some anti-cheat blocks synthetic input. Test first with `--dry-run` (below) |

## Try the PC half without a phone

The whole PC side is testable from the keyboard — deliberately, so scancode injection can be
proven in your game before any of the phone side is involved. See **[pc/README.md](pc/README.md)**.

```bash
python -m pc --dry-run --verbose        # terminal 1: helper, keys logged not pressed
python tools/fakestep.py --ramp 90:190  # terminal 2: pretend to walk, then jog
```

A 30-step ramp logs exactly **four** key events — `W` down, `Shift` added entering run,
`Shift` dropped leaving run, `W` up at the end. One keydown per walk rather than one per
footfall is the whole smoothness design. Drop `--dry-run` to drive a real game.

## Known unknowns

Everything above is built, and the PC half has 87 tests plus live verification. But **no part
of the phone side has run on a real phone yet** — I have no Android device here. Specifically
untested:

- Whether the hardware step detector fires for walking *in place* (Mode B exists for exactly
  this; [DESIGN.md §4b](DESIGN.md))
- Screen-off endurance over a real session
- The pairing and settings screens against a real touchscreen

Expect rough edges on first run, and tell me what you see.

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
