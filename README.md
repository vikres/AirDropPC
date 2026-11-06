# AirDropPC Drop

AirDrop between an iPhone and a Windows PC, over [Tailscale](https://tailscale.com). Copy on one device, press the iPhone's Action Button, paste on the other. Text, links, photos, files. Works on any Wi-Fi or mobile data, nothing goes through a third-party server.

Built for my own use. It works for me; I don't promise support.

**[Download](https://github.com/vikres/iphone-pc-drop/archive/refs/heads/main.zip)** · Windows 10/11

**[Download](https://www.icloud.com/shortcuts/d449036ebccd4806b2b7ed2fc62e4868)** · Iphone shortcut

| You do | What happens |
|---|---|
| Copy on the iPhone, press the Action Button | Text is on the PC clipboard. Files go to Downloads and onto the PC clipboard. |
| Copy on the PC, press the Action Button | Text is on the iPhone clipboard. Files go to Files › On My iPhone › PC. |
| Select files on the PC, press Ctrl+Alt+S | Sent to the iPhone with Taildrop. Use this for big files. |

## Install

Needs Windows 10 or 11, an iPhone and a free Tailscale account. The Action Button exists on iPhone 15 Pro and later; older iPhones use Back Tap (step 7).

1. **Tailscale on both devices.** [PC](https://tailscale.com/download/windows), [iPhone](https://apps.apple.com/app/tailscale/id1470499037), same account on both. In the iPhone app, turn on VPN On Demand so it stays connected.
2. **Python on the PC.** From [python.org](https://www.python.org/downloads/). Tick "Add python.exe to PATH" in the installer.
3. **This project on the PC.** Code → Download ZIP. Unzip it somewhere permanent, e.g. `C:\Tools\iphone-pc-drop`. Not Downloads: the receiver runs from this folder at every logon.
4. **Double-click `Install.cmd`.** Accept the admin prompt. It installs the Python packages, creates your token, opens port 8765 to Tailscale addresses only, and starts the receiver at logon. At the end it shows your PC address and token, as text and as QR codes. Leave the window open.
5. **On the iPhone**, Files app → On My iPhone → new folder named `PC`.
6. **Add the Shortcut:** (https://www.icloud.com/shortcuts/d449036ebccd4806b2b7ed2fc62e4868). It asks for the PC address and the token: scan their QR codes in the Install window with the iPhone camera to copy them, or type them. Then pick the `PC` folder.
7. Settings → Action Button → Shortcut → Sync with PC. (No Action Button: Settings → Accessibility → Touch → Back Tap → Double Tap → Sync with PC.)
   Settings → Apps → Shortcuts → Paste from Other Apps → Allow, otherwise iOS asks on every press.

Check: copy some text on the iPhone, press the Action Button, Ctrl+V on the PC.

## Daily use

Copy, then press the Action Button. The receiver picks the direction:

1. Something was copied on the PC in the last 20 seconds → it goes to the iPhone.
2. Else the iPhone clipboard is new → it goes to the PC.
3. Else the PC has a copy the iPhone hasn't fetched yet → it goes to the iPhone.
4. Else "Nothing new".

The notification says which way it went. An iPhone text that lost to a newer PC copy is kept in `Downloads\iPhone text history.txt`.

- A link from the iPhone also opens in the PC browser.
- Files from the iPhone are put on the PC clipboard too. Files arriving within 20 seconds of each other paste together.
- From the PC: several files arrive as separate files, a folder as a zip, a screenshot as a PNG.
- From the iPhone the Action Button sends one item, and iOS drops the file name (you get `Clipboard <date>.pdf`). For several files, or real names, use Share → Tailscale; they land in Downloads.
- WhatsApp: copying messages gives their text only. For a document, open it → Share → Tailscale. (Whatsapp limitation)
- Files of hundreds of MB exceed the memory iOS gives a Shortcut. Share → Tailscale.
- Ctrl+Alt+S needs "Send Files" on in the Tailscale admin console (Settings → General).

## If it breaks

- Re-run `Install.cmd`. Safe to repeat, keeps your token, fixes most things (Python update, missing package, receiver not started). To update: replace `iphone_drop.py`, re-run it.
- "403" on the iPhone: the token in the Shortcut is wrong. Re-run `Install.cmd` to see it again, and correct it in the Shortcut.
- Still broken: run `python iphone_drop.py` in the folder, errors show in the window. The log is `iphone_drop.log`.
- Uninstall: `Uninstall.cmd`, then delete the folder.

## Safety

- Only devices on your Tailscale network can reach the PC (firewall rule plus an address check in the receiver), and every request needs your token.
- Tailscale encrypts everything end to end.
- The iPhone only gets what was last copied on the PC. It can't browse the disk.
- Send the original github if you want to share it, to not share your tokens)

## How it works

`iphone_drop.py` is a small Flask server on port 8765, started at logon by a scheduled task. The Shortcut POSTs the iPhone clipboard to `/sync`; the receiver compares it with what was copied on the PC and when, and either keeps it or replies with the PC clipboard. iOS never tells a Shortcut when something was copied, only the PC knows its own copy times, so the PC decides the direction. Content fingerprints stop an item bouncing back to where it came from. `/drop` (iPhone → PC only) and `/pull` (PC → iPhone only) are there if you want your own Shortcuts.

Settings are at the top of `iphone_drop.py`; re-run `Install.cmd` after changing them. Tests are in `tests/`.

MIT licence.
