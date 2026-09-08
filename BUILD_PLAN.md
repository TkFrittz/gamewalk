# GameWalk — Build Plan

Companion to [DESIGN.md](DESIGN.md). That document says *what* and *why*; this one says
*in what order, and how I know each piece works.*

> **This plan amends DESIGN.md in three places.** §11 records the deltas in full. The short
> version: config becomes remotely editable from the app (§1 said the phone was dumb), the
> APK is built by CI instead of locally (§7 budgeted ~700 MB of toolchain on this machine),
> and discovery/pairing moves from "comfort" to core (§7 had it in Phase 4).

---

## 1. What "done" means

Two headline requirements, and every milestone below exists to serve one of them:

**A. Install is a download.** I open a GitHub Releases page on my phone, tap an APK, install
it. No Android Studio, no toolchain, no `adb`, no cable, no build. On the PC I run one `.bat`.

**B. Everything is configurable from the phone, live.** Keybinds, speed tiers, sensitivity,
hold timing — all adjustable from the app's settings screen, and every change takes effect
**immediately**, with no restart of the app, the service, or the PC helper. I can be walking
in place, drag a slider, and feel the difference on the next step.

Everything else — screen-off operation, smoothness, latency — is inherited from DESIGN.md and
unchanged.

## 2. The one architectural decision this forces

Requirement B breaks DESIGN.md §1's "the phone is dumb, all config lives on the PC." Once the
app is a settings UI, config has to be readable and writable from the phone. The question is
who *owns* it, and getting this wrong means two copies drifting apart.

**The PC owns config. The app is a remote control, not a second source of truth.**

The PC holds `config.json`, the app fetches it on connect and renders a UI from it, and edits
are sent back as patches the PC applies and persists. There is exactly one authoritative
copy, so there is nothing to reconcile. This also means:

- Config survives reinstalling the app, and survives switching phones.
- Editing `config.json` by hand still works, and the **app's UI updates live when I do** —
  the PC watches the file and pushes changes out. Live updating runs in both directions.
- The app needs no migration logic when the schema grows; it renders whatever the PC sends.

**The exception is settings the phone needs before it can talk to the PC** — the PC's address,
the token, and which sensor mode to use. Those obviously can't live on the PC, so they sit in
the app's own storage. That's the whole split:

| Owned by PC (`config.json`, app edits it live) | Owned by phone (app storage) |
|---|---|
| Tiers: names, keybinds, enter/exit SPM | PC address + port |
| Hold timing (mode, multiplier, clamps) | Pairing token |
| Mode B detector parameters | Sensor mode (A/B) |
| Dead-man timeout, panic hotkey | Sample rate, heartbeat rate |
| Profiles, active profile | Start-on-boot, notification style |

## 3. Milestones

Ordered so that **the riskiest assumption in each area is tested as early as it can be**, and
so nothing is built on top of something unproven.

### M0 — Scaffolding
Repo layout, `run.bat`, `pytest` skeleton, CI for the Python side.

**Done when:** `python -m pc --selftest` exits 0 and CI is green on a push.

---

### M1 — PC core, no phone and no toolchain required
The whole PC half: config load/validate, state machine, key injection, Mode B detector, UDP
listener, dead-man, panic hotkey, live console status line.

Ships with two harnesses, and **the split between them matters** — it means a detector bug
and a state-machine bug can never be mistaken for each other:

- `tools/fakestep.py` — fires synthetic `STEP` packets at a chosen SPM, with ramps. Exercises
  cadence, tiers, hysteresis, hold. No detector involved.
- `tools/replay.py` — plays recorded raw traces through the Mode B detector. Exercises
  detection only.

**Done when:** `fakestep.py --ramp 80:180` visibly walks then runs my character in the real
game, hysteresis doesn't flicker at the boundary, and releasing happens within ~half a second
of the packets stopping.

> **This is the project's real go/no-go gate.** If `SendInput` scancodes don't register in the
> game I care about, I find out here — for free, before any Android work exists. Nothing after
> this milestone is worth building until this one passes.

---

### M2 — Live config protocol
The control channel: `CFG?` / `CFG=` / `CFG!`, versioning, atomic apply, persistence,
file-watching so hand edits push out too. Plus `STAT` telemetry so a client can render a live
readout.

`tools/remote.py` is a CLI config client — it does everything the app's settings screen will
do, so **requirement B is fully testable before the app exists.**

**Done when:** with `fakestep.py` running, `remote.py set tiers.1.enter_spm 130` changes
behaviour on the next step with nothing restarted; and editing `config.json` in an editor
causes `remote.py watch` to print the new value within a second.

---

### M3 — Discovery and pairing
PC answers LAN discovery broadcasts; a PIN-based pairing window hands out the token once.

**Done when:** `remote.py discover` finds the PC from another machine with no address typed,
pairs with a PIN, and the token persists across restarts.

---

### M4 — CI builds a downloadable APK  ← *before the app does anything*
A trivial app — one screen reading "hello" — built by GitHub Actions, signed with a release
key from repository secrets, and published to a GitHub Release.

**Done when:** I open Releases on my phone, tap the APK, and it installs and launches.

> **Sequenced deliberately early.** Requirement A is a *delivery* problem, not an app problem,
> and delivery pipelines fail in boring ways — signing, `minSdk`, Play Protect, permissions
> on the release page. I'd rather hit all of that with a hello-world app than discover it
> while also debugging sensor code. After M4, every later milestone ends with a real APK I can
> install, so the app is never more than one push away from being testable on the phone.

---

### M5 — App core: sensors and screen-off survival
Foreground service, wake-up step sensor with `maxReportLatencyUs = 0`, partial wake lock,
Wi-Fi lock, battery-optimization prompt, UDP sender, heartbeat, Mode A/B switch.

**Done when:** phone in pocket, **screen off, 15 minutes**, and the PC's status line shows a
steady stream with no gaps — verified from the PC log, not from the phone.

**Also answers the biggest open question in DESIGN.md §8:** does the hardware step detector
fire for walking *in place*? First real test lands here. If it doesn't, Mode B is already
built and it's a toggle, not a redesign.

---

### M6 — App config UI, live
Settings screens bound to the PC's config over M2's protocol. Tiers with add/remove, a key
picker, sliders for hold and detector params, and a **live tuning view** — current SPM drawn
against the tier thresholds, and for Mode B the signal drawn against the detection threshold.

Tuning stops being guesswork the moment you can see the threshold sitting above or below your
actual signal.

**Done when:** walking in place, I drag the run threshold and the tier changes under my feet,
with nothing restarted.

---

### M7 — Zero-config first run
Discovery + pairing wired into the app's onboarding: install, open, tap the PC that appears,
type a PIN once, done. Profiles per game. A calibration flow that watches 20 seconds of
walking and jogging and sets the thresholds itself.

**Done when:** a factory-fresh install reaches "walking works" without typing an IP address.

---

### M8 — Comfort
PC tray icon and status window, optional auto-start, in-app update check against the GitHub
Releases API, optional single-file `.exe` for the PC helper.

## 4. Config schema

`config.json`, PC-owned, hot-reloaded, every field editable from the app:

```jsonc
{
  "version": 7,                    // bumped by PC on every change; clients use it to sync
  "port": 5599,
  "deadman_ms": 1000,
  "panic_hotkey": "f8",
  "start_steps": 2,                // steps needed to leave idle (rejects a single jolt)
  "active_profile": "default",
  "profiles": {
    "default": {
      "tiers": [
        { "name": "walk", "keys": ["w"],          "enter_spm": 0,   "exit_spm": 0   },
        { "name": "run",  "keys": ["shift", "w"], "enter_spm": 145, "exit_spm": 130 }
      ],
      "hold": {
        "mode": "adaptive",        // "adaptive" | "fixed"
        "multiplier": 1.8,
        "min_ms": 350,
        "max_ms": 1400,
        "fixed_ms": 650
      },
      "detector": {                // Mode B only
        "dc_tau_ms": 1000,
        "smooth_tau_ms": 60,
        "threshold_factor": 1.4,
        "threshold_floor": 1.1,
        "rearm_ratio": 0.6,
        "refractory_ms": 180
      }
    }
  }
}
```

**Profiles are per-game**, and they're why `active_profile` exists — VRChat and Minecraft want
different keys and different sprint thresholds, and switching should be one tap in the app,
not a re-edit. `tiers` stays an ordered list so a third speed tier is a config entry rather
than a code change; the app renders the list generically for the same reason.

Validation lives on the PC and is strict: a rejected patch is answered with `CFGERR` and the
old config stays live. **The app can't put the PC into a broken state**, which matters when
the settings UI is a phone screen being poked at arm's length.

## 5. Protocol

UDP, ASCII lines, one datagram each. Readable in a packet capture, which is the point.

**Phone → PC** (port 5599)

| Message | Purpose |
|---|---|
| `SL1 <tok> HELLO <seq> <device> <mode> <ver>` | announce, on service start |
| `SL1 <tok> STEP <seq> <t_ms>` | Mode A: a step happened |
| `SL1 <tok> ACC <seq> <t_ms> <x> <y> <z>` | Mode B: one sample, 50 Hz |
| `SL1 <tok> HB <seq>` | heartbeat, 4 Hz while armed |
| `SL1 <tok> STOP <seq>` | user stopped the service |
| `SL1 <tok> CFG? <seq>` | fetch current config |
| `SL1 <tok> CFG= <seq> <json-patch>` | apply a partial config change |
| `SL1 <tok> SUB <seq> <hz>` | start telemetry (settings screen open) |
| `SL1 <tok> UNSUB <seq>` | stop telemetry (screen closed / pocket) |

**PC → phone**

| Message | Purpose |
|---|---|
| `SL1 <tok> CFG! <seq> <version> <json>` | full config — on request *and* pushed on change |
| `SL1 <tok> CFGOK <seq> <version>` | patch applied |
| `SL1 <tok> CFGERR <seq> <reason>` | patch rejected, old config still live |
| `SL1 <tok> STAT <seq> <spm> <tier> <sig> <thr> <armed>` | telemetry while subscribed |

**Discovery** (broadcast, port 5598)

| Message | Purpose |
|---|---|
| `GW-DISCOVER? <ver>` → broadcast | app looks for helpers |
| `GW-HERE <host> <ip> <port> <open\|paired>` | PC answers, unicast |
| `GW-PAIR <pin>` | app submits the PIN shown in the PC console |
| `GW-PAIRED <token>` | PC hands over the token, once |

**Reliability.** Sensor traffic stays fire-and-forget — a lost step costs nothing because
another arrives in ~500 ms, and that self-healing property is why UDP was chosen. But config
must not be lossy, so control messages are retried 3× at 200 ms and made idempotent by
`<seq>` and `<version>`. That's ~20 lines of code, and it keeps one transport for everything
rather than dragging in TCP alongside.

**Telemetry is subscription-based** so the PC isn't shouting `STAT` at a phone in a pocket.
`SUB` when the tuning screen opens, `UNSUB` when it closes — measurable battery, zero cost.

**Pairing** exists so a housemate's phone can't walk my character into a wall. The PIN is
typed once, ever, and `require_pin: false` turns it off for a trusted LAN.

## 6. How the APK gets to my phone

This is requirement A, and it's a CI problem rather than a build problem. **GitHub's runners
already have the Android SDK installed**, which is what removes the ~700 MB that DESIGN.md §7
budgeted for this machine.

```
git tag v0.3.0 && git push --tags
        │
        ▼
 .github/workflows/android.yml   →  gradle assembleRelease
        │                           sign with key from repo secrets
        ▼
 GitHub Release  ──  phone opens the page, taps the APK, installs
```

- **Every push to `main`** builds a debug APK as a workflow artifact — the app is never more
  than a push away from being installable.
- **Every `v*` tag** builds, signs and publishes a Release.
- **Signing uses a real release key** held in repository secrets (`KEYSTORE_BASE64`,
  `KEYSTORE_PASSWORD`, `KEY_ALIAS`, `KEY_PASSWORD`), generated once. Not cosmetic: Android
  refuses to install an update whose signature differs from the installed copy, so a stable
  key is what makes upgrades work instead of forcing an uninstall each time.
- `minSdk 26`, `targetSdk 34`, Kotlin + Compose, no third-party dependencies. Expect ~3 MB.
- **Play Protect will warn** on a sideloaded APK from an unknown developer. That's expected,
  it's one "install anyway" tap, and the README will say so plainly rather than let it look
  like a failure.

Local building stays possible for anyone who wants it, but it stops being on the critical
path — which is the entire point.

## 7. Testing

| Layer | How | Needs |
|---|---|---|
| State machine, detector, protocol, config validation | `pytest`, pure functions, no I/O | nothing |
| Cadence → keys, end to end | `fakestep.py` against a real game | PC only |
| Detector against real walking | recorded traces + `replay.py` | one trace |
| Live config | `remote.py` while `fakestep.py` runs | PC only |
| Screen-off endurance | 15-min session, judged from the PC log | phone + PC |
| Latency | timestamp at send, compare to key injection | phone + PC |

The pattern worth naming: **everything except the two phone rows is testable with no phone
and no Android toolchain.** That's not an accident, it's why M1 and M2 come first.

Traces get recorded once (`pc --record walk.jsonl`) and then the whole Mode B pipeline can be
tuned sitting at the desk, repeatably, against the same walk — instead of standing up and
marching every time a constant changes.

## 8. Risk register

| Risk | When I find out | If it bites |
|---|---|---|
| `SendInput` blocked by the target game | **M1** — before any app work | Try scancode variants; interception driver is the fallback |
| Step detector ignores walking *in place* | **M5**, first install | Mode B, already built in M1 — a toggle, not a redesign |
| OEM battery manager kills the service | M5 endurance run | Exemption prompt + documented per-vendor toggle |
| Hardware batches step events despite `maxReportLatency = 0` | M5 | Mode B, which streams continuously and can't be batched away |
| CI signing / install friction | **M4**, with a hello-world app | Isolated from app complexity by design |
| Router AP isolation blocks UDP | M3 | Detected explicitly, with a clear error instead of silence |
| Config UI bricks the helper | M6 | PC-side validation; bad patch rejected, old config stays live |

## 9. Repo layout

```
gamewalk/
├─ .github/workflows/     android.yml (APK + release), pc-tests.yml
├─ pc/                    server, protocol, config, machine, detector,
│                         keys (SendInput), hotkey, discovery, status
├─ tools/                 fakestep.py, replay.py, remote.py
├─ tests/                 pytest suite
├─ app/                   Kotlin: MainActivity, StepService, SensorSource,
│                         Net, Discovery, ConfigClient, ui/, Prefs
├─ docs/                  R1 design, protocol notes, vendor battery notes
├─ run.bat
├─ DESIGN.md  BUILD_PLAN.md  README.md
```

## 10. Order of work

```
M0 ─► M1 ─► M2 ─► M3        PC side. No phone, no toolchain. Ends fully tunable.
            │
            ▼
           M4 ─► M5 ─► M6 ─► M7 ─► M8
           APK   screen   live    zero-
           pipe   off     config  config
```

M1 is the go/no-go gate. M4 is the delivery gate. Everything else is ordinary work, and after
M4 every milestone ends with an APK I can install from my phone.

## 11. Deltas from DESIGN.md

| DESIGN.md said | This plan says | Why |
|---|---|---|
| §1: phone is dumb, config is a PC file | PC still *owns* config, but the app reads and writes it live | Requirement B. Ownership stays on the PC so there's one source of truth |
| §6: flat config | Adds `profiles`, `active_profile`, `version`, `start_steps` | Per-game setups; `version` is what makes live sync work |
| §7 Phase 2: ~700 MB local toolchain | CI builds the APK; nothing installed here | Requirement A. GitHub runners already have the SDK |
| §7 Phase 4: discovery is comfort | M3/M7, core | "It's all good" can't include typing an IP address |
| §5: one-way protocol | Adds config + telemetry messages, retried | The control channel B requires |
| §4a: heartbeat only | Adds `SUB`/`UNSUB` gating for telemetry | Don't stream stats to a pocketed phone |

Unchanged and still load-bearing: the state machine and hysteresis (§4), continuous key hold
rather than per-step taps, adaptive hold timing, key-set diffing on tier changes, `SendInput`
scancodes (§3), the screen-off mechanics (§2), and the Mode B detector (§4b).
