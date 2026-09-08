# PC helper

Listens for step packets and holds keys. Python 3.10+, standard library only.

```
run.bat                 hold real keys
run.bat --dry-run       log keystrokes instead of pressing them
run.bat --pair          open a pairing window for a new phone
python -m pc --selftest validate config and exit
```

## Testing it without a phone

This is the point of M1: the entire PC half is exercisable from the keyboard.

```bash
# Terminal 1 -- dry run, so it can't type into whatever you have focused
python -m pc --dry-run --verbose

# Terminal 2 -- pretend to be a phone walking, then jogging, then walking
python tools/fakestep.py --ramp 90:190 --period 12
```

A healthy ramp logs **four** key events, not one per step:

```
[dry-run] v w          W pressed once, at the first step
[dry-run] v shift      Shift added on crossing 145 spm
[dry-run] ^ shift      Shift dropped on falling below 130 spm
[dry-run] ^ w          released when the steps stop
```

Seeing `v w` / `^ w` repeat is the stutter bug: the hold is expiring between
steps. Raise `hold.multiplier`.

Other things worth trying:

```bash
python tools/fakestep.py --spm 130 --no-stop    # phone vanishes: dead-man releases
python tools/fakestep.py --spm 120 --stutter    # drops 5% of packets
python tools/remote.py meta                     # every field the app can edit
python tools/remote.py watch                    # live cadence and tier
python tools/remote.py discover                 # find helpers on the LAN
```

## Changing settings while it runs

Nothing needs restarting, and it works in both directions: `remote.py` (and
later the app) patches the live config, and hand-editing `config.json` pushes
out to connected clients.

```bash
python tools/remote.py get
python tools/remote.py set profiles.default.hold.multiplier 2.2
python tools/remote.py set profiles.default.tiers.1.keys '["ctrl","w"]'
```

Bad values are refused and the running config is left alone:

```
$ python tools/remote.py set profiles.default.tiers.1.exit_spm 200
rejected: tiers[1] (run): exit spm (200) must be below enter spm (145),
          otherwise the tier flickers
```

## Files

| File | What |
|---|---|
| `server.py` | UDP listener, config service, main loop |
| `machine.py` | Steps in, held keys out. Cadence, tiers, hysteresis, hold |
| `detector.py` | Mode B: finds steps in raw accelerometer data |
| `keys.py` | `SendInput` with scancodes, and the key-set diffing |
| `config.py` | Validation, patching, `meta` descriptors for the app's UI |
| `discovery.py` | LAN discovery and PIN pairing |
| `hotkey.py` | Global panic key |
| `protocol.py` | The SL1 wire format |

`config.json` is created from `config.default.json` on first run and is
gitignored — it holds your token and your tuning. Delete it to start over.

## Notes

- **Keys go to the focused window.** The game must have focus; that's normal,
  since you're playing it.
- **Panic key is F8** by default. It releases everything and disarms. Press
  again to re-arm.
- **Only one helper at a time.** A second start fails with a clear message
  rather than silently splitting the phone's packets between two processes.
