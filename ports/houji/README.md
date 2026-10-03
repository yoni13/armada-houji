# Armada on Xiaomi 14 (houji)

> [!WARNING]
> **Unofficial and experimental.** This is not an upstream-supported device.
> A fresh install **erases all data on the phone**. Keep the bootloader
> unlocked and **never relock it** while this is installed. Read
> [Before you start](#before-you-start).

This port runs Armada (Plasma Mobile and Steam Game Mode) on the Xiaomi 14
("houji", Snapdragon 8 Gen 3). It boots with the phone's **stock, unlocked Xiaomi
bootloader (ABL)** and runs from internal storage. It does **not** use the
modified-ABL path that Armada's supported handhelds use, so the standard Armada
install guides do not apply.

**Heads-up: an AI agent did most of the work.** The porting, reverse engineering
and debugging were done mainly by an AI coding agent. The device owner supplied
hardware, ran supervised tests and reported what they saw. Treat this as a
community experiment and review the changes before flashing. What was tried and
fixed is in [HISTORY.md](HISTORY.md).

## Contents

- [Before you start](#before-you-start)
- [What works](#what-works)
- [Known issues](#known-issues)
- [Firmware and boot layout](#firmware-and-boot-layout)
- [Build](#build)
- [Install](#install)
- [Update without erasing games](#update-without-erasing-games)
- [Kernel-only updates](#kernel-only-updates)
- [Continuous integration](#continuous-integration)
- [Charging and sleep](#charging-and-sleep)
- [USB and privacy](#usb-and-privacy)
- [NFC Manager](#nfc-manager)

## Before you start

You need:

- A Xiaomi 14 on the reference firmware, **OS3.0.303.0.WNCTWXM** (Taiwan), with an
  **unlocked bootloader**.
- A Linux build host with rootless Podman, about 80 GiB free and an arm64 cross
  toolchain (see [Build](#build)), plus Android platform-tools for flashing.
- A backup of anything on the phone. A fresh install wipes `userdata`.

Know how to get back to Xiaomi fastboot, and keep the original firmware
archive if you may want to return to HyperOS.

**Tested on one phone only:** the development handset, with its N3 panel and its
storage size. Other panel revisions, capacities and regional firmware are
unvalidated. A successful build does not prove the hardware works; validate each
new image on the phone.

## What works

Results below are from the development handset.

### Working

| Feature | Notes |
| --- | --- |
| Boot | Stock ABL, unlocked, slot B. No USB root server and no auto-reboot timer. |
| Plasma Mobile and Steam Game Mode | Both run with GPU rendering, and switching between them works. Game compatibility depends on Armada's translation stack. |
| Display | N3 panel, 1200 × 2670, 120 Hz target (about 119 Hz measured at vblank). RGB format fix removed the pink tint. Not colour-calibrated. |
| Touch | Taps and swipes work, using Xiaomi's stock touch processing core. |
| Rotation | Sensor-driven in Plasma and Game Mode. Touch alignment was tested in Plasma; recheck it in every Game Mode orientation. |
| Wi-Fi | WCN7850, 2.4 and 5 GHz, two streams at 80 MHz. About 509 Mb/s down and 424 Mb/s up in a local test. Speed depends on signal and access point. |
| Bluetooth | Controller pairing and control. |
| Haptics | Short and long vibration. |
| Speakers and microphone | Stereo playback and recording, using the phone's factory speaker calibration. |
| Battery | Voltage, current, charge level, Steam time estimates and USB-PD. |
| USB device | Charging by default. File transfer (MTP) on demand through Armada's switch. USB 3 at 5 Gb/s in both cable orientations, USB 2 fallback. No USB shell or network gadget. |
| USB host (OTG) | Wired gamepad and a Pixel webcam, including 5 Gb/s video. |

### Partly working

| Feature | State |
| --- | --- |
| Sleep | Experimental. Native `s2idle` is the default on battery. The sensor DSP's periodic requests wake the phone, are served, and the phone sleeps again without lighting the screen. Tested for a 50-minute sleep with three such wakes, and for Power-key and alarm wakes. Long-term reliability and battery savings are not established. See [Charging and sleep](#charging-and-sleep). |
| Wired fast charging | Xiaomi authentication and about 19 W at the battery were measured. Stock HyperCharge behaviour and the full 90 W input are not verified. |
| GPS | A satellite fix works with the opt-in development tools. There is no GeoClue provider or navigation integration, and restarting the modem can reboot the phone. |
| NFC reader | Experimental. Card discovery and ISO-DEP activation work. Reading NDEF from physical tags, other tag families and writing are unverified. Off by default. |
| NFC tag emulation | NFC Manager can present a read-only text tag with an automatic or custom 4-byte serial. Verified with another phone. No payment or card copying. |
| Gyro aiming | Not implemented. Sensors drive screen rotation only. |

### Not working or untested

| Feature | State |
| --- | --- |
| Wireless charging | Starts, then stops. Unresolved. |
| Cellular, cameras, fingerprint | Not implemented or validated. A GPS fix does not mean cellular works. |
| Bluetooth audio and other accessories | Untested. |
| USB 3 storage, USB4, DisplayPort | Untested or not claimed. |
| Panel revisions, storage sizes, other regions | Untested. |

## Known issues

- **Sleep may still misbehave.** An intermittent animation freeze around Steam
  wake was reported earlier and never reproduced or explained. The sensor-DSP
  wake and re-sleep path works in testing, but only a few hours of sleep have
  been exercised, not days of standby, and the standby power cost of the
  periodic DSP wakeups has not been measured. Dark resume was tested in Steam
  Game Mode only; the Plasma Mobile session is unverified. If sleep ever
  misbehaves, create `/etc/armada/houji-sleep-classic` to go back to plain
  `systemd-sleep`.
- **Wake from sleep is not instant.** Display wake took roughly one to two
  seconds in testing.
- **Wi-Fi wake-on-LAN (WoWLAN) is off by default.** A faster-wake trial caused a
  network recovery failure, so it was disabled.
- **Charging.** The adapter's 90 W rating is not a measured rate. Computer USB
  ports can supply less than the running system uses. Wireless charging stops.
- **First boot.** Armada's `armada-controller-type.service` may report failed
  with no controller attached. This does not affect touch or sessions. If
  Flatpak setup fails before the clock syncs, connect to Wi-Fi, wait for the
  correct time, then run `sudo systemctl restart armada-flatpak-setup.service`.
- **Kernel warning.** The ath12k driver logs an hwmon resume warning. Wi-Fi
  reconnects afterward.

## Firmware and boot layout

**Firmware.** The reference firmware is **OS3.0.303.0.WNCTWXM**, from Xiaomi's
[public fastboot archive](https://bigota.d.miui.com/OS3.0.303.0.WNCTWXM/houji_tw_global_images_OS3.0.303.0.WNCTWXM_20260727.0000.00_16.0_tw_9ffae02e56.tgz).
The archive SHA-256 and every source revision are pinned in [sources.json](sources.json).
The extracted files the build needs are in [firmware/](firmware/README.md). They
hold stock defaults, not another phone's calibration, saved connections,
Bluetooth pairing keys or location data.

**What it builds on.** Armada **20260926** (`c2fd0485b4db`) with Linux **7.2.6**
plus the patches in `patches/series`, and the release's pinned arm64 OCI image.
Touch, sensor, charging, Gamescope, MTP, audio and GPS components are compiled
from pinned sources. Houji keeps its own pinned Gamescope build for touch rotation.

**Partitions.** Xiaomi's ABL loads Android boot-v4 partitions, so the images map as:

| Partition | Contents |
| --- | --- |
| `boot_b` | Linux kernel |
| `init_boot_b` | Early-boot ramdisk |
| `vendor_boot_b` | Mainline device tree |
| `dtbo_b` | Xiaomi's selection fields with a no-op overlay |
| `vbmeta_b` | Verification disabled |
| `userdata` | ext4 holding a read-only EROFS system image and a writable overlay |

**No ABL, partition table or stock firmware partition is flashed.** The images
keep the stock boot version values (Android 14, 2026-02-01) because the ABL
requires nonzero metadata even when unlocked. These describe the bootloader
contract, not Armada's security patch level.

First boot grows the ext4 filesystem to fill `userdata`. The system reads stock
`modem_b`, `dsp_b` and `persist` for ADSP firmware, DSP libraries and this
handset's calibration, with `ro,noload` mounts. GPS also needs the handset's own
modem firmware and NV data. **Do not erase these partitions or replace them with
files from someone else's phone.**

## Build

Use a Linux host with rootless Podman, at least 80 GiB free and an arm64 cross
toolchain. The scripts use:

- Python 3.11 or newer with `libfdt`; Bash, Git, curl, tar, patch, rsync, Perl,
  bc and kmod (`depmod`).
- On non-arm64 hosts, QEMU arm64 user emulation (`qemu-aarch64-static` or
  `qemu-aarch64`) to check early-boot commands before packaging.
- `aarch64-linux-gnu-gcc`, `g++`, binutils; make, Meson, Ninja, pkg-config,
  patchelf, ccache, flex, bison, OpenSSL and ELF development headers, and pahole.
- `dtc`, `fdtget`, `fdtput`, `fdtoverlay`; `wayland-scanner`, GLib code
  generators, the protobuf-c compiler, glslangValidator, m4 and the host ALSA
  topology library.
- `bsdtar`, zstd, `mkfs.erofs`, `fsck.erofs`, e2fsprogs and Android sparse-image
  tools (`img2simg`, `simg2img`); Android platform-tools for flashing.

The build fetches pinned sources, verifies pinned RPM and firmware hashes, and
links against libraries from an **unstarted public Armada image**. It needs no
phone and no other workspace. A normal build uses the firmware already in
`ports/houji/firmware/` and downloads the pinned sources itself. You need the
full HyperOS archive only to regenerate those bundled firmware files.

From the fork's root:

```sh
python3 ports/houji/build.py
```

Outputs go to `output/houji/` (ignored by Git). The flash bundle is
`output/houji/images/`. Build work and download caches are in `output/houji/work/`.

| Option | Effect |
| --- | --- |
| `--skip-kernel` | Reuse a finished kernel while rebuilding userspace. |
| `--skip-userdata` | Omit the fresh-install `userdata.img`. It is only the root image wrapped in an ext4 filesystem, so `make-userdata.py` can build it later from `rootfs.erofs`. |
| `--kernel-update` | Build only a [kernel-only update](#kernel-only-updates) (kernel, device tree and modules, about 90 MB). Needs `--localversion`. |
| `--localversion kNAME` | Suffix for the kernel release (a leading `-` is added). A kernel update installs beside the running modules, so its release name must differ from the root image's. |
| `--output DIR` | Choose another output directory. |
| `--image IMAGE` | Use an arm64 Armada OCI image built from your fork. This may also require updating pinned development dependencies. |

The default build produces the kernel, all port userspace, the root filesystem,
the boot images and sparse userdata.

To regenerate the bundled firmware from the exact pinned archive:

```sh
python3 ports/houji/firmware.py /path/to/houji-firmware.tgz output/houji/extracted-firmware
```

This also needs `lpunpack`, `mcopy` and about 20 GiB of temporary space
(`--scratch /path/to/temp` picks where). The extractor reads only allowlisted
files and verifies the archive first.

## Install

### From a downloaded release

A release from the **Houji release** workflow replaces the build step. Its root
image comes in 1.9 GB parts because GitHub assets must be under 2 GiB. Download
every file into one directory and join them:

```sh
sha256sum -c PARTS.sha256                    # each downloaded part
cat rootfs.erofs.part-* > rootfs.erofs       # join the root image
sha256sum -c SHA256SUMS                      # boot images and the joined root image
```

That directory then works like `output/houji/images/` below. A fresh install also
needs `python3 make-userdata.py` there first, as described next; an update needs
only `rootfs.erofs`, `images.json` and `stage-update.py`.

### Fresh install (erases userdata)

Start from the reference stock firmware with an unlocked bootloader. Put the
phone in **bootloader fastboot**, not fastbootd. Before writing anything, the
installer checks the product, unlock state, pending snapshots, image checksums,
boot headers and capacities.

```sh
fastboot devices
python3 output/houji/images/flash-internal.py \
  --images-dir output/houji/images --serial YOUR_FASTBOOT_SERIAL --check-only
python3 output/houji/images/flash-internal.py \
  --images-dir output/houji/images --serial YOUR_FASTBOOT_SERIAL --erase-userdata
```

A bundle built here already contains `userdata.img`. A bundle that was built with
`--skip-userdata`, including a downloaded release, does not: it holds only the root
image, and `userdata.img` is just that image inside an ext4 filesystem. Build it
next to the root image before flashing:

```sh
python3 make-userdata.py          # in the bundle directory; --scratch DIR picks the temporary space
```

It checks `rootfs.erofs` against `images.json`, writes `userdata.img` and a small
`userdata.json`, and the installer refuses a `userdata.img` that does not belong
to this bundle's root image. It needs `mke2fs`, `debugfs`, `e2fsck` and `img2simg`
(Android sparse tools) and free scratch space about the size of the root image.

`--erase-userdata` explicitly authorizes losing **all Android and user files**.
The installer writes userdata first, then the boot components, activates slot B
and reboots. First boot should reach Plasma Mobile. Set up Wi-Fi in the normal UI,
and use the session switch to enter Steam Game Mode.

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

Prefer the installer, because raw commands skip its checks. Never use another
Armada device's ABL scripts, format `persist`, or erase modem NV partitions.

### Recovery

If a boot fails, use **Power + Volume Down** to return to fastboot, then reflash
a known-good bundle. Keep the previous boot bundle until a new one is verified.

## Update without erasing games

Ordinary bootc over-the-air updates and Armada's generic rebase updater do not
work with this layout. Use this procedure instead.

1. Build the new bundle, optionally with `--skip-userdata`.
2. Copy `rootfs.erofs`, `images.json` and `ports/houji/install/stage-update.py`
   to a temporary directory on the phone by any transfer method you enable.
3. On the phone, run:

   ```sh
   sudo python3 /path/to/update/stage-update.py /path/to/update \
     --serial YOUR_FASTBOOT_SERIAL
   ```

   This verifies the root image, creates a clean system overlay, keeps your home
   and selected owner settings (including NFC Manager's), and leaves the old root
   available for rollback. It does not copy personal settings into the public bundle.
4. Copy `staged-receipt.json` back to the host and enter bootloader fastboot:

   ```sh
   python3 output/houji/images/flash-internal.py \
     --images-dir output/houji/images --serial YOUR_FASTBOOT_SERIAL \
     --boot-only --staged-receipt /path/to/staged-receipt.json --check-only
   python3 output/houji/images/flash-internal.py \
     --images-dir output/houji/images --serial YOUR_FASTBOOT_SERIAL \
     --boot-only --staged-receipt /path/to/staged-receipt.json
   ```

A boot-only flash without staging the matching root image is rejected.

## Kernel-only updates

A kernel, device-tree or kernel-module change does not need the 7 GB root image.
A kernel update is about 90 MB: it replaces `boot_b` (kernel) and `vendor_boot_b`
(device tree) and adds the new kernel's modules beside the running ones.
`init_boot`, `dtbo`, `vbmeta` and `userdata` (your games and settings) are not
touched. It cannot carry userspace changes (sleep script, services, Gamescope):
those live in the read-only root image, so use the full update above for them.

Build a bundle (the kernel build takes about 15 minutes on 20 cores and leaves a
work directory of about 14 GB):

```sh
python3 ports/houji/build.py --kernel-update --localversion kMYBUILD   # output/houji/kernel-update/
```

1. Copy the bundle to the phone by any method you have enabled and, on the phone:

   ```sh
   sudo python3 /path/to/kernel-update/install-kernel-update.py /path/to/kernel-update \
     --serial YOUR_FASTBOOT_SERIAL
   ```

   This verifies every checksum and each archive entry, installs the modules, and
   writes `kernel-receipt.json`. Running it again is harmless.
2. Copy `kernel-receipt.json` back, put the phone in **bootloader fastboot**, then:

   ```sh
   python3 flash-internal.py --kernel-only --images-dir kernel-update \
     --serial YOUR_FASTBOOT_SERIAL --kernel-receipt kernel-receipt.json --check-only
   python3 flash-internal.py --kernel-only --images-dir kernel-update \
     --serial YOUR_FASTBOOT_SERIAL --kernel-receipt kernel-receipt.json
   ```

   It writes only `boot_b` and `vendor_boot_b`, then reboots.

What stops a bad update:

- **At packaging**, the build refuses a kernel the installed initramfs could not
  boot (the filesystems it mounts, the ramdisk decompressors and the whole UFS
  storage stack down to its PHY, clocks and regulators must be built in), an `Image`
  that does not name exactly the declared release, and modules built for another
  release. It also reads its own archive back with the phone's reader, and it
  only ever overwrites an empty directory or an earlier bundle.
- **On the phone**, only plain files and directories under `modules/<release>` are
  accepted (no links, devices or traversal; owners and special bits are ignored,
  modes are normalised), and free space is checked first. The new tree is verified
  before it is renamed into place and removed again if the check afterwards fails.
  The running kernel's modules and any release that belongs to the root image are
  never touched. An installed tree of the same release that differs is refused
  unless you pass `--replace`, which swaps it by renaming the old one aside and
  puts it back if the new one fails. A run that was interrupted is cleaned up by
  the next one.
- **On the host**, the flasher needs the receipt (it ties this bundle to this
  phone), checks that `boot.img` really is the declared release, and needs slot B,
  an unlocked bootloader and large enough partitions. If the second flash fails
  after the first succeeded, it says the phone now has a new kernel with the old
  device tree.

To undo an update, flash the previous bundle's `boot.img` and `vendor_boot.img`
(a full bundle has them too) with `fastboot flash boot_b ...` and
`fastboot flash vendor_boot_b ...`. The old modules stay installed. Each update
leaves a module tree of about 180 MB on userdata, and a full update starts clean.

**Tested on hardware:**

- Re-installing the already running kernel from its own bundle, then flashing it.
  The packaged `boot.img` was byte-identical to the image that was booting, the
  phone-side install, receipt, preflight and flash all ran, and the phone came back
  on the same kernel with sensors, touch, audio and the DSP running.
- A **different** kernel through the whole path: a fresh local build
  (`7.2.6-armada-houji-kdryrun`, same config and patch series as the running
  kernel, a new release name). The phone-side install put in 1598 modules, the
  receipt and preflight passed, only `boot_b` and `vendor_boot_b` were written, and
  the phone booted the new kernel. Afterwards it matched its earlier state: the
  same two failing units as before, Wi-Fi, audio, touch and sensor services up, the
  DSP running, all three out-of-tree modules resolving under the new release, and
  fewer error-level kernel messages (10, down from 13). A 120-second sleep in Steam
  Game Mode then completed natively (118.5 s suspended, no failures, no DSP crash),
  woken by the RTC alarm.

Not covered: a Power-key wake or a DSP wake on the new kernel (the 120-second test
saw neither), and sleep in the Plasma session. In Plasma, PowerDevil holds a
blocking inhibitor on the power key, so an injected press reached logind but
nothing suspended. That is session behaviour, not something the kernel path
changed, but it was not compared against the previous kernel.

## Continuous integration

One workflow covers the port: **Houji release** (`houji-release.yml`). It runs only
on demand (Actions, Houji release, Run workflow), uses no secrets beyond GitHub's
own token, and needs no phone. It builds the complete image on an arm64 runner and
publishes it as a GitHub pre-release: boot images, the installer tools and the root
image in 1.9 GB parts (GitHub assets must stay under 2 GiB). `userdata.img` is not
published; `make-userdata.py` builds it on your machine. It needs a tag name; turn
**publish** off to keep the files as an artifact instead.

No workflow runs on a push or pull request. When you start a release, it first
checks the tag name (and that the release does not exist yet) and runs the three
checks below as parallel jobs, taking about 4 minutes. The long image build starts
only if all of them pass, so a failed check costs minutes, not hours.

| Check | What it covers |
| --- | --- |
| Unit tests | Every Python and shell test (sleep, charging, thermal, installer, kernel and userdata updates, session switching, NFC and GPS helpers, panel gamma, and the patch checker itself). |
| Kernel patches | The shared Armada kernel series plus Houji's apply in build order, with every hunk parsed and no fuzz, onto the SHA-256-pinned Linux tarball. It then replays the Wi-Fi DBS/SBS parser on the phone's captured record. |
| Userspace patches and native tests | The pinned revisions of hexagonrpc, iio-sensor-proxy, gamescope and tqftpserv take the port's patches. The C and C++ tests are then built and run against those patched trees, ideally in a Fedora 44 container like the image. |

The same checks run locally, which is worth doing before a push:

```sh
ports/houji/unit-tests.sh
python3 ports/houji/check-patches.py kernel      # downloads Linux once (about 150 MB), cached
python3 ports/houji/check-patches.py userspace --keep /tmp/patched
ports/houji/native-tests.sh /tmp/patched         # needs gcc, g++ and glib, gio-unix, gudev, libqmi-glib, libqrtr-glib headers
```

The unit tests also need `zstd`, e2fsprogs and the Android sparse tools
(`img2simg`, `simg2img`); the userdata tests skip without them.

**Armada's image workflows are switched off on this fork** (Build images,
Packages, PR, PR disk image link, Build disk image, Publish disk image and
Promote release), in the repository's Actions settings. No workflow file was
changed, so merging upstream stays conflict-free. They publish signed container
images and need secrets this fork does not have (`SIGNING_SECRET`, R2 keys),
they build Armada's stock images rather than this port, and the gamescope
package recipe they pin (`3.16.29-ogc2`) no longer exists upstream, so they
failed on every push. To bring them back, sync with upstream, add the secrets
and run `gh workflow enable "<name>"`.

## Charging and sleep

### Fast charging

Fast charging is **experimental**, and matching stock HyperCharge behaviour is
not verified.

- Normal wired charging has a 3 A battery-current ceiling.
- Authenticated Xiaomi PD-PPS charging can select a separate 15.6 A ceiling,
  subject to thermal votes, charge level and firmware limits. A staged test
  measured about 19 W at the battery. Neither the ceilings nor the adapter's
  advertised 90 W are measured charging rates.
- The installed test build keeps a conservative 38°C battery cutoff for high
  current and falls back to 500 mA above it.
- The current sources include an **unflashed, hardware-unverified** change that
  allows the firmware's 15–47°C fast-charge range, only with authenticated
  PD-PPS, active firmware fast-charge mode, healthy telemetry and a matching
  kernel guard. Other charging modes keep the conservative limit.

### Sleep

- **On battery**, Power uses native `s2idle` (the default). Light sleep remains
  selectable in Armada's sleep-mode setting.
- **During an authenticated wired charging session**, Power uses light sleep so the
  host thermal monitor keeps running with the screen off. It returns to the
  configured mode when charging ends or the cable is unplugged.
- **Direct native suspend while charging** cuts the charging vote to 500 mA until
  the monitor resumes.
- **Sensor-DSP wakeups.** Every few minutes to an hour the sensor DSP asks the
  AP to save its gyroscope calibration to a registry file. The kernel wakes
  for that request, the file service answers, and `houji-sleep` suspends
  again with the desktop still frozen, so the screen and sound stay off. Any
  other wake (Power key, RTC alarm, USB peripheral, modem) ends the sleep
  normally. This replaced `systemd-sleep` for native sleep, and fixed a
  watchdog that used to take down audio, battery readings and sensors after
  resume. See [HISTORY.md](HISTORY.md#sleep-and-resume) for the cause.

## USB and privacy

USB starts in charging mode. Enable file transfer with Armada's MTP switch. OTG
role detection stays on for wired peripherals. The port adds no automatic SSH,
ADB, serial login, network gadget or test-network connection. Remote access must
be enabled by the owner.

GPS is opt-in and does not start at boot. Its client reports fix validity and
satellite counts without logging coordinates. Position data goes only through an
explicitly requested private channel.

**Do not publish** runtime logs, recordings, modem NV, factory calibration, SSH
keys or saved network profiles.

## NFC Manager

Open **NFC Manager** from KDE's application menu (under Settings). It is a
separate app, not a System Settings page.

**Read tags.** Turn on **Read nearby tags**, then hold a tag against the upper
back of the phone. The app shows scan status and a count of detected tags. Use
**Scan again** to retry. It does not display or save card contents.

**Emulate a tag.** Enter text and tap **Emulate text tag**. Turn on **Custom
serial** to enter a four-byte NFC-A identifier (for example the test value
`12:34:56:78`), or leave it off for automatic selection. Emulation serves only a
read-only NDEF text record of up to 200 UTF-8 bytes.

- Text, serial choice and mode are saved on the phone and restored at boot.
- Emulation keeps running after you close the app or end the desktop session.
- Tap **Stop emulation** to stop broadcasting and return to the saved reader setting.
- NFC starts off on a fresh install.

**How it works.** The interface runs as the desktop user. A background system
service does the hardware work, and Polkit allows changes from the active local
session. Settings live in `/var/lib/armada-nfc/settings.json`, readable only by
root and the authorized app interface. They are not written to logs or build
artifacts, and received card identifiers and contents are never saved. The build
installs the app, desktop entry, policies and a small kernel module that holds the
shared NFC supply during emulation. It uses the GTK4, libadwaita and PyGObject
libraries already in the pinned Armada base, so no private files or Python
downloads are needed. It does not implement payments, physical-card copying or
other card applications.

### Command-line reading

The port uses the stock controller firmware, the Linux NXP NCI driver and a
hash-pinned Fedora `neard` package. No NFC firmware download or card data is
needed. The radio stays off until enabled or a saved enabled mode is restored,
and the app pauses the reader while emulating.

Stop emulation in NFC Manager first. For a single read attempt, run on the phone:

```sh
sudo systemctl start neard
sudo busctl set-property org.neard /org/neard/nfc0 org.neard.Adapter Powered b true
sudo busctl call org.neard /org/neard/nfc0 org.neard.Adapter StartPollLoop s Initiator
sudo busctl tree org.neard
```

Hold the tag against the upper back. The object tree lists detected tags and any
readable NDEF records without dumping their contents or identifiers. The
controller is normally `nfc0`; use the path from `busctl tree org.neard` if it
differs. Applications can use neard's `org.neard.Tag` and `org.neard.Record`
interfaces. A card need not contain NDEF data, and detection does not grant
access to protected sectors.

When finished, stop polling and power the radio down before stopping the service.
If polling already stopped, continue with power-off. Restart the service before
another attempt if an unsupported card left a stale tag object:

```sh
sudo busctl call org.neard /org/neard/nfc0 org.neard.Adapter StopPollLoop
sudo busctl set-property org.neard /org/neard/nfc0 org.neard.Adapter Powered b false
sudo systemctl stop neard
```

Debug packet logging is off. Do not publish tag identifiers or card contents from
separately enabled NFC diagnostic tools.
