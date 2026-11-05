# Tests

Run on Linux, macOS or WSL. The clipboard and the `tailscale` command are faked, no devices needed.

```
pip install flask waitress pyperclip pillow pillow-heif
cd tests
python -I sync_tests.py   .. $PWD              # one-button sync, direction rules, no bounce-back
python -I paste_tests.py  .. $PWD              # files onto the PC clipboard
python -I pull_tests.py   .. $PWD/tmp          # PC -> iPhone fetch
python -I hotkey_tests.py .. $PWD              # Ctrl+Alt+S / Taildrop
python -I sims.py         .. $PWD/tmp/a 8801   # failures: disk full, aborted upload, races
python -I sims_extra.py   .. $PWD/tmp/b 8802   # features and regressions
```

102 checks. The Windows-only parts (notifications, the hotkey, the clipboard file hand-off, the `.ps1` scripts) can only be checked on a real PC.
