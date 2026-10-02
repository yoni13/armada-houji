# Armada on Xiaomi 14 (houji)

Experimental Xiaomi 14 port using the **stock, unlocked Xiaomi ABL**. It boots
Armada from internal userdata with Plasma Mobile and Steam Game Mode. It does
not use Armada's modified ABL installation path for supported handhelds.

Most of this port was implemented and debugged by an AI coding agent, with the
owner testing the physical phone. Treat it as an experimental community port;
review the changes before installing. Investigation notes and fixes are in
[HISTORY.md](HISTORY.md).

## Hardware status

These results describe the development handset. A newly built image still needs
its own hardware validation; a successful build does not establish hardware support.

| Feature | Status and limitations |
| --- | --- |
| Internal boot | Working with stock ABL, unlocked bootloader, slot B. No USB root server or automatic reboot timer. |
| Plasma Mobile / Steam Game Mode | Both launch with GPU rendering; session switching works. Game compatibility depends on Armada's translation stack. |
| Display | N3 panel, 1200 × 2670, fixed 120 Hz target. Kernel vblank measurements were about 119 Hz. RGB format correction fixes the gross pink tint; no color calibration claim. |
| Touch | Stock Xiaomi processing core with a Linux transport and uinput. Taps and swipes work. |
| Rotation | Sensor-driven Plasma and Game Mode rotation. Plasma touch alignment tested; recheck touch in all Game Mode orientations after installing. Gyro aiming is not implemented. |
| Wi-Fi | WCN7850, 2.4 and 5 GHz; two streams and 80 MHz tested. Local iperf reached about 509 Mb/s down and 424 Mb/s up on the tested AP. Rates depend on signal and AP settings. |
| Bluetooth | Controller pairing and control tested. Audio profiles and other accessories untested. |
| Haptics | Short and long vibration effects tested. Application integration varies. |
| Speakers / microphone | Stereo playback and microphone recording tested, using the handset's factory speaker calibration. |
| Battery / wired charging | Battery telemetry, Steam estimates and USB-PD work. Xiaomi fast charging is experimental: authentication and about 19 W battery power tested; stock HyperCharge behavior and full 90 W input power remain unverified. Battery power uses voltage/current, not the firmware's fixed 10 W placeholder. Computer USB may supply less than the running system consumes. |
| Wireless charging | Starts and then stops in testing; unresolved. |
| Sleep | Experimental native `s2idle` is the default on battery; wired fast charging uses light sleep to keep thermal monitoring active. A missing DSP-service wake path has been repaired and exercised: the sensor DSP woke the AP for a request, with audio, sensors and battery telemetry working afterward. Firmware requests can wake the system; automatic return to sleep after serving them is not implemented. Light sleep remains selectable, with GPU/display runtime suspend and display-domain power-off verified in Plasma and Steam. Sensor restart no longer blocks Steam, and Gamescope waits for a fresh frame before restoring the display, with a timed fallback. Experimental WoWLAN is disabled after a network recovery failure. Deeper SoC power collapse, battery savings and long-term reliability are not established. |
| USB device | Charging by default; MTP on demand through Armada's switch. USB 3 at 5 Gb/s tested in both connector orientations, with USB 2 fallback. No USB shell or network gadget. |
| USB host / OTG | Wired gamepad and Pixel webcam tested, including 5 Gb/s webcam transport. No USB 3 storage-drive test; USB4/DisplayPort support is not claimed. |
| GPS | A satellite fix was verified with the opt-in development tools. No default GeoClue provider, navigation integration or location logger. Modem restart during testing remains unreliable. |
| NFC | Experimental reader support. Card discovery, ISO-DEP activation and a card response tested. The test card has no standard NDEF application; reading NDEF contents from physical tags, other tag families and writes are unverified. No payment integration. Off by default. |
| NFC tag emulation | NFC Manager provides a read-only Type 4 text tag with an automatic or custom four-byte NFC-A serial. Text and a synthetic serial were verified with another phone. Other UID lengths and card protocols are not implemented. |
| Cellular / internal cameras / fingerprint | Not implemented or validated. A GPS fix does not establish cellular service. |

Only the development handset's N3 panel and storage variant were tested. Other
panel revisions, capacities and regional firmware combinations need validation.
The installer checks capacity, rather than requiring a particular storage size.

## Firmware and boot layout

The reference firmware is **OS3.0.303.0.WNCTWXM**, Taiwan, from Xiaomi's
[public fastboot archive](https://bigota.d.miui.com/OS3.0.303.0.WNCTWXM/houji_tw_global_images_OS3.0.303.0.WNCTWXM_20260727.0000.00_16.0_tw_9ffae02e56.tgz).
The archive SHA-256 and all source revisions are pinned in [sources.json](sources.json).
Required extracted firmware is included under [firmware/](firmware/README.md).
It contains stock defaults, not another phone's calibration, saved connections,
Bluetooth pairing keys or location data.

The build targets Armada **20260926** (`c2fd0485b4db`), with Linux **7.2.6** plus
the patches in `patches/series` and the final release's pinned arm64 OCI image.
It compiles the port's touch, sensor, charging, Gamescope,
MTP, audio and GPS components from the supplied sources and pinned dependencies.
Houji retains its separately pinned Gamescope build for touchscreen rotation.
The release's native `s2idle` improvements are included in the kernel and
`s2idle` is Houji's default sleep mode. Light sleep remains available through
Armada's sleep-mode setting. The sensor teardown change alone did not prevent
the DSP watchdog. The kernel now lets the sensor file-service listener wake
and answer requests during sleep; this path has been exercised with working
audio and battery telemetry afterward. Native suspend remains experimental;
the release's reported battery savings are not established for Houji.

`boot_b` holds the Linux kernel; `init_boot_b` holds the early-boot ramdisk;
`vendor_boot_b` holds the mainline device tree. `dtbo_b` retains Xiaomi's
selection fields with a no-op overlay. `vbmeta_b` is packaged with verification
disabled. **No ABL, partition table or stock firmware partition is flashed.**

Xiaomi's ABL also requires nonzero boot version metadata even when unlocked.
The images retain the stock compatibility values (Android 14 / 2026-02-01).
Those values describe the bootloader contract, not Armada's security patch level.

Userdata contains an ext4 filesystem with a read-only EROFS system image and a
writable overlay. First boot grows ext4 to the partition's available capacity.
Stock `modem_b`, `dsp_b` and `persist` are read for ADSP firmware, DSP libraries
and this handset's calibration. Ext4 firmware/calibration mounts use `ro,noload`.
GPS additionally needs the handset's stock modem firmware and NV data; these
partitions must not be erased or replaced with files from someone else's phone.

## Build

Use a Linux host with rootless Podman configured, at least 80 GiB of free build
space and an arm64 cross toolchain. The scripts use these host tools:

- Python 3.11 or newer, including `libfdt`; Bash, Git, curl, tar, patch, rsync,
  Perl, bc and kmod (`depmod`).
- On non-arm64 hosts, QEMU arm64 user emulation (`qemu-aarch64-static` or
  `qemu-aarch64`) to check the early-boot commands before packaging.
- `aarch64-linux-gnu-gcc`, `g++`, binutils; make, Meson, Ninja, pkg-config, patchelf,
  ccache, flex, bison, OpenSSL development headers, ELF development headers and pahole.
- `dtc`, `fdtget`, `fdtput`, `fdtoverlay`; `wayland-scanner`, GLib code generators,
  protobuf-c compiler, glslangValidator, m4 and the host ALSA topology library.
- `bsdtar`, zstd, `mkfs.erofs`, `fsck.erofs`, e2fsprogs and Android sparse-image tools
  (`img2simg`, `simg2img`); Android platform-tools for flashing.

The build fetches pinned source revisions, verifies pinned RPM and firmware
hashes, and links against libraries from an **unstarted public Armada image**.
It does not need a phone or the original porting workspace.

No sibling `build/`, `firmware/` or `sources/` directory is required. A normal
build uses the firmware already included in `ports/houji/firmware/` and downloads
the pinned public sources itself. The full HyperOS archive is needed only if
you choose to regenerate those bundled firmware files.

From the fork's root:

```sh
python3 ports/houji/build.py
```

Outputs are under `output/houji/` (ignored by Git). The complete flash bundle is
`output/houji/images/`. Build work and download caches stay under
`output/houji/work/`. To reuse an already completed kernel while rebuilding
userspace, use `--skip-kernel`. To choose another output directory, use `--output`.
`--image` accepts an arm64 Armada OCI image built from your fork; changing it may
also require updating the pinned development dependencies.

The default command builds the kernel, all port userspace, the root filesystem,
the boot images and sparse userdata. `--skip-userdata` omits only the destructive
fresh-install image, for an existing installation's preserving update.

To regenerate the bundled firmware from the exact pinned archive:

```sh
python3 ports/houji/firmware.py /path/to/houji-firmware.tgz output/houji/extracted-firmware
```

This regeneration additionally needs `lpunpack`, `mcopy` and roughly 20 GiB of
temporary space. `--scratch /path/to/temp` selects that space. The extractor
only reads allowlisted files and verifies the archive before extraction.

## Fresh installation — erases userdata

Back up Android data and know how to return to Xiaomi fastboot. Start from the
reference stock firmware with an unlocked bootloader. Connect the phone to the
host in **bootloader fastboot**, not fastbootd. The installer checks the product,
unlock state, pending snapshots, image checksums, boot headers and capacities
before any write. Never relock the bootloader with these images installed.

```sh
fastboot devices
python3 output/houji/images/flash-internal.py \
  --images-dir output/houji/images --serial YOUR_FASTBOOT_SERIAL --check-only
python3 output/houji/images/flash-internal.py \
  --images-dir output/houji/images --serial YOUR_FASTBOOT_SERIAL --erase-userdata
```

`--erase-userdata` explicitly authorizes losing **all Android/user files**.
The installer writes userdata first, then the boot components, activates slot B
and reboots. First boot should reach Plasma Mobile. Configure Wi-Fi through the
normal UI. Use the session switch to enter Steam Game Mode.

The pinned Armada image can report `armada-controller-type.service` failed
when no controller is attached; this does not prevent touch or session startup.
If initial Flatpak setup fails before the clock synchronizes, connect to Wi-Fi,
wait for the correct time, then run
`sudo systemctl restart armada-flatpak-setup.service`.

For reference, the write sequence after preflight is:

```sh
fastboot -s YOUR_FASTBOOT_SERIAL erase userdata
fastboot -s YOUR_FASTBOOT_SERIAL flash userdata output/houji/images/userdata.img
fastboot -s YOUR_FASTBOOT_SERIAL flash boot_b output/houji/images/boot.img
fastboot -s YOUR_FASTBOOT_SERIAL flash init_boot_b output/houji/images/init_boot.img
fastboot -s YOUR_FASTBOOT_SERIAL flash vendor_boot_b output/houji/images/vendor_boot.img
fastboot -s YOUR_FASTBOOT_SERIAL flash dtbo_b output/houji/images/dtbo.img
fastboot -s YOUR_FASTBOOT_SERIAL flash vbmeta_b output/houji/images/vbmeta.img
fastboot -s YOUR_FASTBOOT_SERIAL set_active b
fastboot -s YOUR_FASTBOOT_SERIAL reboot
```

Prefer the installer: the raw commands bypass its checks. Do not use another
Armada device's ABL scripts, format `persist`, or erase modem NV partitions.

## Updating an installed Armada system without erasing games

Build the new bundle, optionally with `--skip-userdata`. Copy `rootfs.erofs`,
`images.json` and `ports/houji/install/stage-update.py` to a temporary directory
on the phone through an owner-enabled transfer method. On the phone, run:

```sh
sudo python3 /path/to/update/stage-update.py /path/to/update \
  --serial YOUR_FASTBOOT_SERIAL
```

This verifies the root image, creates a clean system overlay, preserves the
existing home and selected owner settings, and leaves the old root available
for rollback. It does not transfer personal settings into the public bundle.
NFC Manager settings are preserved locally with the other owner settings.
Use this port's update procedure; Armada's generic bootc/rebase updater does
not manage Houji's stock-ABL userdata layout.
Copy its `staged-receipt.json` back to the host, then enter bootloader fastboot:

```sh
python3 output/houji/images/flash-internal.py \
  --images-dir output/houji/images --serial YOUR_FASTBOOT_SERIAL \
  --boot-only --staged-receipt /path/to/staged-receipt.json --check-only
python3 output/houji/images/flash-internal.py \
  --images-dir output/houji/images --serial YOUR_FASTBOOT_SERIAL \
  --boot-only --staged-receipt /path/to/staged-receipt.json
```

Keep the previous boot bundle until the new system is verified. A boot-only
flash without staging the matching root image is rejected. Ordinary bootc OTA
updates are not supported by this custom layout.

## Charging and sleep

Xiaomi fast charging is experimental; matching stock HyperCharge behavior is
not yet verified. Normal wired charging has a 3 A battery-current ceiling.
Authenticated Xiaomi PD-PPS charging can select a separate 15.6 A ceiling,
subject to thermal votes, charge level and firmware limits. A staged test
measured about 19 W at the battery. These current ceilings and the adapter's
advertised 90 W are not measured charging rates. The port also retains a
conservative 38°C battery-temperature cutoff for high current in the installed
test build, falling back to 500 mA above it. Current sources contain an
unflashed, hardware-unverified change that permits the firmware's 15–47°C
fast-charge range only with authenticated PD-PPS, active firmware fast-charge
mode, healthy telemetry and a matching kernel guard. Other charging modes keep
the conservative limit. Neither path establishes Xiaomi's full charging curve.

During an authenticated wired charging session, Power uses light sleep to keep
the host thermal monitor running with the screen off. When charging ends or the
cable is unplugged, it returns to the configured sleep mode. Native suspend
remains the default on battery. Direct native suspend reduces the charging
vote to 500 mA until the monitor resumes. Wireless charging remains experimental.

## USB and privacy

USB starts in charging mode. Enable file transfer using Armada's MTP switch;
OTG role detection remains available for wired peripherals. No automatic SSH,
ADB, serial login, network gadget or test-network connection is added by this
port. Remote access must be enabled by the owner.

GPS is opt-in and is not started at boot. Its client reports fix validity and
satellite counts without logging coordinates; position data is only sent through
an explicitly requested private output channel. Do not publish runtime logs,
recordings, modem NV, factory calibration, SSH keys or saved network profiles.

## NFC Manager

Open **NFC Manager** from KDE's application menu (under Settings). Enable
**Read nearby tags**, then hold a tag against the upper back of the phone.
The app shows scan status and a count of detected tags. Use **Scan again** for
another discovery attempt. The app does not display or save card contents.

To present a tag to another phone, enter text and tap **Emulate text tag**.
Enable **Custom serial** to enter a four-byte NFC-A identifier, such as the
synthetic test value `12:34:56:78`; leave it disabled for automatic selection.
Emulation serves only a read-only NDEF text record, up to 200 UTF-8 bytes.
Text, serial choice and reader/emulation mode are saved on the phone. Emulation
continues when the app closes or the desktop session ends, and the selected mode
is restored at boot. Reopen the app and tap **Stop emulation** to stop broadcasting
and return to the saved reader setting. Editing tag settings saves them automatically.

Settings live in `/var/lib/armada-nfc/settings.json`, readable only by root and
through the authorized app interface. They are not written to logs or copied into
build artifacts. Received card identifiers and contents are not saved. NFC starts
off on a fresh installation; saved reader/emulation choices persist afterward.

The interface runs as the desktop user. A background system service performs
hardware operations; Polkit permits changes from the active local session.
The ordinary build installs the app, desktop entry, policies and a small
kernel module that holds the shared NFC supply during emulation. It uses the
GTK4, libadwaita and PyGObject libraries already in the pinned Armada base.
No private device files or Python package downloads are required.

This app is a separate menu entry, not a KDE System Settings module. It does
not implement payments, physical-card copying or other card applications.

### Reader implementation and command-line use

The port uses the stock controller firmware, the Linux NXP NCI driver and a
hash-pinned Fedora `neard` package. No NFC firmware download or handset-specific
card data is needed. The reader daemon is available on demand through D-Bus; the
radio stays off until enabled or a saved enabled mode is restored. The app pauses
this reader while emulating a text tag and restores it afterward.

Stop emulation in NFC Manager before using the reader directly. For a single
discovery/read attempt, run on the phone:

```sh
sudo systemctl start neard
sudo busctl set-property org.neard /org/neard/nfc0 org.neard.Adapter Powered b true
sudo busctl call org.neard /org/neard/nfc0 org.neard.Adapter StartPollLoop s Initiator
sudo busctl tree org.neard
```

Hold the tag against the upper back. The object tree shows detected tags and
any readable NDEF records without dumping their contents or identifiers. The
built-in controller normally appears as `nfc0`; use the adapter path reported
by `busctl tree org.neard` if it differs. NFC applications can use neard's
`org.neard.Tag` and `org.neard.Record` interfaces. A detected card need not
contain NDEF data, and detection does not grant access to protected sectors.

To finish, stop polling and power down the radio before stopping the service.
If polling has already stopped, continue with power-off. Restart the service
before another attempt if an unsupported card left a stale tag object:

```sh
sudo busctl call org.neard /org/neard/nfc0 org.neard.Adapter StopPollLoop
sudo busctl set-property org.neard /org/neard/nfc0 org.neard.Adapter Powered b false
sudo systemctl stop neard
```

Debug packet logging is not enabled. Do not publish tag identifiers or card
contents from separately enabled NFC diagnostic tools.
