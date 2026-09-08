# GameWalk

Walk in place with your phone in your pocket, and your PC thinks you're
holding `W`. Jog and it becomes `Shift+W`. Buttons and timing fully customizable. 


> **Status: Works for me, requires some fine tuning and testing. **

## Install

### 1. Phone

**[⬇ Download gamewalk.apk](https://github.com/TkFrittz/gamewalk/releases/download/latest/gamewalk.apk)**
— open that link *on your phone*, tap the file, install.

- Android will ask whether to allow installs from your browser. Say yes.
- Play Protect will warn that the developer is unknown. That's what it always says about a
  sideloaded app; tap **Install anyway**.

### 2. PC

Double-click **`run.bat`**. You need Python 3.10+; nothing else to install. it opens a nice easy to use GUI

### 3. Pair

Open the app → **Find my PC** → tap the one it finds → type that PIN.

Grant the three basic permissions it asks for. 

| Permission | Without it |
|---|---|
| Physical activity | The step sensor returns nothing at all |
| Notifications | No service notification, and Android may kill the service |
| Unrestricted battery | The system throttles it once the phone is idle |

### 4. Walk

Tap **Start walking**, pocket the phone. You can turn the screen off. Focus your game. Walk in place.

## Using it

- **Everything is configured from the phone, or the PC GUI live — keybinds, speed tiers, sensitivity,
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



Expect rough edges on first run, and tell me what you see.


## Known risks


- **Anticheat** MAY treat this like synthetic scancode input like a macro tool. Fine for single-player and
  VRChat; don't point it at competitive multiplayer. It hasn't been a problem for me but yaknow..

