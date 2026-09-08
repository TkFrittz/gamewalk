# GameWalk

Walk in place with your phone in your pocket, **screen off**, and your PC thinks you're
holding `W`. Jog and it becomes `Shift+W`. A VSteps-style walking-in-place controller built
from parts you own.

> **Status: design phase. No code yet.**
> The design is settled and awaiting a final go — read **[DESIGN.md](DESIGN.md)** first;
> it's the source of truth for everything below.

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

| Path | What | Phase |
|---|---|---|
| [DESIGN.md](DESIGN.md) | Full design, decisions and rationale | — |
| [docs/DESIGN-R1-kotlin-udp.md](docs/DESIGN-R1-kotlin-udp.md) | Superseded R1 draft, kept for the record | — |
| `pc/` | Python helper: UDP listener, cadence, state machine, key injection | 1 |
| `tools/` | `fakestep.py` (synthetic cadence), `replay.py` (trace playback) | 1 |
| `app/` | Kotlin Android app + foreground service | 3 |

## Build order

1. **PC helper** — Python 3.10, stdlib only. Fully testable with `fakestep.py`: no phone and
   no Android toolchain required. This is deliberate — it proves scancode injection works in
   your game *before* anything gets installed.
2. **Toolchain** — JDK 17 + Android command-line tools, ~700 MB, scripted, no IDE.
3. **The app** — one screen, one foreground service.
4. **Comfort** — PC discovery beacon, tray icon.

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
