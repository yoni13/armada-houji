# Autofill stub device test

AutofillTest is a one-screen app that makes an unfocused text field holding text visible, the shape of Discord's
two-factor step. On Lepton's Android 11 without an autofill service it dies in `AutofillManager` 3 ms after showing
the field (control run, 2026-10-25: `NullPointerException ... IAutoFillManager.addClient`). With
`AutofillStub` running it should log `survived: enabled=false ...` instead.

1. `bash build.sh` writes `out/autofill-test.apk`. It uses the pinned Android platform and needs `javac`, `d8`,
   `aapt2`, `zipalign`, `apksigner` and `keytool`.
2. Copy `out/autofill-test.apk` and `steam-shortcut.py` to the phone, for example to `~/.cache/autofill-test/`.
3. Install the stub: an image built from this branch has it. On a phone running an older image, copy
   `../../prebuilt/system/framework/armada-autofillstub.jar` and
   `../../overlay/system/etc/init/armada-autofillstub.rc` to `framework/` and `etc/init/` under
   `/usr/share/steam/compatibilitytools.d/lepton-armada/overlay/system/` (as root). Leave them out for the control run.
4. As the Game Mode user: `python3 steam-shortcut.py create AutofillTest /var/home/armada/.cache/autofill-test/autofill-test.apk`
   prints an app id, and `python3 steam-shortcut.py run <app id>` starts it. The first launch installs the app, so
   allow about 60 s. Stop any other Lepton run first.
5. Read `~/.local/share/lepton/steamvr/logs/lepton-steamlaunch-<app id>.log` (strip colors with
   `sed 's/\x1b\[[0-9;]*m//g'`). After `AutofillTest: showing the text field`, `FATAL EXCEPTION` means no stub, and
   `AutofillTest: survived:` means the stub answered.
6. Clean up: `python3 steam-shortcut.py stop <app id>`, then `remove <app id>`, and delete the cache directory.
